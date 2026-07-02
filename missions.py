"""
missions.py — G.I.L. mission system.

A Mission is a multi-step plan returned by the brain for complex requests:
    {"title": "Workday setup", "steps": [{"action", "target", "label"}, ...]}

MissionRunner executes the steps sequentially in a worker thread, publishes
live progress to the activity feed (which the chat window renders as a
checklist card), then asks the brain for a short spoken wrap-up grounded
in the actual step results.

Mission history is appended to data/missions.json for future recall.
"""

import json
import threading
import time
from pathlib import Path

import activity
from logger import get as _get_log

log = _get_log("missions")

_HISTORY_FILE = Path(__file__).parent / "data" / "missions.json"
_MAX_STEPS    = 12


def _record(title: str, results: list) -> None:
    try:
        hist = []
        if _HISTORY_FILE.exists():
            hist = json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
        hist.append({
            "title": title,
            "ts": time.time(),
            "steps": [{"label": l, "status": s, "detail": d[:200]}
                      for l, s, d in results],
        })
        _HISTORY_FILE.write_text(json.dumps(hist[-50:], indent=2),
                                 encoding="utf-8")
    except Exception:
        log.debug("mission history write failed", exc_info=True)


def run_mission(mission: dict, engine) -> None:
    """
    Execute a brain-planned mission in a background thread.
    Speaks a grounded summary when finished and returns the engine
    to the listening state.
    """
    title = (mission.get("title") or "Mission").strip()[:60]
    steps = [s for s in (mission.get("steps") or [])
             if isinstance(s, dict) and s.get("action")][:_MAX_STEPS]
    if not steps:
        log.warning("mission %r had no valid steps", title)
        engine.window.set_state("listening")
        return

    def _run():
        from ears import unmute
        window = engine.window
        activity.set_group(f"Mission: {title}")
        results = []   # (label, "done"/"fail", detail)

        try:
            for i, step in enumerate(steps, 1):
                action = step["action"]
                target = step.get("target") or ""
                kind, auto_label = activity.label_for(action, target)
                label = (step.get("label") or auto_label).strip()[:70]
                aid = activity.start(kind, f"[{i}/{len(steps)}] {label}")
                try:
                    if action == "build_website":
                        from webgen import generate as _wg
                        detail, html_path = _wg(target)
                        if html_path:
                            window.send_rich_to_chat("website", html_path)
                    elif action == "generate_image":
                        from image_gen import generate as _ig, infer_dimensions
                        w, h = infer_dimensions(target)
                        img = _ig(target, width=w, height=h)
                        window.send_rich_to_chat("image", img)
                        detail = f"saved {img.name}"
                    elif action in (
                        "git_status", "git_commit", "git_push", "git_pull",
                        "git_log", "git_diff", "git_branch_create",
                        "git_branch_switch", "git_branch_list", "git_stash",
                        "run_command", "run_tests", "code_search",
                        "find_definition", "find_todos", "project_structure",
                        "deps_outdated", "deps_install", "docker_ps",
                        "docker_start", "docker_stop", "docker_logs",
                        "docker_compose_up", "docker_compose_down",
                        "github_prs", "github_issues", "github_ci",
                    ):
                        from action_router import _run_dev_action_inner
                        detail = _run_dev_action_inner(action, target) or ""
                    else:
                        detail = engine._execute_action(action, target) or ""
                    detail = str(detail).strip()
                    activity.done(aid, detail[:120])
                    results.append((label, "done", detail))
                except Exception as exc:
                    log.error("mission step failed: %s -> %s", action, exc)
                    activity.fail(aid, str(exc)[:120])
                    results.append((label, "fail", str(exc)))
                time.sleep(0.3)   # small gap so the UI progression is visible
        finally:
            activity.clear_group()
            unmute()

        _record(title, results)

        summary = engine.brain.summarize_results(title, results)
        window.set_state("speaking", said=summary)
        window.add_chat_message(summary, "gil")
        engine._speak(summary)
        engine._last_spoke_at[0] = time.time() - 1.5
        engine._last_said[0]     = summary
        window.set_state("listening")

    threading.Thread(target=_run, daemon=True, name="GIL-Mission").start()
