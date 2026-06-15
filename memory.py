"""
memory.py — Project G.I.L.
Persistent memory system — SQLite + FTS5 full-text search.

Three layers:
1. Memories   — facts, preferences, decisions GIL extracts from conversation
2. Session    — last task, last active (lightweight KV in same DB)
3. Extraction — background Ollama call after every turn to learn automatically
"""

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "gil_brain.db"


# ── DB init ───────────────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db() -> None:
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            type         TEXT    NOT NULL DEFAULT 'fact',
            content      TEXT    NOT NULL,
            source       TEXT    DEFAULT '',
            importance   INTEGER DEFAULT 5,
            created_at   REAL    NOT NULL,
            last_accessed REAL,
            access_count INTEGER DEFAULT 0
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            content, type, source,
            content='memories', content_rowid='id'
        );

        CREATE TABLE IF NOT EXISTS session (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at REAL NOT NULL
        );
    """)
    conn.close()


_init_db()

_db_lock = threading.Lock()


# ── Core memory operations ────────────────────────────────────────────────────

def remember(
    content: str,
    mem_type: str = "fact",
    source: str = "",
    importance: int = 5,
) -> int:
    """Store a memory. Deduplicates exact content. Returns ID."""
    content = content.strip()
    if not content:
        return -1

    with _db_lock:
        conn = _get_db()
        existing = conn.execute(
            "SELECT id FROM memories WHERE content = ?", (content,)
        ).fetchone()
        if existing:
            conn.close()
            return existing["id"]

        cur = conn.execute(
            "INSERT INTO memories (type, content, source, importance, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (mem_type, content, source, importance, time.time()),
        )
        mem_id = cur.lastrowid
        conn.execute(
            "INSERT INTO memory_fts (rowid, content, type, source) VALUES (?, ?, ?, ?)",
            (mem_id, content, mem_type, source),
        )
        conn.commit()
        conn.close()

    print(f"[G.I.L. MEMORY] Stored [{mem_type}]: {content[:70]}")
    return mem_id


_FTS5_STRIP   = str.maketrans("", "", "()^+:'\"*")
_FTS5_KEYWORDS = {"and", "or", "not", "near"}


def recall(query: str, limit: int = 5) -> list[dict]:
    """Full-text search memories relevant to query."""
    words = [
        w for w in
        query.translate(_FTS5_STRIP).replace("-", " ").split()
        if len(w) > 2 and w.lower() not in _FTS5_KEYWORDS
    ]
    if not words:
        return []

    fts_query = " OR ".join(words[:6])
    with _db_lock:
        conn = _get_db()
        try:
            results = conn.execute(
                """SELECT m.id, m.type, m.content, m.importance, m.created_at, m.access_count
                   FROM memory_fts f
                   JOIN memories m ON f.rowid = m.id
                   WHERE memory_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (fts_query, limit),
            ).fetchall()
        except Exception:
            results = []

        for r in results:
            conn.execute(
                "UPDATE memories SET last_accessed=?, access_count=access_count+1 WHERE id=?",
                (time.time(), r["id"]),
            )
        conn.commit()
        conn.close()

    return [dict(r) for r in results]


