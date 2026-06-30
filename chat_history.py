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

        # Migrations — CREATE TABLE IF NOT EXISTS is a no-op on tables that
        # already existed before these columns were added, so they must be
        # added explicitly via ALTER TABLE for upgrading users.
        session_cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
        if "name" not in session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN name TEXT")

        msg_cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
        if "rating" not in msg_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN rating INTEGER NOT NULL DEFAULT 0")
        if "is_pinned" not in msg_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN is_pinned INTEGER NOT NULL DEFAULT 0")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_pinned ON messages(is_pinned)")
        conn.commit()
        conn.close()
        _initialized = True


# ── Public API ────────────────────────────────────────────────────────────────

def set_current_session(session_id: str) -> None:
    """
    Switch the active session — call this when the user opens an existing
    chat from the sidebar, so new messages append to THAT conversation
    instead of whatever session was active before.
    """
    global _current_session
    _current_session = session_id


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
    sid = _current_session
    with _lock:
        try:
            conn = _get_conn()
            conn.execute(
                "INSERT INTO messages (session_id, sender, content, ts) VALUES (?,?,?,?)",
                (sid, sender, content.strip(), time.time()),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            print(f"[G.I.L. HISTORY] save_message failed: {exc}")
            return

    # After the first full exchange (user + gil), auto-name the session —
    # same pattern as ChatGPT/Claude: title is generated once, never re-titled.
    if sender == "gil":
        threading.Thread(target=_maybe_auto_name, args=(sid,),
                         daemon=True, name="GIL-AutoName").start()


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


def list_sessions(limit: int = 30) -> list[dict]:
    """
    Return recent sessions for the sidebar, newest first.
    Each dict: {id, name, started_at, msg_count, preview}
    """
    _ensure_schema()
    with _lock:
        try:
            conn = _get_conn()
            rows = conn.execute("""
                SELECT s.id, s.name, s.started_at,
                       COUNT(m.id) as msg_count,
                       MIN(CASE WHEN m.sender='user' THEN m.content END) as preview
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                GROUP BY s.id
                HAVING msg_count > 0
                ORDER BY s.started_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []


_session_name_callback = None   # gui.py registers this to live-update the sidebar


def set_session_name_callback(fn) -> None:
    """Register a callback(session_id, title) fired when a session is auto-named."""
    global _session_name_callback
    _session_name_callback = fn


def _maybe_auto_name(session_id: str) -> None:
    """
    Trigger auto-naming once a session has its first full exchange,
    but only if it doesn't already have a name (auto or user-set).
    """
    try:
        conn = _get_conn()
        row = conn.execute("SELECT name FROM sessions WHERE id=?", (session_id,)).fetchone()
        cnt = conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE session_id=?", (session_id,)
        ).fetchone()
        conn.close()
    except Exception:
        return
    if row and row["name"]:
        return                       # already named — never re-title
    if not cnt or cnt["c"] < 2:
        return                       # need at least one full user+gil exchange
    _auto_name_session(session_id)


def _auto_name_session(session_id: str) -> None:
    """Ask Groq for a short topic title summarizing the conversation so far."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT sender, content FROM messages WHERE session_id=? ORDER BY ts LIMIT 4",
            (session_id,),
        ).fetchall()
        conn.close()
    except Exception:
        return
    if not rows:
        return

    transcript = "\n".join(f"{r['sender']}: {r['content'][:200]}" for r in rows)

    try:
        import os
        import requests
        key = os.getenv("GROQ_API_KEY", "") or os.getenv("GROQ_API_KEY_2", "")
        if not key:
            return
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.1-8b-instant",
                "messages": [
                    {"role": "system", "content":
                        "Generate a short 3-5 word title summarizing this conversation's "
                        "topic. No quotes, no trailing punctuation, no 'Title:' prefix — "
                        "just the title text itself."},
                    {"role": "user", "content": transcript},
                ],
                "max_tokens": 20,
                "temperature": 0.3,
            },
            timeout=8,
        )
        resp.raise_for_status()
        title = resp.json()["choices"][0]["message"]["content"].strip().strip('"\'').strip()
        if not title:
            return
        title = title[:48]
        rename_session(session_id, title)
        if _session_name_callback:
            try:
                _session_name_callback(session_id, title)
            except Exception:
                pass
    except Exception as exc:
        print(f"[G.I.L. HISTORY] auto-name failed: {exc}")


