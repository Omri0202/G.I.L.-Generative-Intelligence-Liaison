"""
cloud_store.py — Vigil's memory when it runs in the cloud.

A free server forgets its disk whenever it restarts. So what Vigil needs to
remember — its watches, the chat, recent voice messages, the logins it reads
mail and the calendar with, the phones it notifies — is packed, encrypted and
kept in a private GitHub repository of the user's own, and unpacked again
when the server starts.

GitHub only ever holds ciphertext: the key that opens it lives in the
server's settings and on the PC that set it up. The repository is written
with a deploy key that opens that one repository and nothing else.

Env (on the server):
  GIL_DATA_REPO    git@github.com:<owner>/<repo>.git — or a local folder, for tests
  GIL_DATA_DEPLOY  the repository's deploy key (OpenSSH private key, or it base64'd
                   onto one line, which every host's settings page takes)
  GIL_DATA_SECRET  the bundle's key (Fernet)
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import threading
from pathlib import Path

from logger import get as _get_log

log = _get_log("vigil.store")

#: What's kept, by name in data/. Everything else there is the server's own scratch.
FILES = ("vigil.json", "vigil_chat.db", "token_usage.json", "vigil_push.json",
         "vigil_vapid.pem", "gmail_token.json", "calendar_token.json",
         "gmail_credentials.json")
VOICE_DIR = "vigil_voice"
VOICE_KEEP = 60              # the newest voice messages survive a restart; older ones don't
BLOB = "state.bin"
BRANCH = "main"
#: GitHub's published host key: the store talks to GitHub, never to whoever answers.
GITHUB_HOST_KEY = ("github.com ssh-ed25519 "
                   "AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl")
GIT_TIMEOUT = 120


class StoreError(Exception):
    """The store couldn't be read or written this time."""


# ── the bundle ────────────────────────────────────────────────────────────────

def _voice_files(data: Path) -> list[Path]:
    folder = data / VOICE_DIR
    if not folder.is_dir():
        return []
    files = sorted((f for f in folder.iterdir() if f.is_file()),
                   key=lambda f: f.stat().st_mtime)
    return files[-VOICE_KEEP:]


def _db_copy(src: Path) -> bytes:
    """A consistent copy of a live SQLite database, mid-write or not."""
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "copy.db"
        a, b = sqlite3.connect(src), sqlite3.connect(dst)
        try:
            a.backup(b)
        finally:
            b.close()
            a.close()
        return dst.read_bytes()


def _members(data: Path) -> list[tuple[str, bytes]]:
    out = []
    for name in FILES:
        path = data / name
        if not path.is_file():
            continue
        out.append((name, _db_copy(path) if name.endswith(".db") else path.read_bytes()))
    for f in _voice_files(data):
        out.append((f"{VOICE_DIR}/{f.name}", f.read_bytes()))
    return out


def fingerprint(data: Path) -> str:
    """Changes whenever anything kept changes — so an idle server doesn't
    write the same state again and again."""
    h = hashlib.sha256()
    for name in FILES:
        path = data / name
        if path.is_file():
            st = path.stat()
            h.update(f"{name}:{st.st_size}:{st.st_mtime_ns}\n".encode())
    for f in _voice_files(data):
        h.update(f"{f.name}\n".encode())
    return h.hexdigest()


def pack(data: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, raw in _members(data):
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            info.mode = 0o600
            tar.addfile(info, io.BytesIO(raw))
    return buf.getvalue()


def _allowed(name: str) -> bool:
    if name in FILES:
        return True
    folder, _, base = name.partition("/")
    return folder == VOICE_DIR and bool(base) and "/" not in base and base not in (".", "..")


def unpack(blob: bytes, data: Path) -> list[str]:
    """Only the names the store keeps, only inside data/ — whatever the bundle says."""
    names = []
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for m in tar.getmembers():
            if not (m.isfile() and _allowed(m.name)):
                log.warning("store: skipped %r", m.name)
                continue
            dest = data / m.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".part")
            tmp.write_bytes(tar.extractfile(m).read())
            tmp.replace(dest)
            names.append(m.name)
    return names


def seal(raw: bytes, secret: str) -> bytes:
    from cryptography.fernet import Fernet
    return Fernet(secret.encode()).encrypt(raw)


def unseal(blob: bytes, secret: str) -> bytes:
    from cryptography.fernet import Fernet, InvalidToken
    try:
        return Fernet(secret.encode()).decrypt(blob)
    except InvalidToken:
        raise StoreError("the stored state doesn't open with this key") from None