def get_important_memories(limit: int = 6) -> list[dict]:
    with _db_lock:
        conn = _get_db()
        results = conn.execute(
            "SELECT * FROM memories ORDER BY importance DESC, access_count DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
    return [dict(r) for r in results]


# ── Smart context builder (injected into every LLM call) ─────────────────────

def build_memory_context(user_message: str) -> str:
    """
    Search memories relevant to what the user just said and return a formatted
    context block for injection into the brain's system prompt.
    Fast — FTS queries only, no LLM call.
    """
    parts = []

    # Relevant memories for this specific message
    if len(user_message) > 5:
        relevant = recall(user_message, limit=4)
        if relevant:
            lines = [f"  - [{m['type']}] {m['content']}" for m in relevant]
            parts.append("RELEVANT MEMORIES:\n" + "\n".join(lines))
    else:
        relevant = []

    # Top important memories (always present)
    important = get_important_memories(limit=5)
    seen = {m["content"] for m in relevant}
    imp_lines = [f"  - {m['content']}" for m in important if m["content"] not in seen]
    if imp_lines:
        parts.append("KEY FACTS I KNOW ABOUT YOU:\n" + "\n".join(imp_lines[:3]))

    # Last session
    last_task  = _get_kv("last_task")
    last_stamp = _get_kv("last_updated")
    if last_task and last_stamp:
        try:
            delta = datetime.now() - datetime.fromisoformat(last_stamp)
            if delta < timedelta(hours=20):
                h    = delta.total_seconds() / 3600
                when = "just now" if h < 1 else (
                    "about an hour ago" if h < 2 else f"about {int(h)} hours ago"
                )
                parts.append(f'LAST SESSION ({when}): "{last_task}"')
        except Exception:
            pass

    return "\n\n".join(parts) if parts else ""


# ── Session KV (last task / timestamp) ───────────────────────────────────────

def record_task(user_text: str, gil_response: str = "") -> None:
    _set_kv("last_task",     user_text.strip())
    _set_kv("last_response", gil_response.strip())
    _set_kv("last_updated",  datetime.now().isoformat())


def get_last_session() -> dict:
    task  = _get_kv("last_task")  or ""
    stamp = _get_kv("last_updated") or ""
    hours_ago, is_recent = None, False
    if stamp:
        try:
            delta     = datetime.now() - datetime.fromisoformat(stamp)
            hours_ago = delta.total_seconds() / 3600
            is_recent = delta < timedelta(hours=20)
        except Exception:
            pass
    return {"last_task": task, "last_active": stamp, "hours_ago": hours_ago, "is_recent": is_recent}


def get_brain_context() -> str:
    """Legacy shim — now delegates to build_memory_context."""
    last = _get_kv("last_task") or ""
    return build_memory_context(last)


def _get_kv(key: str) -> str:
    with _db_lock:
        conn = _get_db()
        row  = conn.execute("SELECT value FROM session WHERE key=?", (key,)).fetchone()
        conn.close()
    return row["value"] if row else ""


def _set_kv(key: str, value: str) -> None:
    with _db_lock:
        conn = _get_db()
        conn.execute(
            "INSERT OR REPLACE INTO session (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, time.time()),
        )
        conn.commit()
        conn.close()


# ── Automatic memory extraction (background, non-blocking) ───────────────────

_extraction_lock  = threading.Lock()   # only one extraction thread at a time
_last_db_error_at: float = 0.0         # guards the single-fire error speech
_DB_ERROR_COOLDOWN = 120.0             # seconds between error announcements


def _memory_extraction_enabled() -> bool:
    try:
        import json
        with open(DB_PATH.parent / "gil_config.json") as f:
            return json.load(f).get("memory_on", True)
    except Exception:
        return True


def extract_memories_background(user_text: str, gil_response: str) -> None:
    """Fire-and-forget: extracts learnable facts from this exchange via Groq."""
    if len(user_text) < 10:
        return
    if not _memory_extraction_enabled():
        return
    threading.Thread(
        target=_run_extraction_safe,
        args=(user_text, gil_response),
        daemon=True,
        name="GIL-MemExtract",
    ).start()


def _run_extraction_safe(user_text: str, gil_response: str) -> None:
    """Wrapper that ensures only one extraction runs at a time."""
    if not _extraction_lock.acquire(blocking=False):
        return  # another extraction is already in flight — drop this one
    try:
        _run_extraction(user_text, gil_response)
    finally:
        _extraction_lock.release()


def _run_extraction(user_text: str, gil_response: str) -> None:
    import os
    groq_key = os.getenv("GROQ_API_KEY", "") or os.getenv("GROQ_API_KEY_2", "")
    if not groq_key:
        return

    prompt = (
        "Extract facts worth remembering from this conversation exchange.\n"
        "Only extract CONCRETE facts: preferences, decisions, names, projects, goals, dislikes, habits.\n"
        "NOT opinions, greetings, vague chat, or filler.\n"
        "Return a JSON array ONLY — no explanation, no markdown:\n"
        '[{"type": "fact|preference|project|decision", "content": "...", "importance": 1-10}]\n'
        "Return [] if nothing is worth remembering. Max 3 items. Be very selective.\n\n"
        f"User: {user_text}\n"
        f"GIL: {gil_response}\n\n"
        "JSON array:"
    )
    try:
        import requests
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {groq_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.1-8b-instant",
                "messages": [
                    {"role": "system", "content": "You extract memorable facts from conversations. Return only a JSON array, nothing else."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 220,
            },
            timeout=12,
        )
        raw = resp.json()["choices"][0]["message"]["content"].strip()

        start = raw.find("[")
        end   = raw.rfind("]") + 1
        if start == -1 or end <= start:
            return

        items = json.loads(raw[start:end])
        for item in items:
            if isinstance(item, dict) and "content" in item:
                c = item["content"].strip()
                if len(c) > 8:
                    remember(
                        content=c,
                        mem_type=item.get("type", "fact"),
                        source=user_text[:60],
                        importance=int(item.get("importance", 5)),
                    )
    except Exception as exc:
        print(f"[G.I.L. MEMORY] Extraction skipped: {exc}")


def _announce_db_error_once() -> None:
    """Speak the scheduler error message at most once per cooldown window, then attempt DB reinit."""
    global _last_db_error_at
    now = time.time()
    if now - _last_db_error_at < _DB_ERROR_COOLDOWN:
        return
    _last_db_error_at = now
    print("[G.I.L. MEMORY] Announcing scheduler error and rebooting module.")
    try:
        from voice import speak
        speak("Apologies, Omri. The scheduler database is unresponsive. I'm rebooting the module now.")
    except Exception:
        pass
    try:
        _init_db()
    except Exception as exc:
        print(f"[G.I.L. MEMORY] DB reinit failed: {exc}")
