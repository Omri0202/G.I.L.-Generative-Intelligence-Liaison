"""
spatial_handrender.py — anatomical hands, posed by your real tracking.

The pose is entirely yours: 21 tracked landmarks per hand, so every finger bends
and the whole hand rotates as it does in life. The SURFACE is generated, which
avoids the background bleed, sensor noise and mismatched lighting that make a
webcam cut-out look pasted onto the scene.

Why a height field rather than drawn shapes:
An earlier version stamped shaded capsules per bone. It read as a hand but the
segments met in visible bands — a balloon-animal look — because each piece was
drawn independently with no shared surface. Here the hand is defined implicitly:
every bone contributes a rounded profile, the profiles are combined with a SMOOTH
maximum so they fuse into one organic form, and the resulting height field is lit
per pixel from its own gradient. Knuckles and webbing then emerge from the
geometry instead of being faked, and the whole surface shades continuously.

Everything is vectorised numpy over the hand's bounding box only, so a full
render costs a few milliseconds.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image

WRIST = 0
CHAINS = ((1, 2, 3, 4), (5, 6, 7, 8), (9, 10, 11, 12),
          (13, 14, 15, 16), (17, 18, 19, 20))
PALM_RING = (0, 1, 5, 9, 13, 17)

DEFAULT_SKIN = (233, 190, 164)

# light rig: one key from upper-left-front, a cool fill, and a rim for silhouette
KEY_DIR = np.array([-0.52, -0.72, 0.46], dtype=np.float32)
KEY_DIR /= np.linalg.norm(KEY_DIR)
FILL_DIR = np.array([0.62, 0.28, 0.72], dtype=np.float32)
FILL_DIR /= np.linalg.norm(FILL_DIR)
FILL_TINT = np.array([0.55, 0.66, 0.85], dtype=np.float32)   # cool bounce
SSS_TINT = np.array([1.00, 0.42, 0.34], dtype=np.float32)    # warm through-light


def skin_from_calibration(calib) -> tuple:
    """Turn a scanned HSV range into a plausible RGB skin tone."""
    try:
        if not calib or not calib.has_skin:
            return DEFAULT_SKIN
        import cv2
        h = (calib.skin_lo[0] + calib.skin_hi[0]) / 2
        s = (calib.skin_lo[1] + calib.skin_hi[1]) / 2
        v = (calib.skin_lo[2] + calib.skin_hi[2]) / 2
        rgb = cv2.cvtColor(np.uint8([[[h, min(255, s * 1.05),
                                       min(255, v * 1.12)]]]),
                           cv2.COLOR_HSV2RGB)[0][0]
        r, g, b = (int(x) for x in rgb)
        if r < 90 or r < b:          # a noisy scan can land somewhere non-skin
            return DEFAULT_SKIN
        return (r, g, b)
    except Exception:
        return DEFAULT_SKIN


def _seg_distance(px, py, ax, ay, bx, by):
    """Perpendicular distance from every pixel to segment a→b, plus how far
    along the segment the closest point sits (for tapering)."""
    vx, vy = bx - ax, by - ay
    L2 = vx * vx + vy * vy
    if L2 < 1e-6:
        return np.hypot(px - ax, py - ay), np.zeros_like(px)
    t = ((px - ax) * vx + (py - ay) * vy) / L2
    t = np.clip(t, 0.0, 1.0)
    cx, cy = ax + t * vx, ay + t * vy
    return np.hypot(px - cx, py - cy), t


def _poly_field(px, py, poly):
    """SIGNED distance to a simple polygon: negative inside, positive outside.

    Signed, not clamped at zero: a field that is flat zero inside gives the palm
    a plateau top and a vertical cliff at the rim, which shades as a slab of
    plasticine. The inside distance is what lets the edge roll over.

    The palm used to be a union of fat capsules radiating from the wrist to each
    knuckle. That is not the shape of a palm: the capsule around the wrist point
    threw a round heel well BELOW the wrist, and the unions between spokes swelled
    wider than any hand, so the whole thing read as a mitten with fingers stuck
    on. A palm is a tapered quad, so describe it as one and give it a thickness.

    Done with two distance transforms rather than a Python loop over the edges.
    Looping cost about 20ms for an eleven-sided palm — most of a frame — because
    every edge walked the whole palm bounding box in numpy. Rasterise the polygon
    once and let OpenCV do both transforms in C.
    """
    import cv2
    h, w = px.shape
    pts = np.round(np.asarray(poly, dtype=np.float32)
                   - (float(px[0, 0]), float(py[0, 0]))).astype(np.int32)
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [pts], 1)
    # PRECISE, not the 3x3 approximation: the approximation advances distance in
    # discrete steps, and the surface normals are the GRADIENT of this field, so
    # every step became a visible band running down the palm.
    mode = 3
    d_out = cv2.distanceTransform(1 - mask, cv2.DIST_L2, mode)
    if not mask.any():                    # degenerate palm: everything outside
        return d_out.astype(np.float32)
    d_in = cv2.distanceTransform(mask, cv2.DIST_L2, mode)
    return (d_out - d_in).astype(np.float32)


def _expand_by(poly, dist):
    """Push a polygon outward by an absolute distance.

    Absolute, not a scale factor: the polygon is the palm's MIDSURFACE, and the
    flesh has to reach `dist` past it. Scaling by a ratio moves small palms less
    than large ones, and under-expanding visibly narrows the hand, because the
    rolled profile now falls to nothing exactly at the boundary."""
    c = np.mean(poly, axis=0)
    out = []
    for p in poly:
        v = np.asarray(p, dtype=np.float32) - c
        n = float(np.linalg.norm(v)) or 1.0
        out.append(tuple(c + v * ((n + dist) / n)))
    return out


#: p-norm exponent for fusing parts. Higher = sharper joins, lower = puffier.
#: Eight rather than seven so both directions are reachable by squaring and
#: square roots: x**8 is three multiplies and x**(1/8) is three sqrts, where a
#: seventh power needs a general pow() on every pixel of the field.
_UNION_P = 8.0
#: palm half-thickness and knuckle bump, both as a fraction of the hand span.
#: the knuckle must exceed the palm or it cannot show through as a ridge, but
#: only just — a large margin turns the knuckles into loose balls
_PALM_T, _KNUCKLE_T = 0.085, 0.074
#: how far past the midsurface the palm flesh reaches, in units of its thickness
_PALM_SWELL = 1.15


def _union(acc, prof):
    """Accumulate one part into a smooth union (a p-norm of the profiles).

    NOT the usual polynomial smooth-max: that adds k/4 wherever two fields are
    equal, INCLUDING the empty space where both are zero. With ~30 parts the
    background floated up past the alpha threshold and the hand rendered as a
    filled rectangle. A p-norm is exact when one side is zero, so empty space
    stays empty, and still swells pleasingly where parts overlap."""
    return acc + np.power(prof, _UNION_P)


class HandRenderer:
    """Renders one hand as a lit implicit surface."""

    #: Internal render width; the result is scaled to wherever it's drawn.
    #: Cost is quadratic in this, and the hand lands on screen around 225px, so
    #: rendering much above that buys nothing — the surface carries no detail
    #: finer than its own shading.
    RES = 156

    def __init__(self, skin=DEFAULT_SKIN):
        self.skin = np.array(skin, dtype=np.float32) / 255.0

    def _bones(self, q, span, depth):
        """(ax, ay, bx, by, ra, rb) for every bone, radii tapered and
        depth-scaled so a hand reaching forward genuinely thickens."""
        out = []
        for chain in CHAINS:
            idx = (WRIST,) + chain if chain[0] == 1 else chain
            n = len(idx) - 1
            for k, (a, b) in enumerate(zip(idx, idx[1:])):
                t0, t1 = k / n, (k + 1) / n
                # A finger is ~19mm across on a ~176mm hand, so its radius is
                # near 0.055 of the span. The old 0.088 drew fingers 30mm wide,
                # which is most of why the hand read as inflated. Real fingers
                # also barely taper — the old -0.040 pinched them to sausages.
                base = 0.070 if chain[0] == 1 else 0.057
                ra = span * (base - 0.013 * t0) * depth[a]
                rb = span * (base - 0.013 * t1) * depth[b]
                out.append((q[a][0], q[a][1], q[b][0], q[b][1],
                            max(1.2, ra), max(1.0, rb)))
        return out

    def render(self, pts_xy, zs, frame_w=None, frame_h=None, pad=0.16):
        """pts_xy: (21,2) pixels. zs: (21,) MediaPipe depth (negative = nearer).

        Returns (RGBA image, (offset_x, offset_y), (width, height)) — the last
        two both in the INPUT pixel space.

        The extent matters: the image itself is always rendered at a fixed
        internal resolution, so its pixel size says nothing about how large the
        hand actually is. Callers that scaled by the image size drew a hand of
        constant size that never responded to reach."""
        p = np.asarray(pts_xy, dtype=np.float32)
        span = float(np.linalg.norm(p[12] - p[0])) or 1.0
        margin = span * pad
        lo = p.min(0) - margin
        hi = p.max(0) + margin
        w_px, h_px = float(hi[0] - lo[0]), float(hi[1] - lo[1])
        if w_px < 4 or h_px < 4:
            return (Image.new("RGBA", (8, 8), (0, 0, 0, 0)),
                    (float(lo[0]), float(lo[1])), (w_px, h_px))

        # work at a fixed internal resolution so cost is stable
        res_w = self.RES
        res_h = max(8, int(self.RES * h_px / w_px))
        sx, sy = res_w / w_px, res_h / h_px
        q = (p - lo) * (sx, sy)
        span_r = span * (sx + sy) * 0.5

        z = np.asarray(zs, dtype=np.float32)
        spread = float(np.ptp(z))
        # MediaPipe z is negative toward the camera → invert to 0 far .. 1 near
        zn = (1.0 - (z - z.min()) / spread) if spread > 1e-6 else np.zeros_like(z)
        depth = 0.84 + 0.34 * zn

        yy, xx = np.mgrid[0:res_h, 0:res_w].astype(np.float32)
        acc = np.zeros((res_h, res_w), np.float32)

        def add(ax, ay, bx, by, ra, rb, flat=1.0):
            """Accumulate one capsule, evaluated ONLY inside its own bounds.

            Each part covers a few percent of the frame, so computing every
            primitive across the whole image wasted ~95% of the work and pinned
            the render at 6fps."""
            rmax = max(ra, rb)
            x0 = max(0, int(min(ax, bx) - rmax) - 1)
            x1 = min(res_w, int(max(ax, bx) + rmax) + 2)
            y0 = max(0, int(min(ay, by) - rmax) - 1)
            y1 = min(res_h, int(max(ay, by) + rmax) + 2)
            if x1 <= x0 or y1 <= y0:
                return
            sx_, sy_ = xx[y0:y1, x0:x1], yy[y0:y1, x0:x1]
            d, t = _seg_distance(sx_, sy_, ax, ay, bx, by)
            r = ra + (rb - ra) * t
            # `flat` squashes the profile in Z without shrinking its footprint —
            # a palm is broad but not as deep as it is wide, and a purely
            # circular cross-section made it balloon like a mitten.
            prof = np.sqrt(np.clip(r * r - d * d, 0, None)) * flat
            prof *= prof
            prof *= prof
            acc[y0:y1, x0:x1] += prof * prof          # prof**8 by squaring

        import cv2
        # ── palm: one tapered slab following the real hand outline ────────────
        # The wrist is a single landmark, but a palm does not converge to a
        # point — it meets the wrist across a heel roughly three quarters as
        # wide as the knuckles. Using the bare landmark drew a cone.
        w_axis = q[17] - q[5]
        kw = float(np.linalg.norm(w_axis)) or 1.0
        u_w = w_axis / kw
        hw = kw * 0.38
        # ...and it is rounded, not cut square. Two bare corner points gave the
        # heel sharp right angles no hand has.
        axis = q[0] - q[9]
        axis = axis / (float(np.linalg.norm(axis)) or 1.0)
        heel_arc = [tuple(q[0] + u_w * (hw * t)
                          + axis * (hw * 0.40 * math.sqrt(max(0.0, 1 - t * t))))
                    for t in (-1.0, -0.62, 0.0, 0.62, 1.0)]
        palm_t = span_r * _PALM_T * depth[9]
        palm_poly = _expand_by(
            [heel_arc[0], tuple(q[1]), tuple(q[5]), tuple(q[9]),
             tuple(q[13]), tuple(q[17])] + heel_arc[:0:-1],
            palm_t * _PALM_SWELL)
        pts_arr = np.asarray(palm_poly, np.float32)
        x0 = max(0, int(pts_arr[:, 0].min() - palm_t) - 1)
        x1 = min(res_w, int(pts_arr[:, 0].max() + palm_t) + 2)
        y0 = max(0, int(pts_arr[:, 1].min() - palm_t) - 1)
        y1 = min(res_h, int(pts_arr[:, 1].max() + palm_t) + 2)
        if x1 > x0 and y1 > y0:
            sdf = _poly_field(xx[y0:y1, x0:x1], yy[y0:y1, x0:x1], palm_poly)
            # The polygon is rasterised to whole pixels, so its distance field
            # climbs in stair steps along the edges. Height barely varies on the
            # plateau, but it varies across the rim — and since normals are the
            # GRADIENT of the height, every step showed up as a rib running down
            # both sides of the palm. A sub-pixel blur of the FIELD removes them
            # without touching the fingers, which are analytic and already smooth.
            sdf = cv2.GaussianBlur(sdf, (0, 0), 1.15)
            # Roll the surface over on a circular ease. The reach is wide enough
            # that the palm domes across its whole width instead of sitting flat
            # with a rounded lip, which read as a slab.
            roll = max(1.0, palm_t * 2.6)
            u = np.clip(-sdf / roll, 0.0, 1.0)
            prof = palm_t * np.sqrt(np.clip(u * (2.0 - u), 0.0, 1.0))
            prof *= prof
            prof *= prof
            acc[y0:y1, x0:x1] += prof * prof          # prof**8 by squaring

        # knuckles: small domes at the metacarpal heads, so the back of the hand
        # has the ridge it should instead of shading as one smooth pillow
        for j in (5, 9, 13, 17):
            kr = span_r * _KNUCKLE_T * depth[j]
            add(q[j][0], q[j][1], q[j][0], q[j][1], kr, kr, flat=0.86)

        # thenar: the muscle mass at the base of the thumb. It is what actually
        # joins the thumb to the hand — without it the thumb hangs off the
        # silhouette as a separate sausage.
        add(q[0][0], q[0][1], q[2][0], q[2][1],
            span_r * 0.100 * depth[0], span_r * 0.072 * depth[2], flat=0.82)

        # ── fingers ───────────────────────────────────────────────────────────
        for ax, ay, bx, by, ra, rb in self._bones(q, span_r, depth):
            add(ax, ay, bx, by, ra, rb)

        # the eighth root, as three square roots
        height = np.sqrt(np.sqrt(np.sqrt(np.maximum(acc, 0.0))))
        if not (height > 0.35).any():
            return (Image.new("RGBA", (8, 8), (0, 0, 0, 0)),
                    (float(lo[0]), float(lo[1])), (w_px, h_px))

        self.last_height = height          # kept for diagnostics
        # ── normals from the height field ─────────────────────────────────────
        # A light blur first: the profile has a hard rim at the silhouette, and
        # differentiating that raw produces a chrome-like edge.
        # ~1px of smoothing: enough to kill the stair-stepping on the silhouette,
        # not enough to erase the knuckles.
        hs = cv2.GaussianBlur(height, (0, 0), 1.1)
        gy, gx = np.gradient(hs)
        # nz is in the SAME units as the gradient (height per pixel), so it must
        # be near 1. Setting it to a fraction of the hand span made it ~75
        # against gradients of ~1, so every normal pointed at the camera and the
        # hand rendered perfectly flat.
        n = np.stack([-gx, -gy, np.ones_like(hs)], axis=-1)
        n /= np.linalg.norm(n, axis=-1, keepdims=True) + 1e-6

        # ── shading ───────────────────────────────────────────────────────────
        # Every exponent below is reached by multiplying and square-rooting
        # rather than by `**`. A fractional power calls pow() once per pixel,
        # and six of them across a 200x230 field cost more than the whole rest
        # of the render. The exponents shifted by at most a tenth, which is not
        # visible; the frame time halved, which is.
        fill = np.clip(n @ FILL_DIR, 0, 1)
        # wrap-around term: skin keeps catching light past the terminator
        w = np.clip((n @ KEY_DIR + 0.42) / 1.42, 0, 1)
        wrap = w * np.sqrt(w)                             # w**1.5

        # half-vector specular, broad and weak — skin is not plastic
        view = np.array([0, 0, 1], np.float32)
        hv = KEY_DIR + view
        hv /= np.linalg.norm(hv)
        s = np.clip(n @ hv, 0, 1)
        s = s * s
        s = s * s
        s = s * s
        spec = s * s * 0.11                               # s**16, matte not plastic

        # thin parts glow warm, standing in for light passing through flesh
        t_ = np.clip(1.0 - hs / (span_r * 0.55), 0, 1)
        thin = t_ * t_
        # contact darkening in the creases between fingers
        ao = np.sqrt(np.clip(hs / (span_r * 0.42), 0, 1))

        base = self.skin[None, None, :]
        col = base * (0.20 + 0.92 * wrap[..., None]) * (0.55 + 0.45 * ao[..., None])
        col += base * FILL_TINT[None, None, :] * fill[..., None] * 0.24
        col += base * SSS_TINT[None, None, :] * thin[..., None] * 0.26
        col += spec[..., None]
        # rim light picks the hand off a dark background
        r_ = np.clip(1.0 - n[..., 2], 0, 1)
        rim = r_ * r_ * np.sqrt(r_)                       # r**2.5
        col += rim[..., None] * np.array([0.34, 0.46, 0.60], np.float32) * 0.55

        col = np.clip(col, 0, 1)
        rgb = (col * 255).astype(np.uint8)

        # Antialias the silhouette from the height field. The ramp is ~1.5px:
        # scaling it to the hand span made the edge dozens of pixels wide and
        # the hand looked like a soft mitten.
        a = np.clip((height - 0.30) / 1.5, 0, 1)
        a = cv2.GaussianBlur(a.astype(np.float32), (0, 0), 0.7)
        alpha = (np.clip(a, 0, 1) * 255).astype(np.uint8)

        img = Image.fromarray(np.dstack([rgb, alpha]), "RGBA")
        return img, (float(lo[0]), float(lo[1])), (w_px, h_px)
