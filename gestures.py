"""
gestures.py -- Project G.I.L.
Real-time hand gesture action bridge.

Polls the gesture state file written by camera_viewer.py at ~30 fps and fires
GIL system actions based on gesture type and hold duration.

Gesture -> Action map:
  index_point  -- air mouse (cursor control)
  pinch        -- left click (only while in cursor mode)
  peace        -- screenshot (hold ~0.8 s)
  thumbs_up    -- volume +10 (hold ~0.6 s)
  thumbs_down  -- volume -10 (hold ~0.6 s)
  fist         -- mute toggle (hold ~0.6 s)
  three_up     -- next track (hold ~0.6 s)
  call_me      -- previous track (hold ~0.6 s)
  open_hand    -- announce gesture mode (hold ~0.7 s)
"""

import json
import os
import time
import threading
import tempfile
import ctypes
from datetime import datetime
from pathlib import Path

GESTURE_FILE = Path(tempfile.gettempdir()) / "gil_gesture_state.json"

# Frames at ~30 fps required before a gesture triggers
_HOLD_FRAMES: dict = {
    "index_point":  3,    # cursor engages immediately
    "pinch":        4,    # click -- short but deliberate
    "peace":       15,    # screenshot -- ~0.5 s
    "thumbs_up":   15,    # volume up -- ~0.5 s
    "thumbs_down": 15,
    "fist":        15,    # mute / cursor exit
    "open_hand":   18,    # ~0.6 s
    "three_up":    15,    # next track
    "call_me":     15,    # prev track
}

# Frames a DIFFERENT gesture must persist before the hold counter resets.
# Prevents a single flickered bad frame from wiping out accumulated hold time.
_HYSTERESIS = 3

# Minimum seconds between consecutive triggers of the same gesture
_COOLDOWN: dict = {
    "index_point":  0.0,   # continuous
    "pinch":        0.75,
    "peace":        3.0,
    "thumbs_up":    1.2,
    "thumbs_down":  1.2,
    "fist":         2.0,
    "open_hand":    3.0,
    "three_up":     1.2,
    "call_me":      1.2,
}


