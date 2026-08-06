"""
spatial.py — start/stop G.I.L.'s Spatial Control.

The overlay runs as its own process: it needs a tkinter main loop, and GIL's GUI
already owns one in this process. Keeping it separate also means a crash in the
tracker can never take GIL down with it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from logger import get as _get_log

log = _get_log("spatial")

ROOT = Path(__file__).parent
SCRIPT = ROOT / "spatial_overlay.py"
WORKSPACE = ROOT / "spatial_workspace.py"
MODEL = ROOT / "data" / "hand_landmarker.task"
TUTORIAL_FLAG = ROOT / "data" / "spatial_tutorial_done"

_proc: subprocess.Popen | None = None


def is_running() -> bool:
    return _proc is not None and _proc.poll() is None


def first_time() -> bool:
    return not TUTORIAL_FLAG.exists()


def start() -> tuple[bool, str]:
    """Launch the overlay. Returns (ok, message for the user)."""
    global _proc
    if is_running():
        return True, "Spatial control is already on."
    if not SCRIPT.is_file():
        return False, "The spatial overlay is missing from this install."
    if not MODEL.is_file():
        return False, ("The hand-tracking model isn't installed "
                       "(data/hand_landmarker.task).")
    try:
        # pythonw where available so no console window flashes up
        exe = sys.executable
        if exe.lower().endswith("python.exe"):
            pyw = Path(exe).with_name("pythonw.exe")
            if pyw.is_file():
                exe = str(pyw)
        _proc = subprocess.Popen(
            [exe, str(SCRIPT)], cwd=str(ROOT),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:
        log.error("spatial start failed", exc_info=True)
        return False, f"I couldn't start spatial control: {exc}"

    if first_time():
        return True, ("**Spatial control on.** ✋\n\nHold your hand up to the "
                      "camera — I'll walk you through it once, then get out of "
                      "the way.")
    return True, ("**Spatial control on.** ✋ Pinch to select, fist to grab a "
                  "window, two fists to zoom. `Esc` or `/spatial off` to stop.")


def stop() -> tuple[bool, str]:
    global _proc
    if not is_running():
        return True, "Spatial control is already off."
    try:
        subprocess.run(["taskkill", "/PID", str(_proc.pid), "/T", "/F"],
                       capture_output=True, timeout=5,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        try:
            _proc.terminate()
        except Exception:
            pass
    _proc = None
    return True, "**Spatial control off.** The camera is released."


def toggle() -> tuple[bool, str]:
    return stop() if is_running() else start()


_ws: subprocess.Popen | None = None


def workspace_running() -> bool:
    return _ws is not None and _ws.poll() is None


def open_workspace(hands: bool = True) -> tuple[bool, str]:
    """Lift the open windows into the Spatial Workspace."""
    global _ws
    if workspace_running():
        return True, "The workspace is already open."
    if not WORKSPACE.is_file():
        return False, "The spatial workspace is missing from this install."
    try:
        args = [sys.executable, str(WORKSPACE)]
        if not hands:
            args.append("--nohands")
        _ws = subprocess.Popen(args, cwd=str(ROOT))
    except Exception as exc:
        log.error("workspace start failed", exc_info=True)
        return False, f"I couldn't open the workspace: {exc}"
    return True, ("**Spatial Workspace.** 🪐 Your windows are floating — "
                  "fist to carry one, pinch to drop into it, `Esc` to come back.")


def scan_hands() -> tuple[bool, str]:
    """Learn the user's skin tone so the rendered hands are theirs."""
    script = ROOT / "spatial_scan.py"
    if not script.is_file():
        return False, "The hand scanner is missing from this install."
    try:
        subprocess.Popen([sys.executable, str(script)], cwd=str(ROOT))
    except Exception as exc:
        return False, f"I couldn't start the hand scan: {exc}"
    return True, ("**Scanning your hands.** ✋ Hold an open hand up to the "
                  "camera and turn it slowly — I'll match the hands I draw to "
                  "your own.")


def close_workspace() -> tuple[bool, str]:
    global _ws
    if not workspace_running():
        return True, "The workspace isn't open."
    try:
        subprocess.run(["taskkill", "/PID", str(_ws.pid), "/T", "/F"],
                       capture_output=True, timeout=5,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        try:
            _ws.terminate()
        except Exception:
            pass
    _ws = None
    return True, "Workspace closed."


def reset_tutorial() -> None:
    """Show the walkthrough again next time."""
    try:
        TUTORIAL_FLAG.unlink(missing_ok=True)
    except Exception:
        pass
