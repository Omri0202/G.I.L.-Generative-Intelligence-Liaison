"""
session_manager.py — Project G.I.L.
Smart session continuity — startup greeting and shutdown summary.
GIL feels like resuming from where you left off, not starting fresh.
"""

import time
import threading
from datetime import datetime, timedelta
from pathlib import Path

# ── Session state ─────────────────────────────────────────────────────────────

_session_start: float = time.time()
_session_goals: list[str] = []
_session_app_switches: int = 0
_shutdown_callbacks: list = []   # called on clean shutdown


def record_session_goal(goal: str) -> None:
    if goal and goal not in _session_goals:
        _session_goals.append(goal)


def record_app_switch() -> None:
    global _session_app_switches
    _session_app_switches += 1


def get_session_duration_minutes() -> float:
    return (time.time() - _session_start) / 60.0


# ── Startup greeting ──────────────────────────────────────────────────────────

def build_startup_greeting(username: str = "Omri") -> str:
    """
    Build a context-aware startup greeting based on last session.
    Returns the speech string GIL should say aloud.
    """
    from memory import get_last_session, build_memory_context
    from goal_tracker import get_active_goal

    last  = get_last_session()
    now   = datetime.now()
    hour  = now.hour

    # Time-of-day greeting
    if 5 <= hour < 12:
        tod = "Good morning"
    elif 12 <= hour < 18:
        tod = "Good afternoon"
    elif 18 <= hour < 23:
        tod = "Good evening"
    else:
        tod = "Working late again"

    goal = get_active_goal()

    if last.get("is_recent") and last.get("last_task"):
        hours_ago = last.get("hours_ago") or 0
        raw_task  = last["last_task"]

        # Try to parse as a JSON topic array (new format)
        try:
            import json as _j
            topics = _j.loads(raw_task)
            if isinstance(topics, list) and topics:
                if len(topics) == 1:
                    topic_str = topics[0]
                elif len(topics) == 2:
                    topic_str = f"{topics[0]} and {topics[1]}"
                else:
                    topic_str = ", ".join(topics[:-1]) + f", and {topics[-1]}"
                last_task = topic_str
            else:
                last_task = raw_task[:60]
        except Exception:
            last_task = raw_task[:60]

        if hours_ago < 0.5:
            greeting = (f"Welcome back, {username}. "
                        f"Last session you covered: {last_task}. Picking up where we left off?")
        elif hours_ago < 3:
            greeting = (f"{tod}, {username}. "
                        f"Last session you covered: {last_task}. Still on that, or something new?")
        elif hours_ago < 12:
            greeting = (f"{tod}, {username}. "
                        f"Last session you covered: {last_task}. Want to continue?")
        else:
            greeting = (f"{tod}, {username}. "
                        f"Last session you covered: {last_task}. Ready to pick it back up?")
    elif goal:
        greeting = (f"{tod}, {username}. "
                    f"Picking up from {goal.get('description', '')[:50]}.")
    else:
        greeting = (f"{tod}, {username}. "
                    f"All systems online. What are we working on today?")

    return greeting


# ── Shutdown summary ──────────────────────────────────────────────────────────

def build_shutdown_summary(username: str = "Omri") -> str:
    """Speech said when GIL shuts down cleanly."""
    duration = get_session_duration_minutes()
    goals    = _session_goals

    if duration < 1:
        return f"Short session. Closing down, {username}."

    if duration < 60:
        dur_str = f"{int(duration)} minutes"
    else:
        h = int(duration // 60)
        m = int(duration % 60)
        dur_str = f"{h} hour{'s' if h > 1 else ''}{f' {m} minutes' if m else ''}"

    if goals:
        goal_str = goals[-1][:40]
        return (f"Saving session. {dur_str} of work on {goal_str}. "
                f"Talk later, {username}.")
    else:
        return (f"Saving session. {dur_str} on the clock. "
                f"Talk later, {username}.")


def on_shutdown(fn) -> None:
    _shutdown_callbacks.append(fn)


def trigger_shutdown(username: str = "Omri") -> str:
    summary = build_shutdown_summary(username)
    for fn in list(_shutdown_callbacks):
        try:
            fn(summary)
        except Exception:
            pass
    # Persist session summary to memory
    try:
        from memory import remember
        goal = _session_goals[-1] if _session_goals else ""
        dur  = int(get_session_duration_minutes())
        if goal:
            remember(
                f"Session {datetime.now().strftime('%Y-%m-%d')}: worked on '{goal}' for {dur} min.",
                mem_type="project",
                importance=4,
            )
    except Exception:
        pass
    return summary


# ── Session context for brain prompt ─────────────────────────────────────────

def build_session_context() -> str:
    duration = get_session_duration_minutes()
    if duration < 1:
        when = "just started"
    elif duration < 60:
        when = f"{int(duration)} min active"
    else:
        when = f"{int(duration//60)}h {int(duration%60)}m active"

    lines = [f"SESSION: {when}"]
    if _session_goals:
        lines.append(f"  Goals this session: {' → '.join(g[:30] for g in _session_goals[-3:])}")

    return "\n".join(lines)
