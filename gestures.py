"""
gestures.py -- Project G.I.L.
Real-time hand gesture action bridge.

Reads gesture_config.json for bindings (hot-reloads every 5 s).
Writes result text to RESULT_FILE so camera_viewer.py can display it.

Supported action types in gesture_config.json:
  builtin  -- one of: volume_up, volume_down, screenshot, mute_toggle,
              next_track, prev_track, dnd_toggle, announce
  open_app -- target = app name (opened via Windows shell)
  open_url -- target = full URL (opened in default browser)

Two-hand combos (always active, not configurable):
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

GESTURE_FILE        = Path(tempfile.gettempdir()) / "gil_gesture_state.json"
RESULT_FILE         = Path(tempfile.gettempdir()) / "gil_gesture_result.json"
_GESTURE_CONFIG_FILE = Path(__file__).parent / "data" / "gesture_config.json"

# ── Default bindings (fallback if config missing) ─────────────────────────────
_DEFAULT_CONFIG = {
    "thumbs_up":   {"type": "builtin", "action": "volume_up",   "label": "Vol Up"},
    "thumbs_down": {"type": "builtin", "action": "volume_down", "label": "Vol Down"},
    "peace":       {"type": "builtin", "action": "screenshot",  "label": "Screenshot"},
    "fist":        {"type": "builtin", "action": "mute_toggle", "label": "Mute"},
    "open_hand":   {"type": "builtin", "action": "announce",    "label": "Announce"},
    "rock_on":     {"type": "builtin", "action": "dnd_toggle",  "label": "DND Mode"},
    "three_up":    {"type": "builtin", "action": "next_track",  "label": "Next Track"},
    "call_me":     {"type": "builtin", "action": "prev_track",  "label": "Prev Track"},
}

_config      = dict(_DEFAULT_CONFIG)
_config_lock = threading.Lock()
_config_mtime = 0.0


def _load_config():
    global _config, _config_mtime
    try:
        mtime = _GESTURE_CONFIG_FILE.stat().st_mtime
        if mtime <= _config_mtime:
            return
        data = json.loads(_GESTURE_CONFIG_FILE.read_text(encoding="utf-8"))
        merged = dict(_DEFAULT_CONFIG)
        merged.update(data.get("gestures", {}))
        with _config_lock:
            _config      = merged
            _config_mtime = mtime
        print("[G.I.L. GESTURE] Config reloaded.")
    except Exception:
        pass


_load_config()

# ── Hold frames required before gesture fires (at ~30 fps) ────────────────────
_HOLD_FRAMES: dict = {
    "index_point":  3,
    "pinch":        2,
    "peace":       15,
    "thumbs_up":   15,
    "thumbs_down": 15,
    "fist":        15,
    "open_hand":   18,
    "three_up":    15,
    "call_me":     15,
    "rock_on":     20,
}

_COMBO_HOLD     = 10
_HYSTERESIS     = 3
_X_LO, _X_HI   = 0.12, 0.88
_Y_LO, _Y_HI   = 0.04, 0.82

def _remap(v, lo, hi):
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))

_COOLDOWN: dict = {
    "index_point":  0.0,
    "pinch":        0.75,
    "peace":        3.0,
    "thumbs_up":    1.2,
    "thumbs_down":  1.2,
    "fist":         2.0,
    "open_hand":    3.0,
    "three_up":     1.2,
    "call_me":      1.2,
    "rock_on":      4.0,
}

_COMBO_COOLDOWN = 1.5


class GestureWatcher:
    def __init__(self, speak_fn=None):
        self._speak    = speak_fn
        self._running  = False
        self._thread   = None

        self._hold_gesture = None
        self._hold_count   = 0
        self._break_count  = 0
        self._last_trigger = {}
        self._last_ts      = 0.0
        self._last_config_reload = 0.0

        self._mode         = "command"
        self._cur_x        = 0.5
        self._cur_y        = 0.5
        self._pinch_held   = False
        self._pinch_breaks = 0

        self._drag_x0  = 0.5
        self._drag_y0  = 0.5
        self._drag_cx0 = 0.5
        self._drag_cy0 = 0.5
        self._drag_moved    = 0.0
        self._mousedown_at  = 0.0
        self._pinch_raw_count = 0
        self._in_pinch      = False

        self._prev_raw_x = 0.5
        self._prev_raw_y = 0.5

        self._combo_gesture   = None
        self._combo_count     = 0
        self._combo_cooldown  = {}

        self._muted = False

        user32         = ctypes.windll.user32
        self._screen_w = user32.GetSystemMetrics(0)
        self._screen_h = user32.GetSystemMetrics(1)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

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
        self._running         = False
        self._mode            = "command"
        self._in_pinch        = False
        self._pinch_raw_count = 0
        self._combo_gesture   = None
        self._combo_count     = 0
        if self._pinch_held:
            try:
                import pyautogui
                pyautogui.mouseUp()
            except Exception:
                pass
            self._pinch_held = False
        print("[G.I.L. GESTURE] Watcher stopped.")

    # ── Polling loop ──────────────────────────────────────────────────────────

    def _loop(self):
        try:
            import pyautogui
            pyautogui.FAILSAFE = False
            pyautogui.PAUSE    = 0.0
        except ImportError:
            pyautogui = None

        while self._running:
            time.sleep(0.033)

            # Hot-reload config every 5 s
            now_t = time.time()
            if now_t - self._last_config_reload > 5.0:
                _load_config()
                self._last_config_reload = now_t

            state = self._read_state()
            if state is None:
                continue

            ts           = state.get("ts", 0.0)
            gesture      = state.get("gesture")
            gesture2     = state.get("gesture2")
            raw_x        = float(state.get("x", 0.5))
            raw_y        = float(state.get("y", 0.5))
            hand_visible = bool(state.get("hand", gesture is not None))

            if ts <= self._last_ts:
                continue
            self._last_ts = ts

            # Hold counter with hysteresis
            if gesture == self._hold_gesture:
                self._break_count  = 0
                self._hold_count  += 1
            else:
                self._break_count += 1
                if self._break_count >= _HYSTERESIS:
                    self._hold_gesture = gesture
                    self._hold_count   = 0
                    self._break_count  = 0

            # Two-hand combos
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

            # Cursor mode
            if self._mode == "cursor":
                mx = _remap(raw_x, _X_LO, _X_HI)
                my = _remap(raw_y, _Y_LO, _Y_HI)

                _vel = ((raw_x - self._prev_raw_x)**2 + (raw_y - self._prev_raw_y)**2) ** 0.5
                self._prev_raw_x = raw_x
                self._prev_raw_y = raw_y

                if hand_visible:
                    if self._pinch_held:
                        dx = mx - self._drag_x0
                        dy = my - self._drag_y0
                        target_x = self._drag_cx0 + dx
                        target_y = self._drag_cy0 + dy
                        self._cur_x = 0.35 * target_x + 0.65 * self._cur_x
                        self._cur_y = 0.35 * target_y + 0.65 * self._cur_y
                        _dm = (dx**2 + dy**2) ** 0.5
                        self._drag_moved = max(self._drag_moved, _dm)
                    elif self._in_pinch or gesture == "pinch":
                        pass
                    else:
                        _DEAD = 0.007
                        if _vel > _DEAD:
                            _ema = min(0.72, 0.18 + _vel * 10.0)
                            self._cur_x = _ema * mx + (1 - _ema) * self._cur_x
                            self._cur_y = _ema * my + (1 - _ema) * self._cur_y
                    sx = max(0, min(self._screen_w - 1, int(self._cur_x * self._screen_w)))
                    sy = max(0, min(self._screen_h - 1, int(self._cur_y * self._screen_h)))
                    if pyautogui:
                        pyautogui.moveTo(sx, sy)

                if gesture == "pinch":
                    self._pinch_raw_count += 1
                    self._pinch_breaks     = 0
                    if self._pinch_raw_count >= 2:
                        self._in_pinch = True
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
                        self._in_pinch        = False
                        self._pinch_raw_count = 0
                        if pyautogui:
                            pyautogui.click(sx, sy)
                    elif self._pinch_held:
                        self._pinch_breaks += 1
                        if self._pinch_breaks >= 4:
                            self._pinch_held   = False
                            self._pinch_breaks = 0
                            self._drag_moved   = 0.0
                            if pyautogui:
                                pyautogui.mouseUp()
                    else:
                        self._pinch_raw_count = 0

                if (gesture == "peace"
                        and self._hold_count >= _HOLD_FRAMES["peace"]
                        and not self._pinch_held
                        and self._can_trigger("peace_rclick")):
                    self._last_trigger["peace_rclick"] = time.time()
                    if pyautogui and hand_visible:
                        pyautogui.rightClick(sx, sy)
                    print("[G.I.L. GESTURE] Right click")

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
                    self._write_result("Cursor: OFF")
                    print("[G.I.L. GESTURE] Exited cursor mode.")
                continue

            # Command mode
            if gesture == "index_point":
                if self._hold_count >= _HOLD_FRAMES["index_point"]:
                    self._mode  = "cursor"
                    self._cur_x           = _remap(raw_x, _X_LO, _X_HI)
                    self._cur_y           = _remap(raw_y, _Y_LO, _Y_HI)
                    self._pinch_raw_count = 0
                    self._in_pinch        = False
                    self._say("Cursor on.")
                    self._write_result("Cursor: ON")
                    print("[G.I.L. GESTURE] Entered cursor mode.")
                continue

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

    # ── Action dispatch ───────────────────────────────────────────────────────

    def _execute(self, gesture):
        print("[G.I.L. GESTURE] Action: " + gesture)
        with _config_lock:
            cfg = dict(_config.get(gesture, _DEFAULT_CONFIG.get(gesture, {})))

        action_type = cfg.get("type", "builtin")
        action      = cfg.get("action", "")
        target      = cfg.get("target", "")
        label       = cfg.get("label", gesture)

        if action_type == "open_url" and target:
            self._open_url(target, label)
        elif action_type == "open_app" and target:
            self._open_app(target, label)
        else:
            self._execute_builtin(action)

    def _execute_builtin(self, action):
        try:
            import pyautogui
        except ImportError:
            pyautogui = None

        if action == "volume_up":
            self._adjust_volume(+10)
        elif action == "volume_down":
            self._adjust_volume(-10)
        elif action == "screenshot":
            self._screenshot(pyautogui)
        elif action == "mute_toggle":
            self._muted = not self._muted
            if pyautogui:
                pyautogui.press("volumemute")
            msg = "Muted." if self._muted else "Unmuted."
            self._say(msg)
            self._write_result(msg)
        elif action == "next_track":
            if pyautogui:
                pyautogui.press("nexttrack")
            self._write_result("Next track")
        elif action == "prev_track":
            if pyautogui:
                pyautogui.press("prevtrack")
            self._write_result("Prev track")
        elif action == "announce":
            self._say("Gesture mode active.")
            self._write_result("Mode: Active")
        elif action == "dnd_toggle":
            self._toggle_dnd()

    def _open_url(self, url, label=""):
        import webbrowser
        webbrowser.open(url)
        msg = f"Opening {label or url[:30]}"
        self._say(msg)
        self._write_result(msg)

    def _open_app(self, app_name, label=""):
        import subprocess as _sp
        try:
            _sp.Popen(
                ["cmd", "/c", "start", "", app_name],
                creationflags=_sp.CREATE_NO_WINDOW,
            )
            msg = f"Opening {label or app_name}"
            self._say(msg)
            self._write_result(msg)
        except Exception as exc:
            print(f"[G.I.L. GESTURE] Open app failed: {exc}")

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
            self._write_result("Screenshot saved")
        except Exception as exc:
            print("[G.I.L. GESTURE] Screenshot failed: " + str(exc))

    def _adjust_volume(self, delta):
        try:
            from pc_control import get_system_volume, set_system_volume
            new_vol = max(0, min(100, get_system_volume() + delta))
            set_system_volume(new_vol)
            msg = f"Volume {new_vol}%"
            self._say(msg)
            self._write_result(msg)
        except Exception as exc:
            print("[G.I.L. GESTURE] Volume error: " + str(exc))

    def _toggle_dnd(self):
        try:
            from modes import get_current_mode, set_mode
            new_mode = "normal" if get_current_mode() == "dnd" else "dnd"
            msg = set_mode(new_mode)
            self._say(msg)
            self._write_result(f"Mode: {new_mode.upper()}")
        except Exception as exc:
            print("[G.I.L. GESTURE] DND toggle error: " + str(exc))

    # ── Two-hand combos ───────────────────────────────────────────────────────

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
            self._write_result("Screen locked")
        except Exception as exc:
            print("[G.I.L. GESTURE] Lock screen error: " + str(exc))

    def _max_volume(self):
        try:
            from pc_control import set_system_volume
            set_system_volume(100)
            self._say("Volume maxed.")
            self._write_result("Volume: 100%")
        except Exception as exc:
            print("[G.I.L. GESTURE] Max volume error: " + str(exc))

    def _show_desktop(self):
        try:
            import pyautogui
            pyautogui.hotkey("win", "d")
            self._say("Showing desktop.")
            self._write_result("Show desktop")
        except Exception as exc:
            print("[G.I.L. GESTURE] Show desktop error: " + str(exc))

    def _say(self, text):
        if self._speak:
            try:
                self._speak(text)
            except Exception:
                pass

    def _write_result(self, text):
        try:
            tmp = RESULT_FILE.with_name("gil_gesture_result_tmp.json")
            tmp.write_text(
                json.dumps({"text": text, "ts": time.time()}),
                encoding="utf-8",
            )
            tmp.replace(RESULT_FILE)
        except Exception:
            pass

    def _read_state(self):
        try:
            return json.loads(GESTURE_FILE.read_bytes())
        except Exception:
            return None
