"""
reminders.py — Project G.I.L.
Voice-triggered reminders with spoken alerts.
Supports: "remind me to X in N minutes/hours" and "remind me to X at HH:MM"
"""

import json
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

_DATA_FILE                               = Path(__file__).parent / "data" / "reminders.json"
_speak_callback: Callable[[str], None] | None = None
_window_ref                              = None


def set_speak_callback(fn: Callable[[str], None]) -> None:
    global _speak_callback
    _speak_callback = fn


def set_window_ref(window) -> None:
    global _window_ref
    _window_ref = window


# ── Storage ───────────────────────────────────────────────────────────────────

def _load() -> list:
    try:
        if _DATA_FILE.exists():
            return json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _save(reminders: list) -> None:
    _DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    _DATA_FILE.write_text(json.dumps(reminders, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Parsing ───────────────────────────────────────────────────────────────────

def _parse_duration_secs(text: str) -> int | None:
    text  = text.lower()
    total = 0
    found = False
    for pat, mult in ((r"(\d+)\s*hour", 3600), (r"(\d+)\s*minute", 60), (r"(\d+)\s*second", 1)):
        m = re.search(pat, text)
        if m:
            total += int(m.group(1)) * mult
            found  = True
    return total if found else None


def add_reminder(text: str) -> str:
    lower = text.lower()

    # Duration format: "in N minutes/hours"
    dur_m = re.search(r"in\s+(\d+(?:\s+and\s+\d+)?\s*(?:hour|minute|second)[s]?)", lower)
    if dur_m:
        secs = _parse_duration_secs(dur_m.group(0))
        if secs:
            task_m = re.search(r"remind\s+me\s+(?:to\s+)?(.+?)\s+in\s+\d", lower)
            task   = task_m.group(1).strip() if task_m else text.strip()
            _schedule(task, time.time() + secs)
            mins = secs // 60
            return (
                f"Got it — I'll remind you to {task} in "
                f"{mins} minute{'s' if mins != 1 else ''}."
                if mins > 0 else
                f"Got it — I'll remind you to {task} in {secs} seconds."
            )

    # Time format: "at 3pm" / "at 15:30"
    time_m = re.search(r"at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", lower)
    if time_m:
        hr     = int(time_m.group(1))
        mn     = int(time_m.group(2) or 0)
        period = (time_m.group(3) or "").lower()
        if period == "pm" and hr < 12:
            hr += 12
        elif period == "am" and hr == 12:
            hr  = 0
        now    = datetime.now()
        target = now.replace(hour=hr, minute=mn, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        task_m = re.search(r"remind\s+me\s+(?:to\s+)?(.+?)\s+at\s+\d", lower)
        task   = task_m.group(1).strip() if task_m else text.strip()
        _schedule(task, target.timestamp())
        return f"Got it — I'll remind you to {task} at {target.strftime('%I:%M %p')}."

    return "I couldn't parse that. Try: 'remind me to call mom in 30 minutes'."


def _schedule(task: str, fire_at: float) -> None:
    reminders = _load()
    reminders.append({"task": task, "fire_at": fire_at, "done": False})
    _save(reminders)
    _arm(task, fire_at)


def _arm(task: str, fire_at: float) -> None:
    delay = max(0, fire_at - time.time())

    def _fire():
        time.sleep(delay)
        msg = f"Reminder: {task}."
        print(f"[G.I.L. REMINDER] {msg}")
        if _speak_callback:
            try:
                _speak_callback(msg)
            except Exception as exc:
                print(f"[G.I.L. REMINDER] Speak error: {exc}")
        if _window_ref:
            try:
                _window_ref.after(0, lambda m=msg: _window_ref.show_proactive_suggestion(m))
            except Exception:
                pass

    threading.Thread(target=_fire, daemon=True, name="GIL-Reminder").start()


def list_reminders() -> str:
    active = [r for r in _load() if not r.get("done") and r["fire_at"] > time.time()]
    if not active:
        return "No active reminders."
    parts = []
    for r in active:
        dt = datetime.fromtimestamp(r["fire_at"])
        parts.append(f"{r['task']} at {dt.strftime('%I:%M %p')}")
    return "Reminders: " + "; ".join(parts) + "."


def restore_pending() -> None:
    """Re-arm reminders that survived a restart."""
    now = time.time()
    for r in _load():
        if not r.get("done") and r["fire_at"] > now:
            _arm(r["task"], r["fire_at"])
    print("[G.I.L. REMINDERS] Pending reminders restored.")
