"""
updater.py — G.I.L. auto-updater
Checks GitHub releases for a newer version and applies the update in-place.

How updates work
----------------
1. Background thread checks the GitHub releases API on startup.
2. If a newer version is available, shows a toast notification.
3. User clicks "Update" — GIL downloads the new GIL.zip from the release.
4. Files are extracted over the current installation directory.
5. User data (.env, chat_history.db, user_profile.json, etc.) is never touched.
6. User clicks "Restart" — the updated code takes effect.

For PyInstaller builds, only the _internal/ folder (Python code + libs) is
replaced. The GIL.exe launcher stays untouched (it rarely needs updating).
"""

import os
import sys
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path

import requests

from version import VERSION, GITHUB_REPO, ASSET_NAME

_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# Files and folders that must never be overwritten during an update
_PROTECTED = {
    ".env",
    "chat_history.db",
    "user_profile.json",
    "gmail_token.json",
    "calendar_token.json",
    "gil_brain.db",
    "gil_memory.json",
    "gesture_config.json",
    "face_embeddings.json",
}


# ── Version comparison ─────────────────────────────────────────────────────────

def _parse(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.strip().lstrip("v").split("."))


# ── GitHub API ────────────────────────────────────────────────────────────────

def fetch_latest_release() -> dict | None:
    """
    Hit the GitHub releases API.
    Returns {version, download_url, notes} if a newer version exists, else None.
    """
    try:
        resp = requests.get(
            _API_URL,
            timeout=6,
            headers={"Accept": "application/vnd.github.v3+json",
                     "User-Agent": "GIL-Updater/1.0"},
        )
        if resp.status_code != 200:
            return None
        data    = resp.json()
        latest  = data.get("tag_name", "").lstrip("v").strip()
        if not latest:
            return None
        if _parse(latest) <= _parse(VERSION):
            return None              # already up to date

        for asset in data.get("assets", []):
            if asset.get("name", "").lower() == ASSET_NAME.lower():
                return {
                    "version":      latest,
                    "download_url": asset["browser_download_url"],
                    "notes":        (data.get("body") or "").strip()[:300],
                }
    except Exception as exc:
        print(f"[G.I.L. UPDATER] Check failed: {exc}")
    return None


# ── Download & install ────────────────────────────────────────────────────────

def download_and_install(
    download_url: str,
    on_progress: callable | None = None,
) -> tuple[bool, str]:
    """
    Download GIL.zip and extract it over the current installation.
    Returns (success: bool, message: str).
    on_progress(fraction: float) is called during download.
    """
    install_dir = _get_install_dir()
    print(f"[G.I.L. UPDATER] Installing into: {install_dir}")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "GIL_update.zip"

            # ── Download ──────────────────────────────────────────────────────
            resp  = requests.get(download_url, stream=True, timeout=120)
            total = int(resp.headers.get("content-length", 0))
            done  = 0
            with open(zip_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    fh.write(chunk)
                    done += len(chunk)
                    if on_progress and total:
                        on_progress(done / total)

            # ── Extract ───────────────────────────────────────────────────────
            extract_dir = Path(tmp) / "extracted"
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)

            # The zip may contain a top-level GIL/ folder
            src = extract_dir / "GIL"
            if not src.exists():
                src = extract_dir

            # ── Copy files ────────────────────────────────────────────────────
            copied = 0
            for item in src.rglob("*"):
                if item.is_dir():
                    continue
                if item.name in _PROTECTED:
                    continue

                rel = item.relative_to(src)
                dst = install_dir / rel

                # Never overwrite the running .exe itself
                if dst.suffix.lower() == ".exe" and dst.exists():
                    continue

                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(item), str(dst))
                copied += 1

            print(f"[G.I.L. UPDATER] Copied {copied} files.")
            return True, f"Updated successfully. {copied} files replaced."

    except Exception as exc:
        msg = f"Update failed: {exc}"
        print(f"[G.I.L. UPDATER] {msg}")
        return False, msg


def _get_install_dir() -> Path:
    """
    Returns the root of the current GIL installation.
    - PyInstaller build: parent of GIL.exe
    - Source run:        project root (where main.py lives)
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


# ── Background check + GUI notification ──────────────────────────────────────

def check_and_notify(window) -> None:
    """
    Spawn a background thread that checks for updates.
    If one is available, calls window.show_update_toast(info).
    Safe to call before the window is fully visible.
    """
    def _run():
        info = fetch_latest_release()
        if info:
            print(f"[G.I.L. UPDATER] New version available: {info['version']}")
            window.after(0, lambda: window.show_update_toast(info))

    threading.Thread(target=_run, daemon=True, name="GIL-UpdateCheck").start()
