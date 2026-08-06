"""
spatial_reader.py — one camera thread serving every spatial mode.

Publishes, per hand: where it is pointing, what it is doing, and — when asked —
the hand's own pixels cut out of the frame, ready to composite. Kept in one
place so the overlay and the workspace can't drift apart, and so the webcam is
only ever opened once.
"""

from __future__ import annotations

import math
import threading
import time
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

import spatial_hands as sh

MODEL = Path(__file__).parent / "data" / "hand_landmarker.task"

WRIST, THUMB, INDEX, MIDDLE = 0, 4, 8, 12
PIPS = {8: 6, 12: 10, 16: 14, 20: 18}

# The camera sees more than you can comfortably reach, so a central box maps to
# the whole screen — otherwise the display's edges are unreachable.
X_LO, X_HI = 0.20, 0.80
Y_LO, Y_HI = 0.18, 0.76


def _remap(v, lo, hi):
    return min(1.0, max(0.0, (v - lo) / (hi - lo)))


def _d(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


class Hand:
    """One hand's published state."""
    __slots__ = ("x", "y", "gesture", "rgba", "origin", "span", "label",
                 "pts", "zs")

    def __init__(self):
        self.x = self.y = 0.5
        self.gesture = "none"
        self.rgba = None          # raw camera cut-out (only if cutouts=True)
        self.origin = (0, 0)      # top-left of rgba in camera-frame pixels
        self.span = 1.0
        self.label = ""
        self.pts = None           # (21,2) landmark pixels — drives the render
        self.zs = None            # (21,) relative depth, for shading + sorting


class SpatialReader(threading.Thread):
    daemon = True

    def __init__(self, cutouts: bool = True, calib=None):
        super().__init__(name="SpatialReader")
        self.cutouts = cutouts
        self.calib = calib if calib is not None else sh.HandCalibration.load()
        self.lock = threading.Lock()
        self.hands: list[Hand] = []
        self.frame_size = (640, 480)
        self.err = ""
        self.fps = 0.0
        self._stop = threading.Event()
        self._scan_samples = []
        self._scanning = False
        self._prev_g = {}         # handedness → last gesture, for hysteresis

    # ── calibration ("scan my hands") ─────────────────────────────────────────
    def begin_scan(self):
        with self.lock:
            self._scan_samples = []
            self._scanning = True

    def scan_progress(self) -> int:
        return len(self._scan_samples)

    def finish_scan(self):
        with self.lock:
            self._scanning = False
            samples = list(self._scan_samples)
        if not samples:
            return None
        calib = sh.HandCalibration.from_samples(np.vstack(samples))
        calib.save()
        self.calib = calib
        return calib

    def stop(self):
        self._stop.set()

    # ── gesture ───────────────────────────────────────────────────────────────
    @staticmethod
    def classify(lm, prev: str = "none") -> str:
        w = lm[WRIST]
        ext = {t: _d(lm[t], w) > _d(lm[p], w) * 1.05 for t, p in PIPS.items()}
        n = sum(ext.values())
        hand = max(_d(lm[WRIST], lm[MIDDLE]), 1e-6)
        pinch = _d(lm[THUMB], lm[INDEX]) / hand

        # Pinch used to require the MIDDLE finger extended, but pinching
        # naturally curls the middle finger along with the index — so a real
        # pinch failed this test, fell through to `n == 0`, and was reported as
        # a FIST. Pinching to open a panel grabbed it instead. What actually
        # separates the two is whether the hand is balled at all, so ask that
        # instead: any finger still out means this is a pinch, not a fist.
        #
        # The threshold is also hysteretic. A single cutoff sat right where a
        # held pinch jitters, so the gesture flickered pinch→none→pinch many
        # times a second and never held long enough to register.
        if pinch < (0.52 if prev == "pinch" else 0.40) and n >= 1:
            return "pinch"
        if n == 0:
            return "fist"
        if n >= 3:
            return "open"
        if ext[8] and not ext[12]:
            return "point"
        return "none"

    def run(self):
        try:
            opts = vision.HandLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=str(MODEL)),
                running_mode=vision.RunningMode.VIDEO, num_hands=2,
                min_hand_detection_confidence=0.5,
                min_hand_presence_confidence=0.5,
                min_tracking_confidence=0.5)
            landmarker = vision.HandLandmarker.create_from_options(opts)
        except Exception as exc:
            self.err = f"hand tracker unavailable: {exc}"
            return

        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            self.err = "camera unavailable — another app may be using it"
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        t0 = time.time()
        last = t0
        try:
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.02)
                    continue
                h, w = frame.shape[:2]
                self.frame_size = (w, h)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                try:
                    res = landmarker.detect_for_video(
                        mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb),
                        int((time.time() - t0) * 1000))
                except Exception:
                    continue

                out = []
                for i, lm in enumerate(res.hand_landmarks or []):
                    hd = Hand()
                    try:
                        hd.label = res.handedness[i][0].category_name
                    except Exception:
                        hd.label = ""
                    # hysteresis is per hand, keyed on handedness rather than
                    # list position — the detector reorders the list freely
                    hd.gesture = self.classify(lm, self._prev_g.get(hd.label, "none"))
                    self._prev_g[hd.label] = hd.gesture
                    cx = (lm[THUMB].x + lm[INDEX].x) / 2
                    cy = (lm[THUMB].y + lm[INDEX].y) / 2
                    # mirrored so moving right on screen means moving right
                    hd.x = 1.0 - _remap(cx, X_LO, X_HI)
                    hd.y = _remap(cy, Y_LO, Y_HI)
                    pts = sh._px(lm, w, h)
                    hd.pts = pts
                    hd.zs = np.array([p.z for p in lm], dtype=np.float32)
                    hd.span = sh.hand_span(pts)

                    if self._scanning and len(self._scan_samples) < 40:
                        s = sh.scan_samples(frame, pts)
                        if len(s):
                            self._scan_samples.append(s)

                    if self.cutouts:
                        rgba, origin = sh.cut_out(frame, pts, self.calib)
                        hd.rgba, hd.origin = rgba, origin
                    out.append(hd)

                with self.lock:
                    self.hands = out
                now = time.time()
                self.fps = 0.85 * self.fps + 0.15 / max(now - last, 1e-3)
                last = now
        finally:
            cap.release()
            try:
                landmarker.close()
            except Exception:
                pass

    def snapshot(self) -> list[Hand]:
        with self.lock:
            return list(self.hands)
