"""
proactive.py — Project G.I.L.
Rules-based background intelligence engine.
Fires non-intrusive suggestions when conditions are met, respecting cooldowns.
"""

import threading
import time
from datetime import datetime
from typing import Callable

import psutil

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
        _last_cpu_pct = psutil.cpu_percent(interval=1)
        return _last_cpu_pct > 85
    except Exception:
        return False


def _gil_idle_minutes() -> float:
    return (time.time() - _last_user_interaction) / 60.0


def _has_error_window() -> bool:
    """Very lightweight check — look for common error dialog titles in window list."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        result = [False]
        ERROR_TITLES = {"error", "exception", "critical", "fatal", "failed", "crash",
                        "unhandled", "traceback", "runtime error"}

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
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
