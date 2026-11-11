"""
profile_store.py — Project G.I.L.
The profile half of GIL's memory: what GIL knows about how Omri works.

Three parts, all stored in data/gil_brain.db:
1. Preferences — how Omri likes things done, with confidence scoring
                 (table `preferences`). Injected into every brain call.
2. Goals       — what Omri is working on right now, across sessions
                 (table `goals`). Injected into every brain call, with a
                 check-in every 45 minutes.
3. Seed        — the starting facts and preferences GIL knows from day one.
                 Run once:  python profile_store.py --seed
                 Safe to re-run — uses deduplication.

The recall half (memories, last session, schedule, saved chat history and the
session greeting/summary) lives in memory.py.

Merged from preferences.py, goal_tracker.py and seed_memory.py.
"""

import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

DB_PATH = Path(__file__).parent / "data" / "gil_brain.db"
_pref_lock = threading.Lock()
_goal_lock = threading.Lock()

# ── DB init ───────────────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db() -> None:
    with _pref_lock:
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
    with _goal_lock:
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


# ═════════════════════════════════════════════════════════════════════════════
#  1. Preferences
# ═════════════════════════════════════════════════════════════════════════════

# ── Preferences API ───────────────────────────────────────────────────────────

DOMAINS = ("coding", "writing", "music", "learning", "apps", "schedule", "ui", "general")


def get_preference(domain: str, key: str, default: str = "") -> str:
    with _pref_lock:
        conn = _get_db()
        row  = conn.execute(
            "SELECT value FROM preferences WHERE domain=? AND key=?", (domain, key)
        ).fetchone()
        conn.close()
    return row["value"] if row else default


def set_preference(domain: str, key: str, value: str,
                   confidence: float = 0.7, source: str = "") -> None:
    with _pref_lock:
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
    with _pref_lock:
        conn = _get_db()
        conn.execute(
            "UPDATE preferences SET confidence=MIN(1.0, confidence+?), updated_at=? "
            "WHERE domain=? AND key=?",
            (delta, time.time(), domain, key),
        )
        conn.commit()
        conn.close()


def list_preferences(domain: str = "") -> list[dict]:
    with _pref_lock:
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
    with _pref_lock:
        conn = _get_db()
        conn.execute("DELETE FROM preferences WHERE domain=? AND key=?", (domain, key))
        conn.commit()
        conn.close()


# ── Auto-learning from conversation ───────────────────────────────────────────

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


# ═════════════════════════════════════════════════════════════════════════════
#  2. Goals
# ═════════════════════════════════════════════════════════════════════════════

# ── In-memory state ───────────────────────────────────────────────────────────

_current_goal: dict | None = None
_ask_cooldown: float = 0.0          # unix ts — don't ask twice in a session
_ASK_COOLDOWN_SECS = 60.0 * 20     # wait 20 min before asking about same app again
_asked_apps: set[str] = set()       # apps we already asked about this session
_checkin_callbacks: list[Callable] = []   # fired when it's time to check in


def _load_active_goal() -> dict | None:
    with _goal_lock:
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM goals WHERE active=1 ORDER BY last_seen_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
    return dict(row) if row else None


def _save_goal(description: str, app: str = "", file: str = "") -> dict:
    now = time.time()
    with _goal_lock:
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
    with _goal_lock:
        conn = _get_db()
        conn.execute("UPDATE goals SET last_seen_at=? WHERE id=?", (time.time(), goal_id))
        conn.commit()
        conn.close()


# ── Goals API ─────────────────────────────────────────────────────────────────

def start_goal_tracking() -> None:
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
        with _goal_lock:
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


# ═════════════════════════════════════════════════════════════════════════════
#  3. Seed — what GIL knows about Omri from day one
# ═════════════════════════════════════════════════════════════════════════════

