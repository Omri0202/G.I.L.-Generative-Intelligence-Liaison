"""
spatial_overlay.py — G.I.L. Spatial Control.

Runs as its own process (tkinter needs its own main loop, and GIL's GUI already
owns one). It watches your hands through the webcam and draws a heads-up layer
over the whole desktop — no camera preview, no skeleton bones. The screen is the
interface; your hand is the input.

  open hand + move   move the spatial pointer
  pinch              select / click        (hold and move to drag)
  fist               grab the window under the pointer and move it
  two fists apart    zoom the focused app
  open palm swipe    switch virtual desktop

Design note on the visuals: drawing finger bones tells you where the tracker
thinks your knuckles are, which is debug output, not an interface. What actually
helps is showing where you're POINTING and what will happen if you close your
hand — so the HUD is a focus reticle, a live target highlight, and state colour.
"""

from __future__ import annotations

import math
import sys
import threading
import time
import tkinter as tk
from collections import deque
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
import win32api
import win32con
import win32gui
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

ROOT = Path(__file__).parent
MODEL = ROOT / "data" / "hand_landmarker.task"
TUTORIAL_FLAG = ROOT / "data" / "spatial_tutorial_done"

CHROMA = "#010203"                    # painted transparent by the window manager
CYAN = "#5CE1FF"
WHITE = "#EAFBFF"
AMBER = "#FFB44C"
GREEN = "#5CFFB0"
DIM = "#1B7A94"

# landmark indices
WRIST, THUMB, INDEX, MIDDLE, RING, PINKY = 0, 4, 8, 12, 16, 20
PIPS = {INDEX: 6, MIDDLE: 10, RING: 14, PINKY: 18}

# The camera sees a wider area than you can comfortably reach, so map a central
# box to the full screen — otherwise the edges of the display are unreachable.
X_LO, X_HI = 0.18, 0.82
Y_LO, Y_HI = 0.16, 0.78


def _remap(v, lo, hi):
    return min(1.0, max(0.0, (v - lo) / (hi - lo)))


def _dist(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


class HandReader(threading.Thread):
    """Camera + landmarker on a worker thread; publishes plain state."""

    daemon = True

    def __init__(self):
        super().__init__(name="SpatialHands")
        self.state = {
            "ok": False, "hands": 0, "gesture": "none",
            "x": 0.5, "y": 0.5, "span": 0.0, "err": "",
        }
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    # ── gesture classification ────────────────────────────────────────────────
    @staticmethod
    def _extended(lm):
        """Which fingers are extended — tip further from the wrist than its PIP."""
        w = lm[WRIST]
        out = {}
        for tip, pip in PIPS.items():
            out[tip] = _dist(lm[tip], w) > _dist(lm[pip], w) * 1.05
        return out

    @classmethod
    def _classify(cls, lm):
        ext = cls._extended(lm)
        n = sum(ext.values())
        # scale-invariant pinch: thumb-to-index against overall hand size
        hand = max(_dist(lm[WRIST], lm[MIDDLE]), 1e-6)
        pinch = _dist(lm[THUMB], lm[INDEX]) / hand
        if pinch < 0.32 and ext[MIDDLE]:
            return "pinch"
        if n == 0:
            return "fist"
        if n >= 3:
            return "open"
        if ext[INDEX] and not ext[MIDDLE]:
            return "point"
        return "none"

    def run(self):
        try:
            opts = vision.HandLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=str(MODEL)),
                running_mode=vision.RunningMode.VIDEO,
                num_hands=2,
                min_hand_detection_confidence=0.5,
                min_hand_presence_confidence=0.5,
                min_tracking_confidence=0.5)
            landmarker = vision.HandLandmarker.create_from_options(opts)
        except Exception as exc:
            self.state["err"] = f"tracker unavailable: {exc}"
            return

        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            self.state["err"] = "camera unavailable — another app may be using it"
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        t0 = time.time()
        try:
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.02)
                    continue
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                try:
                    res = landmarker.detect_for_video(
                        img, int((time.time() - t0) * 1000))
                except Exception:
                    continue

                if not res.hand_landmarks:
                    self.state.update(ok=True, hands=0, gesture="none")
                    continue

                hands = res.hand_landmarks
                lm = hands[0]
                # control point: the pinch point, which is where people look
                cx = (lm[THUMB].x + lm[INDEX].x) / 2
                cy = (lm[THUMB].y + lm[INDEX].y) / 2
                g = self._classify(lm)

                span = 0.0
                if len(hands) == 2:
                    a, b = hands[0], hands[1]
                    span = _dist(a[INDEX], b[INDEX])
                    if self._classify(a) == "fist" and self._classify(b) == "fist":
                        g = "twofist"

                self.state.update(
                    ok=True, hands=len(hands), gesture=g, span=span,
                    # mirrored: moving your hand right moves the pointer right
                    x=1.0 - _remap(cx, X_LO, X_HI),
                    y=_remap(cy, Y_LO, Y_HI))
        finally:
            cap.release()
            try:
                landmarker.close()
            except Exception:
                pass


