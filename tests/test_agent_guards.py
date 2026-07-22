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


# ── report formatting: markdown reports must never be spoken as markdown ──────

def test_speakable_strips_markdown_for_tts():
    report = ("I traced your run. Three real problems:\n\n"
              "**1. Search was line-based** — multi-line patterns never matched.\n"
              "Fixed with whole-file regex.\n\n"
              "Plus 2 quality fixes:\n- Noise excluded.\n\n117 passed.")
    spoken = ca._speakable(report)
    assert spoken == "I traced your run."
    for ch in ("*", "#", "`", "\n"):
        assert ch not in spoken
    assert ca._speakable("") == "Task finished."

def test_agent_prompt_demands_the_report_format():
    assert "REPORT FORMAT" in ca._SYSTEM
    assert "Plus N quality fixes:" in ca._SYSTEM
    assert "MICRO-TYPOGRAPHY" in ca._SYSTEM

def test_agent_prompt_rejects_vague_findings_and_loop_termination_false_positives():
    # the exact false-positive GIL produced on actions.py's EnumKey/OSError loop
    assert "NORMAL termination idiom" in ca._SYSTEM
    assert "CONCRETE failure" in ca._SYSTEM

def test_opening_ack_is_deterministic_and_names_the_real_project():
    # a guaranteed code-generated hook line, independent of the model's own "why"
    assert "G.I.L." in ca._opening_ack("look at the gil project for bugs")
    assert "G.I.L." in ca._opening_ack("review your code for issues")
    ack = ca._opening_ack(r"fix the bug in C:\Users\Omri\Desktop\myapp\main.py")
    assert "`myapp`" in ack and "main.py" not in ack   # folder, not the filename
    assert ca._opening_ack("add a dark theme toggle")   # never empty/crashes


def test_read_file_clips_dense_minified_lines(tmp_path):
    # a single 15k-char line (bundled JS/CSS) used to be dumped whole, which
    # poisoned a small model's context and caused repeated bad replies
    f = tmp_path / "dense.js"
    f.write_text("const a=1;\n" + ("x" * 15000) + "\nconst b=2;\n", encoding="utf-8")
    out = ca._t_read_file({"path": str(f), "lines": 10})
    assert len(out) < 1000
    assert "chars]" in out   # the clip note is present

def test_trim_biggest_result_shrinks_dense_tool_dump():
    msgs = [{"role": "user", "content": "RESULT:\n" + "x" * 3000}]
    assert ca._trim_biggest_result(msgs) is True
    assert len(msgs[0]["content"]) < 500
    # nothing large enough → no-op, doesn't crash
    small = [{"role": "user", "content": "RESULT:\nshort"}]
    assert ca._trim_biggest_result(small) is False
    assert small[0]["content"] == "RESULT:\nshort"


def test_brain_prompt_still_formats_with_all_placeholders():
    # a stray single brace in the persona would crash the brain at runtime
    import string, gil_brain
    fields = {f for _, f, _, _ in string.Formatter().parse(gil_brain._SYSTEM) if f}
    out = gil_brain._SYSTEM.format(**{f: "X" for f in fields})
    assert "REPORT FIELD" in out                      # written-report spec present
    assert "1-3 plain spoken sentences" in out        # speech stays TTS-safe


# ── Feature #8: layered session checkpoints & instant LIFO undo ───────────────

def _fresh_ckpt_state():
    ca._session_backups.clear()
    ca._checkpoint_stack.clear()
    ca._ckpt_gen = 0


def _edit(p, content):
    ca._checkpoint(p)            # exactly what write_file/edit_file do first
    p.write_text(content, encoding="utf-8")


def test_undo_is_lifo_to_last_checkpoint():
    _fresh_ckpt_state()
    d = Path(tempfile.mkdtemp())
    a, c = d / "a.txt", d / "c.txt"
    a.write_text("A0", encoding="utf-8")          # pre-existing; c is created
    _edit(a, "A1"); _edit(c, "C1")                # batch 1
    ca.create_checkpoint("first")
    _edit(a, "A2")                                # batch 2 (active)

    # re-editing a file after a checkpoint must not clobber the earlier backup:
    # the sealed (gen 0) and active (gen 1) layers point to distinct backups.
    key = str(a.resolve())
    sealed = ca._checkpoint_stack[0]["files"][key]
    active = ca._session_backups[key]
    assert sealed != active and sealed.exists() and active.exists()
    assert sealed.name.startswith("g0_") and active.name.startswith("g1_")

    ca.undo_last_changes()                        # revert batch 2 only
    assert a.read_text(encoding="utf-8") == "A1"
    assert c.read_text(encoding="utf-8") == "C1"  # untouched by this undo

    ca.undo_last_changes()                        # pop checkpoint 'first'
    assert a.read_text(encoding="utf-8") == "A0"
    assert not c.exists()                          # created file removed
    assert "Nothing to undo" in ca.undo_last_changes()


