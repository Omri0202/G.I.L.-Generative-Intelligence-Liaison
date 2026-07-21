"""
subagents.py — background sub-agent task spawning for G.I.L. (Feature #10).

Lets GIL work a second task in the background while the main conversation keeps
going: `/spawn <task>` starts a detached coding agent, `/tasks` shows what's
running, and the report is delivered to the chat when it finishes.

Safety: a background agent runs READ-ONLY by default. Nobody is watching to
approve its writes, so it investigates and reports instead of silently changing
files. `/spawn auto <task>` opts into a writing agent explicitly.

Isolation: each sub-agent gets its own engine shim (its own cancel/running flags,
so it can't disturb the foreground agent), a quiet window that captures its output
instead of rendering it, a thread-local approval policy (an env var would be
process-global and would flip the main agent's policy too), and a thread-local
activity mute so its tool cards don't interleave into the live feed.
"""

from __future__ import annotations

import itertools
import threading
import time

from logger import get as _get_log

log = _get_log("subagents")

_MAX_CONCURRENT = 3

_lock = threading.Lock()
_counter = itertools.count(1)
_agents: dict[int, dict] = {}      # id -> serializable record
_engines: dict[int, object] = {}   # id -> engine shim (for cancellation)


class _QuietWindow:
    """Absorbs a sub-agent's UI pushes and keeps its chat output for the report.
    Unknown methods no-op, so new push methods can't crash a background run."""

    def __init__(self):
        self.messages: list[str] = []

    def add_chat_message(self, text, who="gil"):
        self.messages.append(str(text))

    def set_state(self, *a, **k):
        return None

    def __getattr__(self, name):
        def _noop(*a, **k):
            return None
        return _noop


class _SubEng:
    """Engine shim: its own agent flags + quiet window, everything else falls
    through to the real engine (config, paths, brain, …)."""

    def __init__(self, real, window):
        object.__setattr__(self, "_real", real)
        self.window = window
        self._agent_running = False
        self._agent_cancel = False
        self._code_mode = True
        self._agent_gate = None
        self._hunk_gate = None
        self._last_spoke_at = [0.0]
        self._last_said = [""]

    def _speak(self, *a, **k):
        return None               # a background agent never talks over the user

    def __getattr__(self, name):
        return getattr(self._real, name)


def _record(rec: dict) -> dict:
    """Serializable snapshot with a live elapsed time."""
    out = dict(rec)
    end = rec.get("ended") or time.time()
    out["elapsed"] = int(end - rec["started"])
    return out


def status() -> list[dict]:
    """All sub-agents this session, newest first."""
    with _lock:
        return [_record(r) for r in sorted(_agents.values(),
                                           key=lambda r: r["id"], reverse=True)]


def running_count() -> int:
    with _lock:
        return sum(1 for r in _agents.values() if r["status"] == "running")


def get(aid: int) -> dict | None:
    with _lock:
        rec = _agents.get(int(aid))
        return _record(rec) if rec else None


def stop(aid: int) -> bool:
    """Ask a running sub-agent to wind down at its next step."""
    eng = _engines.get(int(aid))
    if eng is None:
        return False
    try:
        eng._agent_cancel = True
        return True
    except Exception:
        return False


def spawn(eng, task: str, mode: str = "readonly") -> dict:
    """Start a background sub-agent. Returns {ok, id} or {ok: False, error}."""
    task = (task or "").strip()
    if not task:
        return {"ok": False, "error": "Give me a task to run in the background."}
    if running_count() >= _MAX_CONCURRENT:
        return {"ok": False,
                "error": f"Already running {_MAX_CONCURRENT} background tasks — "
                         "let one finish first (`/tasks` shows them)."}
    mode = mode if mode in ("readonly", "auto") else "readonly"
    win = _QuietWindow()
    sub = _SubEng(eng, win)
    with _lock:
        aid = next(_counter)
        rec = {"id": aid, "task": task, "mode": mode, "status": "running",
               "started": time.time(), "ended": None, "result": "", "error": ""}
        _agents[aid] = rec
        # registered BEFORE the thread starts, so stop() can cancel a sub-agent
        # that hasn't had a chance to run its first line yet
        _engines[aid] = sub

    def _run():
        import code_agent
        import activity
        code_agent.set_thread_policy(mode)   # thread-local: main agent unaffected
        activity.mute_thread()               # keep the live feed clean
        try:
            code_agent.run_agent(sub, task)
            rec["result"] = win.messages[-1] if win.messages else "(no report produced)"
            rec["status"] = "cancelled" if getattr(sub, "_agent_cancel", False) else "done"
        except Exception as exc:             # never let a sub-agent kill the app
            log.debug("sub-agent %s failed", aid, exc_info=True)
            rec["error"] = str(exc)[:400]
            rec["status"] = "failed"
        finally:
            rec["ended"] = time.time()
            code_agent.set_thread_policy(None)
            activity.unmute_thread()
            _engines.pop(aid, None)
            _notify(eng, rec)

    threading.Thread(target=_run, daemon=True, name=f"GIL-SubAgent-{aid}").start()
    return {"ok": True, "id": aid, "mode": mode}


def _notify(eng, rec: dict) -> None:
    """Deliver the finished sub-agent's report into the main conversation."""
    try:
        win = eng.window
        secs = int((rec.get("ended") or time.time()) - rec["started"])
        head = f"background task **#{rec['id']}** · {secs}s · _{rec['task']}_"
        if rec["status"] == "done":
            body = f"**Finished** {head}\n\n{rec['result']}"
        elif rec["status"] == "cancelled":
            body = f"**Stopped** {head} — cancelled."
        else:
            body = f"**Failed** {head}\n\n{rec['error']}"
        win.add_chat_message(body, "gil")
        push = getattr(win, "subagents", None)
        if callable(push):
            push(status())
    except Exception:
        log.debug("sub-agent notify failed", exc_info=True)
