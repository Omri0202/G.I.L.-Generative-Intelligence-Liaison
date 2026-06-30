"""
gil_icons.py — G.I.L.
Hand-drawn line icons rendered with PIL ImageDraw — no emoji, no color-glyph
font fallback. Thin stroke, transparent background, single color, matching
GIL's monochrome design language (same spirit as Claude's icon set).

Usage:
    from gil_icons import icon
    photo = icon("search", color="#3FDDFA", size=18)
    ctk.CTkButton(parent, image=photo, text="", ...)
"""
import math
from PIL import Image, ImageDraw
import customtkinter as ctk

_SS   = 4    # supersample factor — draw big, downscale for anti-aliasing
_GRID = 24   # logical viewBox, like an SVG 0..24 coordinate space
_cache: dict = {}


# ── Drawing primitives (logical 24x24 grid coordinates) ───────────────────────

def _canvas(ss_size: int):
    img = Image.new("RGBA", (ss_size, ss_size), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def _pt(p, ss_size):
    f = ss_size / _GRID
    return (p[0] * f, p[1] * f)


def _stroke(draw, pts, color, width, ss_size, closed=False):
    """Draw a polyline with rounded joints and rounded end caps."""
    scaled = [_pt(p, ss_size) for p in pts]
    if closed:
        scaled = scaled + [scaled[0]]
    draw.line(scaled, fill=color, width=round(width), joint="curve")
    r = width / 2
    for (x, y) in (scaled[0], scaled[-1]):
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color)


def _fill_poly(draw, pts, color, ss_size):
    draw.polygon([_pt(p, ss_size) for p in pts], fill=color)


def _circle(draw, cx, cy, r, color, width, ss_size, fill=False):
    f = ss_size / _GRID
    bbox = [(cx - r) * f, (cy - r) * f, (cx + r) * f, (cy + r) * f]
    if fill:
        draw.ellipse(bbox, fill=color)
    else:
        draw.ellipse(bbox, outline=color, width=round(width))


def _arc(draw, cx, cy, r, start, end, color, width, ss_size):
    f = ss_size / _GRID
    bbox = [(cx - r) * f, (cy - r) * f, (cx + r) * f, (cy + r) * f]
    draw.arc(bbox, start=start, end=end, fill=color, width=round(width))


# ── Icon definitions ──────────────────────────────────────────────────────────

def _i_search(d, c, w, ss):
    _circle(d, 10.3, 10.3, 6.3, c, w, ss)
    _stroke(d, [(15, 15), (21, 21)], c, w, ss)


def _i_export(d, c, w, ss):
    _stroke(d, [(12, 3), (12, 15)], c, w, ss)
    _stroke(d, [(7.5, 10.5), (12, 15), (16.5, 10.5)], c, w, ss)
    _stroke(d, [(4, 16.5), (4, 20), (20, 20), (20, 16.5)], c, w, ss)


def _star_pts():
    cx, cy, r_out, r_in = 12, 12.4, 9.2, 3.7
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        r = r_out if i % 2 == 0 else r_in
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return pts


def _i_star_outline(d, c, w, ss):
    _stroke(d, _star_pts(), c, w, ss, closed=True)


def _i_star_filled(d, c, w, ss):
    _fill_poly(d, _star_pts(), c, ss)


def _i_thumbs_up(d, c, w, ss):
    _stroke(d, [(4, 11), (7.2, 11), (7.2, 20), (4, 20)], c, w, ss, closed=True)
    _stroke(d, [(7.2, 11), (11.2, 4.3), (13, 5), (13.6, 6.8), (12.4, 11),
                (18, 11), (19.7, 12.6), (19, 15.8), (17.5, 20), (7.2, 20)],
            c, w * 0.95, ss)


def _i_thumbs_down(d, c, w, ss):
    flip = lambda p: (p[0], 24 - p[1])
    _stroke(d, [flip(p) for p in [(4, 11), (7.2, 11), (7.2, 20), (4, 20)]],
            c, w, ss, closed=True)
    _stroke(d, [flip(p) for p in [
        (7.2, 11), (11.2, 4.3), (13, 5), (13.6, 6.8), (12.4, 11),
        (18, 11), (19.7, 12.6), (19, 15.8), (17.5, 20), (7.2, 20)]],
        c, w * 0.95, ss)


def _i_edit(d, c, w, ss):
    _stroke(d, [(4, 20), (5, 16), (16.5, 4.5), (19.5, 7.5), (8, 19), (4, 20)],
            c, w, ss)
    _stroke(d, [(4, 20), (8, 19)], c, w, ss)


def _i_attach(d, c, w, ss):
    _stroke(d, [
        (16.2, 6.4), (10.1, 12.5), (8.3, 14.3), (7.8, 16.3), (8.8, 17.9),
        (10.7, 18.2), (12.2, 17.1), (12.8, 16.4), (18.4, 10.8),
        (19.7, 9.0), (19.3, 6.6), (17.3, 5.3), (15.1, 5.8), (13.7, 7.0),
        (6.7, 14.0), (5.5, 16.4), (6.3, 19.0), (8.7, 20.4), (11.3, 19.8),
        (13.0, 18.2),
    ], c, w * 0.92, ss)