def test_undo_all_restores_session_start():
    _fresh_ckpt_state()
    d = Path(tempfile.mkdtemp())
    a, c = d / "a.txt", d / "c.txt"
    a.write_text("A0", encoding="utf-8")
    _edit(a, "A1"); _edit(c, "C1")
    ca.create_checkpoint("cp1")
    _edit(a, "A2")
    ca.create_checkpoint("cp2")
    _edit(a, "A3")                                # spread across three layers

    ca.undo_all_changes()
    assert a.read_text(encoding="utf-8") == "A0"  # oldest backup wins
    assert not c.exists()
    st = ca.checkpoint_status()
    assert st["pending"] == 0 and st["checkpoints"] == [] and not st["can_undo"]


def test_checkpoint_status_tracks_pending_and_points():
    _fresh_ckpt_state()
    d = Path(tempfile.mkdtemp())
    a = d / "a.txt"; a.write_text("x", encoding="utf-8")
    assert ca.checkpoint_status()["can_undo"] is False
    _edit(a, "y")
    assert ca.checkpoint_status()["pending"] == 1
    ca.create_checkpoint("cp")
    st = ca.checkpoint_status()
    assert st["pending"] == 0 and st["checkpoints"] == ["cp"] and st["can_undo"]


# ── /goal convergence: _cap_context must always terminate ────────────────────

def test_cap_context_terminates_when_system_and_task_exceed_the_cap():
    """The old trim produced 400+28=428 chars, which stayed above the 400-char
    candidate threshold, so the same message was 'trimmed' forever. Because
    messages[0:2] are never trimmed, any run whose system+task exceeded the cap
    spun at 100% CPU until killed — the real cause of /goal hanging for 15+ min."""
    import threading
    msgs = [{"role": "system", "content": "S" * 9000},
            {"role": "user", "content": "T" * 500}]
    for i in range(6):
        msgs.append({"role": "user", "content": ("R%d" % i) * 300})
    done = []
    t = threading.Thread(target=lambda: (ca._cap_context(msgs, 8000), done.append(True)),
                         daemon=True)
    t.start(); t.join(10)
    assert done, "_cap_context did not terminate — infinite loop regression"
    # every trimmable body settled at or under the floor
    assert all(len(m["content"]) <= 400 for m in msgs[2:])


def test_cap_context_still_caps_a_normal_transcript():
    msgs = [{"role": "system", "content": "S" * 500},
            {"role": "user", "content": "T" * 200}]
    for _ in range(20):
        msgs.append({"role": "user", "content": "X" * 3000})
    ca._cap_context(msgs, 8000)
    assert sum(len(m["content"]) for m in msgs) <= 8000


def test_cap_context_leaves_a_small_transcript_alone():
    msgs = [{"role": "system", "content": "a" * 100},
            {"role": "user", "content": "b" * 100}]
    before = [m["content"] for m in msgs]
    ca._cap_context(msgs, 8000)
    assert [m["content"] for m in msgs] == before


def test_search_skips_runtime_noise_dirs():
    # browser-profile / generated-asset trees hold thousands of files and no
    # source; walking them cost 51s on one /goal run
    for noisy in ("whatsapp_profile", "generated_images", "learning_projects",
                  "face_db", "node_modules", "__pycache__"):
        assert noisy in ca._SEARCH_SKIP_DIRS


# ── /goal convergence: stop once the run stops learning ──────────────────────

def test_convergence_stalls_after_three_barren_reads():
    c = ca._Convergence()
    big = "x" * 200
    c.record("read_file", big)              # novel → a real finding
    assert not c.stalled() and c.progress == 1
    for _ in range(3):
        c.record("read_file", big)          # same content → nothing learned
    assert c.stalled() and "nothing new" in c.reason()


def test_convergence_resets_on_new_content_or_an_action():
    big, other = "x" * 200, "y" * 200
    c = ca._Convergence()
    c.record("read_file", big); c.record("read_file", big)
    c.record("read_file", other)            # genuinely new → reset
    assert not c.stalled()
    c2 = ca._Convergence()
    for _ in range(3):
        c2.record("read_file", big)
    c2.record("edit_file", "OK — edited f.py")   # concrete action → reset
    assert not c2.stalled()


def test_convergence_treats_errors_and_empty_reads_as_barren():
    c = ca._Convergence()
    c.record("search", "ERROR: nothing found")
    c.record("read_file", "")
    c.record("list_dir", "ok")              # too short to be a finding
    assert c.stalled()


def test_convergence_ignores_bookkeeping_tools():
    c = ca._Convergence()
    c.record("read_file", "x" * 200)
    for _ in range(5):
        c.record("plan_next", "advanced")   # not a read, not an action
    assert c.dry == 0 and not c.stalled()


