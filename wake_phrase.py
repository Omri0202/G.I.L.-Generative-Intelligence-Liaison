"""
wake_phrase.py — G.I.L.
Wake phrase detection, stripping, and edit-distance helpers.
Pure functions — no shared state, safe to import from anywhere.
"""

import json
from pathlib import Path

# ── Recognised spoken variants ────────────────────────────────────────────────

HELLO_VARIANTS = {
    "hello", "helo", "hullo", "hallow", "halo",
    "hey", "hi", "hei", "yo", "ok", "okay",
}

GIL_VARIANTS = {
    "gill", "gil", "g.i.l", "gail",
    "jill", "jil", "phil", "gio", "geo",
    "deal", "feel", "heal", "neil", "real",
    "guild",
}


# ── Levenshtein edit distance ─────────────────────────────────────────────────

def edit_distance(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]


# ── Custom wake phrase (from settings) ───────────────────────────────────────

def load_wake_phrase() -> str:
    try:
        cfg = Path(__file__).parent / "data" / "gil_config.json"
        return json.loads(cfg.read_text()).get("wake_phrase", "").lower().strip()
    except Exception:
        return ""


# Module-level cache — reloaded on each GIL start
CUSTOM_WAKE: str = load_wake_phrase()


# ── Detection ─────────────────────────────────────────────────────────────────

def contains_wake_phrase(text: str) -> bool:
    cleaned = text.lower().replace(".", "").replace(",", "").replace("!", "").replace("?", "")

    if CUSTOM_WAKE and CUSTOM_WAKE in cleaned:
        return True

    words = cleaned.split()

    for i, w in enumerate(words[:-1]):
        if w in HELLO_VARIANTS or edit_distance(w, "hello") <= 1:
            nxt = words[i + 1]
            if nxt in GIL_VARIANTS or edit_distance(nxt, "gil") <= 2:
                return True

    if words and (words[0] in GIL_VARIANTS or edit_distance(words[0], "gil") <= 1):
        return True

    return False


def strip_wake_phrase(text: str) -> str:
    """Return the part of `text` that comes after the wake phrase, or ''."""
    cleaned = text.lower().replace(".", "").replace(",", "").replace("!", "").replace("?", "")
    words   = cleaned.split()
    orig    = text.split()

    for i, w in enumerate(words[:-1]):
        if w in HELLO_VARIANTS:
            nxt = words[i + 1]
            if nxt in GIL_VARIANTS or edit_distance(nxt, "gil") <= 1:
                after = " ".join(orig[i + 2:]).strip().lstrip(",").lstrip(".").strip()
                return after if len(after) > 2 else ""

    if words and (words[0] in GIL_VARIANTS or edit_distance(words[0], "gil") <= 1):
        after = " ".join(orig[1:]).strip().lstrip(",").lstrip(".").strip()
        return after if len(after) > 2 else ""

    return ""


def is_addressed(text: str) -> bool:
    """True if the utterance directly names GIL."""
    words = text.lower().replace(",", "").replace(".", "").split()
    return any(w in GIL_VARIANTS or edit_distance(w, "gil") <= 1 for w in words)
