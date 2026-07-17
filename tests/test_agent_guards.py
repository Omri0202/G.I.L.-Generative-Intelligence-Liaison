"""
Trust guards for G.I.L.'s coding agent — the self-review that lets the user
rely on GIL's edits. Pure-Python, no GUI/network.
Run: python -m pytest tests/test_agent_guards.py -q
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import code_agent as ca


# ── intent: look/find/review → report only; fix/add/change → modify ───────────

def test_investigate_vs_change_mode():
    for t in ("what bugs are there?", "look at the gil project",
              "find bugs in main.py", "review the error handling",
              "tell me how the brain works"):
        assert ca._task_mode(t) == "investigate", t
    for t in ("fix the voice bug", "enable voice", "add a dark theme",
              "refactor gil_brain", "improve the code agent"):
        assert ca._task_mode(t) == "change", t


# ── no-op / trivial-edit guard (the trust-killer the user hit) ────────────────

def test_pointless_edit_rejects_unused_exception_var():
    assert ca._pointless_edit("except Exception:", "except Exception as e:")
    assert ca._pointless_edit("a = 1", "a = 1")            # identical
    # a real change must NOT be flagged
    assert ca._pointless_edit("except Exception:",
                              "except Exception as e:\n    log.error(e)") is None
    assert ca._pointless_edit("x = 1", "x = 2") is None


# ── syntax check: reject broken, allow valid ──────────────────────────────────

def test_validate_syntax_python():
    assert ca._validate_syntax("x.py", "def f():\n    return 1\n") is None
    assert ca._validate_syntax("x.py", "def f(:\n    pass")           # broken

def test_validate_syntax_json():
    assert ca._validate_syntax("c.json", '{"a": 1}') is None
    assert ca._validate_syntax("c.json", '{"a": 1,}')                 # trailing comma

def test_bracket_check_handles_regex_and_templates():
    # regex char classes and template literals must NOT be flagged
    assert ca._bracket_error('const re=/[a-z}{]+/g; f(`hi ${a+b}`); g([1]);') is None
    assert ca._bracket_error('let x = a[0] / b[1] / c;') is None
    # genuine breakage IS caught
    assert ca._bracket_error('function f(){ return [1,2 }')


# ── precheck: the exact bad edit, a breaking edit, and a good edit ────────────

def test_precheck_change_flow():
    f = Path(tempfile.gettempdir()) / "cg_guard_test.py"
    f.write_text("def foo():\n    x = compute()\n    return x\n", encoding="utf-8")
    # breaking edit → rejected
    assert ca._precheck_change("edit_file",
        {"path": str(f), "old": "return x", "new": "return ("})
    # ambiguous / not-found old → rejected
    assert ca._precheck_change("edit_file",
        {"path": str(f), "old": "nope", "new": "return 0"})
    # good, meaningful, unique edit → allowed
    assert ca._precheck_change("edit_file",
        {"path": str(f), "old": "return x", "new": "return x + 1"}) is None


# ── reviewer agent + model selection ──────────────────────────────────────────

def test_json_parser_handles_verdicts_and_actions():
    assert ca._extract_json('{"verdict":"reject","reason":"off task"}', "verdict")["verdict"] == "reject"
    assert ca._extract_json('note: {"verdict":"warn","reason":"x"} end', "verdict")["verdict"] == "warn"
    assert ca._extract_action('```json\n{"tool":"done","args":{}}\n```')["tool"] == "done"

def test_reviewer_degrades_gracefully_without_keys(monkeypatch):
    # no API keys → reviewer must not crash; it approves with an 'unavailable' note
    monkeypatch.setattr(ca, "_keys", lambda: [])
    out = ca._review_change("fix the bug", "edit_file",
                            {"path": "x.py", "old": "a", "new": "b"})
    assert out["verdict"] == "approve" and "unavailable" in out["reason"]

def test_agent_models_defaults_to_safe_fallback():
    models = ca._agent_models()
    assert models[-1] == "llama-3.1-8b-instant"
    assert "llama-3.3-70b-versatile" in models


# ── search must actually work: multi-line patterns + filename help ────────────

def test_search_matches_multiline_pattern(tmp_path):
    (tmp_path / "m.py").write_text("try:\n    x()\nexcept Exception:\n    pass\n",
                                   encoding="utf-8")
    out = ca._t_search({"pattern": r"except[^\n]*:\s*\n\s*pass", "dir": str(tmp_path)})
    assert "m.py" in out and "No matches" not in out   # line-based scan missed this

def test_search_falls_back_to_filename_hint(tmp_path):
    (tmp_path / "eyes.py").write_text("print('vision')\n", encoding="utf-8")
    out = ca._t_search({"pattern": "gil_eyes.py", "dir": str(tmp_path)})
    assert "eyes.py" in out    # no text match, but the similar filename is suggested

def test_read_and_list_suggest_real_names(tmp_path):
    (tmp_path / "eyes.py").write_text("x=1\n", encoding="utf-8")
    miss = ca._t_read_file({"path": str(tmp_path / "gil_eyes.py")})
    assert "does not exist" in miss and "eyes.py" in miss
