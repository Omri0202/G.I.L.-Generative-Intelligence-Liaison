"""
Follow-up directive handling — a short "redo my last answer differently" message
must be understood against the conversation, not treated as a new topic or
dropped as unintelligible. Pure logic, no network.
Run: python -m pytest tests/test_followups.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import gil_brain as gb


def test_detects_language_switch_follow_ups():
    # the reported case: "בעברית" after a whale answer got "Say that again?"
    for t in ("בעברית", "עברית", "תכתוב בעברית", "in Spanish", "in french",
              "write it in french", "translate that", "translate this"):
        assert gb._is_followup_directive(t), t


def test_detects_reformat_follow_ups():
    for t in ("make it shorter", "make it simpler", "shorter", "longer",
              "again", "summarize it", "in more detail", "redo it"):
        assert gb._is_followup_directive(t), t


def test_does_not_fire_on_real_requests():
    for t in ("info about whales", "fix the bug in main.py",
              "what is the capital of France",
              "translate the docstring in gui.py to a comment and add tests",
              "write a function that sorts a list"):
        assert not gb._is_followup_directive(t), t


def test_only_fires_on_short_messages():
    # a genuine sentence that merely starts with a trigger word is not a follow-up
    assert not gb._is_followup_directive(
        "translate the whole module into typescript and wire it up")


def test_last_assistant_answer_skips_tiny_acks_and_json():
    history = [
        {"role": "assistant", "content": '{"speech":"","actions":[]}'},
        {"role": "user", "content": "info about whales"},
        {"role": "assistant", "content": "Whales are marine mammals " * 10},
        {"role": "user", "content": "בעברית"},
    ]
    ans = gb._last_assistant_answer(history)
    assert ans.startswith("Whales are marine mammals")


def test_last_assistant_answer_empty_when_no_substantive_turn():
    assert gb._last_assistant_answer([{"role": "assistant", "content": "Ready."}]) == ""
    assert gb._last_assistant_answer([]) == ""


# ── empty-reply retry (mocked, no network) ────────────────────────────────────

def test_empty_reply_to_a_followup_triggers_a_retry():
    """gpt-oss intermittently returns only hidden reasoning (empty content). For
    a follow-up directive that surfaced as "Say that again?" and looked like GIL
    forgetting the chat. The brain must retry once and use the real answer."""
    from unittest.mock import patch, MagicMock
    from gil_brain import GILBrain

    brain = GILBrain.__new__(GILBrain)
    brain.username = "Omri"
    brain.pending_results = []
    brain.history = [
        {"role": "user", "content": "info about whales"},
        {"role": "assistant", "content": "Whales are large marine mammals. " * 8},
    ]

    def _resp(content):
        r = MagicMock(); r.status_code = 200
        r.json.return_value = {"choices": [{"message": {"content": content}}]}
        r.raise_for_status = MagicMock()
        return r

    # first call: empty content; second (the retry): a real Hebrew answer
    replies = [_resp('{"speech": "", "actions": []}'),
               _resp('{"speech": "לווייתנים הם יונקים ימיים.", "actions": []}')]
    with patch("gil_brain._session") as sess, \
         patch("gil_brain.GROQ_KEYS", ["k"]), \
         patch("gil_brain._load_user_profile", return_value="Name: Omri"):
        sess.post.side_effect = replies
        out = brain.query("בעברית", mode="chat")

    assert sess.post.call_count == 2, "brain did not retry the empty reply"
    assert "לווייתנים" in (out.get("speech") or ""), out