def test_convergence_enforces_a_wall_clock_budget():
    now = [1000.0]
    c = ca._Convergence(time_budget=300.0, now=lambda: now[0])
    assert not c.should_conclude()
    now[0] += 301
    assert c.should_conclude() and c.reason() == "time budget reached"


def test_investigation_budget_is_tighter_than_change_budget():
    assert ca._time_budget("investigate") < ca._time_budget("change")


# ── /goal Phase 1.5: survey phrasing, tool caps, no suite runs while exploring ──

def test_survey_phrasing_is_investigate_not_change():
    """"look at X for improvements" asks WHAT to improve — the action word is the
    subject of a survey, not an imperative. Scoring it 'change' silently unlocked
    the long time budget, the 240s command cap and the verify-with-pytest
    playbook, which is how that exact task ran the suite twice and never
    reported back."""
    for t in ("look at the gil project for improvements",
              "look at the gil project for things to improve",
              "review the code for possible optimizations",
              "check the project for bugs to fix"):
        assert ca._task_mode(t) == "investigate", t
    # a genuine imperative must still be a change
    for t in ("find and fix the bug in main.py",
              "look at main.py and fix the crash",
              "check the config and enable voice",
              "improve the code agent"):
        assert ca._task_mode(t) == "change", t


def test_investigation_playbook_forbids_running_the_suite():
    inv = ca._playbook_block("look at the gil project for improvements")
    assert "DO NOT RUN THE TEST SUITE" in inv
    assert "python -m pytest" not in inv        # not offered as a first move either
    # a change task still verifies with tests
    assert "pytest" in ca._playbook_block("fix the voice bug in main.py")


def test_run_timeout_is_thread_local_with_a_safe_default():
    import threading
    ca.set_thread_run_timeout(None)
    assert ca._run_timeout() == 240.0
    ca.set_thread_run_timeout(60)
    seen = []
    t = threading.Thread(target=lambda: seen.append(ca._run_timeout()))
    t.start(); t.join()
    assert ca._run_timeout() == 60.0 and seen[0] == 240.0   # no cross-thread leak
    ca.set_thread_run_timeout(None)


def test_run_timeout_kills_a_slow_command_with_actionable_advice():
    ca.set_thread_run_timeout(3)
    try:
        out = ca._t_run("Start-Sleep -Seconds 30")
    finally:
        ca.set_thread_run_timeout(None)
    assert "exceeded the 3s limit" in out
    assert "Do NOT run it again" in out          # steers it to a cheaper move


# ── codemode routing: a non-coding request must NOT reach the code agent ──────

def test_is_coding_task_routes_research_and_knowledge_to_the_brain():
    """The reported bug: in /codemode "do research on whales" was force-fed to
    the code agent, which can only think in terms of THIS project — so it grepped
    the codebase for "whale" for 15 minutes. These must read as non-coding."""
    for t in ("do research on whales", "research whales",
              "what is the capital of France", "who is the president of Brazil",
              "what was the cold war", "tell me about the roman empire",
              "write a poem about the sea", "look up the weather in Tokyo",
              "translate hola to english", "research the best pasta recipe"):
        assert ca.is_coding_task(t) is False, t


def test_is_coding_task_keeps_real_coding_work_in_the_agent():
    for t in ("fix the bug in main.py", "look at the gil project for improvements",
              "refactor the code agent", "add a dark theme toggle",
              "run the tests", "why does the stop button not work",
              "debug the crash in gui.py", "implement caching for the brain",
              "what is broken in chat_web.py", "how do I run the build"):
        assert ca.is_coding_task(t) is True, t


def test_is_coding_task_lets_code_signals_win_ties():
    # "research" + a code reference is still a coding task, not web research
    assert ca.is_coding_task("research how the approval flow works in the code")
    assert ca.is_coding_task("research the codebase for silent excepts")


def test_is_coding_task_defaults_to_coding_when_ambiguous():
    # they explicitly entered the developer terminal — no signal either way stays code
    assert ca.is_coding_task("make the thing better") is True
    assert ca.is_coding_task("") is True


def test_is_coding_task_routes_non_latin_natural_language_to_the_brain():
    # a Hebrew message has no English signal and must not default to coding —
    # this is the exact case ("בעברית") that still hit the code agent
    for t in ("בעברית", "תכתוב בעברית", "מה זה לוויתן", "ספר לי על לווייתנים"):
        assert ca.is_coding_task(t) is False, t
    # …unless it explicitly names code — a Hebrew bug report about main.py stays code
    assert ca.is_coding_task("תקן את הבאג ב main.py") is True


def test_is_coding_task_catches_info_and_lookup_phrasings():
    for t in ("info about whales", "give me info on sharks",
              "translate that to spanish"):
        assert ca.is_coding_task(t) is False, t
