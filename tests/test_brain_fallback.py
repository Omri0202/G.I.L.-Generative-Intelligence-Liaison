"""
Brain fallback + parser resilience. The exact failure chain from the field:
all keys 429 → fallback to a decommissioned model (gemma2-9b-it) → 400 accepted
as "success" → raise_for_status → no answer. Plus: a fallback model that answers
in prose instead of the JSON envelope had its reply dropped to empty.
Run: python -m pytest tests/test_brain_fallback.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import gil_brain as gb


# ── fallback model list must only name models the account serves ──────────────

def test_fallback_list_excludes_the_decommissioned_gemma():
    # gemma2-9b-it 404/400s now; it must never be a fallback again
    assert "gemma2-9b-it" not in gb._FALLBACK_MODELS


def test_fallback_list_is_non_empty_and_reasonable():
    assert gb._FALLBACK_MODELS
    # a fallback identical to the default brain shares its rate limit, so it
    # can't rescue a rate-limited reply. Live-ness: tests/test_models_live.py
    assert gb._SMART_MODEL not in gb._FALLBACK_MODELS


# ── parser salvages a prose reply instead of dropping it to empty ─────────────

def test_parse_json_salvages_hebrew_prose():
    p = gb._parse_json("אוקיי, הנה עובדות על לווייתנים.")
    assert p["speech"].startswith("אוקיי")


def test_parse_json_salvages_english_prose():
    p = gb._parse_json("Whales are large marine mammals belonging to Cetacea.")
    assert p["speech"].startswith("Whales")


def test_parse_json_still_parses_the_real_envelope():
    p = gb._parse_json('{"speech": "Done.", "actions": []}')
    assert p["speech"] == "Done." and p["action"] is None


def test_parse_json_does_not_treat_broken_json_as_prose():
    # a truncated JSON attempt (leading brace) must not be surfaced verbatim
    assert gb._parse_json('{"speech": "half').get("speech") == ""


def test_parse_json_prose_reply_is_not_empty_for_the_never_silent_guard():
    # the engine's never-silent check keys off speech; prose must fill it so a
    # rate-limited fallback answer never surfaces as "Say that again?"
    p = gb._parse_json("Here is a full answer with real content in it.")
    assert p["speech"].strip()
