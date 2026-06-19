"""
seed_memory.py — Project G.I.L.
Run once to pre-populate GIL's memory and preferences DB with everything known about Omri.
Safe to re-run — uses deduplication.

Usage:  python seed_memory.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from memory import remember
from preferences import set_preference

print("[SEED] Populating GIL's memory with everything known about Omri...\n")

# ── Core identity facts ───────────────────────────────────────────────────────

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
    ("project",     "GIL uses Groq API (llama-3.1-8b-instant primary, fallbacks: gemma2-9b-it, llama-3.3-70b-versatile). Omri has at least 2 Groq API keys.", 8),
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

for mem_type, content, importance in FACTS:
    mem_id = remember(content, mem_type=mem_type, source="session_seed", importance=importance)
    print(f"  [{mem_type:12s}] (id={mem_id}) {content[:70]}{'...' if len(content) > 70 else ''}")

print(f"\n[SEED] {len(FACTS)} memories stored.\n")

# ── Preferences table ─────────────────────────────────────────────────────────

print("[SEED] Populating preferences table...\n")

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

for domain, key, value, confidence in PREFS:
    set_preference(domain, key, value, confidence=confidence, source="session_seed")
    print(f"  [{domain:10s}] {key}: {value[:60]}{'...' if len(value) > 60 else ''} (conf={confidence:.1f})")

print(f"\n[SEED] {len(PREFS)} preferences stored.")
print("\n[SEED] Done. GIL now knows Omri from day one.\n")
