"""
preferences.py — Project G.I.L.
Structured user preference storage with confidence scoring.
Injected into every brain call so GIL remembers how Omri likes things done.
"""

import sqlite3
import threading
import time
from pathlib import Path

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
            CREATE TABLE IF NOT EXISTS preferences (
                domain      TEXT NOT NULL,
                key         TEXT NOT NULL,
                value       TEXT NOT NULL,
                confidence  REAL NOT NULL DEFAULT 0.5,
                source      TEXT DEFAULT '',
                updated_at  REAL NOT NULL,
                PRIMARY KEY (domain, key)
            );
        """)
        conn.commit()
        conn.close()


_init_db()


# ── Public API ────────────────────────────────────────────────────────────────

DOMAINS = ("coding", "writing", "music", "learning", "apps", "schedule", "ui", "general")


def get_preference(domain: str, key: str, default: str = "") -> str:
    with _lock:
        conn = _get_db()
        row  = conn.execute(
            "SELECT value FROM preferences WHERE domain=? AND key=?", (domain, key)
        ).fetchone()
        conn.close()
    return row["value"] if row else default


def set_preference(domain: str, key: str, value: str,
                   confidence: float = 0.7, source: str = "") -> None:
    with _lock:
        conn = _get_db()
        conn.execute(
            "INSERT OR REPLACE INTO preferences (domain, key, value, confidence, source, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (domain, key, value, min(1.0, confidence), source, time.time()),
        )
        conn.commit()
        conn.close()
    print(f"[G.I.L. PREF] [{domain}] {key} = {value!r} (conf={confidence:.1f})")


def boost_confidence(domain: str, key: str, delta: float = 0.1) -> None:
    with _lock:
        conn = _get_db()
        conn.execute(
            "UPDATE preferences SET confidence=MIN(1.0, confidence+?), updated_at=? "
            "WHERE domain=? AND key=?",
            (delta, time.time(), domain, key),
        )
        conn.commit()
        conn.close()


def list_preferences(domain: str = "") -> list[dict]:
    with _lock:
        conn = _get_db()
        if domain:
            rows = conn.execute(
                "SELECT * FROM preferences WHERE domain=? ORDER BY confidence DESC, updated_at DESC",
                (domain,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM preferences ORDER BY domain, confidence DESC"
            ).fetchall()
        conn.close()
    return [dict(r) for r in rows]


def delete_preference(domain: str, key: str) -> None:
    with _lock:
        conn = _get_db()
        conn.execute("DELETE FROM preferences WHERE domain=? AND key=?", (domain, key))
        conn.commit()
        conn.close()


# ── Auto-learning from conversation ──────────────────────────────────────────

_ALWAYS_PATTERNS = [
    ("always use", 1.0), ("always do", 1.0), ("always prefer", 1.0),
    ("i always", 0.85), ("i prefer", 0.8), ("i like to", 0.75),
    ("i want you to always", 1.0), ("never use", 1.0), ("don't use", 0.9),
]

_DOMAIN_KEYWORDS = {
    "coding":   ["code", "python", "javascript", "function", "class", "variable",
                 "library", "framework", "import", "syntax", "comment", "type hint"],
    "learning": ["explain", "learn", "study", "course", "video", "tutorial",
                 "understand", "concept", "teach"],
    "apps":     ["app", "software", "program", "tool", "use", "open", "launch"],
    "schedule": ["morning", "night", "evening", "work", "break", "time", "hours"],
    "writing":  ["write", "essay", "document", "email", "message", "tone", "style"],
    "music":    ["music", "song", "playlist", "spotify", "genre", "play"],
    "ui":       ["dark mode", "theme", "font", "size", "layout", "display"],
}


def _detect_domain(text: str) -> str:
    lower = text.lower()
    best_domain = "general"
    best_count  = 0
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in lower)
        if count > best_count:
            best_count  = count
            best_domain = domain
    return best_domain


def learn_from_exchange(user_text: str, gil_response: str = "") -> None:
    """
    Scan the exchange for explicit preference statements.
    Runs fast — no LLM call needed.
    """
    lower = user_text.lower()
    for pattern, confidence in _ALWAYS_PATTERNS:
        if pattern in lower:
            idx     = lower.find(pattern)
            content = user_text[idx + len(pattern):].strip().rstrip(".!?")
            if len(content) < 8 or len(content) > 120:
                continue
            domain = _detect_domain(user_text)
            # derive a key from the first 3 significant words
            words  = [w for w in content.lower().split()
                      if w not in ("the","a","an","to","of","and","or","for","i")]
            key    = "_".join(words[:3]) or content[:20].replace(" ", "_")
            set_preference(domain, key, content, confidence=confidence, source=user_text[:60])
            break


# ── Context builder ───────────────────────────────────────────────────────────

def build_preference_context() -> str:
    """Formatted string injected into brain system prompt."""
    all_prefs = list_preferences()
    if not all_prefs:
        return ""

    # Only include high-confidence preferences (≥0.6) for the prompt
    high = [p for p in all_prefs if p["confidence"] >= 0.6]
    if not high:
        return ""

    by_domain: dict[str, list[str]] = {}
    for p in high:
        d = p["domain"]
        by_domain.setdefault(d, []).append(f"{p['key'].replace('_',' ')}: {p['value']}")

    lines = ["YOUR PREFERENCES (respect these):"]
    for domain, items in sorted(by_domain.items()):
        lines.append(f"  [{domain.upper()}] " + " · ".join(items[:4]))

    return "\n".join(lines)
