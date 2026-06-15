"""
goal_tracker.py — Project G.I.L.
Tracks what the user is currently working on across sessions.
GIL asks once when context switches, stores the answer, injects it into every brain call.
"""

import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

DB_PATH = Path(__file__).parent / "data" / "gil_brain.db"
_lock   = threading.Lock()

# ── DB init ───────────────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db() -> None:
    with _lock:
        conn = _get_db()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS goals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                description TEXT    NOT NULL,
                app_context TEXT    DEFAULT '',
                file_context TEXT   DEFAULT '',
                started_at  REAL    NOT NULL,
                last_seen_at REAL   NOT NULL,
                ended_at    REAL,
                active      INTEGER DEFAULT 1
            );
        """)
        conn.commit()
        conn.close()


_init_db()

# ── In-memory state ───────────────────────────────────────────────────────────

_current_goal: dict | None = None
_ask_cooldown: float = 0.0          # unix ts — don't ask twice in a session
_ASK_COOLDOWN_SECS = 60.0 * 20     # wait 20 min before asking about same app again
_asked_apps: set[str] = set()       # apps we already asked about this session
_checkin_callbacks: list[Callable] = []   # fired when it's time to check in


def _load_active_goal() -> dict | None:
    with _lock:
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM goals WHERE active=1 ORDER BY last_seen_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
    return dict(row) if row else None


def _save_goal(description: str, app: str = "", file: str = "") -> dict:
    now = time.time()
    with _lock:
        conn = _get_db()
        # deactivate any previous active goals
        conn.execute("UPDATE goals SET active=0, ended_at=? WHERE active=1", (now,))
        cur = conn.execute(
            "INSERT INTO goals (description, app_context, file_context, started_at, last_seen_at, active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (description.strip(), app, file, now, now),
        )
        gid = cur.lastrowid
        conn.commit()
        conn.close()
    return {"id": gid, "description": description, "app_context": app,
            "file_context": file, "started_at": now, "last_seen_at": now}


def _touch_goal(goal_id: int) -> None:
    with _lock:
        conn = _get_db()
        conn.execute("UPDATE goals SET last_seen_at=? WHERE id=?", (time.time(), goal_id))
        conn.commit()
        conn.close()


# ── Public API ────────────────────────────────────────────────────────────────

def start() -> None:
    """Load active goal from DB on startup."""
    global _current_goal
    _current_goal = _load_active_goal()
    if _current_goal:
        print(f"[G.I.L. GOAL] Resumed: {_current_goal['description'][:60]}")
    # Start the check-in loop
    threading.Thread(target=_checkin_loop, daemon=True, name="GIL-GoalCheckin").start()


def get_active_goal() -> dict | None:
    return _current_goal


def get_goal_text() -> str:
    g = _current_goal
    if not g:
        return ""
    return g.get("description", "")


def set_goal(description: str, app: str = "", file: str = "") -> None:
    global _current_goal
    _current_goal = _save_goal(description, app, file)
    print(f"[G.I.L. GOAL] Set: {description[:60]}")


def clear_goal() -> None:
    global _current_goal
    if _current_goal:
        with _lock:
            conn = _get_db()
            conn.execute("UPDATE goals SET active=0, ended_at=? WHERE active=1", (time.time(),))
            conn.commit()
            conn.close()
    _current_goal = None
    print("[G.I.L. GOAL] Goal cleared.")


def goal_age_minutes() -> float:
    g = _current_goal
    if not g:
        return 0.0
    return (time.time() - g.get("started_at", time.time())) / 60.0


def update_from_reply(user_text: str, app: str = "", file: str = "") -> None:
    """
    Called after user speaks — if they mention a goal/task, update it.
    Simple heuristic: look for intent phrases in the text.
    """
    global _current_goal
    lower = user_text.lower()
    goal_starters = ("i'm working on", "i am working on", "working on", "trying to",
                     "i need to", "i want to", "i'm trying to", "building", "debugging",
                     "fixing", "writing", "studying", "learning about", "i'm learning",
                     "im working on", "im trying to")
    for phrase in goal_starters:
        if phrase in lower:
            # Extract everything after the trigger phrase as the goal
            idx = lower.find(phrase)
            goal_text = user_text[idx + len(phrase):].strip().rstrip(".!?")
            if len(goal_text) > 6:
                set_goal(goal_text, app=app, file=file)
                return

    # If goal exists, just touch it
    if _current_goal:
        _touch_goal(_current_goal["id"])
        _current_goal["last_seen_at"] = time.time()


def should_ask_about_context(app: str) -> bool:
    """
    Returns True if GIL should ask what the user is working on.
    Respects cooldown and per-app-session tracking.
    """
    if not app or app.lower() in ("", "explorer", "unknown"):
        return False
    app_key = app.lower().strip()
    if app_key in _asked_apps:
        return False
    # Don't ask if there's already an active goal set in last 30 min
    g = _current_goal
    if g and (time.time() - g.get("last_seen_at", 0)) < 1800:
        return False
    return True


def mark_asked(app: str) -> None:
    _asked_apps.add(app.lower().strip())


def on_checkin(fn: Callable) -> None:
    """Register callback for when it's time to check in on active goal."""
    _checkin_callbacks.append(fn)


def build_goal_context() -> str:
    """Formatted string for injection into brain prompt."""
    g = _current_goal
    if not g:
        return ""
    age = goal_age_minutes()
    if age < 1:
        when = "just started"
    elif age < 60:
        when = f"{int(age)} min ago"
    else:
        when = f"{int(age/60)}h ago"
    lines = [f"ACTIVE GOAL: {g['description']}"]
    if g.get("app_context"):
        lines.append(f"  App: {g['app_context']}")
    if g.get("file_context"):
        lines.append(f"  File: {g['file_context']}")
    lines.append(f"  Started: {when}")
    return "\n".join(lines)


# ── Check-in loop ─────────────────────────────────────────────────────────────

_CHECKIN_INTERVAL = 45 * 60   # 45 minutes
_last_checkin     = time.time()


def _checkin_loop() -> None:
    global _last_checkin
    while True:
        time.sleep(60)
        g = _current_goal
        if g and (time.time() - _last_checkin) >= _CHECKIN_INTERVAL:
            _last_checkin = time.time()
            msg = f"Still on {g['description'][:50]}?"
            for fn in list(_checkin_callbacks):
                try:
                    fn(msg)
                except Exception:
                    pass
