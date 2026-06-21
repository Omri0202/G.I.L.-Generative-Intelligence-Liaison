"""
chat_history.py — G.I.L.
Persistent chat storage. Every user↔GIL exchange is saved to SQLite and
reloaded when the chat window re-opens, even after a full restart.

Public API
----------
init_session()          → call once per GIL startup
save_message(s, text)   → 'user' or 'gil'
load_recent(n)          → list[dict] oldest-first
end_session()           → call on shutdown (optional, for clean ended_at)
"""

import sqlite3
import threading
import time
import uuid
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "chat_history.db"

_lock              = threading.Lock()
_current_session   = ""   # set by init_session()
_initialized       = False


# ── Init ──────────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_schema() -> None:
    global _initialized
    if _initialized:
        return
    with _lock:
        if _initialized:
            return
        conn = _get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id         TEXT  PRIMARY KEY,
                started_at REAL  NOT NULL,
                ended_at   REAL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT    NOT NULL,
                sender     TEXT    NOT NULL,
                content    TEXT    NOT NULL,
                ts         REAL    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(ts);
        """)
        conn.commit()
        conn.close()
        _initialized = True


# ── Public API ────────────────────────────────────────────────────────────────

def init_session() -> str:
    """
    Start a new chat session. Call once when GIL launches.
    Returns the session id.
    """
    global _current_session
    _ensure_schema()
    _current_session = str(uuid.uuid4())
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
            (_current_session, time.time()),
        )
        conn.commit()
        conn.close()
    return _current_session


def save_message(sender: str, content: str) -> None:
    """
    Persist a message to the current session.
    sender: 'user' or 'gil'
    Silently no-ops if session hasn't been initialised.
    """
    if not _current_session or not content.strip():
        return
    _ensure_schema()
    with _lock:
        try:
            conn = _get_conn()
            conn.execute(
                "INSERT INTO messages (session_id, sender, content, ts) VALUES (?,?,?,?)",
                (_current_session, sender, content.strip(), time.time()),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            print(f"[G.I.L. HISTORY] save_message failed: {exc}")


def load_recent(limit: int = 80) -> list[dict]:
    """
    Return the most-recent `limit` messages, oldest-first.
    Each dict has: sender, content, ts, session_id, is_session_start
    `is_session_start` is True on the first message of a session block.
    """
    _ensure_schema()
    with _lock:
        try:
            conn = _get_conn()
            rows = conn.execute("""
                SELECT session_id, sender, content, ts
                FROM messages
                ORDER BY ts DESC
                LIMIT ?
            """, (limit,)).fetchall()
            conn.close()
        except Exception as exc:
            print(f"[G.I.L. HISTORY] load_recent failed: {exc}")
            return []

    # Reverse to oldest-first
    rows = list(reversed(rows))

    result = []
    prev_session = None
    for row in rows:
        d = {
            "session_id":       row["session_id"],
            "sender":           row["sender"],
            "content":          row["content"],
            "ts":               row["ts"],
            "is_session_start": row["session_id"] != prev_session,
        }
        prev_session = row["session_id"]
        result.append(d)

    return result


def end_session() -> None:
    """Mark the current session's end time. Optional — only for clean records."""
    if not _current_session:
        return
    with _lock:
        try:
            conn = _get_conn()
            conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?",
                (time.time(), _current_session),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass


def clear_old(days: int = 30) -> None:
    """Delete messages older than `days` days. Call on startup to keep DB small."""
    _ensure_schema()
    cutoff = time.time() - days * 86400
    with _lock:
        try:
            conn = _get_conn()
            conn.execute("DELETE FROM messages WHERE ts < ?", (cutoff,))
            conn.execute(
                "DELETE FROM sessions WHERE ended_at IS NOT NULL AND ended_at < ?",
                (cutoff,),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            print(f"[G.I.L. HISTORY] clear_old failed: {exc}")