def _i_copy(d, c, w, ss):
    _stroke(d, [(8.5, 8.5), (20, 8.5), (20, 20), (8.5, 20)], c, w, ss, closed=True)
    _stroke(d, [(4, 15.5), (4, 4), (15.5, 4)], c, w, ss)


def _i_regenerate(d, c, w, ss):
    _arc(d, 11, 11, 8, 130, 360, c, w, ss)
    _stroke(d, [(21, 7), (21, 11.5), (16.5, 11.5)], c, w, ss)
    _arc(d, 13, 13, 8, -50, 175, c, w, ss)
    _stroke(d, [(3, 17), (3, 12.5), (7.5, 12.5)], c, w, ss)


def _i_send(d, c, w, ss):
    _stroke(d, [(12, 19.5), (12, 5)], c, w, ss)
    _stroke(d, [(6, 11), (12, 5), (18, 11)], c, w, ss)


def _i_robot(d, c, w, ss):
    _stroke(d, [(4, 8.5), (20, 8.5), (20, 19.5), (4, 19.5)], c, w, ss, closed=True)
    _stroke(d, [(12, 8.5), (12, 4.5)], c, w, ss)
    _circle(d, 12, 3.6, 1.3, c, w, ss, fill=True)
    _circle(d, 8.7, 14, 1.6, c, w, ss, fill=True)
    _circle(d, 15.3, 14, 1.6, c, w, ss, fill=True)


def _i_activity(d, c, w, ss):
    _stroke(d, [(3, 13), (8, 13), (10, 6.5), (14, 19.5), (16, 13), (21, 13)],
            c, w, ss)


def _i_close(d, c, w, ss):
    _stroke(d, [(6, 6), (18, 18)], c, w, ss)
    _stroke(d, [(18, 6), (6, 18)], c, w, ss)


def _i_trash(d, c, w, ss):
    _stroke(d, [(5, 7), (19, 7)], c, w, ss)
    _stroke(d, [(9, 7), (9, 4.5), (15, 4.5), (15, 7)], c, w, ss)
    _stroke(d, [(7, 7), (8, 20), (16, 20), (17, 7)], c, w, ss)
    _stroke(d, [(10.3, 10.5), (10.3, 16.5)], c, w * 0.8, ss)
    _stroke(d, [(13.7, 10.5), (13.7, 16.5)], c, w * 0.8, ss)


def _i_sun(d, c, w, ss):
    _circle(d, 12, 12, 4, c, w, ss)
    for ang in range(0, 360, 45):
        rad = math.radians(ang)
        x1, y1 = 12 + 6.5 * math.cos(rad), 12 + 6.5 * math.sin(rad)
        x2, y2 = 12 + 9.5 * math.cos(rad), 12 + 9.5 * math.sin(rad)
        _stroke(d, [(x1, y1), (x2, y2)], c, w * 0.85, ss)


def _i_moon(d, c, w, ss):
    _stroke(d, [
        (15.5, 4.2), (13.4, 5.6), (12.3, 8.0), (12.6, 10.7),
        (14.2, 13.0), (16.7, 14.2), (19.4, 14.0), (21.5, 12.6),
        (20.6, 15.4), (18.6, 17.7), (15.8, 18.9), (12.7, 18.7),
        (9.9, 17.2), (8.0, 14.6), (7.4, 11.4), (8.1, 8.2),
        (10.0, 5.5), (12.7, 3.9), (15.5, 4.2),
    ], c, w, ss, closed=True)


_ICONS = {
    "search":       _i_search,
    "export":       _i_export,
    "star_outline": _i_star_outline,
    "star_filled":  _i_star_filled,
    "thumbs_up":    _i_thumbs_up,
    "thumbs_down":  _i_thumbs_down,
    "edit":         _i_edit,
    "attach":       _i_attach,
    "copy":         _i_copy,
    "regenerate":   _i_regenerate,
    "send":         _i_send,
    "robot":        _i_robot,
    "activity":     _i_activity,
    "close":        _i_close,
    "trash":        _i_trash,
    "sun":          _i_sun,
    "moon":         _i_moon,
}


def icon(name: str, color: str = "#3FDDFA", size: int = 18, stroke: float = 1.7):
    """
    Return a cached CTkImage line icon.
    name: one of the keys in _ICONS
    color: hex stroke color
    size: final pixel size (square)
    stroke: line width in logical (24-grid) units
    """
    key = (name, color, size, stroke)
    if key in _cache:
        return _cache[key]
    if name not in _ICONS:
        raise ValueError(f"Unknown icon: {name!r}. Available: {sorted(_ICONS)}")

    ss = size * _SS
    img, draw = _canvas(ss)
    w = max(1.0, stroke * _SS)
    _ICONS[name](draw, color, w, ss)
    img = img.resize((size, size), Image.LANCZOS)

    photo = ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
    _cache[key] = photo
    return photo


def available() -> list[str]:
    return sorted(_ICONS)
