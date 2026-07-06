"""
file_access.py — G.I.L. permission-gated file access (Claude-style).

Principle (mirrors Claude Code): GIL never reads, lists, or edits files on
the computer without permission. The FIRST time he wants to touch a folder
he asks in the chat; once the user approves that folder it stays readable
for the rest of the session (and, if the user says "always", forever) — so
he doesn't nag on every file inside it. Writing is a separate, always-asked
permission handled by the code agent's per-change approval.

Approved folders:
  - session grants: live in memory, cleared on restart
  - persistent grants: saved to data/gil_config.json -> "allowed_dirs"
Home-owned, non-system folders under the user profile are pre-trusted for
READ so ordinary "what's in my Downloads" requests are smooth; anything
outside (other drives, Windows/System, Program Files) always asks.
"""

import json
import os
import threading
from pathlib import Path

from logger import get as _get_log

log = _get_log("fileaccess")

_CFG = Path(__file__).parent / "data" / "gil_config.json"
_session_grants: set[str] = set()
_lock = threading.Lock()

# Folders under the user's profile that are safe to READ without a prompt.
_HOME = Path.home()
_PRETRUSTED = {
    (_HOME / n).resolve() for n in
    ("Documents", "Downloads", "Desktop", "Pictures", "Music", "Videos")
}
_PRETRUSTED.add((Path(__file__).parent).resolve())   # GIL's own project

# Never auto-trust these even if under home — always ask.
_SENSITIVE = ("appdata", ".ssh", ".aws", "credentials", "cookies",
              "\\windows", "\\system32", "program files")


def _load_persistent() -> set[str]:
    try:
        cfg = json.loads(_CFG.read_text(encoding="utf-8"))
        return {str(Path(p).resolve()) for p in cfg.get("allowed_dirs", [])}
    except Exception:
        return set()


def _save_persistent(dirs: set[str]) -> None:
    try:
        cfg = json.loads(_CFG.read_text(encoding="utf-8")) if _CFG.exists() else {}
        cfg["allowed_dirs"] = sorted(dirs)
        _CFG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        log.error("could not persist allowed_dirs", exc_info=True)


def _folder_of(path: str | Path) -> Path:
    p = Path(os.path.expandvars(os.path.expanduser(str(path))))
    try:
        p = p.resolve()
    except Exception:
        pass
    return p if p.is_dir() else p.parent


def is_read_allowed(path: str | Path) -> bool:
    """True if GIL may READ this path without asking."""
    folder = _folder_of(path)
    fs = str(folder).lower()
    if any(s in fs for s in _SENSITIVE):
        # sensitive → only if explicitly granted
        return str(folder) in _session_grants or str(folder) in _load_persistent()
    # pre-trusted home folders (and their children)
    for t in _PRETRUSTED:
        try:
            if folder == t or t in folder.parents or folder in t.parents:
                return True
        except Exception:
            continue
    with _lock:
        if str(folder) in _session_grants:
            return True
    return str(folder) in _load_persistent()


def grant(path: str | Path, remember: bool = False) -> None:
    folder = str(_folder_of(path))
    with _lock:
        _session_grants.add(folder)
    if remember:
        dirs = _load_persistent()
        dirs.add(folder)
        _save_persistent(dirs)
    log.info("granted %s access to %s", "persistent" if remember else "session", folder)


def request(engine, path: str | Path, why: str,
            on_approved, remember_option: bool = True) -> bool:
    """
    Ensure read access to `path`. If already allowed, run on_approved() now and
    return True. Otherwise post a permission request in the chat, arm the
    yes/no gate, and return False (on_approved runs later if the user says yes).
    """
    if is_read_allowed(path):
        on_approved()
        return True
    folder = _folder_of(path)
    engine._pending_file_access = {
        "folder": str(folder),
        "on_approved": on_approved,
    }
    remember_hint = ' (say "always" to remember this folder)' if remember_option else ""
    msg = (f"I need your permission to access **{folder}** — {why}.\n\n"
           f"Say **yes** to allow it for now{remember_hint}, or **no** to skip.")
    try:
        engine.window.add_chat_message(msg, "gil")
        spoken = f"Can I access {folder.name or folder}? {why}."
        engine.window.set_state("speaking", said=spoken)
        engine._speak(spoken)
        engine._last_spoke_at[0] = __import__("time").time() - 1.5
        engine._last_said[0] = spoken
        engine.window.set_state("listening")
    except Exception:
        log.debug("permission prompt failed", exc_info=True)
    return False
