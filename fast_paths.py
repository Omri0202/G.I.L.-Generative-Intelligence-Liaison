"""
fast_paths.py — G.I.L.
Pure detection functions that bypass the LLM for obvious commands.
No shared state — all functions are stateless and import-safe.
"""

import re as _re
import urllib.parse as _urlparse
import datetime

# ── Study subject fast-path ───────────────────────────────────────────────────

_STUDY_SUBJECTS: dict[str, tuple[str, str | None]] = {
    "computer science": ("Computer science — what topic are you on? Tell me and I'll explain it or find you a video.", None),
    "programming":      ("Programming — what language or concept? I can walk through it with you or find a video.", None),
    "python":           ("Python — what are you working on or stuck on? I can explain it or search a tutorial video.", None),
    "data structures":  ("Data structures — which one? I can explain it or find you a visual video on it.", None),
    "algorithms":       ("Algorithms — which one? Tell me and I'll break it down or find a good video.", None),
    "math":             ("Math — which topic exactly? I can explain it or find you a video — just say the word.", None),
    "algebra":          ("Algebra — what specifically? Equations, functions, factoring? Tell me and I'll explain or find a video.", None),
    "calculus":         ("Calculus — derivatives or integrals? I can explain it or find a 3Blue1Brown video — want that?", None),
    "trigonometry":     ("Trigonometry — unit circle, identities, or something else? Tell me what you need.", None),
    "physics":          ("Physics — which topic? Mechanics, waves, electricity? I can explain or find a video.", None),
    "chemistry":        ("Chemistry — what are you covering? I can explain it or search a video on it.", None),
    "biology":          ("Biology — which topic? I can explain or find a video — what are you studying?", None),
    "history":          ("History — which period or event? I can explain it or find a documentary or video.", None),
    "economics":        ("Economics — micro or macro? What's the specific topic?", None),
    "statistics":       ("Statistics — probability, distributions, hypothesis testing? Tell me the topic and I'll explain or find a video.", None),
    "cs50":             ("CS50 — which week or topic are you on? I can explain it or find the lecture video.", None),
}

_STUDY_TRIGGERS = {
    "studying", "doing", "learning", "working on",
    "stuck on", "need help with", "help me with",
}


def fast_study_resolve(text: str) -> tuple[str, str | None] | None:
    """Return (speech, url) if text mentions studying a known subject, else None."""
    lower = text.lower()
    has_study = any(t in lower for t in _STUDY_TRIGGERS) or "i'm" in lower or "im" in lower
    if not has_study:
        return None
    for keyword, result in _STUDY_SUBJECTS.items():
        if keyword in lower:
            return result
    return None


# ── Fast URL resolver ─────────────────────────────────────────────────────────

_FAST_URLS: list[tuple[tuple[str, ...], str, str]] = [
    (("whatsapp",),   "https://web.whatsapp.com",  "WhatsApp"),
    (("youtube",),    "https://youtube.com",        "YouTube"),
    (("gmail",),      "https://mail.google.com",    "Gmail"),
    (("github",),     "https://github.com",         "GitHub"),
    (("reddit",),     "https://reddit.com",         "Reddit"),
    (("netflix",),    "https://netflix.com",        "Netflix"),
    (("instagram",),  "https://instagram.com",      "Instagram"),
    (("twitter",),    "https://twitter.com",        "Twitter"),
    (("discord",),    "https://discord.com/app",    "Discord"),
    (("linkedin",),   "https://linkedin.com",       "LinkedIn"),
]

_OPEN_TRIGGERS = {"open", "go to", "take me to", "navigate to", "show", "launch", "start"}


def fast_url_resolve(text: str) -> tuple[str, str] | None:
    """Return (url, label) for obvious 'open X' commands, else None."""
    lower = text.lower()
    if not any(t in lower for t in _OPEN_TRIGGERS):
        return None
    for keywords, url, label in _FAST_URLS:
        if all(k in lower for k in keywords):
            return url, label
    return None


# ── Fast YouTube search resolver ──────────────────────────────────────────────

_YT_PATS = [
    _re.compile(
        r'(?:show|find|get|play|search(?:\s+youtube)?(?:\s+for)?)\s+(?:me\s+)?(?:the\s+|a\s+)?'
        r'(.+?)\s+(?:trailer|video|clip|gameplay)(?:\s+on\s+youtube)?$',
        _re.IGNORECASE,
    ),
    _re.compile(
        r'(?:look\s+up|search\s+(?:youtube\s+for|for)|youtube\s+search(?:\s+for)?)\s+'
        r'(?:the\s+)?(.+?)(?:\s+(?:trailer|video|clip|gameplay))?$',
        _re.IGNORECASE,
    ),
    _re.compile(
        r'^(.+?)\s+(?:trailer|video|clip)\s+(?:on\s+)?youtube$',
        _re.IGNORECASE,
    ),
]

_YT_INDICATORS = {
    "trailer", "youtube", "video on", "clip of", "gameplay",
    "find me a video", "show me a video", "search youtube", "look up",
}


def fast_youtube_resolve(text: str) -> str | None:
    """Return a YouTube search URL if the user asks for a video, else None."""
    lower = text.lower()
    if not any(ind in lower for ind in _YT_INDICATORS):
        return None
    for pat in _YT_PATS:
        m = pat.search(text)
        if m:
            query = m.group(1).strip().strip(".,!?")
            query = _re.sub(r'^(the|a|an|some|me|us)\s+', '', query, flags=_re.IGNORECASE)
            if 2 < len(query) < 120:
                return "https://www.youtube.com/results?search_query=" + _urlparse.quote_plus(query)
    return None


# ── Greeting loop guard ───────────────────────────────────────────────────────

_GREETING_PHRASES = (
    "how can i assist", "how may i assist", "how can i help",
    "i am g.i.l", "i'm g.i.l", "i am gil", "greetings",
    "hello, i am", "hi, i am", "at your service",
    "what can i do for you", "what would you like",
)


def is_greeting_response(speech: str) -> bool:
    """True if the LLM fell into a greeting loop — suppress the response."""
    lower = speech.lower()
    return any(p in lower for p in _GREETING_PHRASES)


# ── Startup greeting ──────────────────────────────────────────────────────────

def build_greeting(username: str) -> str:
    """Context-aware startup greeting powered by session_manager."""
    try:
        from session_manager import build_startup_greeting
        return build_startup_greeting(username)
    except Exception:
        pass
    hour   = datetime.datetime.now().hour
    period = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
    return f"Good {period}, {username}. G.I.L. online."
