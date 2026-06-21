"""
session_memory.py — G.I.L.
Persists the brain's conversation history between GIL restarts.

On shutdown  → save the last MAX_PAIRS exchanges to data/session_memory.json
On startup   → reload them into brain.history so GIL remembers what you were
               working on (up to 24 hours ago).

Only user + assistant messages are saved — system prompts are not persisted
because they are rebuilt fresh on every start.
"""

import json
import time
from pathlib import Path
from logger import get as _get_log

log      = _get_log("session_memory")
_PATH    = Path(__file__).parent / "data" / "session_memory.json"
MAX_PAIRS = 8          # keep last 8 exchange pairs (16 messages max)
MAX_AGE   = 86_400     # 24 hours — older context is stale and ignored


def save(history: list) -> None:
    """
    Persist recent history to disk.
    Call on GIL shutdown or periodically.
    """
    try:
        # Keep only conversational turns, trim to last MAX_PAIRS pairs
        turns    = [h for h in history if h.get("role") in ("user", "assistant")]
        trimmed  = turns[-(MAX_PAIRS * 2):]
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(
            json.dumps({"saved_at": time.time(), "history": trimmed},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info("session memory saved (%d messages)", len(trimmed))
    except Exception as exc:
        log.warning("could not save session memory: %s", exc)


def load() -> list:
    """
    Return saved history to seed the brain on startup.
    Returns [] if nothing saved, file missing, or context is too old.
    """
    try:
        data     = json.loads(_PATH.read_text(encoding="utf-8"))
        saved_at = data.get("saved_at", 0)
        if time.time() - saved_at > MAX_AGE:
            log.info("session memory too old (>24 h) — starting fresh")
            return []
        history = data.get("history", [])
        log.info("loaded %d messages from previous session", len(history))
        return history
    except FileNotFoundError:
        return []
    except Exception as exc:
        log.warning("could not load session memory: %s", exc)
        return []
