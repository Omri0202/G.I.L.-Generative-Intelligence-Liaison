"""
spatial_handrender.py — VR-style hands, drawn from your real tracking.

The pose is entirely yours: 21 tracked landmarks per hand, so every finger bends
and the whole hand rotates exactly as it does in life. What's synthetic is the
SURFACE — clean shaded geometry rather than webcam pixels, which avoids the
background bleed, sensor noise and mismatched lighting that make a raw cut-out
look pasted on.

The 3D read comes from four things, none of which needs a 3D engine:
  · depth sorting        — bones nearer the camera drawn last, using MediaPipe z
  · thickness by depth   — a hand reaching forward genuinely swells
  · a lit gradient       — one key light upper-left, so tubes read as round
  · rim light + contact shade — separates fingers from palm and from each other
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

# landmark chains
WRIST = 0
CHAINS = ((1, 2, 3, 4), (5, 6, 7, 8), (9, 10, 11, 12),
          (13, 14, 15, 16), (17, 18, 19, 20))
PALM_RING = (0, 1, 5, 9, 13, 17)

SS = 2                       # supersample factor; downsampled for clean edges

# default skin, replaced by whatever the scan measured
DEFAULT_SKIN = (232, 188, 160)
KEY = (1.0, -1.0)            # key light direction, upper-left


def _shade(base, amount):
    """amount -1 (shadow) .. +1 (highlight)."""
    if amount >= 0:
        return tuple(int(c + (255 - c) * amount * 0.85) for c in base)
    return tuple(int(c * (1.0 + amount * 0.62)) for c in base)


def skin_from_calibration(calib) -> tuple:
    """Turn the scanned HSV range into an RGB skin tone."""
    try:
        if not calib or not calib.has_skin:
            return DEFAULT_SKIN
        h = (calib.skin_lo[0] + calib.skin_hi[0]) / 2
        s = (calib.skin_lo[1] + calib.skin_hi[1]) / 2
        v = (calib.skin_lo[2] + calib.skin_hi[2]) / 2
        hsv = np.uint8([[[h, min(255, s * 1.05), min(255, v * 1.12)]]])
        import cv2
        rgb = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)[0][0]
        # keep it in plausible skin territory even if the scan was noisy
        r, g, b = (int(x) for x in rgb)
        if r < 90 or r < b:
            return DEFAULT_SKIN
        return (r, g, b)
    except Exception:
        return DEFAULT_SKIN


class HandRenderer:
    """Draws one hand as a shaded 3D-looking mesh of capsules."""

    def __init__(self, skin=DEFAULT_SKIN):
        self.skin = skin

    # ── geometry helpers ─────────────────────────────────────────────────────
    @staticmethod
    def _capsule(draw, a, b, ra, rb, fill):
        """A tapered tube between two joints — the building block of a finger."""
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        ln = math.hypot(dx, dy) or 1e-6
        nx, ny = -dy / ln, dx / ln
        quad = [(ax + nx * ra, ay + ny * ra), (bx + nx * rb, by + ny * rb),
                (bx - nx * rb, by - ny * rb), (ax - nx * ra, ay - ny * ra)]
        draw.polygon(quad, fill=fill)
        draw.ellipse([ax - ra, ay - ra, ax + ra, ay + ra], fill=fill)
        draw.ellipse([bx - rb, by - rb, bx + rb, by + rb], fill=fill)

    def _lit_capsule(self, draw, a, b, ra, rb, depth):
        """Capsule with a light ramp across it, so it reads as round."""
        base = _shade(self.skin, depth * 0.30)
        # body
        self._capsule(draw, a, b, ra, rb, _shade(base, -0.16))
        # lit core, offset toward the key light
        ox, oy = KEY[0] * ra * 0.26, KEY[1] * ra * 0.26
        self._capsule(draw, (a[0] + ox, a[1] + oy), (b[0] + ox, b[1] + oy),
                      ra * 0.70, rb * 0.70, base)
        # specular sliver
        self._capsule(draw, (a[0] + ox * 1.7, a[1] + oy * 1.7),
                      (b[0] + ox * 1.7, b[1] + oy * 1.7),
                      ra * 0.34, rb * 0.34, _shade(base, 0.26))

    # ── main ─────────────────────────────────────────────────────────────────
    def render(self, pts_xy, zs, out_w, out_h, pad=26):
        """pts_xy: (21,2) in any pixel space. zs: (21,) relative depth.

        Returns (RGBA image, (offset_x, offset_y)) positioned in that space."""
        p = np.asarray(pts_xy, dtype=np.float32)
        x0, y0 = p.min(0) - pad
        x1, y1 = p.max(0) + pad
        w, h = max(8, int(x1 - x0)), max(8, int(y1 - y0))
        img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)

        q = (p - (x0, y0)) * SS
        span = float(np.linalg.norm(q[12] - q[0])) or 1.0

        z = np.asarray(zs, dtype=np.float32)
        spread = float(np.ptp(z))       # np.ptp, not z.ptp() — removed in NumPy 2
        if spread > 1e-6:
            # MediaPipe z is negative toward the camera, so invert: 0 far .. 1 near
            zn = 1.0 - (z - z.min()) / spread
        else:
            zn = np.zeros_like(z)
        # nearer = thicker; this is what sells reaching toward the screen
        depth_scale = 0.82 + 0.36 * zn

        # ── palm ──
        palm = [tuple(q[i]) for i in PALM_RING]
        cx, cy = np.mean([pt[0] for pt in palm]), np.mean([pt[1] for pt in palm])
        grown = [(cx + (px - cx) * 1.22, cy + (py - cy) * 1.22) for px, py in palm]
        base = _shade(self.skin, float(zn[9]) * 0.30)
        d.polygon(grown, fill=_shade(base, -0.14))
        # soft highlight across the back of the hand
        d.polygon([(cx + (px - cx) * 0.78 + KEY[0] * span * 0.05,
                    cy + (py - cy) * 0.78 + KEY[1] * span * 0.05)
                   for px, py in palm], fill=base)

        # ── fingers, far to near ──
        # Shadow is drawn once per FINGER, not per bone. Outlining every segment
        # separately laid a dark band across each knuckle, which made the hand
        # read as a chain of sausages instead of one continuous digit.
        fingers = []
        for chain in CHAINS:
            idx = (WRIST,) + chain if chain[0] == 1 else chain
            segs = []
            for k, (a, b) in enumerate(zip(idx, idx[1:])):
                t = k / max(len(idx) - 2, 1)
                ra = max(1.5, span * (0.108 - 0.045 * t) * depth_scale[a])
                rb = max(1.2, span * (0.108 - 0.045 * (t + 0.34)) * depth_scale[b])
                segs.append((a, b, ra, rb))
            depth = float(np.mean([zn[i] for i in idx]))
            fingers.append((depth, segs))
        fingers.sort(key=lambda t: t[0])         # painter's algorithm

        for depth, segs in fingers:
            for a, b, ra, rb in segs:            # one silhouette for the finger
                self._capsule(d, tuple(q[a]), tuple(q[b]), ra * 1.10, rb * 1.10,
                              _shade(self.skin, -0.52))
            for a, b, ra, rb in segs:            # then the lit surface
                self._lit_capsule(d, tuple(q[a]), tuple(q[b]), ra, rb, depth)

        # ── knuckles, so the hand doesn't look like plumbing ──
        for i in (5, 9, 13, 17):
            r = span * 0.085 * depth_scale[i]
            c = _shade(self.skin, 0.12 + float(zn[i]) * 0.2)
            d.ellipse([q[i][0] - r, q[i][1] - r, q[i][0] + r, q[i][1] + r], fill=c)

        # ── fingernails: small, but they read strongly as "a hand" ──
        for chain in CHAINS:
            tip, prev = chain[-1], chain[-2]
            v = q[tip] - q[prev]
            n = np.linalg.norm(v) or 1.0
            v = v / n
            r = span * 0.052 * depth_scale[tip]
            cxn, cyn = q[tip] - v * r * 0.35
            d.ellipse([cxn - r, cyn - r * 0.8, cxn + r, cyn + r * 0.8],
                      fill=_shade(self.skin, 0.34))

        img = img.filter(ImageFilter.SMOOTH)
        img = img.resize((w, h), Image.LANCZOS)
        return img, (float(x0), float(y0))
