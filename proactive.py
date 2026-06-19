"""
proactive.py — Project G.I.L.
Rules-based background intelligence engine.
Fires non-intrusive suggestions when conditions are met, respecting cooldowns.
"""

import ctypes
import ctypes.wintypes
import threading
import time
from datetime import datetime
from typing import Callable

import psutil

# Cached WINFUNCTYPE callback prototype — must live at module level to avoid GC
_EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

# ── Rule registry ─────────────────────────────────────────────────────────────

class Rule:
    __slots__ = ("name", "cooldown", "last_fired", "check", "message")
    def __init__(self, name: str, cooldown: float, check: Callable, message: Callable | str):
        self.name       = name
        self.cooldown   = cooldown     # seconds
        self.last_fired = 0.0
        self.check      = check        # () -> bool
        self.message    = message      # str or () -> str

    def ready(self) -> bool:
        return (time.time() - self.last_fired) >= self.cooldown

    def get_message(self) -> str:
        return self.message() if callable(self.message) else self.message

    def fire(self) -> str:
        self.last_fired = time.time()
        return self.get_message()


# ── Shared state (set by integration layer in main.py) ────────────────────────

_last_user_interaction: float = time.time()
_last_app              = ""
_idle_notified         = False
_show_callback: Callable | None = None   # gui.show_proactive_suggestion


def set_show_callback(fn: Callable) -> None:
    global _show_callback
    _show_callback = fn


def record_interaction() -> None:
    """Call this every time the user speaks or GIL responds."""
    global _last_user_interaction, _idle_notified
    _last_user_interaction = time.time()
    _idle_notified         = False


def set_active_app(app: str) -> None:
    global _last_app
    _last_app = app


# ── Condition helpers ─────────────────────────────────────────────────────────

def _is_late_night() -> bool:
    h = datetime.now().hour
    return h >= 23 or h < 4


_last_cpu_pct: float = 0.0


def _cpu_hot() -> bool:
    global _last_cpu_pct
    try:
        _last_cpu_pct = psutil.cpu_percent(interval=None)
        return _last_cpu_pct > 85
    except Exception:
        return False


def _gil_idle_minutes() -> float:
    return (time.time() - _last_user_interaction) / 60.0


def _has_error_window() -> bool:
    """Very lightweight check — look for common error dialog titles in window list."""
    try:
        user32 = ctypes.windll.user32
        result = [False]
        ERROR_TITLES = {"error", "exception", "critical", "fatal", "failed", "crash",
                        "unhandled", "traceback", "runtime error"}

        @_EnumWindowsProc
        def enum_cb(hwnd, lparam):
            length = user32.GetWindowTextLengthW(hwnd)
            if 3 < length < 120:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                t = buf.value.lower()
                if any(kw in t for kw in ERROR_TITLES):
                    result[0] = True
                    return False   # stop enumeration
            return True

        user32.EnumWindows(enum_cb, 0)
        return result[0]
    except Exception:
        return False


def _active_app_name() -> str:
    try:
        from context_engine import get_active_context
        return get_active_context().get("app", "")
    except Exception:
        return _last_app


# ── Battery helpers ───────────────────────────────────────────────────────────

_battery_pct: float  = 100.0
_battery_plugged: bool = True

def _refresh_battery() -> None:
    global _battery_pct, _battery_plugged
    try:
        b = psutil.sensors_battery()
        if b:
            _battery_pct    = b.percent
            _battery_plugged = b.power_plugged
    except Exception:
        pass

def _battery_low() -> bool:
    _refresh_battery()
    return not _battery_plugged and _battery_pct < 20

def _battery_critical() -> bool:
    _refresh_battery()
    return not _battery_plugged and _battery_pct < 8

def _battery_msg() -> str:
    return f"Battery at {int(_battery_pct)}% and not charging. Plug in soon."

def _battery_critical_msg() -> str:
    return f"Battery critical — {int(_battery_pct)}%. Plug in now or you'll lose your work."


# ── Meeting soon helpers ──────────────────────────────────────────────────────

_meeting_cache: dict = {"title": "", "minutes": 0, "refreshed_at": 0.0}

def _refresh_meeting_cache() -> None:
    if time.time() - _meeting_cache["refreshed_at"] < 240:
        return
    _meeting_cache["refreshed_at"] = time.time()
    _meeting_cache["minutes"] = 0
    try:
        from gcalendar import get_upcoming_events
        from datetime import datetime as _dt
        events = get_upcoming_events(days=1)
        now = _dt.now()
        for event in events:
            start_str = event.get("start", "")
            if not start_str:
                continue
            try:
                start = _dt.fromisoformat(start_str.replace("Z", ""))
                mins  = (start - now).total_seconds() / 60
                if 2 < mins <= 20:
                    _meeting_cache["title"]   = event.get("summary", "Meeting")
                    _meeting_cache["minutes"] = int(mins)
                    return
            except Exception:
                continue
    except Exception:
        pass

