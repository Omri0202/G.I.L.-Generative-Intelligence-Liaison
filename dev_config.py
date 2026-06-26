"""
dev_config.py — G.I.L.
Developer mode configuration — reads/writes data/dev_config.json
and applies settings (GitHub token, editor, etc.) to the environment.
"""

import json
import os
import subprocess
from pathlib import Path

_CFG = Path(__file__).parent / "data" / "dev_config.json"


def _load() -> dict:
    try:
        return json.loads(_CFG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    _CFG.parent.mkdir(parents=True, exist_ok=True)
    existing = _load()
    existing.update(data)
    _CFG.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Public API ────────────────────────────────────────────────────────────────

def is_enabled() -> bool:
    return _load().get("enabled", False)


def enable() -> None:
    _save({"enabled": True})
    _apply_env()


def disable() -> None:
    _save({"enabled": False})


def get(key: str, default=None):
    return _load().get(key, default)


def save(**kwargs) -> None:
    _save(kwargs)
    _apply_env()


def _apply_env() -> None:
    """Push stored settings into the running process environment."""
    cfg = _load()
    token = cfg.get("github_token", "")
    if token:
        os.environ["GITHUB_TOKEN"] = token
    interval = cfg.get("screen_interval", 30)
    os.environ["GIL_SCREEN_INTERVAL"] = str(interval)


def detect_editors() -> list[dict]:
    """Return list of {name, cmd, icon} for editors found on this machine."""
    candidates = [
        {"name": "VS Code",    "cmd": "code",         "icon": ""},
        {"name": "Cursor",     "cmd": "cursor",       "icon": ""},
        {"name": "WebStorm",   "cmd": "webstorm",     "icon": ""},
        {"name": "PyCharm",    "cmd": "pycharm",      "icon": ""},
        {"name": "Vim",        "cmd": "vim",          "icon": ""},
        {"name": "Neovim",     "cmd": "nvim",         "icon": ""},
        {"name": "Notepad++",  "cmd": "notepad++",    "icon": ""},
        {"name": "Sublime",    "cmd": "subl",         "icon": ""},
    ]
    found = []
    for ed in candidates:
        try:
            r = subprocess.run(["where", ed["cmd"]], capture_output=True, timeout=3)
            if r.returncode == 0:
                found.append(ed)
        except Exception:
            pass
    if not found:
        found.append({"name": "VS Code", "cmd": "code", "icon": ""})
    return found


# Apply on import (in case a stored token needs to be in the env)
_apply_env()
