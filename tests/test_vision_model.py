"""
Shared vision provider (Phase 2) — pure logic, no network.
Run: python -m pytest tests/test_vision_model.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import vision_model as vm


# ── reasoning-block stripping ────────────────────────────────────────────────
# The current vision model emits <think>…</think> before its answer. Callers
# that parse JSON (dev_screen) or show text (eyes/screen_vision) must never see it.

def test_strips_a_complete_reasoning_block():
    raw = "<think>Let me look. The bar is red.</think>The button is red."
    assert vm.strip_thinking(raw) == "The button is red."


def test_strips_an_unterminated_reasoning_block():
    assert vm.strip_thinking("<think>still deliberating") == ""


def test_keeps_text_that_has_no_reasoning_block():
    assert vm.strip_thinking("Plain answer.") == "Plain answer."


def test_strip_handles_empty_and_none():
    assert vm.strip_thinking("") == ""
    assert vm.strip_thinking(None) == ""


def test_json_survives_stripping_for_dev_screen():
    import json
    raw = '<think>I see a traceback.</think>{"issue": "KeyError", "file": "gui.py"}'
    assert json.loads(vm.strip_thinking(raw))["file"] == "gui.py"


# ── payload shape ────────────────────────────────────────────────────────────

def test_image_message_shape():
    msgs = vm.image_message("what is this?", "QUJD")
    assert msgs[0]["role"] == "user"
    parts = msgs[0]["content"]
    assert parts[0] == {"type": "text", "text": "what is this?"}
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,QUJD")


# ── the TPM guard rail ───────────────────────────────────────────────────────

def test_send_width_stays_within_the_tpm_ceiling():
    # measured on a 1920x1080 desktop against the 8000 TPM tier:
    # 1000px wide -> 429, 760px -> fine. Raising this silently breaks every
    # vision feature with a rate-limit error.
    assert vm.SEND_WIDTH <= 800


def test_no_key_fails_cleanly_without_calling_out(monkeypatch):
    monkeypatch.setattr(vm, "keys", lambda: [])
    ok, ans, err = vm.chat(vm.image_message("x", "y"))
    assert ok is False and ans == "" and "key" in err.lower()


def test_model_is_named_in_exactly_one_place():
    # the outage this module exists to prevent: the id copy-pasted into three
    # modules, so one decommission killed them all at once
    import re
    src_dir = Path(__file__).parent.parent
    offenders = []
    for name in ("eyes.py", "screen_vision.py", "dev_screen.py", "visual_inspector.py"):
        text = (src_dir / name).read_text(encoding="utf-8", errors="replace")
        # a model id assigned to a constant (comments/prose don't count)
        if re.search(r"^_?\w*MODELS?\s*=\s*[\"\[]", text, re.M):
            offenders.append(name)
    assert not offenders, f"these still pin their own vision model: {offenders}"