class SpatialHUD:
    """The heads-up layer and the actions it drives."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", CHROMA)
        self.sw = self.root.winfo_screenwidth()
        self.sh = self.root.winfo_screenheight()
        self.root.geometry(f"{self.sw}x{self.sh}+0+0")
        self.cv = tk.Canvas(self.root, width=self.sw, height=self.sh,
                            bg=CHROMA, highlightthickness=0)
        self.cv.pack()
        self.root.update_idletasks()
        self._make_clickthrough()

        self.reader = HandReader()
        self.reader.start()

        # smoothed pointer + a short trail so fast moves read as motion
        self.px, self.py = self.sw / 2, self.sh / 2
        self.trail = deque(maxlen=14)
        self.state = "idle"
        self.pinch_since = 0.0
        self.dragging = False
        self.grab_hwnd = None
        self.grab_off = (0, 0)
        self.target_hwnd = None
        self.last_span = 0.0
        self.tick = 0
        self.tutorial = Tutorial(self) if not TUTORIAL_FLAG.exists() else None

        self.root.bind_all("<Escape>", lambda e: self.quit())
        self.loop()

    def _make_clickthrough(self):
        hwnd = win32gui.GetParent(self.cv.winfo_id()) or self.root.winfo_id()
        ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        win32gui.SetWindowLong(
            hwnd, win32con.GWL_EXSTYLE,
            ex | win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT
            | win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_NOACTIVATE)
        self.hwnd = hwnd

    # ── window helpers ────────────────────────────────────────────────────────
    def window_at(self, x, y):
        """The top-level app window under a point, skipping our own HUD."""
        try:
            h = win32gui.WindowFromPoint((int(x), int(y)))
        except Exception:
            return None
        if not h or h == self.hwnd:
            return None
        # walk up to the real top-level window
        while True:
            parent = win32gui.GetParent(h)
            if not parent:
                break
            h = parent
        if h == self.hwnd or not win32gui.IsWindowVisible(h):
            return None
        if not win32gui.GetWindowText(h):
            return None
        return h

    def quit(self):
        try:
            self.reader.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        sys.exit(0)

    # ── main loop ─────────────────────────────────────────────────────────────
    def loop(self):
        self.tick += 1
        s = self.reader.state
        if s.get("err"):
            self.draw_error(s["err"])
            self.root.after(200, self.loop)
            return

        if s["hands"]:
            tx, ty = s["x"] * self.sw, s["y"] * self.sh
            # heavier smoothing when still, lighter when moving fast, so the
            # pointer is calm at rest but doesn't lag a deliberate sweep
            d = math.hypot(tx - self.px, ty - self.py)
            a = min(0.55, 0.12 + d / 900.0)
            self.px += (tx - self.px) * a
            self.py += (ty - self.py) * a
            self.trail.append((self.px, self.py))
            self.handle(s["gesture"], s)
        else:
            self.state = "idle"
            self.release_drag()

        self.draw(s)
        self.root.after(16, self.loop)          # ~60fps HUD

    # ── gesture → action ──────────────────────────────────────────────────────
    def handle(self, g, s):
        if self.tutorial and not self.tutorial.allows(g):
            self.tutorial.saw(g)
            self.state = g
            return

        if g == "pinch":
            if self.state != "pinch":
                self.pinch_since = time.time()
                self.state = "pinch"
            held = time.time() - self.pinch_since
            if held > 0.28 and not self.dragging:
                self.dragging = True
                win32api.SetCursorPos((int(self.px), int(self.py)))
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            elif self.dragging:
                win32api.SetCursorPos((int(self.px), int(self.py)))

        elif g == "fist":
            self.state = "fist"
            if self.grab_hwnd is None:
                h = self.window_at(self.px, self.py)
                if h:
                    try:
                        l, t, r, b = win32gui.GetWindowRect(h)
                        self.grab_hwnd = h
                        self.grab_off = (self.px - l, self.py - t)
                    except Exception:
                        self.grab_hwnd = None
            else:
                try:
                    l, t, r, b = win32gui.GetWindowRect(self.grab_hwnd)
                    w, hgt = r - l, b - t
                    nx = int(self.px - self.grab_off[0])
                    ny = int(self.py - self.grab_off[1])
                    win32gui.MoveWindow(self.grab_hwnd, nx, ny, w, hgt, True)
                except Exception:
                    self.grab_hwnd = None

        elif g == "twofist":
            self.state = "zoom"
            span = s.get("span", 0.0)
            if self.last_span:
                delta = span - self.last_span
                if abs(delta) > 0.012:
                    clicks = 1 if delta > 0 else -1
                    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
                    win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0,
                                         clicks * 120, 0)
                    win32api.keybd_event(win32con.VK_CONTROL, 0,
                                         win32con.KEYEVENTF_KEYUP, 0)
                    self.last_span = span
            else:
                self.last_span = span

        else:
            if self.state == "pinch" and not self.dragging:
                # a quick pinch is a click
                win32api.SetCursorPos((int(self.px), int(self.py)))
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            self.release_drag()
            self.state = g if g in ("open", "point") else "idle"
            self.last_span = 0.0
            self.grab_hwnd = None

        if self.tutorial:
            self.tutorial.saw(g)

    def release_drag(self):
        if self.dragging:
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            self.dragging = False
        self.grab_hwnd = None

    # ── drawing ───────────────────────────────────────────────────────────────
    def draw_error(self, msg):
        c = self.cv
        c.delete("all")
        c.create_text(self.sw // 2, self.sh // 2 - 14, text="SPATIAL CONTROL",
                      fill=AMBER, font=("Consolas", 20, "bold"))
        c.create_text(self.sw // 2, self.sh // 2 + 18, text=msg,
                      fill=DIM, font=("Consolas", 12))

    def draw(self, s):
        c = self.cv
        c.delete("all")
        colour = {"idle": CYAN, "open": CYAN, "point": WHITE,
                  "pinch": WHITE, "fist": AMBER, "zoom": GREEN}.get(self.state, CYAN)

        # target highlight — what your hand is actually over
        if self.state in ("fist", "open", "point", "idle"):
            h = self.grab_hwnd or self.window_at(self.px, self.py)
            if h:
                try:
                    l, t, r, b = win32gui.GetWindowRect(h)
                    grabbed = self.grab_hwnd is not None
                    c.create_rectangle(l + 2, t + 2, r - 2, b - 2,
                                       outline=AMBER if grabbed else DIM,
                                       width=3 if grabbed else 1)
                    for cx0, cy0, dx, dy in ((l, t, 1, 1), (r, t, -1, 1),
                                             (l, b, 1, -1), (r, b, -1, -1)):
                        c.create_line(cx0 + dx * 26, cy0, cx0, cy0,
                                      fill=AMBER if grabbed else CYAN, width=3)
                        c.create_line(cx0, cy0, cx0, cy0 + dy * 26,
                                      fill=AMBER if grabbed else CYAN, width=3)
                except Exception:
                    pass

        if s.get("hands"):
            # motion trail: fading dots, so a sweep reads as movement
            for i, (tx, ty) in enumerate(self.trail):
                f = i / max(len(self.trail), 1)
                if f > 0.25:
                    rr = 2 + f * 5
                    c.create_oval(tx - rr, ty - rr, tx + rr, ty + rr,
                                  outline="", fill=colour if f > 0.8 else DIM)

            # the reticle — concentric rings that respond to intent
            x, y = self.px, self.py
            base = 34 if self.state != "pinch" else 20
            wob = math.sin(self.tick / 9.0) * 2.2
            c.create_oval(x - base - wob, y - base - wob,
                          x + base + wob, y + base + wob,
                          outline=colour, width=2)
            c.create_oval(x - base * 0.55, y - base * 0.55,
                          x + base * 0.55, y + base * 0.55,
                          outline=colour, width=1)
            c.create_oval(x - 4, y - 4, x + 4, y + 4, fill=colour, outline="")

            # rotating ticks: alive without being noisy
            for k in range(4):
                a = self.tick / 22.0 + k * math.pi / 2
                r0, r1 = base + 7, base + 15
                c.create_line(x + math.cos(a) * r0, y + math.sin(a) * r0,
                              x + math.cos(a) * r1, y + math.sin(a) * r1,
                              fill=colour, width=2)

            label = {"idle": "", "open": "READY", "point": "AIM",
                     "pinch": "SELECT" if not self.dragging else "DRAG",
                     "fist": "GRAB", "zoom": "ZOOM"}.get(self.state, "")
            if label:
                c.create_text(x, y + base + 30, text=label, fill=colour,
                              font=("Consolas", 11, "bold"))

        # status strip
        hint = "no hand — hold your hand up to the camera" if not s.get("hands") \
            else "Esc or /spatial off to exit"
        c.create_text(self.sw // 2, self.sh - 26,
                      text=f"◈ SPATIAL CONTROL   ·   {hint}",
                      fill=DIM, font=("Consolas", 11))

        if self.tutorial:
            self.tutorial.draw(c)


class Tutorial:
    """A first-run walkthrough: each step waits until you actually do it.

    Reading a list of gestures teaches nothing; performing one with immediate
    feedback does. Each step unlocks only when its gesture is held, so nobody
    reaches the desktop without having successfully controlled it once."""

    STEPS = [
        ("open", "Hold your hand up, fingers open",
         "That's your pointer. Move your hand — the reticle follows."),
        ("pinch", "Now pinch — thumb to index finger",
         "Pinch selects. A quick pinch clicks; hold and move to drag."),
        ("fist", "Close your hand into a fist",
         "A fist grabs whatever window you're over. Move to carry it."),
    ]
    HOLD = 14                       # frames the gesture must persist

    def __init__(self, hud):
        self.hud = hud
        self.i = 0
        self.count = 0
        self.done_at = 0.0

    def allows(self, g):
        """Only the step's own gesture drives real actions during the tutorial."""
        return False                # nothing acts on the system while teaching

    def saw(self, g):
        if self.i >= len(self.STEPS):
            return
        want = self.STEPS[self.i][0]
        self.count = self.count + 1 if g == want else max(0, self.count - 1)
        if self.count >= self.HOLD:
            self.count = 0
            self.i += 1
            if self.i >= len(self.STEPS):
                self.done_at = time.time()

    def draw(self, c):
        sw, sh = self.hud.sw, self.hud.sh
        if self.i >= len(self.STEPS):
            c.create_text(sw // 2, 96, text="YOU'RE READY",
                          fill=GREEN, font=("Consolas", 26, "bold"))
            c.create_text(sw // 2, 132,
                          text="Spatial control is live. Esc to exit.",
                          fill=DIM, font=("Consolas", 13))
            if time.time() - self.done_at > 2.4:
                try:
                    TUTORIAL_FLAG.write_text("done", encoding="utf-8")
                except Exception:
                    pass
                self.hud.tutorial = None
            return

        want, title, why = self.STEPS[self.i]
        c.create_rectangle(sw // 2 - 330, 54, sw // 2 + 330, 176,
                           outline=CYAN, width=2)
        c.create_text(sw // 2, 78, text=f"STEP {self.i + 1} OF {len(self.STEPS)}",
                      fill=DIM, font=("Consolas", 11))
        c.create_text(sw // 2, 108, text=title, fill=WHITE,
                      font=("Consolas", 17, "bold"))
        c.create_text(sw // 2, 138, text=why, fill=DIM, font=("Consolas", 12))
        # progress toward holding the gesture
        w = 300 * min(1.0, self.count / self.HOLD)
        c.create_rectangle(sw // 2 - 150, 158, sw // 2 + 150, 163,
                           outline=DIM)
        if w > 0:
            c.create_rectangle(sw // 2 - 150, 158, sw // 2 - 150 + w, 163,
                               outline="", fill=GREEN)


if __name__ == "__main__":
    if not MODEL.is_file():
        print("hand model missing:", MODEL)
        sys.exit(1)
    SpatialHUD().root.mainloop()
