"""
spatial_hands.py — your real hands, cut out of the camera and composited into
the workspace.

Not a drawn skeleton and not a rigged 3D model: these are the actual pixels of
your hand, masked out of the webcam frame and drawn over the scene. Because it
is the real image, it rotates, tilts and foreshortens exactly as your hand does
— the thing a stick figure can never get right.

How the mask is built (MediaPipe gives landmarks, not a segmentation):
  1. a palm polygon through the knuckle and wrist points
  2. tapered capsules along every finger bone
  3. optional skin-tone gate, learned when you scan your hand, which trims the
     sleeve and background that a purely geometric mask leaves behind
  4. a feathered edge so it composites without a hard cut-out line
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import numpy as np

CALIB_FILE = Path(__file__).parent / "data" / "hand_calibration.json"

# landmark indices
WRIST = 0
THUMB_CHAIN = (1, 2, 3, 4)
INDEX_CHAIN = (5, 6, 7, 8)
MIDDLE_CHAIN = (9, 10, 11, 12)
RING_CHAIN = (13, 14, 15, 16)
PINKY_CHAIN = (17, 18, 19, 20)
CHAINS = (THUMB_CHAIN, INDEX_CHAIN, MIDDLE_CHAIN, RING_CHAIN, PINKY_CHAIN)
PALM = (0, 1, 5, 9, 13, 17)

# Finger thickness as a fraction of hand span, tapering toward the tip.
# Tuned by eye against saved cut-outs: the first pass was half again this size
# and the palm blobs spilled past the hand, dragging the background in with them.
# Slightly TIGHT is right — the feather softens the edge, and a mask that leaks
# looks far worse than one that clips a millimetre of skin.
_BASE_T = 0.078
_TIP_T = 0.040
_PALM_R = 0.19          # knuckle-centre disc
_WRIST_R = 0.13         # heel of the hand


class HandCalibration:
    """What GIL remembers about your hands after a scan."""

    def __init__(self, skin_lo=None, skin_hi=None, span=None):
        self.skin_lo = skin_lo          # HSV lower bound
        self.skin_hi = skin_hi          # HSV upper bound
        self.span = span                # wrist→middle-tip, normalised

    @property
    def has_skin(self) -> bool:
        return self.skin_lo is not None and self.skin_hi is not None

    def save(self):
        try:
            CALIB_FILE.parent.mkdir(parents=True, exist_ok=True)
            CALIB_FILE.write_text(json.dumps({
                "skin_lo": self.skin_lo, "skin_hi": self.skin_hi,
                "span": self.span}), encoding="utf-8")
        except Exception:
            pass

    @classmethod
    def load(cls) -> "HandCalibration":
        try:
            d = json.loads(CALIB_FILE.read_text(encoding="utf-8"))
            return cls(d.get("skin_lo"), d.get("skin_hi"), d.get("span"))
        except Exception:
            return cls()

    @classmethod
    def from_samples(cls, samples) -> "HandCalibration":
        """Learn skin tone from HSV pixels sampled inside the hand mask.

        Percentiles rather than min/max: a few stray dark or blown-out pixels
        would otherwise widen the range until it matches the whole room."""
        if not len(samples):
            return cls()
        arr = np.asarray(samples, dtype=np.float32)
        lo = np.percentile(arr, 4, axis=0)
        hi = np.percentile(arr, 96, axis=0)
        # hue is the discriminating channel; be generous on saturation/value so
        # shadow and highlight on the same hand still register as skin
        lo = [max(0, float(lo[0]) - 8), max(10, float(lo[1]) - 55),
              max(25, float(lo[2]) - 70)]
        hi = [min(179, float(hi[0]) + 8), min(255, float(hi[1]) + 55),
              min(255, float(hi[2]) + 70)]
        return cls(lo, hi)


def _px(lm, w, h):
    return np.array([[p.x * w, p.y * h] for p in lm], dtype=np.float32)


def hand_span(pts) -> float:
    return float(np.linalg.norm(pts[12] - pts[0])) or 1.0


def build_mask(pts, shape, span=None) -> np.ndarray:
    """A filled silhouette of the hand from its landmarks."""
    h, w = shape[:2]
    mask = np.zeros((h, w), np.uint8)
    span = span or hand_span(pts)

    # palm: the landmark hull sits inside the flesh, so grow it a little — but
    # by dilating the hull itself rather than stamping big discs, which used to
    # bulge well past the silhouette
    palm = pts[list(PALM)].astype(np.int32)
    cv2.fillConvexPoly(mask, cv2.convexHull(palm), 255)
    grow = max(1, int(span * 0.085))
    mask = cv2.dilate(mask, np.ones((grow | 1,) * 2, np.uint8))
    cv2.circle(mask, tuple(pts[9].astype(int)), int(span * _PALM_R), 255, -1)
    cv2.circle(mask, tuple(pts[0].astype(int)), int(span * _WRIST_R), 255, -1)

    # fingers: tapered capsules along each bone
    for chain in CHAINS:
        idx = (WRIST,) + chain if chain is THUMB_CHAIN else chain
        for a, b in zip(idx, idx[1:]):
            t0 = np.interp(idx.index(a), [0, len(idx) - 1], [_BASE_T, _TIP_T])
            t1 = np.interp(idx.index(b), [0, len(idx) - 1], [_BASE_T, _TIP_T])
            pa, pb = pts[a].astype(int), pts[b].astype(int)
            cv2.line(mask, tuple(pa), tuple(pb),
                     255, max(2, int(span * (t0 + t1))))
            cv2.circle(mask, tuple(pb), max(2, int(span * t1)), 255, -1)
    return mask


def refine_with_skin(mask, frame_bgr, calib: HandCalibration) -> np.ndarray:
    """Trim sleeve and background using the skin tone learned at scan time."""
    if not calib.has_skin:
        return mask
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    skin = cv2.inRange(hsv, np.array(calib.skin_lo, np.uint8),
                       np.array(calib.skin_hi, np.uint8))
    skin = cv2.morphologyEx(skin, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    combined = cv2.bitwise_and(mask, skin)
    # never let the skin gate delete the hand outright — if it kills too much
    # (bad light, gloves, unusual colour), fall back to pure geometry
    if cv2.countNonZero(combined) < cv2.countNonZero(mask) * 0.35:
        return mask
    return combined


def cut_out(frame_bgr, pts, calib: HandCalibration | None = None,
            feather: int = 9):
    """Return (rgba, (x, y)) — the hand's pixels with a soft alpha edge.

    rgba is cropped to the hand's bounding box so only a small image has to be
    pushed to the UI each frame."""
    span = hand_span(pts)
    mask = build_mask(pts, frame_bgr.shape, span)
    if calib is not None:
        mask = refine_with_skin(mask, frame_bgr, calib)

    # smooth the silhouette, then feather so it blends instead of cutting
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            np.ones((int(span * 0.10) | 1,) * 2, np.uint8))
    mask = cv2.GaussianBlur(mask, (feather | 1, feather | 1), 0)

    ys, xs = np.nonzero(mask > 8)
    if not len(xs):
        return None, (0, 0)
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    pad = int(span * 0.08)
    h, w = mask.shape
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(w, x1 + pad), min(h, y1 + pad)

    crop = frame_bgr[y0:y1, x0:x1]
    a = mask[y0:y1, x0:x1]
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    rgba = np.dstack([rgb, a])
    return rgba, (x0, y0)


def scan_samples(frame_bgr, pts) -> np.ndarray:
    """HSV pixels well inside the hand — the training data for a scan."""
    span = hand_span(pts)
    mask = build_mask(pts, frame_bgr.shape, span)
    # Erode hard so only confidently-interior pixels train the skin model. The
    # first attempt sampled near the edge, swept in wall and poster, and learned
    # a hue range of 0-179 — i.e. "everything is skin", which gates nothing.
    mask = cv2.erode(mask, np.ones((max(3, int(span * 0.22)) | 1,) * 2, np.uint8))
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    return hsv[mask > 128]