# ── the repository ────────────────────────────────────────────────────────────

def _key_text(value: str) -> str:
    value = value.strip()
    if not value or value.startswith("-----"):
        return value
    try:
        return base64.b64decode(value, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return value


class Store:
    def __init__(self, data: Path, repo: str, secret: str, deploy_key: str = ""):
        self.data = Path(data)
        self.repo = repo
        self.secret = secret
        self.deploy_key = deploy_key
        self.saved = ""                  # fingerprint of what's in the repo now
        self.writable = False            # only once the repo's state is known: never overwrite blind
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, data: Path) -> "Store | None":
        repo, secret = os.environ.get("GIL_DATA_REPO", ""), os.environ.get("GIL_DATA_SECRET", "")
        if not (repo and secret):
            return None
        return cls(data, repo, secret, _key_text(os.environ.get("GIL_DATA_DEPLOY", "")))

    def _git(self, *args: str, cwd: Path, home: Path) -> str:
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        if self.deploy_key:
            key, hosts = home / "deploy_key", home / "known_hosts"
            if not key.exists():
                key.write_text(self.deploy_key.strip() + "\n", encoding="utf-8")
                os.chmod(key, 0o600)
                hosts.write_text(GITHUB_HOST_KEY + "\n", encoding="utf-8")
            env["GIT_SSH_COMMAND"] = (f'ssh -i "{key.as_posix()}" -o IdentitiesOnly=yes '
                                      f'-o UserKnownHostsFile="{hosts.as_posix()}" '
                                      f'-o StrictHostKeyChecking=yes -o BatchMode=yes')
        try:
            r = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True,
                               text=True, timeout=GIT_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StoreError(f"git {args[0]}: {exc}") from None
        if r.returncode:
            raise StoreError(f"git {args[0]}: {(r.stderr or r.stdout).strip()[:300]}")
        return r.stdout

    def restore(self) -> str:
        """Unpack what the repository holds into data/: "restored", or "empty"
        for a repository nothing's been saved to yet. Raises StoreError when
        it can't tell — then nothing may be saved over it either."""
        with self._lock, tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            work = home / "repo"
            work.mkdir()
            self._git("init", "-q", cwd=work, home=home)
            heads = self._git("ls-remote", "--heads", self.repo, BRANCH, cwd=work, home=home)
            if not heads.strip():
                self.writable = True
                return "empty"
            self._git("fetch", "-q", "--depth", "1", self.repo, BRANCH, cwd=work, home=home)
            raw = self._git_bytes(work, home)
            names = unpack(unseal(raw, self.secret), self.data)
            self.saved = fingerprint(self.data)
            self.writable = True
            log.info("store: restored %d file(s)", len(names))
            return "restored"

    def _git_bytes(self, work: Path, home: Path) -> bytes:
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        r = subprocess.run(["git", "show", f"FETCH_HEAD:{BLOB}"], cwd=work, env=env,
                           capture_output=True, timeout=GIT_TIMEOUT)
        if r.returncode:
            raise StoreError(f"git show: {r.stderr.decode(errors='replace').strip()[:300]}")
        return r.stdout

    def save(self, force: bool = False) -> bool:
        """Write the state to the repository if it changed. One commit, no
        history: the repository holds the latest state and nothing older."""
        if not self.writable:
            return False
        with self._lock:
            now = fingerprint(self.data)
            if now == self.saved and not force:
                return False
            blob = seal(pack(self.data), self.secret)
            with tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                work = home / "repo"
                work.mkdir()
                (work / BLOB).write_bytes(blob)
                self._git("init", "-q", cwd=work, home=home)
                self._git("checkout", "-q", "-b", BRANCH, cwd=work, home=home)
                self._git("add", BLOB, cwd=work, home=home)
                self._git("-c", "user.name=G.I.L. Vigil", "-c", "user.email=vigil@localhost",
                          "commit", "-q", "-m", "Vigil state", cwd=work, home=home)
                self._git("push", "-q", "--force", self.repo, f"{BRANCH}:{BRANCH}",
                          cwd=work, home=home)
            self.saved = now
            log.info("store: saved (%d KB)", len(blob) // 1024)
            return True


def available() -> str:
    """'' when git and ssh are here to use; otherwise what's missing."""
    missing = [tool for tool in ("git", "ssh") if not shutil.which(tool)]
    return ", ".join(missing)