def _meeting_soon() -> bool:
    _refresh_meeting_cache()
    return _meeting_cache["minutes"] > 0

def _meeting_msg() -> str:
    m = _meeting_cache["minutes"]
    t = _meeting_cache["title"]
    return f"Heads up — '{t}' starts in {m} minute{'s' if m != 1 else ''}."


# ── Unsaved file helpers ──────────────────────────────────────────────────────

def _has_unsaved_file() -> bool:
    try:
        from context_engine import get_active_context
        title = get_active_context().get("title", "")
        # VS Code / Cursor: "● filename" prefix  |  Notepad++: adds asterisk
        return title.startswith("●") or (title.startswith("*") and "Notepad" in title)
    except Exception:
        return False

def _unsaved_msg() -> str:
    try:
        from context_engine import get_active_context
        f = get_active_context().get("file", "that file")
        return f"You have unsaved changes in {f}. Want me to save it?"
    except Exception:
        return "You have unsaved changes. Want me to save?"


# ── Schedule learning helpers ─────────────────────────────────────────────────

_schedule_suggestion: list[str] = [""]   # filled at startup check

def _check_schedule_pattern() -> bool:
    try:
        from memory import get_schedule_suggestion
        suggestion = get_schedule_suggestion()
        if suggestion:
            _schedule_suggestion[0] = suggestion
            return True
    except Exception:
        pass
    return False

def _schedule_msg() -> str:
    return _schedule_suggestion[0]


# ── Rules ─────────────────────────────────────────────────────────────────────

def _build_rules() -> list[Rule]:
    return [
        Rule(
            name="late_night",
            cooldown=3600,   # once per hour max
            check=_is_late_night,
            message=lambda: (
                f"It's {datetime.now().strftime('%I:%M %p')}, Omri. "
                "Want to set a stopping point for tonight?"
            ),
        ),
        Rule(
            name="cpu_hot",
            cooldown=300,
            check=_cpu_hot,
            message=lambda: (
                f"CPU's running hot ({int(_last_cpu_pct)}%). "
                "Something's working hard — want me to check what?"
            ),
        ),
        Rule(
            name="error_dialog",
            cooldown=120,
            check=_has_error_window,
            message="I see an error dialog on your screen. Want me to look it up?",
        ),
        Rule(
            name="idle_check",
            cooldown=1800,
            check=lambda: _gil_idle_minutes() > 30 and not _is_late_night(),
            message=lambda: (
                f"You've been in {_active_app_name() or 'that app'} for a while. "
                "Need help with anything?"
            ),
        ),
        Rule(
            name="memory_comeback",
            cooldown=7200,
            check=lambda: False,   # triggered externally via trigger_memory_comeback()
            message="",
        ),
        Rule(
            name="battery_low",
            cooldown=1800,
            check=_battery_low,
            message=_battery_msg,
        ),
        Rule(
            name="battery_critical",
            cooldown=300,
            check=_battery_critical,
            message=_battery_critical_msg,
        ),
        Rule(
            name="meeting_soon",
            cooldown=600,
            check=_meeting_soon,
            message=_meeting_msg,
        ),
        Rule(
            name="unsaved_file",
            cooldown=600,
            check=_has_unsaved_file,
            message=_unsaved_msg,
        ),
        Rule(
            name="schedule_reminder",
            cooldown=86400,   # once per day
            check=_check_schedule_pattern,
            message=_schedule_msg,
        ),
    ]


_rules = _build_rules()
_rule_map = {r.name: r for r in _rules}


def trigger_memory_comeback(text: str) -> None:
    """Called by brain when a recurring issue is detected in memories."""
    r = _rule_map.get("memory_comeback")
    if r and r.ready():
        _emit(text)
        r.last_fired = time.time()


# ── Emit ─────────────────────────────────────────────────────────────────────

def _proactive_enabled() -> bool:
    try:
        import json
        from pathlib import Path
        with open(Path(__file__).parent / "data" / "gil_config.json") as f:
            return json.load(f).get("proactive_on", True)
    except Exception:
        return True


def _emit(message: str) -> None:
    if not _proactive_enabled():
        return
    print(f"[G.I.L. PROACTIVE] {message}")
    if _show_callback:
        try:
            _show_callback(message)
        except Exception as exc:
            print(f"[G.I.L. PROACTIVE] callback err: {exc}")


# ── Main loop ─────────────────────────────────────────────────────────────────

_running = False


def start() -> None:
    global _running
    if _running:
        return
    _running = True
    threading.Thread(target=_loop, daemon=True, name="GIL-Proactive").start()
    print("[G.I.L. PROACTIVE] Engine started.")


def _loop() -> None:
    while _running:
        time.sleep(10)
        for rule in _rules:
            try:
                if rule.ready() and rule.check():
                    _emit(rule.fire())
            except Exception as exc:
                print(f"[G.I.L. PROACTIVE] Rule {rule.name} error: {exc}")


def stop() -> None:
    global _running
    _running = False
