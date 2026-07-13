"""
gil_config.py — one robust place to read/write data/gil_config.json.

A corrupt config file used to silently break features: voice_enabled() would
fall back to "unmuted" and set_voice_enabled() couldn't save, so the mute
button appeared dead. This helper tolerates corruption — it recovers the first
valid JSON object it can find — and writes atomically so a config can never be
left half-written again.
"""

import json
import re
import threading
from pathlib import Path

from logger import get as _get_log

log = _get_log("config")

_PATH = Path(__file__).parent / "data" / "gil_config.json"
_LOCK = threading.Lock()


def load() -> dict:
    """Return the config dict, recovering from corruption where possible."""
    try:
        raw = _PATH.read_text(encoding="utf-8")
    except Exception:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        # Recover: parse the first well-formed {...} object in the file.
        try:
            depth = start = 0
            for i, ch in enumerate(raw):
                if ch == "{":
                    if depth == 0:
                        start = i
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        data = json.loads(raw[start:i + 1])
                        log.warning("recovered corrupt gil_config.json")
                        save(data)   # rewrite it clean
                        return data
        except Exception:
            log.error("gil_config.json unreadable — using defaults", exc_info=True)
        return {}


def save(cfg: dict) -> bool:
    """Atomic write: never leaves a half-written / corrupt config behind."""
    with _LOCK:
        try:
            _PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = _PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            tmp.replace(_PATH)   # atomic on Windows + POSIX
            return True
        except Exception:
            log.error("could not save gil_config.json", exc_info=True)
            return False


def get(key: str, default=None):
    return load().get(key, default)


def set(key: str, value) -> bool:
    cfg = load()
    cfg[key] = value
    return save(cfg)
