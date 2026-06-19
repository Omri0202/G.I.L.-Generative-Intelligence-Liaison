"""
gestures.py -- Project G.I.L.
Real-time hand gesture action bridge.

Polls the gesture state file written by camera_viewer.py at ~30 fps and fires
GIL system actions based on gesture type and hold duration.

Single-hand gesture → action map:
  index_point  -- air mouse (cursor control)
  pinch        -- left click / drag (only while in cursor mode)
  peace        -- right click (cursor mode) / screenshot (command mode, ~0.8 s)
  thumbs_up    -- volume +10 (hold ~0.6 s)
  thumbs_down  -- volume -10 (hold ~0.6 s)
  fist         -- mute toggle (hold ~0.6 s) / exit cursor mode
  three_up     -- next track (hold ~0.6 s)
  call_me      -- previous track (hold ~0.6 s)
  open_hand    -- announce gesture mode (hold ~0.7 s)
  rock_on      -- toggle DND mode (hold ~0.7 s)

Two-hand combos (hold ~0.6 s with both hands):
  fist + fist            -- lock screen
  thumbs_up + thumbs_up  -- volume to 100%
  open_hand + open_hand  -- show desktop (Win+D)
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
    "pinch":        2,    # click -- fast enough to feel responsive (~67ms)
    "peace":       15,    # screenshot (command) / right-click (cursor) -- ~0.5 s
    "thumbs_up":   15,    # volume up -- ~0.5 s
    "thumbs_down": 15,
    "fist":        15,    # mute / cursor exit
    "open_hand":   18,    # ~0.6 s
    "three_up":    15,    # next track
    "call_me":     15,    # prev track
    "rock_on":     20,    # DND toggle -- ~0.7 s (deliberate)
}

# Two-hand combo: frames both gestures must be held simultaneously before firing
_COMBO_HOLD = 10   # ~0.33 s

# Frames a DIFFERENT gesture must persist before the hold counter resets.
# Prevents a single flickered bad frame from wiping out accumulated hold time.
_HYSTERESIS = 3

# Cursor input remapping: maps the typical hand movement range inside the camera
# frame to the full screen. Tune these if the cursor doesn't reach screen edges.
# X uses the stable MCP knuckle (narrower natural range), Y uses the fingertip.
_X_LO, _X_HI = 0.12, 0.88   # left / right boundary of useful hand range
_Y_LO, _Y_HI = 0.04, 0.82   # top  / bottom — fingertip reaches these extremes

def _remap(v, lo, hi):
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))

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
    "rock_on":      4.0,   # DND toggle -- long cooldown, it's a mode change
}

_COMBO_COOLDOWN = 1.5   # seconds between combo retriggers


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
        self._mode         = "command"
        self._cur_x        = 0.5
        self._cur_y        = 0.5
        self._pinch_held   = False   # True while mouseDown is active (drag)
        self._pinch_breaks = 0       # consecutive non-pinch frames while drag is held

        # Drag anchors: recorded at mouseDown; delta-based movement prevents cursor
        # jumping when pinch forms (index tip moves toward thumb, shifting raw coords)
        self._drag_x0  = 0.5
        self._drag_y0  = 0.5
        self._drag_cx0 = 0.5
        self._drag_cy0 = 0.5
        self._drag_moved    = 0.0   # max displacement since mouseDown (drag detection)
        self._mousedown_at  = 0.0   # timestamp of mouseDown
        self._pinch_raw_count = 0   # consecutive raw pinch frames
        self._in_pinch      = False  # pinch confirmed, pending click-vs-drag resolution

        # Previous raw hand position (for velocity-based EMA smoothing)
        self._prev_raw_x = 0.5
        self._prev_raw_y = 0.5

        # Two-hand combo tracking
        self._combo_gesture   = None   # tuple(sorted([g1, g2])) or None
        self._combo_count     = 0
        self._combo_cooldown  = {}     # combo_key → last trigger time

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
        self._running       = False
        self._mode          = "command"
        self._in_pinch      = False
        self._pinch_raw_count = 0
        self._combo_gesture = None
        self._combo_count   = 0
        if self._pinch_held:
            try:
                import pyautogui
                pyautogui.mouseUp()
            except Exception:
                pass
            self._pinch_held = False
        print("[G.I.L. GESTURE] Watcher stopped.")

    # ---- Polling loop --------------------------------------------------------

    def _loop(self):
        try:
            import pyautogui
            pyautogui.FAILSAFE = False
            pyautogui.PAUSE    = 0.0
        except ImportError:
            pyautogui = None

        while self._running:
            time.sleep(0.033)   # ~30 fps

            state = self._read_state()
            if state is None:
                continue

            ts           = state.get("ts", 0.0)
            gesture      = state.get("gesture")
            gesture2     = state.get("gesture2")   # second hand (may be None)
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
            # TWO-HAND COMBO DETECTION (runs regardless of mode)
            # Both hands must hold the same gesture simultaneously for _COMBO_HOLD
            # frames. Only fires when not in the middle of an active drag.
            # ==================================================================
            if gesture and gesture2 and not self._pinch_held:
                combo = tuple(sorted([gesture, gesture2]))
                if combo == self._combo_gesture:
                    self._combo_count += 1
                else:
                    self._combo_gesture = combo
                    self._combo_count   = 0

                if self._combo_count >= _COMBO_HOLD:
                    combo_key = combo[0] + "/" + combo[1]
                    elapsed_c = time.time() - self._combo_cooldown.get(combo_key, 0.0)
                    if elapsed_c >= _COMBO_COOLDOWN:
                        self._combo_cooldown[combo_key] = time.time()
                        self._combo_count = 0
                        threading.Thread(
                            target=self._execute_combo, args=(combo,),
                            daemon=True, name="GIL-Combo",
                        ).start()
            else:
                self._combo_gesture = None
                self._combo_count   = 0

            # ==================================================================
            # CURSOR MODE (sticky) -- entered via index_point, exited via fist
            # ==================================================================
            if self._mode == "cursor":
                # Remap hand position to screen range (runs once per frame)
                mx = _remap(raw_x, _X_LO, _X_HI)
                my = _remap(raw_y, _Y_LO, _Y_HI)

                # Velocity of raw hand position between frames (for adaptive EMA).
                _vel = ((raw_x - self._prev_raw_x)**2 + (raw_y - self._prev_raw_y)**2) ** 0.5
                self._prev_raw_x = raw_x
                self._prev_raw_y = raw_y

                if hand_visible:
                    if self._pinch_held:
                        # Drag mode: follow hand delta from anchor
                        dx = mx - self._drag_x0
                        dy = my - self._drag_y0
                        target_x = self._drag_cx0 + dx
                        target_y = self._drag_cy0 + dy
                        self._cur_x = 0.35 * target_x + 0.65 * self._cur_x
                        self._cur_y = 0.35 * target_y + 0.65 * self._cur_y
                        _dm = (dx**2 + dy**2) ** 0.5
                        self._drag_moved = max(self._drag_moved, _dm)
                    elif self._in_pinch or gesture == "pinch":
                        # Cursor frozen: pinch is forming OR pending click resolution.
                        # Ensures the click lands exactly where the user was aiming.
                        pass
                    else:
                        # Velocity-scaled EMA: heavy smoothing when still (precise
                        # targeting), snappy on fast sweeps (full-screen coverage).
                        _DEAD = 0.007   # dead zone — filters micro-tremors
                        if _vel > _DEAD:
                            _ema = min(0.72, 0.18 + _vel * 10.0)
                            self._cur_x = _ema * mx + (1 - _ema) * self._cur_x
                            self._cur_y = _ema * my + (1 - _ema) * self._cur_y
                    sx = max(0, min(self._screen_w - 1, int(self._cur_x * self._screen_w)))
                    sy = max(0, min(self._screen_h - 1, int(self._cur_y * self._screen_h)))
                    if pyautogui:
                        pyautogui.moveTo(sx, sy)

                # Click / drag state machine.
                #
                # Two-phase approach keeps clicks atomic (no cursor drift between
                # mouseDown and mouseUp):
                #
                #   Phase 1 — pinch pending:  2 raw pinch frames detected → _in_pinch
                #   Phase 2a — quick release: pinch released before drag threshold
                #              → pyautogui.click() (atomic — both events same coord)
                #   Phase 2b — sustained hold: pinch held 6+ frames → drag mode
                #              → mouseDown … moveTo … mouseUp
                if gesture == "pinch":
                    self._pinch_raw_count += 1
                    self._pinch_breaks     = 0

                    if self._pinch_raw_count >= 2:
                        self._in_pinch = True

                    # Long hold → enter drag mode (mouseDown)
                    if self._pinch_raw_count >= 6 and not self._pinch_held:
                        self._in_pinch     = False
                        self._pinch_held   = True
                        self._mousedown_at = time.time()
                        self._drag_moved   = 0.0
                        self._drag_x0  = mx
                        self._drag_y0  = my
                        self._drag_cx0 = self._cur_x
                        self._drag_cy0 = self._cur_y
                        if pyautogui:
                            pyautogui.mouseDown()
                else:
                    if self._in_pinch and not self._pinch_held:
                        # Quick pinch released → atomic click at the frozen position
                        self._in_pinch        = False
                        self._pinch_raw_count = 0
                        if pyautogui:
                            pyautogui.click(sx, sy)
                    elif self._pinch_held:
                        # Drag release with 4-frame grace (prevents drop on noise)
                        self._pinch_breaks += 1
                        if self._pinch_breaks >= 4:
                            self._pinch_held   = False
                            self._pinch_breaks = 0
                            self._drag_moved   = 0.0
                            if pyautogui:
                                pyautogui.mouseUp()
                    else:
                        self._pinch_raw_count = 0

                # Peace held = right click at current cursor position
                if (gesture == "peace"
                        and self._hold_count >= _HOLD_FRAMES["peace"]
                        and not self._pinch_held
                        and self._can_trigger("peace_rclick")):
                    self._last_trigger["peace_rclick"] = time.time()
                    if pyautogui and hand_visible:
                        pyautogui.rightClick(sx, sy)
                    print("[G.I.L. GESTURE] Right click")

                # Fist held = exit cursor mode (release drag first if active)
                if gesture == "fist" and self._hold_count >= _HOLD_FRAMES["fist"]:
                    if self._pinch_held:
                        self._pinch_held   = False
                        self._pinch_breaks = 0
                        self._drag_moved   = 0.0
                        if pyautogui:
                            pyautogui.mouseUp()
                    self._in_pinch        = False
                    self._pinch_raw_count = 0
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
                    self._cur_x           = _remap(raw_x, _X_LO, _X_HI)
                    self._cur_y           = _remap(raw_y, _Y_LO, _Y_HI)
                    self._pinch_raw_count = 0
                    self._in_pinch        = False
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

        elif gesture == "rock_on":
            self._toggle_dnd()

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

    def _toggle_dnd(self):
        try:
            from modes import get_current_mode, set_mode
            new_mode = "normal" if get_current_mode() == "dnd" else "dnd"
            msg = set_mode(new_mode)
            self._say(msg)
        except Exception as exc:
            print("[G.I.L. GESTURE] DND toggle error: " + str(exc))

    # ---- Two-hand combo actions -----------------------------------------------

    def _execute_combo(self, combo):
        print("[G.I.L. GESTURE] Combo: " + str(combo))
        key = combo[0] + "/" + combo[1]
        if key == "fist/fist":
            self._lock_screen()
        elif key == "thumbs_up/thumbs_up":
            self._max_volume()
        elif key == "open_hand/open_hand":
            self._show_desktop()

    def _lock_screen(self):
        try:
            import subprocess
            subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])
            self._say("Locking screen.")
        except Exception as exc:
            print("[G.I.L. GESTURE] Lock screen error: " + str(exc))

    def _max_volume(self):
        try:
            from pc_control import set_system_volume
            result = set_system_volume(100)
            if "error" in result.lower() or "failed" in result.lower() or "install" in result.lower():
                print("[G.I.L. GESTURE] Max volume error: " + result)
                self._say("Volume control failed.")
            else:
                self._say("Volume maxed.")
        except Exception as exc:
            print("[G.I.L. GESTURE] Max volume error: " + str(exc))

    def _show_desktop(self):
        try:
            import pyautogui
            pyautogui.hotkey("win", "d")
            self._say("Showing desktop.")
        except Exception as exc:
            print("[G.I.L. GESTURE] Show desktop error: " + str(exc))

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
