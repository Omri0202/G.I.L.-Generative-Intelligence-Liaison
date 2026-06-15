"""
modes.py — Project G.I.L.
Behavioral mode manager.
Modes: normal, dnd, study, fun.
"""

import threading
import time
from typing import Callable

_current_mode:      str                              = "normal"
_paused_callback:   Callable[[bool], None] | None   = None
_window_ref                                          = None
_speak_ref:         Callable[[str], None] | None     = None
_pomodoro_active                                     = [False]

# Modes that block proactive announcements (emails, WhatsApp, etc.)
_PROACTIVE_BLOCKED = {"dnd", "study"}

# Modes that pause voice input
_PAUSED_MODES = {"dnd"}


def set_paused_callback(fn: Callable[[bool], None]) -> None:
    global _paused_callback
    _paused_callback = fn


def set_window_ref(window) -> None:
    global _window_ref
    _window_ref = window


def set_speak_ref(fn: Callable[[str], None]) -> None:
    global _speak_ref
    _speak_ref = fn


def get_current_mode() -> str:
    return _current_mode


def is_proactive_blocked() -> bool:
    """Returns True when proactive announcements (email, WhatsApp) should be suppressed."""
    return _current_mode in _PROACTIVE_BLOCKED


def set_mode(mode_name: str) -> str:
    global _current_mode
    name = mode_name.lower().strip()

    # Accept aliases
    _ALIASES = {
        "do not disturb": "dnd",
        "donotdisturb":   "dnd",
        "silent":         "dnd",
        "quiet":          "dnd",
        "focus":          "dnd",
        "default":        "normal",
    }
    name = _ALIASES.get(name, name)

    _VALID = {"normal", "dnd", "study", "fun"}
    if name not in _VALID:
        return f"I only have three modes: do not disturb, study, and fun. Say 'normal mode' to reset."

    prev          = _current_mode
    _current_mode = name
    paused        = name in _PAUSED_MODES

    if _paused_callback:
        _paused_callback(paused)

    # DND — hide window completely
    if name == "dnd":
        if _window_ref:
            try:
                _window_ref.after(0, _window_ref.withdraw)
            except Exception:
                pass
    elif prev == "dnd":
        if _window_ref:
            try:
                _window_ref.after(0, _window_ref.deiconify)
            except Exception:
                pass

    # Study — start/stop Pomodoro
    if name == "study":
        _start_pomodoro()
    elif prev == "study" and name != "study":
        _stop_pomodoro()

    # Fun — play Spotify
    if name == "fun":
        threading.Thread(target=_activate_fun_mode, daemon=True, name="GIL-FunMode").start()

    descriptions = {
        "normal": "Normal mode — everything's live.",
        "dnd":    "Do not disturb — I'll go silent. Say my name or press the hotkey when you need me.",
        "study":  "Study mode on. Pomodoro started — 25 minutes of focus, then a break. No interruptions.",
        "fun":    "Fun mode on — Spotify is going. What do you want to play?",
    }
    return descriptions[name]


def _activate_fun_mode() -> None:
    """Launch Spotify silently so it's ready — don't auto-play, let user choose."""
    try:
        from spotify_control import _launch_spotify_silent, _get_sp
        sp = _get_sp()
        if sp and sp.devices().get("devices"):
            return   # already running, nothing to do
        _launch_spotify_silent()
    except Exception as exc:
        print(f"[G.I.L. MODES] Fun mode Spotify launch error: {exc}")


def _start_pomodoro() -> None:
    _pomodoro_active[0] = True

    def _loop():
        session = 0
        while _pomodoro_active[0]:
            session += 1
            if _speak_ref:
                _speak_ref(f"Session {session} — 25 minutes. Go.")
            for _ in range(25 * 60):
                if not _pomodoro_active[0]:
                    return
                time.sleep(1)
            if not _pomodoro_active[0]:
                return
            if _speak_ref:
                _speak_ref("Time. Take a 5-minute break.")
            for _ in range(5 * 60):
                if not _pomodoro_active[0]:
                    return
                time.sleep(1)

    threading.Thread(target=_loop, daemon=True, name="GIL-Pomodoro").start()


def _stop_pomodoro() -> None:
    _pomodoro_active[0] = False
