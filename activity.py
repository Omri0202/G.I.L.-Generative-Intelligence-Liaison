"""
activity.py — G.I.L. live activity feed.

Claude-style transparency layer: every action GIL performs is published
here as an event, and any subscriber (the chat window, the HUD) can render
it live — spinner while running, check/cross when finished, with duration.

Thread-safe. Emitters call from worker threads; subscribers are invoked
on the emitter's thread and must marshal to the UI thread themselves
(ChatWindow does this with .after()).

Usage (emitter):
    import activity
    aid = activity.start("app", "Opening Chrome")
    ...
    activity.done(aid, "Launched Chrome.")        # or activity.fail(aid, "not found")

    activity.instant("note", "Saved note")        # one-shot completed event

Usage (subscriber):
    activity.subscribe(my_callback)               # callback(entry_dict)
    Entry dict: {id, kind, title, detail, status, t0, duration, group}
    status: "running" | "done" | "fail"
"""

import itertools
import threading
import time

from logger import get as _get_log

log = _get_log("activity")

_lock         = threading.Lock()
_subscribers: list = []
_counter      = itertools.count(1)
_entries: dict[int, dict] = {}
_group: list[str] = [""]        # current group label (e.g. mission title)

# Human-readable labels for brain/router actions.
# {t} is replaced with the action target.
ACTION_LABELS = {
    "open_app":        ("app",      "Opening {t}"),
    "open_url":        ("web",      "Opening {t}"),
    "web_search":      ("search",   "Searching the web: {t}"),
    "web_research":    ("search",   "Researching: {t}"),
    "system_vitals":   ("system",   "Checking system vitals"),
    "take_screenshot": ("system",   "Taking a screenshot"),
    "sign_in":         ("system",   "Signing in to {t}"),
    "build":           ("code",     "Building project: {t}"),
    "open_terminal":   ("code",     "Running in terminal: {t}"),
    "focus_window":    ("system",   "Focusing {t}"),
    "arrange_windows": ("system",   "Arranging windows: {t}"),
    "close_window":    ("system",   "Closing {t}"),
    "minimize_all":    ("system",   "Minimizing all windows"),
    "maximize_window": ("system",   "Maximizing {t}"),
    "open_file":       ("file",     "Opening file: {t}"),
    "read_file":       ("file",     "Reading file: {t}"),
    "list_directory":  ("file",     "Listing folder: {t}"),
    "find_file":       ("file",     "Finding file: {t}"),
    "set_clipboard":   ("system",   "Copying to clipboard"),
    "get_clipboard":   ("system",   "Reading clipboard"),
    "tv":              ("system",   "TV: {t}"),
    "set_mode":        ("system",   "Switching to {t} mode"),
    "pc":              ("system",   "PC: {t}"),
    "pc_volume":       ("system",   "Volume: {t}"),
    "weather":         ("search",   "Fetching weather"),
    "reminder":        ("note",     "Setting reminder"),
    "list_reminders":  ("note",     "Listing reminders"),
    "note":            ("note",     "Saving note"),
    "list_notes":      ("note",     "Listing notes"),
    "clip_history":    ("system",   "Reading clipboard history"),
    "spotify":         ("media",    "Spotify: {t}"),
    "briefing":        ("search",   "Running your briefing"),
    "nearby":          ("search",   "Finding nearby {t}"),
    "directions":      ("search",   "Getting directions to {t}"),
    "my_location":     ("search",   "Locating you"),
    "food_delivery":   ("web",      "Opening food delivery"),
    "news":            ("search",   "Fetching news"),
    "open_article":    ("web",      "Opening article"),
    "calendar":        ("note",     "Checking calendar"),
    "add_event":       ("note",     "Adding calendar event"),
    "look":            ("vision",   "Looking through the camera"),
    "open_camera":     ("vision",   "Opening camera"),
    "close_camera":    ("vision",   "Closing camera"),
    "build_website":   ("code",     "Building website"),
    "generate_image":  ("image",    "Generating image"),
    "create_3d":       ("code",     "Creating 3D scene: {t}"),
    # Dev tools
    "git_status":      ("code", "git status"),
    "git_commit":      ("code", "git commit"),
    "git_push":        ("code", "git push"),
    "git_pull":        ("code", "git pull"),
    "git_log":         ("code", "git log"),
    "git_diff":        ("code", "git diff"),
    "git_branch_create": ("code", "Creating branch {t}"),
    "git_branch_switch": ("code", "Switching to branch {t}"),
    "git_branch_list": ("code", "Listing branches"),
    "git_stash":       ("code", "git stash {t}"),
    "run_command":     ("code", "Running: {t}"),
    "run_tests":       ("code", "Running tests"),
    "code_search":     ("code", "Searching code: {t}"),
    "find_definition": ("code", "Finding definition: {t}"),
    "find_todos":      ("code", "Finding TODOs"),
    "project_structure": ("code", "Mapping project structure"),
    "deps_outdated":   ("code", "Checking outdated packages"),
    "deps_install":    ("code", "Installing {t}"),
    "docker_ps":       ("code", "Listing containers"),
    "docker_start":    ("code", "Starting container {t}"),
    "docker_stop":     ("code", "Stopping container {t}"),
    "docker_logs":     ("code", "Container logs: {t}"),
    "docker_compose_up":   ("code", "docker compose up"),
    "docker_compose_down": ("code", "docker compose down"),
    "github_prs":      ("code", "Fetching your PRs"),
    "github_issues":   ("code", "Fetching issues"),
    "github_ci":       ("code", "Checking CI status"),
}


