"""
triggers.py — Project G.I.L.
User-defined phrase macros: a spoken phrase fires a sequence of actions
and an optional follow-up question.

Action format inside each trigger:
  {"type": "open_app"|"open_url"|"web_search", "target": "..."}
"""

import json
import os
import uuid

_PATH = os.path.join(os.path.dirname(__file__), "data", "triggers.json")


def _load() -> dict:
    try:
        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        with open(_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"triggers": []}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_all() -> list[dict]:
    return _load()["triggers"]


def add_trigger(phrase: str, actions: list[dict], followup: str = "") -> dict:
    data    = _load()
    trigger = {
        "id":       str(uuid.uuid4())[:8],
        "phrase":   phrase.lower().strip(),
        "actions":  actions,
        "followup": followup.strip(),
    }
    data["triggers"].append(trigger)
    _save(data)
    return trigger


def delete_trigger(trigger_id: str) -> bool:
    data   = _load()
    before = len(data["triggers"])
    data["triggers"] = [t for t in data["triggers"] if t["id"] != trigger_id]
    if len(data["triggers"]) < before:
        _save(data)
        return True
    return False


def match_trigger(text: str) -> dict | None:
    """Return the first trigger whose phrase appears anywhere in text, or None."""
    lower = text.lower()
    for t in get_all():
        if t["phrase"] and t["phrase"] in lower:
            return t
    return None


_FUZZY_SKIP = {
    "i", "i'm", "im", "about", "to", "a", "the", "my", "me", "we",
    "going", "gonna", "want", "would", "like", "please", "and", "or",
    "just", "am", "is", "are", "was", "be", "do", "let", "can", "will",
    "now", "get", "got", "its", "it", "up", "on", "in", "of", "for",
    # Generic words that appear in many phrases and create false positives
    "mode", "activate", "enable", "turn", "set", "start", "go",
}


def fuzzy_match_trigger(text: str, threshold: float = 0.65) -> dict | None:
    """
    Return the best-matching trigger by word overlap if score >= threshold.
    Used as a fallback when exact phrase match fails — catches intent-phrased
    commands like "I'm about to stream CS:GO" matching a "streaming setup" trigger.
    """
    lower = text.lower()
    user_words = {
        w.strip(".,!?'\"") for w in lower.split()
        if w.strip(".,!?'\"") not in _FUZZY_SKIP and len(w.strip(".,!?'\"")) > 2
    }
    if not user_words:
        return None

    best_score = 0.0
    best_trig  = None
    for t in get_all():
        phrase_words = {
            w for w in t["phrase"].split()
            if w not in _FUZZY_SKIP and len(w) > 2
        }
        if not phrase_words:
            continue
        overlap = len(user_words & phrase_words) / len(phrase_words)
        if overlap > best_score:
            best_score = overlap
            best_trig  = t

    return best_trig if best_score >= threshold else None