class GestureWatcher:
    """
    One instance per camera session.
    Call start() when the camera opens, stop() when it closes.
    """

    def __init__(self, speak_fn=None):
        self._speak    = speak_fn
        self._running  = False
        self._thread   = None

        # State machine
        self._hold_gesture = None
        self._hold_count   = 0
        self._break_count  = 0   # consecutive frames where gesture differs from hold_gesture
        self._last_trigger = {}
        self._last_ts      = 0.0

        # Cursor (air-mouse) state
        # _mode: "command" (default) or "cursor" (sticky -- only fist exits it)
        self._mode  = "command"
        self._cur_x = 0.5
        self._cur_y = 0.5

        # Mute toggle tracking
        self._muted = False

        user32          = ctypes.windll.user32
        self._screen_w  = user32.GetSystemMetrics(0)
        self._screen_h  = user32.GetSystemMetrics(1)

    # ---- Lifecycle -----------------------------------------------------------

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop, daemon=True, name="GIL-GestureWatch"
        )
        self._thread.start()
        print("[G.I.L. GESTURE] Watcher started.")

    def stop(self):
        self._running = False
        self._mode    = "command"
        print("[G.I.L. GESTURE] Watcher stopped.")

    # ---- Polling loop --------------------------------------------------------

    def _loop(self):
        try:
            import pyautogui
            pyautogui.FAILSAFE = False
            pyautogui.PAUSE    = 0.0
        except ImportError:
            pyautogui = None

        EMA          = 0.35   # lower = smoother but more lag

        while self._running:
            time.sleep(0.033)   # ~30 fps

            state = self._read_state()
            if state is None:
                continue

            ts           = state.get("ts", 0.0)
            gesture      = state.get("gesture")
            raw_x        = float(state.get("x", 0.5))
            raw_y        = float(state.get("y", 0.5))
            hand_visible = bool(state.get("hand", gesture is not None))

            if ts <= self._last_ts:
                continue
            self._last_ts = ts

            # Hold counter with hysteresis -- a single flickered frame doesn't
            # reset progress; the new gesture must persist for _HYSTERESIS frames.
            if gesture == self._hold_gesture:
                self._break_count  = 0
                self._hold_count  += 1
            else:
                self._break_count += 1
                if self._break_count >= _HYSTERESIS:
                    self._hold_gesture = gesture
                    self._hold_count   = 0
                    self._break_count  = 0

            # ==================================================================
            # CURSOR MODE (sticky) -- entered via index_point, exited via fist
            # ==================================================================
            if self._mode == "cursor":
                # Always move cursor when hand is visible (tracks index tip)
                if hand_visible:
                    self._cur_x = EMA * raw_x + (1 - EMA) * self._cur_x
                    self._cur_y = EMA * raw_y + (1 - EMA) * self._cur_y
                    sx = max(0, min(self._screen_w - 1, int(self._cur_x * self._screen_w)))
                    sy = max(0, min(self._screen_h - 1, int(self._cur_y * self._screen_h)))
                    if pyautogui:
                        pyautogui.moveTo(sx, sy)

                # Pinch = click
                if gesture == "pinch":
                    if (self._hold_count >= _HOLD_FRAMES["pinch"]
                            and self._can_trigger("pinch")):
                        self._last_trigger["pinch"] = time.time()
                        if pyautogui:
                            threading.Thread(
                                target=pyautogui.click, daemon=True,
                                name="GIL-GestureClick"
                            ).start()

                # Fist held = exit cursor mode
                elif gesture == "fist" and self._hold_count >= _HOLD_FRAMES["fist"]:
                    self._mode = "command"
                    self._last_trigger["fist_exit"] = time.time()
                    self._say("Cursor off.")
                    print("[G.I.L. GESTURE] Exited cursor mode.")

                # Everything else ignored in cursor mode
                continue

            # ==================================================================
            # COMMAND MODE -- gestures fire actions; index_point enters cursor
            # ==================================================================

            # Index point: enter sticky cursor mode
            if gesture == "index_point":
                if self._hold_count >= _HOLD_FRAMES["index_point"]:
                    self._mode  = "cursor"
                    # Seed cursor at current finger position (no jump)
                    self._cur_x = raw_x
                    self._cur_y = raw_y
                    self._say("Cursor on.")
                    print("[G.I.L. GESTURE] Entered cursor mode.")
                continue

            # All other hold gestures
            if self._hold_count < _HOLD_FRAMES.get(gesture, 9999):
                continue
            if not self._can_trigger(gesture):
                continue

            self._last_trigger[gesture] = time.time()
            threading.Thread(
                target=self._execute, args=(gesture,), daemon=True,
                name="GIL-Gesture-" + (gesture or "")
            ).start()

    def _can_trigger(self, gesture):
        if not gesture:
            return False
        elapsed = time.time() - self._last_trigger.get(gesture, 0.0)
        return elapsed >= _COOLDOWN.get(gesture, 2.0)

    # ---- Action execution (runs in its own thread) --------------------------

    def _execute(self, gesture):
        print("[G.I.L. GESTURE] Action: " + gesture)
        try:
            import pyautogui
        except ImportError:
            pyautogui = None

        if gesture == "peace":
            self._screenshot(pyautogui)

        elif gesture == "thumbs_up":
            self._adjust_volume(+10)

        elif gesture == "thumbs_down":
            self._adjust_volume(-10)

        elif gesture == "fist":
            self._muted = not self._muted
            if pyautogui:
                pyautogui.press("volumemute")
            self._say("Muted." if self._muted else "Unmuted.")

        elif gesture == "three_up":
            if pyautogui:
                pyautogui.press("nexttrack")

        elif gesture == "call_me":
            if pyautogui:
                pyautogui.press("prevtrack")

        elif gesture == "open_hand":
            self._say("Gesture mode active.")

    def _screenshot(self, pyautogui):
        try:
            pics = os.path.join(os.path.expanduser("~"), "Pictures")
            os.makedirs(pics, exist_ok=True)
            fname = "GIL_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".png"
            path  = os.path.join(pics, fname)
            if pyautogui:
                img = pyautogui.screenshot()
                img.save(path)
                print("[G.I.L. GESTURE] Screenshot: " + path)
            self._say("Screenshot saved.")
        except Exception as exc:
            print("[G.I.L. GESTURE] Screenshot failed: " + str(exc))

    def _adjust_volume(self, delta):
        try:
            from pc_control import get_system_volume, set_system_volume
            new_vol = max(0, min(100, get_system_volume() + delta))
            set_system_volume(new_vol)
            self._say("Volume " + str(new_vol) + ".")
        except Exception as exc:
            print("[G.I.L. GESTURE] Volume error: " + str(exc))

    def _say(self, text):
        if self._speak:
            try:
                self._speak(text)
            except Exception:
                pass

    # ---- File reader ---------------------------------------------------------

    def _read_state(self):
        try:
            return json.loads(GESTURE_FILE.read_bytes())
        except Exception:
            return None
