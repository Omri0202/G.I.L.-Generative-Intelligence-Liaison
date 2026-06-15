"""
meeting_detector.py — Project G.I.L.
Detects active Zoom/Teams/Meet sessions and auto-switches to Presentation mode.
Polls process list every 15 seconds.
"""

import threading
import time
from typing import Callable

import psutil

_MEETING_KEYWORDS = {"zoom", "msteams", "teams", "webex", "gotomeeting", "skype"}

_in_meeting      = [False]
_running         = [False]
_mode_callback: Callable[[str], None] | None = None


def set_mode_callback(fn: Callable[[str], None]) -> None:
    global _mode_callback
    _mode_callback = fn


def is_in_meeting() -> bool:
    return _in_meeting[0]


def _detect() -> bool:
    try:
        for proc in psutil.process_iter(["name", "status"]):
            name   = (proc.info.get("name") or "").lower().replace(".exe", "")
            status = proc.info.get("status", "")
            if status == "running":
                for kw in _MEETING_KEYWORDS:
                    if kw in name:
                        return True
    except Exception:
        pass
    return False


def start_meeting_watcher() -> None:
    if _running[0]:
        return
    _running[0] = True

    def _loop():
        while _running[0]:
            detected = _detect()
            if detected and not _in_meeting[0]:
                _in_meeting[0] = True
                print("[G.I.L. MEETING] Meeting detected — switching to Presentation mode.")
                if _mode_callback:
                    try:
                        _mode_callback("presentation")
                    except Exception as exc:
                        print(f"[G.I.L. MEETING] Mode callback error: {exc}")
            elif not detected and _in_meeting[0]:
                _in_meeting[0] = False
                print("[G.I.L. MEETING] Meeting ended — switching to Normal mode.")
                if _mode_callback:
                    try:
                        _mode_callback("normal")
                    except Exception as exc:
                        print(f"[G.I.L. MEETING] Mode callback error: {exc}")
            time.sleep(15)

    threading.Thread(target=_loop, daemon=True, name="GIL-MeetingDetector").start()
    print("[G.I.L. MEETING] Meeting detector active (15s poll).")
