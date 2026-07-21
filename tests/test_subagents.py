"""
Background sub-agent task spawning (Feature #10) — isolation guarantees and
lifecycle. No GUI/network: code_agent.run_agent is stubbed.
Run: python -m pytest tests/test_subagents.py -q
"""

import sys
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import activity
import code_agent as ca
import subagents


def _reset():
    subagents._agents.clear()
    subagents._engines.clear()
    ca.set_thread_policy(None)


def _eng():
    msgs = []

    class W:
        def add_chat_message(self, t, who="gil"):
            msgs.append(t)

    e = types.SimpleNamespace(window=W(), _code_mode=True)
    return e, msgs


def _wait(pred, timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


# ── isolation: the whole point of a background agent ──────────────────────────

def test_thread_policy_does_not_leak_to_other_threads(monkeypatch):
    monkeypatch.delenv("GIL_APPROVAL_MODE", raising=False)
    ca.set_thread_policy("readonly")
    seen = []
    t = threading.Thread(target=lambda: seen.append(ca._approval_policy()))
    t.start(); t.join()
    assert ca._approval_policy() == "readonly"     # this thread
    assert seen[0] == "ask"                        # everyone else
    ca.set_thread_policy(None)


def test_activity_mute_is_thread_local():
    events = []
    activity.subscribe(events.append)
    try:
        def muted():
            activity.mute_thread()
            activity.instant("code", "quiet", "x")
            activity.unmute_thread()
        t = threading.Thread(target=muted); t.start(); t.join()
        activity.instant("code", "loud", "y")
        titles = [e.get("title") for e in events]
        assert "quiet" not in titles and "loud" in titles
    finally:
        activity.unsubscribe(events.append)


def test_subagent_runs_readonly_and_is_isolated(monkeypatch):
    _reset()
    monkeypatch.delenv("GIL_APPROVAL_MODE", raising=False)
    eng, _ = _eng()
    seen = {}

    def fake(sub, task, hints=None):
        seen["policy"] = ca._approval_policy()
        seen["own_window"] = sub.window is not eng.window
        seen["own_flags"] = hasattr(sub, "_agent_cancel")
        seen["fallthrough"] = sub._code_mode        # comes from the real engine
        sub.window.add_chat_message("**Solved** — done", "gil")

    monkeypatch.setattr(ca, "run_agent", fake)
    r = subagents.spawn(eng, "look for bugs")
    assert r["ok"] and r["mode"] == "readonly"
    assert _wait(lambda: subagents.get(r["id"])["status"] == "done")
    assert seen["policy"] == "readonly"
    assert seen["own_window"] and seen["own_flags"] and seen["fallthrough"]
    assert ca._approval_policy() == "ask"           # main thread untouched


# ── lifecycle ─────────────────────────────────────────────────────────────────

def test_report_is_delivered_to_the_main_chat(monkeypatch):
    _reset()
    eng, msgs = _eng()
    monkeypatch.setattr(ca, "run_agent",
                        lambda s, t, h=None: s.window.add_chat_message("found 3 issues"))
    r = subagents.spawn(eng, "audit")
    assert _wait(lambda: any("Finished" in m for m in msgs))
    assert any("found 3 issues" in m for m in msgs)


def test_concurrency_cap(monkeypatch):
    _reset()
    eng, _ = _eng()
    gate = threading.Event()
    monkeypatch.setattr(ca, "run_agent", lambda s, t, h=None: gate.wait(4))
    try:
        ids = [subagents.spawn(eng, f"t{i}") for i in range(subagents._MAX_CONCURRENT)]
        assert all(x["ok"] for x in ids)
        over = subagents.spawn(eng, "one too many")
        assert not over["ok"] and "Already running" in over["error"]
    finally:
        gate.set()


def test_stop_works_before_the_thread_starts(monkeypatch):
    _reset()
    eng, _ = _eng()
    gate = threading.Event()
    monkeypatch.setattr(ca, "run_agent", lambda s, t, h=None: gate.wait(4))
    r = subagents.spawn(eng, "long one")
    assert subagents.stop(r["id"]) is True     # engine registered synchronously
    gate.set()
    assert _wait(lambda: subagents.get(r["id"])["status"] == "cancelled")


def test_failure_is_contained_and_reported(monkeypatch):
    _reset()
    eng, msgs = _eng()

    def boom(s, t, h=None):
        raise RuntimeError("agent exploded")

    monkeypatch.setattr(ca, "run_agent", boom)
    r = subagents.spawn(eng, "will fail")
    assert _wait(lambda: subagents.get(r["id"])["status"] == "failed")
    assert "exploded" in subagents.get(r["id"])["error"]
    assert any("Failed" in m for m in msgs)


def test_empty_task_rejected():
    _reset()
    eng, _ = _eng()
    assert subagents.spawn(eng, "   ")["ok"] is False


def test_auto_mode_is_opt_in(monkeypatch):
    _reset()
    eng, _ = _eng()
    monkeypatch.setattr(ca, "run_agent", lambda s, t, h=None: None)
    assert subagents.spawn(eng, "x", "auto")["mode"] == "auto"
    assert subagents.spawn(eng, "y", "nonsense")["mode"] == "readonly"


def test_status_snapshot_is_serializable(monkeypatch):
    _reset()
    eng, _ = _eng()
    monkeypatch.setattr(ca, "run_agent", lambda s, t, h=None: None)
    subagents.spawn(eng, "a task")
    import json
    rows = subagents.status()
    json.dumps(rows)                                  # must not contain the engine
    assert rows[0]["task"] == "a task" and "elapsed" in rows[0]