FACTS = [
    # Identity
    ("fact",        "Omri's full name is Omri. Email: oklainert@gmail.com.", 10),
    ("fact",        "Omri is a Hebrew native speaker. He also speaks English fluently.", 9),
    ("fact",        "Omri uses Windows 11 Pro as his operating system.", 8),
    ("fact",        "Omri is an advanced Python developer.", 9),
    ("fact",        "Omri's primary code editor is VS Code or Cursor.", 7),

    # Project G.I.L.
    ("project",     "Omri's main project is Project G.I.L. — a JARVIS-style voice-controlled desktop AI, located at C:\\Users\\Omri\\project_gil\\.", 10),
    ("project",     "Project G.I.L. architecture: main.py (audio loop), gil_brain.py (Groq LLM), memory.py (SQLite FTS5), proactive.py (rules engine), gestures.py (MediaPipe), camera_viewer.py, voice.py (ElevenLabs TTS), ears.py (wake phrase), actions.py, context_engine.py, modes.py, webgen.py.", 9),
    ("project",     "GIL uses the Groq API: gpt-oss-120b is the brain; gpt-oss-20b and qwen3.8-27b handle quick work, fallbacks and vision. Omri has 2 Groq API keys on separate accounts.", 8),
    ("project",     "GIL's wake phrase is 'Hello G.I.L.'", 9),
    ("project",     "GIL uses ElevenLabs for TTS. If ElevenLabs fails, falls back to Windows SAPI.", 8),
    ("project",     "GIL has proactive rules: battery low/critical, meeting soon, unsaved file detection, CPU hot, late night, idle check, schedule learning.", 7),
    ("project",     "GIL has gesture control via MediaPipe: hand gestures trigger volume, screenshot, etc.", 7),
    ("project",     "GIL can control a smart TV via IR blaster or API.", 7),
    ("project",     "GIL's webgen module can build websites on voice command — uses Groq + Claude API.", 7),

    # Home setup
    ("fact",        "Omri has a smart TV that GIL can control.", 7),
    ("fact",        "Omri has a webcam that GIL can open for gesture recognition and visual context.", 7),
    ("fact",        "Omri uses Spotify for music. GIL controls it via the Spotify API.", 8),
    ("fact",        "Omri uses WhatsApp for messaging. GIL can scrape and recap WhatsApp messages.", 7),
    ("fact",        "Omri uses Gmail. GIL can read and recap Gmail.", 7),
    ("fact",        "Omri uses Google Calendar. GIL can read events and add appointments.", 7),
    ("fact",        "Omri uses Chrome as his primary browser.", 6),

    # Personality / communication
    ("preference",  "Omri wants GIL to be sharp, confident, and direct — like JARVIS. Never chatbot-y, never sycophantic.", 10),
    ("preference",  "Omri hates it when GIL says 'Certainly', 'Of course', 'As an AI', 'I cannot', 'Anything else?', 'Great question', or 'Absolutely'.", 10),
    ("preference",  "Omri wants GIL to challenge and push back when he is wrong — not just agree.", 9),
    ("preference",  "Omri wants exact, ready-to-paste commands — never vague instructions like 'run the appropriate command'.", 10),
    ("preference",  "Omri prefers short spoken responses (1-3 sentences max). Detailed output goes in the report field.", 9),
    ("frustration", "Omri is frustrated when GIL gives vague instructions instead of exact copy-paste commands.", 9),
    ("frustration", "Omri dislikes when GIL is overly apologetic or hedges instead of just acting.", 8),

    # Work habits
    ("habit",       "Omri works late at night regularly.", 8),
    ("habit",       "Omri iterates very rapidly — when a bug is found, he wants it fixed immediately.", 9),
    ("habit",       "Omri uses voice commands while working instead of stopping to type.", 8),
    ("habit",       "Omri builds ambitious all-in-one systems from scratch and keeps expanding them.", 8),
    ("habit",       "Omri writes informally in chat (casual spelling/grammar) but expects professional execution from GIL.", 7),

    # Current goals
    ("project",     "Omri's goal for GIL: make it feel like a real personal AI that knows him — JARVIS-level autonomy and personality.", 10),
    ("project",     "Omri added proactive intelligence to GIL: battery alerts, meeting alerts, unsaved file detection, schedule learning.", 8),
    ("project",     "Omri added multi-agent routing to GIL: coding agent, research agent, creative agent.", 8),
    ("project",     "Omri added real-time web research (DuckDuckGo API) to GIL's actions.", 7),
    ("project",     "Omri is working on making GIL's memory massive — seeding it with all known facts so GIL knows him from day one.", 9),
]

PREFS = [
    # General
    ("general", "response_tone",        "Sharp, confident, JARVIS-like. Never apologetic or sycophantic.",  1.0),
    ("general", "response_length",      "1-3 sentences max for TTS. Detail goes in report field.",          1.0),
    ("general", "challenge_when_wrong", "Yes — Omri explicitly wants pushback, not agreement.",             1.0),
    ("general", "exact_commands",       "Always give exact ready-to-paste commands, never vague ones.",      1.0),
    ("general", "address_as",          "Omri (or Sir in very formal contexts)",                              1.0),
    ("general", "language",             "Reply in Hebrew if Omri writes Hebrew, English otherwise.",         1.0),
    # UI
    ("ui",      "theme",                "Dark always — never suggest or use light themes.",                  1.0),
    ("ui",      "editor",               "VS Code or Cursor",                                                 0.9),
    # Music
    ("music",   "service",              "Spotify — always use spotify action, never open_app for music.",    1.0),
    # Coding
    ("coding",  "primary_language",     "Python",                                                            1.0),
    ("coding",  "style",                "Professional, modular, no unnecessary comments.",                   0.9),
    ("coding",  "wants_exact_code",     "Yes — give copy-paste ready code snippets, not pseudo-code.",       1.0),
    # Schedule
    ("schedule","typical_work_hours",   "Late night. Omri regularly works after 10pm.",                      0.85),
    # Apps
    ("apps",    "browser",              "Chrome",                                                            0.9),
    ("apps",    "messaging",            "WhatsApp",                                                          0.9),
    ("apps",    "email",                "Gmail — oklainert@gmail.com",                                       0.95),
    ("apps",    "calendar",             "Google Calendar",                                                   0.9),
    ("apps",    "tts",                  "ElevenLabs",                                                        0.95),
]


def seed() -> None:
    """Pre-populate memories and preferences. Safe to re-run (deduplicates)."""
    from memory import remember

    print("[SEED] Populating GIL's memory with everything known about Omri...\n")

    for mem_type, content, importance in FACTS:
        mem_id = remember(content, mem_type=mem_type, source="session_seed", importance=importance)
        print(f"  [{mem_type:12s}] (id={mem_id}) {content[:70]}{'...' if len(content) > 70 else ''}")

    print(f"\n[SEED] {len(FACTS)} memories stored.\n")

    print("[SEED] Populating preferences table...\n")

    for domain, key, value, confidence in PREFS:
        set_preference(domain, key, value, confidence=confidence, source="session_seed")
        print(f"  [{domain:10s}] {key}: {value[:60]}{'...' if len(value) > 60 else ''} (conf={confidence:.1f})")

    print(f"\n[SEED] {len(PREFS)} preferences stored.")
    print("\n[SEED] Done. GIL now knows Omri from day one.\n")


if __name__ == "__main__":
    import sys
    if "--seed" in sys.argv[1:]:
        seed()
    else:
        print("Usage:  python profile_store.py --seed")