def backfill_unnamed_sessions(limit: int = 8) -> None:
    """
    One-time catch-up: auto-name existing sessions that have a full exchange
    but never got titled (e.g. they were created before auto-naming existed,
    or before the schema migration that added the `name` column).
    Runs each session sequentially with a short gap to stay polite to Groq.
    """
    def _run():
        try:
            conn = _get_conn()
            rows = conn.execute("""
                SELECT s.id FROM sessions s
                JOIN messages m ON m.session_id = s.id
                WHERE s.name IS NULL
                GROUP BY s.id
                HAVING COUNT(m.id) >= 2
                ORDER BY s.started_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
            conn.close()
        except Exception:
            return
        for row in rows:
            _auto_name_session(row["id"])
            time.sleep(1.5)   # stagger Groq calls

    threading.Thread(target=_run, daemon=True, name="GIL-BackfillNames").start()


def rename_session(session_id: str, name: str) -> None:
    """Give a session a custom display name."""
    _ensure_schema()
    with _lock:
        try:
            conn = _get_conn()
            conn.execute("UPDATE sessions SET name=? WHERE id=?", (name, session_id))
            conn.commit()
            conn.close()
        except Exception:
            pass


def load_session(session_id: str) -> list[dict]:
    """Load all messages for a specific session, oldest-first."""
    _ensure_schema()
    with _lock:
        try:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT sender, content, ts FROM messages WHERE session_id=? ORDER BY ts",
                (session_id,)
            ).fetchall()
            conn.close()
            return [{"sender": r["sender"], "content": r["content"],
                     "ts": r["ts"], "session_id": session_id,
                     "is_session_start": False}
                    for r in rows]
        except Exception:
            return []


def fork_session(session_id: str, before_ts: float) -> str:
    """
    Create a new session containing every message from `session_id` that
    happened strictly before `before_ts`. Used for conversation branching:
    editing an earlier message forks the chat from that point instead of
    overwriting it — the original conversation stays intact and reachable
    from the sidebar.
    Returns the new session id.
    """
    new_id = str(uuid.uuid4())
    _ensure_schema()
    with _lock:
        try:
            conn = _get_conn()
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                (new_id, time.time()),
            )
            rows = conn.execute(
                "SELECT sender, content, ts FROM messages "
                "WHERE session_id=? AND ts < ? ORDER BY ts",
                (session_id, before_ts),
            ).fetchall()
            for r in rows:
                conn.execute(
                    "INSERT INTO messages (session_id, sender, content, ts) VALUES (?,?,?,?)",
                    (new_id, r["sender"], r["content"], r["ts"]),
                )
            conn.commit()
            conn.close()
        except Exception as exc:
            print(f"[G.I.L. HISTORY] fork_session failed: {exc}")
    return new_id


def new_chat_session() -> str:
    """Start a completely fresh session (for New Chat button)."""
    global _current_session
    _current_session = str(uuid.uuid4())
    _ensure_schema()
    with _lock:
        try:
            conn = _get_conn()
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                (_current_session, time.time()),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
    return _current_session


def get_current_session() -> str:
    return _current_session


def rate_message(msg_id: int, rating: int) -> None:
    """Rate a GIL message. rating: 1=thumbs up, -1=thumbs down, 0=neutral."""
    _ensure_schema()
    with _lock:
        try:
            conn = _get_conn()
            conn.execute("UPDATE messages SET rating=? WHERE id=?", (rating, msg_id))
            conn.commit(); conn.close()
        except Exception:
            pass


def pin_message(msg_id: int, pinned: bool = True) -> None:
    """Star/unstar a message."""
    _ensure_schema()
    with _lock:
        try:
            conn = _get_conn()
            conn.execute("UPDATE messages SET is_pinned=? WHERE id=?",
                         (1 if pinned else 0, msg_id))
            conn.commit(); conn.close()
        except Exception:
            pass


def load_pinned() -> list[dict]:
    """Return all starred messages across all sessions."""
    _ensure_schema()
    with _lock:
        try:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT id, session_id, sender, content, ts FROM messages "
                "WHERE is_pinned=1 ORDER BY ts DESC LIMIT 50"
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []


def get_last_message_id() -> int | None:
    """Return the rowid of the most recently saved message (for rating/pinning)."""
    _ensure_schema()
    with _lock:
        try:
            conn = _get_conn()
            row = conn.execute(
                "SELECT id FROM messages ORDER BY ts DESC LIMIT 1"
            ).fetchone()
            conn.close()
            return row["id"] if row else None
        except Exception:
            return None


def search_sessions(query: str) -> list[dict]:
    """Search sessions where name or message content matches query."""
    _ensure_schema()
    q = f"%{query}%"
    with _lock:
        try:
            conn = _get_conn()
            rows = conn.execute("""
                SELECT DISTINCT s.id, s.name, s.started_at,
                       COUNT(m.id) as msg_count,
                       MIN(CASE WHEN m.sender='user' THEN m.content END) as preview
                FROM sessions s
                JOIN messages m ON m.session_id = s.id
                WHERE s.name LIKE ? OR m.content LIKE ?
                GROUP BY s.id
                ORDER BY s.started_at DESC
                LIMIT 20
            """, (q, q)).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []


def export_session(session_id: str) -> str:
    """Return the session as a plain-text transcript."""
    import datetime as _dt
    _ensure_schema()
    with _lock:
        try:
            conn = _get_conn()
            s = conn.execute(
                "SELECT name, started_at FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            rows = conn.execute(
                "SELECT sender, content, ts FROM messages WHERE session_id=? ORDER BY ts",
                (session_id,)
            ).fetchall()
            conn.close()
        except Exception:
            return ""

    name = (dict(s).get("name") or "GIL Chat") if s else "GIL Chat"
    started = (_dt.datetime.fromtimestamp(dict(s)["started_at"]).strftime("%Y-%m-%d %H:%M")
               if s else "")

    lines = [f"G.I.L. — {name}", f"Session: {started}", "=" * 60, ""]
    for r in rows:
        d      = dict(r)
        who    = "You" if d["sender"] == "user" else "G.I.L."
        ts_str = _dt.datetime.fromtimestamp(d["ts"]).strftime("%H:%M")
        lines.append(f"[{ts_str}] {who}")
        lines.append(d["content"])
        lines.append("")
    return "\n".join(lines)


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
