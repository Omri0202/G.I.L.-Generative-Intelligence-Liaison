"""
autonomous.py — G.I.L. autonomous task loop.

Given a goal, GIL works toward it on his own: plan the next step, execute it,
observe the real result, re-plan — repeating until the goal is genuinely
complete, no user input required between iterations. This is the difference
from missions.py (a fixed pre-planned step list): here every step is chosen
in light of what actually happened.

Autonomous mode is ON BY DEFAULT (config key "autonomous_mode"). Complex,
multi-step task requests engage it automatically; simple questions and single
commands do not. The user can interrupt at any time by saying/typing "stop".

Safety:
  - hard iteration cap (no infinite loops)
  - destructive commands still blocked (error_fixer blocklist)
  - code edits still route through the code agent's per-change approval
  - every step streams to the live activity feed
  - a global stop flag lets the user abort mid-loop
"""

import json
import threading
import time
from pathlib import Path

import activity
from logger import get as _get_log

log = _get_log("auto")

_MAX_ITERS = 15
_HISTORY_FILE = Path(__file__).parent / "data" / "autonomous_log.json"

# Actions the loop can run directly (read-only / safe / self-guarding).
# Anything not here is executed through the normal action dispatch, which
# already enforces approval + blocklist for the risky ones.
_DEV_ACTIONS = {
    "git_status", "git_commit", "git_push", "git_pull", "git_log", "git_diff",
    "git_branch_create", "git_branch_switch", "git_branch_list", "git_stash",
    "run_command", "run_tests", "code_search", "find_definition", "find_todos",
    "project_structure", "deps_outdated", "deps_install", "docker_ps",
    "docker_start", "docker_stop", "docker_logs", "docker_compose_up",
    "docker_compose_down", "github_prs", "github_issues", "github_ci",
}


def is_enabled() -> bool:
    try:
        cfg = json.loads((Path(__file__).parent / "data" / "gil_config.json")
                         .read_text(encoding="utf-8"))
        return bool(cfg.get("autonomous_mode", True))
    except Exception:
        return True


def set_enabled(on: bool) -> None:
    try:
        p = Path(__file__).parent / "data" / "gil_config.json"
        cfg = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        cfg["autonomous_mode"] = bool(on)
        p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        log.error("could not save autonomous setting", exc_info=True)


def stop(engine) -> None:
    """User asked to abort the running loop."""
    flag = getattr(engine, "_auto_stop", None)
    if flag is not None:
        flag[0] = True


def is_running(engine) -> bool:
    return bool(getattr(engine, "_auto_running", False))


def _record(goal: str, history: list, outcome: str) -> None:
    try:
        hist = []
        if _HISTORY_FILE.exists():
            hist = json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
        hist.append({"goal": goal, "ts": time.time(), "outcome": outcome,
                     "steps": [{"action": h["action"],
                                "target": str(h.get("target", ""))[:120],
                                "result": str(h.get("result", ""))[:200]}
                               for h in history]})
        _HISTORY_FILE.write_text(json.dumps(hist[-40:], indent=2),
                                 encoding="utf-8")
    except Exception:
        log.debug("autonomous log write failed", exc_info=True)


def _execute(engine, action: str, target: str) -> str:
    """Run one planned action, return a short text result for the planner."""
    try:
        if action in ("build_website",):
            from webgen import generate as _wg
            msg, path = _wg(target)
            if path:
                engine.window.send_rich_to_chat("website", path)
            return msg
        if action == "generate_image":
            from image_gen import generate as _ig, infer_dimensions
            w, h = infer_dimensions(target)
            img = _ig(target, width=w, height=h)
            engine.window.send_rich_to_chat("image", img)
            return f"image saved: {img.name}"
        if action == "web_research":
            import research
            chat_md, _ = research.run(target)
            engine.window.add_chat_message(chat_md, "gil")
            return chat_md[:500]
        if action == "code_task":
            import code_agent
            code_agent.run_agent(engine, target)   # blocks; handles approvals
            return "code agent finished (see chat for changes)"
        if action in _DEV_ACTIONS:
            from action_router import _run_dev_action_inner
            return _run_dev_action_inner(action, target) or "done"
        return str(engine._execute_action(action, target) or "done")
    except Exception as exc:
        log.error("autonomous step failed: %s -> %s", action, exc)
        return f"ERROR: {exc}"


def run(engine, goal: str) -> None:
    """Start the autonomous loop in a background thread."""
    goal = (goal or "").strip()
    if not goal:
        return

    engine._auto_stop = [False]
    engine._auto_running = True

    def _loop():
        from ears import unmute
        window = engine.window
        activity.set_group(f"Autonomous: {goal[:48]}")
        history: list[dict] = []
        outcome = "incomplete"

        ack = "On it — I'll work through this and let you know when it's done."
        window.set_state("speaking", said=ack)
        engine._speak(ack)
        engine._last_spoke_at[0] = time.time()

        try:
            for i in range(1, _MAX_ITERS + 1):
                if engine._auto_stop[0]:
                    outcome = "stopped by user"
                    break
                pid = activity.start("brain", f"Planning step {i}")
                plan = engine.brain.plan_next_step(goal, history)
                activity.done(pid)

                if plan.get("complete"):
                    outcome = "complete"
                    summary = plan.get("summary") or "Task complete."
                    window.add_chat_message("**Done** — " + summary, "gil")
                    window.set_state("speaking", said=summary[:280])
                    engine._speak(summary[:280])
                    engine._last_spoke_at[0] = time.time() - 1.5
                    engine._last_said[0] = summary
                    break

                action = str(plan.get("action") or "").strip()
                target = str(plan.get("target") or "").strip()
                reason = str(plan.get("reason") or action)[:80]
                if not action:
                    outcome = "no action planned"
                    break

                kind, _ = activity.label_for(action, target)
                aid = activity.start(kind, f"[{i}] {reason}")
                result = _execute(engine, action, target)
                bad = result.startswith("ERROR")
                (activity.fail if bad else activity.done)(aid, result[:120])
                history.append({"action": action, "target": target,
                                "result": result})
                time.sleep(0.3)
            else:
                outcome = "hit step limit"
                msg = (f"I worked through {_MAX_ITERS} steps on that. Here's where "
                       "it stands — tell me to keep going if you want more.")
                window.add_chat_message(msg, "gil")
                window.set_state("speaking", said=msg)
                engine._speak(msg)
                engine._last_spoke_at[0] = time.time() - 1.5
        finally:
            activity.clear_group()
            _record(goal, history, outcome)
            engine._auto_running = False
            try:
                unmute()
            except Exception:
                pass
            window.set_state("listening")

    threading.Thread(target=_loop, daemon=True, name="GIL-Autonomous").start()
