"""
Core logic tests for G.I.L. — pure-Python modules only (no GUI, no network).
Run: python -m pytest tests/ -q
"""

import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
# Stub the audio layer so imports never touch a microphone
sys.modules.setdefault("ears", types.SimpleNamespace(
    unmute=lambda: None, mute=lambda: None))


# ── activity bus ──────────────────────────────────────────────────────────────

def test_activity_lifecycle():
    import activity
    events = []
    fn = lambda e: events.append((e["id"], e["status"]))
    activity.subscribe(fn)
    try:
        aid = activity.start("app", "Opening Chrome")
        activity.done(aid, "ok")
        assert (aid, "running") in events and (aid, "done") in events
    finally:
        activity.unsubscribe(fn)


def test_activity_labels():
    import activity
    kind, label = activity.label_for("web_search", "x" * 100)
    assert kind == "search" and len(label) < 80
    kind, _ = activity.label_for("edit_website", "darker")
    assert kind == "web"


def test_activity_group():
    import activity
    activity.set_group("Mission: T")
    assert activity.in_group()
    activity.clear_group()
    assert not activity.in_group()


# ── error fixer ───────────────────────────────────────────────────────────────

def test_blocklist():
    import error_fixer
    assert error_fixer.is_blocked("format C:")
    assert error_fixer.is_blocked("shutdown /r")
    assert error_fixer.is_blocked("dd if=/dev/zero")
    assert not error_fixer.is_blocked("python -m pip install requests")
    assert not error_fixer.is_blocked("Restart-Service Spooler")


def test_fix_report():
    import error_fixer
    rep = error_fixer.format_report({
        "diagnosis": "Missing package.", "fix_type": "auto",
        "commands": ["python -m pip install requests"], "steps": []})
    assert "yes" in rep and "pip install requests" in rep


# ── code agent ────────────────────────────────────────────────────────────────

def test_free_commands():
    import code_agent
    assert code_agent._is_free_command("git status")
    assert code_agent._is_free_command("python -m pytest tests/")
    assert not code_agent._is_free_command("python -m pip install x")
    assert not code_agent._is_free_command("Remove-Item foo")


def test_edit_tool_and_undo(tmp_path):
    import code_agent
    f = tmp_path / "m.py"
    f.write_text("a = 1\n", encoding="utf-8")
    code_agent._session_backups.clear()
    r = code_agent._t_edit_file({"path": str(f), "old": "a = 1", "new": "a = 2"})
    assert r.startswith("OK") and "a = 2" in f.read_text()
    assert code_agent._session_backups   # checkpoint recorded
    msg = code_agent.undo_last_changes()
    assert "restored 1" in msg and "a = 1" in f.read_text()


def test_edit_tool_ambiguity(tmp_path):
    import code_agent
    f = tmp_path / "m.py"
    f.write_text("x\nx\n", encoding="utf-8")
    r = code_agent._t_edit_file({"path": str(f), "old": "x", "new": "y"})
    assert r.startswith("ERROR") and "2 times" in r


# ── webgen ────────────────────────────────────────────────────────────────────

def test_intent_heuristic(monkeypatch):
    import webgen
    monkeypatch.setattr(webgen, "_groq_keys", lambda: [])   # force heuristic
    assert webgen.classify_intent("about the country israel") == "info"
    assert webgen.classify_intent("my coffee shop") == "promo"


def test_offline_site_tokens():
    import webgen
    html = webgen._offline_site("test topic")
    html = webgen._inject_images(html, {})
    assert "HERO_IMG" not in html and "<!--GIL-OFFLINE-->" in html


def test_editor_injection_idempotent():
    from web_editor import inject_editor
    h = "<html><body>hi</body></html>"
    once = inject_editor(h)
    assert 'id="gil-ed-js"' in once
    assert inject_editor(once) == once


def test_subpages_by_intent():
    import webgen
    assert webgen._subpages_for("info")[0][1] == "History"
    assert webgen._subpages_for("promo")[0][1] == "About"


# ── missions ──────────────────────────────────────────────────────────────────

def test_mission_runner_stub():
    import missions

    chat, spoken = [], []

    class W:
        def set_state(self, *a, **k): pass
        def add_chat_message(self, t, s): chat.append(t)
        def send_rich_to_chat(self, *a): pass

    class B:
        def summarize_results(self, title, results):
            ok = sum(1 for _, s, _ in results if s == "done")
            return f"{ok}/{len(results)} done"

    class E:
        window = W(); brain = B()
        _last_spoke_at = [0.0]; _last_said = [""]
        def _speak(self, t): spoken.append(t)
        def _execute_action(self, a, t):
            if a == "boom":
                raise RuntimeError("x")
            return "ok"

    missions.run_mission({"title": "T", "steps": [
        {"action": "open_app", "target": "chrome"},
        {"action": "boom", "target": ""}]}, E())
    time.sleep(2.5)
    assert spoken and "1/2" in spoken[-1]


# ── brain helpers ─────────────────────────────────────────────────────────────

def test_parse_mission_passthrough():
    from gil_brain import _parse_json
    p = _parse_json('{"speech": "s", "mission": {"title": "t", "steps": '
                    '[{"action": "open_app", "target": "x"}]}, "actions": []}')
    assert p["mission"]["title"] == "t"


def test_system_prompt_formats():
    import gil_brain
    s = gil_brain._SYSTEM.format(
        username="U", datetime="d", time_ex="t", credentials="c",
        memory_context="m", user_profile="p", screen_context="s",
        projects_context="pr", location_context="l", camera_state="x")
    for token in ("fix_error", "code_task", "set_voice", "edit_website",
                  "analyze_screen", "mission"):
        assert token in s, token