def label_for(action: str, target: str = "") -> tuple[str, str]:
    """Return (kind, human label) for a brain action name."""
    kind, tpl = ACTION_LABELS.get(action, ("system", action.replace("_", " ")))
    t = (target or "").strip()
    if len(t) > 48:
        t = t[:45] + "…"
    try:
        label = tpl.format(t=t) if "{t}" in tpl else tpl
    except Exception:
        label = tpl
    # "Opening " with empty target reads badly — strip trailing separators
    return kind, label.rstrip(" :").strip()


def subscribe(fn) -> None:
    with _lock:
        if fn not in _subscribers:
            _subscribers.append(fn)


def unsubscribe(fn) -> None:
    with _lock:
        if fn in _subscribers:
            _subscribers.remove(fn)


def set_group(title: str) -> None:
    """Set a group label (e.g. a mission title) attached to subsequent events."""
    _group[0] = title or ""


def clear_group() -> None:
    _group[0] = ""


def in_group() -> bool:
    """True while a mission group is active — used by low-level emitters to
    avoid duplicating the step cards the mission runner already publishes."""
    return bool(_group[0])


def _publish(entry: dict) -> None:
    with _lock:
        subs = list(_subscribers)
    for fn in subs:
        try:
            fn(dict(entry))
        except Exception:
            log.debug("subscriber failed", exc_info=True)


def start(kind: str, title: str, detail: str = "") -> int:
    aid = next(_counter)
    entry = {
        "id": aid, "kind": kind, "title": title, "detail": detail,
        "status": "running", "t0": time.time(), "duration": 0.0,
        "group": _group[0],
    }
    with _lock:
        _entries[aid] = entry
        # keep the map bounded
        if len(_entries) > 200:
            for k in list(_entries)[:100]:
                _entries.pop(k, None)
    _publish(entry)
    return aid


def _finish(aid: int, status: str, detail: str) -> None:
    with _lock:
        entry = _entries.get(aid)
    if not entry:
        return
    entry["status"]   = status
    entry["duration"] = time.time() - entry["t0"]
    if detail:
        entry["detail"] = detail
    _publish(entry)


def done(aid: int, detail: str = "") -> None:
    _finish(aid, "done", detail)


def fail(aid: int, detail: str = "") -> None:
    _finish(aid, "fail", detail)


def update(aid: int, detail: str) -> None:
    with _lock:
        entry = _entries.get(aid)
    if not entry:
        return
    entry["detail"] = detail
    _publish(entry)


def instant(kind: str, title: str, detail: str = "", status: str = "done") -> int:
    """One-shot event that is already finished when published."""
    aid = next(_counter)
    entry = {
        "id": aid, "kind": kind, "title": title, "detail": detail,
        "status": status, "t0": time.time(), "duration": 0.0,
        "group": _group[0],
    }
    with _lock:
        _entries[aid] = entry
    _publish(entry)
    return aid
