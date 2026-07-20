"""
Hunk-by-hunk diff reviewer (Feature #3) — pure logic, no GUI/network.
Run: python -m pytest tests/test_diff_hunks.py -q
"""

import sys
import threading
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import diff_hunks as dh
import code_agent as ca


# ── build_hunks ───────────────────────────────────────────────────────────────

def test_two_independent_changes_are_two_hunks():
    o = "l1\nl2\nl3\nl4\nl5\nl6\n"
    n = "l1\nX2\nl3\nl4\nl5\nX6\n"
    h = dh.build_hunks(o, n)
    assert len(h) == 2
    assert h[0]["id"] == 1 and h[1]["id"] == 2
    assert h[0]["old_text"].strip() == "l2" and h[0]["new_text"].strip() == "X2"


def test_identical_content_has_no_hunks():
    assert dh.build_hunks("same\ntext\n", "same\ntext\n") == []


def test_summary_counts_added_and_removed():
    o, n = "a\nb\nc\n", "a\nB\nc\nd\n"
    assert dh.summarize(dh.build_hunks(o, n)) == "2 hunks · +2 −1"


# ── apply_hunks: accept / reject / edit / defaults ────────────────────────────

def _two_hunk_case():
    return ("l1\nl2\nl3\nl4\nl5\nl6\n", "l1\nX2\nl3\nl4\nl5\nX6\n")


def test_accept_all_equals_proposed():
    o, n = _two_hunk_case()
    assert dh.apply_hunks(o, n, {}) == n            # empty map = accept-by-default


def test_reject_all_equals_original():
    o, n = _two_hunk_case()
    dec = {1: {"action": "reject"}, 2: {"action": "reject"}}
    assert dh.apply_hunks(o, n, dec) == o


def test_partial_accept_splices_cleanly():
    o, n = _two_hunk_case()
    dec = {1: {"action": "accept"}, 2: {"action": "reject"}}
    assert dh.apply_hunks(o, n, dec) == "l1\nX2\nl3\nl4\nl5\nl6\n"


def test_edit_uses_supplied_text_with_newline_fixup():
    o, n = _two_hunk_case()
    dec = {1: {"action": "edit", "text": "EDITED"}, 2: {"action": "reject"}}
    out = dh.apply_hunks(o, n, dec)
    assert "EDITED\n" in out and "X6" not in out


def test_string_keyed_decisions_from_json():
    # the browser serializes numeric ids to strings — both must resolve
    o, n = _two_hunk_case()
    dec = {"1": {"action": "reject"}, "2": {"action": "accept"}}
    assert dh.apply_hunks(o, n, dec) == "l1\nl2\nl3\nl4\nl5\nX6\n"


# ── edge cases ────────────────────────────────────────────────────────────────

def test_new_file_is_one_all_add_hunk():
    h = dh.build_hunks("", "a\nb\nc\n")
    assert len(h) == 1 and h[0]["old_count"] == 0
    assert dh.apply_hunks("", "a\nb\nc\n", {1: {"action": "reject"}}) == ""


def test_no_trailing_newline_roundtrips():
    o, n = "x\ny\nz", "x\nY\nz"
    assert dh.apply_hunks(o, n, {}) == n
    assert dh.apply_hunks(o, n, {1: {"action": "reject"}}) == o


# ── code_agent preview + gate helpers ─────────────────────────────────────────

def test_edit_original_proposed_single_match(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("a=1\nb=2\nc=3\n", encoding="utf-8")
    o, p, path = ca._edit_original_proposed(
        "edit_file", {"path": str(f), "old": "b=2", "new": "b=20"})
    assert p == "a=1\nb=20\nc=3\n" and path == f


def test_edit_original_proposed_ambiguous_returns_none(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("a=1\nb=2\nc=3\n", encoding="utf-8")
    o, p, path = ca._edit_original_proposed(
        "edit_file", {"path": str(f), "old": "=", "new": "X"})
    assert o is None and path is None            # 3 matches → no clean preview


def test_ask_hunk_review_resolves_through_gate():
    o, n = _two_hunk_case()
    hunks = dh.build_hunks(o, n)

    class Win:
        def request_hunk_review(self, payload):
            def resolve():
                g = eng._hunk_gate
                g["decisions"] = {"1": {"action": "accept"}, "2": {"action": "reject"}}
                g["event"].set()
            threading.Thread(target=resolve).start()

    eng = types.SimpleNamespace(window=Win(), _hunk_gate=None, _agent_cancel=False)
    dec = ca._ask_hunk_review(eng, "m.py", hunks, "why")
    assert dec == {"1": {"action": "accept"}, "2": {"action": "reject"}}
    assert dh.apply_hunks(o, n, dec) == "l1\nX2\nl3\nl4\nl5\nl6\n"


def test_ask_hunk_review_cancel_returns_none():
    o, n = _two_hunk_case()
    hunks = dh.build_hunks(o, n)

    class Win:
        def request_hunk_review(self, payload):
            def resolve():
                g = eng._hunk_gate
                g["cancelled"] = True
                g["event"].set()
            threading.Thread(target=resolve).start()

    eng = types.SimpleNamespace(window=Win(), _hunk_gate=None, _agent_cancel=False)
    assert ca._ask_hunk_review(eng, "m.py", hunks, "why") is None
