"""
spatial_workspace.py — G.I.L. Spatial Workspace.

Your open applications lift off the desktop and become live, floating panels you
arrange with your hands. Reach to aim, close your fist to carry a panel, pinch
to drop into one. Escape returns everything as it was.

Every panel is a real, running window — a live DWM mirror, not a screenshot. You
are looking at the actual thing, still playing video, still blinking its cursor.

Design intent: this mode is a place, not a dialog. It gets a deep, quiet
backdrop, generous spacing, motion with real weight, and no chrome that isn't
doing work. Nothing here borrows GIL's chat styling — a spatial environment
should feel like somewhere you went, not a window you opened.
"""

from __future__ import annotations

import ctypes
import math
import random
import sys
import time
import tkinter as tk
from pathlib import Path

import win32api
import win32con
import win32gui

import spatial_mirror as sm

ROOT = Path(__file__).parent
TUTORIAL_FLAG = ROOT / "data" / "spatial_tutorial_done"

# ── palette ───────────────────────────────────────────────────────────────────
# Cool, deep, low-chroma so the real window content is the brightest thing on
# screen. The accent only appears on things you can act on.
BG_TOP = (9, 13, 24)
BG_BOT = (3, 5, 10)
ACCENT = "#7DDBFF"
ACCENT_SOFT = "#2C6B87"
GRAB = "#FFC46B"
TEXT = "#DCE9F2"
MUTED = "#4A6076"

# ── gesture timing ────────────────────────────────────────────────────────────
#: frames a pinch must hold before it acts. Two is enough to reject a twitch
#: while still feeling immediate; the loop runs at ~60fps, so this is ~33ms.
PINCH_HOLD = 2
#: non-pinch frames tolerated before the pinch is considered released. Tracking
#: drops the odd frame, and without this each drop restarted the whole gesture.
PINCH_GRACE = 5
#: frames a fist must hold before it picks a panel up, so the fist-like frame on
#: the way into a pinch doesn't snatch one.
FIST_HOLD = 2

# ── desktop shelf ─────────────────────────────────────────────────────────────
#: icon size as a fraction of its cell, at rest and fully magnified
SHELF_ICON, SHELF_GROW = 0.56, 0.62
#: how far the magnification reaches, in cells
SHELF_REACH = 1.7
#: smallest cell worth showing; below this the shelf wraps to another row
SHELF_MIN_CELL = 62.0
#: icon bitmaps are cached per quantised size — resizing every frame cannot hold
#: 60fps, and a continuous size would defeat the cache entirely
SHELF_QUANT = 6
#: icons are pulled once at this size and resized down locally, so the shelf
#: keeps one bitmap per item rather than one per item per size
SHELF_SRC_PX = 128

# ── commit hold ───────────────────────────────────────────────────────────────
# A pinch arms a target; holding it commits. Durations are scaled to the cost of
# the mistake — focusing an already-open window is cheap, launching an app is
# not — and both are long enough that brushing past something does nothing.
ARM_PANEL = 0.34
ARM_DESKTOP = 0.58

# ── hands ─────────────────────────────────────────────────────────────────────
#: on-screen hand size, as a multiple of its true camera-pixel extent mapped to
#: the screen. 1.0 would be "life size" for a camera that saw exactly what you
#: see; the camera is much wider than your reach, so that reads as enormous.
HAND_SCALE = 0.46


def _blend(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def _hex(rgb):
    return "#%02x%02x%02x" % rgb


class Panel:
    """One floating window, with physics so it moves like an object."""

    __slots__ = ("mirror", "x", "y", "s", "tx", "ty", "ts", "vx", "vy",
                 "home_x", "home_y", "hover", "grabbed", "seed")

    def __init__(self, mirror: sm.Mirror, x: float, y: float, s: float):
        self.mirror = mirror
        self.x = self.tx = self.home_x = x
        self.y = self.ty = self.home_y = y
        self.s = self.ts = s
        self.vx = self.vy = 0.0
        self.hover = 0.0            # 0..1, eased — drives lift and glow
        self.grabbed = False
        self.seed = random.random() * 6.283

    def step(self, dt: float, tick: int):
        # critically-damped-ish spring: arrives quickly, never oscillates
        k, d = 16.0, 2.0 * math.sqrt(16.0)
        ax = (self.tx - self.x) * k - self.vx * d
        ay = (self.ty - self.y) * k - self.vy * d
        self.vx += ax * dt
        self.vy += ay * dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.s += (self.ts - self.s) * min(1.0, dt * 9.0)

    def rect(self, base_w: float, tick: int):
        """Screen rectangle, including a slow idle drift so it feels afloat.

        Hover scales the panel up as well as lifting it: on a flat plane, size
        IS distance, so growing toward the viewer is what "coming forward"
        actually looks like. A lift alone just slides it up the screen."""
        drift = 0.0 if self.grabbed else math.sin(tick / 62.0 + self.seed) * 3.0
        w = base_w * self.s * (1.0 + 0.13 * self.hover)
        h = w / max(self.mirror.aspect, 0.35)
        lift = self.hover * 16.0
        return (self.x - w / 2, self.y - h / 2 + drift - lift,
                self.x + w / 2, self.y + h / 2 + drift - lift)


class DeskItem:
    """One thing on your real desktop, sitting on the shelf.

    Magnification is proximity-based rather than strictly on/off: the item under
    your hand grows most and its neighbours grow a little, which is what makes a
    dense shelf readable without a separate zoom mode. At this size the label
    would be unreadable, so only the focused item names itself.
    """

    __slots__ = ("name", "path", "kind", "x", "y", "mag", "hover")

    def __init__(self, name, path, kind, x=0.0, y=0.0):
        self.name = name
        self.path = path
        self.kind = kind
        self.x = x
        self.y = y
        self.mag = 0.0        # 0..1 proximity, eased
        self.hover = 0.0      # 0..1, only for the single focused item

    def size(self, cell: float) -> float:
        return cell * (SHELF_ICON + SHELF_GROW * self.mag)

    def rect(self, cell: float):
        s = self.size(cell)
        lift = self.mag * cell * 0.30      # rises out of the shelf as it grows
        return (self.x - s / 2, self.y - s / 2 - lift,
                self.x + s / 2, self.y + s / 2 - lift)


class Workspace:
    def __init__(self, use_hands: bool = True):
        self.root = tk.Tk()
        self.root.title("G.I.L. Spatial Workspace")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.sw = self.root.winfo_screenwidth()
        self.sh = self.root.winfo_screenheight()
        self.root.geometry(f"{self.sw}x{self.sh}+0+0")
        self.cv = tk.Canvas(self.root, width=self.sw, height=self.sh,
                            highlightthickness=0, bd=0, bg=_hex(BG_BOT))
        self.cv.pack()
        # A full update(), and the hwnd derived from the ROOT rather than the
        # canvas: DwmRegisterThumbnail needs a realised TOP-LEVEL window as its
        # destination. The canvas's parent is the inner tk window, which returns
        # E_INVALIDARG for every source — silently mirroring nothing.
        self.root.update()
        self.hwnd = win32gui.GetParent(self.root.winfo_id()) or self.root.winfo_id()

        self._backdrop()
        self.panels: list[Panel] = []
        # set before _layout, which returns early when nothing is open — without
        # this, every geometry call would raise on an empty desktop
        self.base_w = self.sw * 0.3
        # the shelf claims the bottom band first — panel layout sizes itself
        # around whatever is left
        self._collect_desktop()
        self._layout_desktop()
        self._collect()
        self._layout()

        self.px, self.py = self.sw / 2, self.sh / 2
        self.state = "idle"
        self.held: Panel | None = None
        self.hold_off = (0.0, 0.0)
        self.tick = 0
        self.last = time.time()
        self.closing = False
        self.status = ""
        self.status_until = 0.0
        self.pinch_frames = 0
        self.pinch_fired = False     # one action per pinch, not one per frame
        self.pinch_grace = 0         # dropped frames tolerated mid-pinch
        self.fist_frames = 0
        self._faults = 0             # frames lost to exceptions
        self.arm = None              # what a sustained pinch is about to open
        self.arm_t = 0.0             # 0..1 progress toward opening it
        self.awaiting = None         # (name, deadline) for a launching app

        # Hands are the point, but the mouse must work too: hand tracking can
        # fail (camera busy, bad light) and being stuck in a fullscreen mode you
        # can't drive is the exact trap we just fixed elsewhere.
        self.reader = None
        self.hand_imgs = {}          # keeps PhotoImages alive while displayed
        self._hand_cache = {}        # index -> (photo, anchor fx, anchor fy)
        self.scan_mode = False
        self.scale_ref = None        # two-hand distance when scaling began
        self.hand_renderer = None
        if use_hands:
            try:
                import spatial_handrender as hr
                import spatial_hands as sh
                from spatial_reader import SpatialReader
                calib = sh.HandCalibration.load()
                # the scan's job now: set the skin tone of the rendered hands
                self.hand_renderer = hr.HandRenderer(
                    hr.skin_from_calibration(calib))
                # cutouts off — we render geometry, so the camera pixels and the
                # per-frame masking they need are pure cost
                self.reader = SpatialReader(cutouts=False, calib=calib)
                self.reader.start()
            except Exception:
                self.reader = None
        self.cv.bind("<Motion>", self._mouse_move)
        self.cv.bind("<Button-1>", self._mouse_down)
        self.cv.bind("<ButtonRelease-1>", self._mouse_up)
        self.root.bind_all("<Escape>", lambda e: self.quit())
        self.root.bind_all("<Tab>", lambda e: self._toggle_shelf())
        self.root.focus_force()

        self.tutorial = Tutorial(self) if not TUTORIAL_FLAG.exists() else None
        self.loop()

    # ── setup ─────────────────────────────────────────────────────────────────
    def _backdrop(self):
        """A quiet deep-space gradient with a few stars, rendered once."""
        try:
            from PIL import (Image, ImageChops, ImageDraw, ImageFilter,
                             ImageTk)
        except Exception:
            self.bg_img = None
            return
        w, h = self.sw, self.sh
        img = Image.new("RGB", (w, h), _hex(BG_BOT))
        d = ImageDraw.Draw(img)
        for y in range(0, h, 2):
            t = y / max(h - 1, 1)
            # ease so most of the frame is dark and the glow sits up top
            d.rectangle([0, y, w, y + 2], fill=_blend(BG_TOP, BG_BOT, t ** 0.75))
        # ── nebula: several coloured blooms, blurred and added ─────────────────
        # Additive rather than blended. Blending toward a colour washes the
        # whole frame toward grey and flattens it; adding light leaves the dark
        # parts genuinely dark, which is what gives the depth.
        rnd = random.Random(7)
        neb = Image.new("RGB", (w // 4, h // 4), (0, 0, 0))
        nd = ImageDraw.Draw(neb)
        for cx, cy, rx, ry, col in (
                (0.50, -0.10, 0.46, 0.40, (26, 58, 92)),     # cool crown
                (0.16, 0.30, 0.30, 0.34, (40, 30, 76)),      # violet, left
                (0.86, 0.44, 0.26, 0.30, (14, 52, 70)),      # teal, right
                (0.50, 0.86, 0.62, 0.26, (10, 30, 52))):     # floor haze
            nd.ellipse([(cx - rx) * w / 4, (cy - ry) * h / 4,
                        (cx + rx) * w / 4, (cy + ry) * h / 4], fill=col)
        neb = neb.filter(ImageFilter.GaussianBlur(w // 34)) \
                 .resize((w, h), Image.BICUBIC)
        img = ImageChops.add(img, neb)

        # ── stars, in three depths ────────────────────────────────────────────
        # A single size and brightness reads as noise. Depth comes from most of
        # them being barely there, a few being crisp, and a handful blooming.
        stars = Image.new("RGB", (w, h), (0, 0, 0))
        sd = ImageDraw.Draw(stars)
        for _ in range(430):                                  # far, faint dust
            x, y = rnd.randrange(w), rnd.randrange(h)
            v = rnd.randint(16, 46)
            sd.point((x, y), fill=(v, v + 4, v + 10))
        for _ in range(130):                                  # mid
            x, y = rnd.randrange(w), rnd.randrange(h)
            v = rnd.randint(60, 120)
            sd.ellipse([x, y, x + 1, y + 1], fill=(v, v + 6, v + 14))
        img = ImageChops.add(img, stars)

        bright = Image.new("RGB", (w, h), (0, 0, 0))
        bd = ImageDraw.Draw(bright)
        for _ in range(26):                                   # near, blooming
            x, y = rnd.randrange(w), rnd.randrange(h)
            v = rnd.randint(120, 210)
            bd.ellipse([x - 1, y - 1, x + 2, y + 2], fill=(v, v + 6, v + 16))
        img = ImageChops.add(img, bright.filter(ImageFilter.GaussianBlur(2.2)))
        img = ImageChops.add(img, bright)

        # ── vignette ──────────────────────────────────────────────────────────
        # Darkening the corners pulls the eye to the middle, where the panels
        # are, and stops the gradient from banding into the screen edges.
        vig = Image.new("L", (w // 8, h // 8), 0)
        ImageDraw.Draw(vig).ellipse(
            [-w // 32, -h // 26, w // 8 + w // 32, h // 8 + h // 26], fill=255)
        vig = vig.filter(ImageFilter.GaussianBlur(w // 90)) \
                 .resize((w, h), Image.BICUBIC).point(lambda v: 70 + v * 0.72)
        img = ImageChops.multiply(img, Image.merge("RGB", (vig, vig, vig)))

        self.bg_img = ImageTk.PhotoImage(img)
        # A small copy kept for sampling. tkinter cannot draw translucently, so
        # every soft edge is faked by blending toward "the background" — and
        # blending toward one constant only works if the background IS constant.
        # Against a lit nebula it painted black frames around every panel.
        self.bg_pil = img.resize((max(1, w // 8), max(1, h // 8)), Image.BOX)

    def _collect(self):
        for h, title in sm.list_windows(exclude_hwnd=self.hwnd):
            m = sm.Mirror(self.hwnd, h, title)
            if m.handle:
                self.panels.append(Panel(m, self.sw / 2, self.sh / 2, 0.35))

    def _rescan(self) -> bool:
        """Adopt windows that appeared since the last look.

        Without this the workspace only ever showed what was running when it
        opened, so an app launched from the shelf started invisibly behind it
        and there was no way to reach the thing you just asked for.
        """
        known = {p.mirror.src for p in self.panels}
        added = False
        for h, title in sm.list_windows(exclude_hwnd=self.hwnd):
            if h in known:
                continue
            m = sm.Mirror(self.hwnd, h, title)
            if not m.handle:
                continue
            # arrive small and near the middle, then spring into the layout —
            # a new panel popping in at full size reads as a glitch
            p = Panel(m, self.sw / 2, self.sh * 0.42, 0.30)
            p.ts = 1.0
            self.panels.append(p)
            added = True
            if self.awaiting:
                self.say(f"{title[:34]} is here", 2.0)
                self.awaiting = None
        return added

    def _layout(self):
        """Lay panels on a gentle arc — depth suggested by scale, not perspective.

        DWM mirrors are axis-aligned rectangles, so real perspective is off the
        table. Scaling and a curved baseline read as depth honestly, instead of
        pretending to a 3D we can't render.

        Sizing fits the CELL in both axes. A width-only rule overflows the moment
        a tall window is on screen: a portrait panel is much taller than a
        landscape one at the same width, so rows ran off the bottom and the
        outer columns ran off the right."""
        n = len(self.panels)
        if not n:
            return
        cols = min(n, 4 if n > 6 else 3 if n > 2 else n)
        rows = math.ceil(n / cols)

        margin_x, top_bar = 0.05, 0.16
        # leave the shelf its band, plus a gap so a magnified icon never
        # collides with the lowest row of panels
        bottom_bar = max(0.12, getattr(self, "shelf_h", 0.0) / self.sh + 0.03)
        avail_w = self.sw * (1 - margin_x * 2)
        avail_h = self.sh * (1 - top_bar - bottom_bar)
        cell_w = avail_w / cols
        cell_h = avail_h / rows
        gap_x, gap_y = cell_w * 0.10, cell_h * 0.22   # room for titles + glow

        # the widest panel that still fits its cell for EVERY aspect present
        widest = 1.0
        for p in self.panels:
            a = max(p.mirror.aspect, 0.35)
            fit_w = min(cell_w - gap_x, (cell_h - gap_y) * a)
            widest = min(widest, fit_w) if widest != 1.0 else fit_w
        self.base_w = max(140.0, widest)

        top = self.sh * top_bar + cell_h / 2
        for i, p in enumerate(self.panels):
            r, c = divmod(i, cols)
            in_row = min(cols, n - r * cols)
            spread = (c - (in_row - 1) / 2)
            x = self.sw / 2 + spread * cell_w
            off = abs(spread) / max(in_row / 2, 1)
            y = top + r * cell_h + off * off * (cell_h * 0.05)
            p.tx, p.home_x = x, x
            p.ty, p.home_y = y, y
            p.ts = 1.0 - off * 0.07

    # ── the real desktop ──────────────────────────────────────────────────────
    def _collect_desktop(self):
        """Your actual desktop, as things you can reach.

        Mirrors only ever show what is ALREADY running, which left the workspace
        unable to open anything you hadn't started yet. The desktop window is
        itself mirrorable, but a mirror is one flat picture — you cannot hover a
        single icon inside it — so the items are enumerated individually and get
        their own hit-box, magnification and depth.
        """
        self.items = []
        self.item_photos = {}          # (path, px) -> PhotoImage
        self.item_src = {}             # path -> PIL image at SHELF_SRC_PX
        self._photo_order = []
        self.shelf_h = 0.0
        self.shelf_cell = 72.0
        self.shelf_visible = True
        self.sd = None
        try:
            import spatial_desktop as sd
            self.sd = sd
            for d in sd.list_desktop():
                self.items.append(DeskItem(d["name"], d["path"], d["kind"]))
        except Exception:
            self.items = []

    def _layout_desktop(self):
        """Fit every desktop item along the bottom, wrapping to more rows rather
        than shrinking past legibility. Seeing all of it is the point."""
        n = len(self.items)
        self.shelf_h = 0.0
        if not n:
            return
        avail = self.sw * 0.94
        rows = 1
        while rows < 4 and avail / math.ceil(n / rows) < SHELF_MIN_CELL:
            rows += 1
        per = math.ceil(n / rows)
        cell = min(avail / per, self.sh * 0.125)
        self.shelf_cell = cell
        self.shelf_rows = rows
        self.shelf_per = per
        # rows, plus headroom for a magnified icon and its label
        self.shelf_h = rows * cell * 1.02 + cell * 0.62

        bottom = self.sh - cell * 0.60
        for i, it in enumerate(self.items):
            r, c = divmod(i, per)
            in_row = min(per, n - r * per)
            row_w = in_row * cell
            it.x = (self.sw - row_w) / 2 + cell * (c + 0.5)
            it.y = bottom - (rows - 1 - r) * cell * 1.02
        self._bake_shelf()

    def _bake_shelf(self):
        """Composite the resting shelf into ONE bitmap.

        Thirty-nine separate RGBA images on the canvas cost about 29ms a frame,
        which is most of a frame budget spent redrawing icons that are not
        moving. Only the few near your hand ever change, so the rest are baked
        once and drawn as a single blit; magnified ones are drawn over the top,
        and since a magnified icon is always strictly larger than its resting
        one, it covers it completely.
        """
        self.shelf_photo = None
        if not self.items or not self.sd:
            return
        try:
            from PIL import Image, ImageTk
        except Exception:
            return
        cell = self.shelf_cell
        top = self.sh - self.shelf_h
        base = Image.new("RGBA", (int(self.sw), int(math.ceil(self.shelf_h))),
                         (0, 0, 0, 0))
        px = max(16, int(round(cell * SHELF_ICON / SHELF_QUANT)) * SHELF_QUANT)
        for it in self.items:
            src = self.item_src.get(it.path)
            if it.path not in self.item_src:
                try:
                    src = self.sd.icon_for(it.path, SHELF_SRC_PX)
                except Exception:
                    src = None
                self.item_src[it.path] = src
            if src is None:
                continue
            ic = src.resize((px, px), Image.LANCZOS)
            base.alpha_composite(ic, (int(it.x - px / 2),
                                      int(it.y - top - px / 2)))
        self.shelf_photo = ImageTk.PhotoImage(base)
        self.shelf_top = top

    def item_at(self, x, y):
        best = None
        for it in self.items:
            l, t, r, b = it.rect(self.shelf_cell)
            if l <= x <= r and t <= y <= b:
                best = it          # later items sit on top of earlier ones
        return best

    def _update_shelf(self, dt):
        """Ease each item's magnification from how near your hand is."""
        if not self.items:
            return
        reach = self.shelf_cell * SHELF_REACH
        focus = self.item_at(self.px, self.py)
        for it in self.items:
            dx = (self.px - it.x) / reach
            dy = (self.py - it.y) / (reach * 0.85)
            d2 = dx * dx + dy * dy
            want = math.exp(-d2 * 1.5) if d2 < 8.0 else 0.0
            it.mag += (want - it.mag) * min(1.0, dt * 15.0)
            it.hover += ((1.0 if it is focus else 0.0) - it.hover) * min(1.0, dt * 15.0)

    def _icon_photo(self, it: DeskItem, px: float):
        """A tk bitmap for this icon at (quantised) px. Resizing every icon every
        frame cannot hold 60fps, and an unquantised size would never hit."""
        px = max(16, int(round(px / SHELF_QUANT)) * SHELF_QUANT)
        key = (it.path, px)
        photo = self.item_photos.get(key)
        if photo is not None:
            return photo
        if it.path not in self.item_src:
            try:
                self.item_src[it.path] = self.sd.icon_for(it.path, SHELF_SRC_PX)
            except Exception:
                self.item_src[it.path] = None
        src = self.item_src.get(it.path)
        if src is None:
            return None
        try:
            from PIL import Image, ImageTk
            photo = ImageTk.PhotoImage(src.resize((px, px), Image.LANCZOS))
        except Exception:
            return None
        self.item_photos[key] = photo
        self._photo_order.append(key)
        while len(self._photo_order) > 420:      # bound as items are magnified
            self.item_photos.pop(self._photo_order.pop(0), None)
        return photo

    def open_desk(self, it: DeskItem):
        """Launch a desktop item WITHOUT leaving.

        The app starts behind the workspace and arrives as its own panel once
        its window exists, so choosing something adds it to the space rather
        than ejecting you from it. Leaving later hands you the app already
        running. Quitting on every launch made a mis-aimed pinch cost the whole
        session, which is exactly what went wrong in practice.
        """
        if self.closing or not self.sd:
            return
        it.mag = 1.35
        if self.sd.open_item(it.path):
            self.say(f"Opening {it.name[:34]}…", 2.6)
            # give it a while to put a window up; a cold app can be slow
            self.awaiting = (it.name, time.time() + 25.0)
        else:
            self.say(f"Couldn't open {it.name[:30]}", 2.0)

    # ── input ─────────────────────────────────────────────────────────────────
    def _mouse_move(self, e):
        self.px, self.py = e.x, e.y

    def _mouse_down(self, e):
        self.px, self.py = e.x, e.y
        p = self.panel_at(e.x, e.y)
        if p:
            self.select(p)
            return
        it = self.item_at(e.x, e.y)
        if it:
            self.open_desk(it)

    def _mouse_up(self, e):
        pass

    def panel_at(self, x, y):
        for p in reversed(self.panels):
            l, t, r, b = p.rect(self.base_w, self.tick)
            if l <= x <= r and t <= y <= b:
                return p
        return None

    def select(self, p: Panel):
        """Drop into a window: focus it and leave the workspace."""
        if self.closing:
            return
        self.closing = True
        self.say(f"Opening {p.mirror.title[:36]}")
        p.ts = 1.22
        self.root.after(170, lambda: self._finish_select(p))

    def _finish_select(self, p):
        try:
            for q in self.panels:
                q.mirror.hide()
            self.root.withdraw()
            self.root.update()
            p.mirror.focus()
        except Exception:
            pass
        self.quit()

    def say(self, msg, secs=1.6):
        self.status = msg
        self.status_until = time.time() + secs

    def grab(self, p: Panel):
        self.held = p
        p.grabbed = True
        self.hold_off = (p.x - self.px, p.y - self.py)

    def drop(self):
        if self.held:
            self.held.grabbed = False
            self.held = None

    # ── loop ──────────────────────────────────────────────────────────────────
    def loop(self):
        """One frame. Never lets an exception escape.

        The next frame is scheduled by the tail of this one, so ANY exception
        here used to break the chain permanently: the window stayed up but
        stopped repainting and stopped responding, which is indistinguishable
        from the workspace having closed itself. A single bad frame must never
        be able to end the session, so the frame is wrapped and the schedule is
        re-armed in a finally.
        """
        if self.closing:
            return
        try:
            self._frame()
        except Exception:
            self._faults += 1
            try:
                import traceback
                (ROOT / "data" / "spatial_errors.log").open("a", encoding="utf-8")\
                    .write(f"\n--- frame {self.tick} ---\n{traceback.format_exc()}")
            except Exception:
                pass
            # a persistently broken frame is worse than leaving: say so and go,
            # rather than sitting on a frozen fullscreen window
            if self._faults > 240:
                self.say("Spatial hit an error — leaving", 1.0)
                return self.quit()
        finally:
            if not self.closing:
                self.root.after(16, self.loop)

    def _frame(self):
        if win32api.GetAsyncKeyState(win32con.VK_ESCAPE) & 0x8000:
            return self.quit()

        now = time.time()
        dt = min(0.05, now - self.last)
        self.last = now
        self.tick += 1

        if self.reader is not None:
            self._hands()

        # prune windows that closed while we were open
        gone = [p for p in self.panels if not p.mirror.alive()]
        for p in gone:
            p.mirror.close()
            self.panels.remove(p)
        changed = bool(gone)

        # ...and adopt ones that appeared. Twice a second is often enough to
        # feel immediate, and cheap enough not to matter; every frame would
        # enumerate every top-level window sixty times a second for nothing.
        if self.tick % 30 == 0:
            if self.awaiting and time.time() > self.awaiting[1]:
                self.say(f"{self.awaiting[0][:30]} didn't open a window", 2.4)
                self.awaiting = None
            changed |= self._rescan()
        if changed:
            self._layout()

        self._update_shelf(dt)
        self._advance_arm(dt)

        hovered = self.panel_at(self.px, self.py) if not self.held else self.held
        for p in self.panels:
            want = 1.0 if p is hovered else 0.0
            p.hover += (want - p.hover) * min(1.0, dt * 11.0)
            if p is self.held:
                p.tx = self.px + self.hold_off[0]
                p.ty = self.py + self.hold_off[1]
            p.step(dt, self.tick)

        self.draw(hovered)          # rescheduling is handled by loop()'s finally

    def _hands(self):
        if self.reader.err:
            self.say("hand tracking unavailable — use the mouse", 0.4)
            return
        hands = self.reader.snapshot()
        self.hands = hands
        if not hands:
            if self.state == "fist":
                self.drop()
            self.scale_ref = None
            self.state = "idle"
            return

        primary = hands[0]
        tx, ty = primary.x * self.sw, primary.y * self.sh
        d = math.hypot(tx - self.px, ty - self.py)
        a = min(0.5, 0.14 + d / 1100.0)
        self.px += (tx - self.px) * a
        self.py += (ty - self.py) * a

        g = primary.gesture
        if self.tutorial and not self.tutorial.ready():
            self.tutorial.saw(g)
            self.state = g
            return

        # ── two hands: pull apart to grow a panel, together to shrink ────────
        if len(hands) == 2 and all(h.gesture in ("fist", "pinch") for h in hands):
            a2, b2 = hands[0], hands[1]
            gap = math.hypot((a2.x - b2.x) * self.sw, (a2.y - b2.y) * self.sh)
            target = self.held or self.panel_at(self.px, self.py)
            if target:
                if self.scale_ref is None:
                    self.scale_ref = (gap, target.ts)
                else:
                    ref_gap, ref_scale = self.scale_ref
                    if ref_gap > 40:
                        target.ts = max(0.45, min(2.4,
                                                  ref_scale * (gap / ref_gap)))
                self.state = "scale"
                return
        else:
            self.scale_ref = None

        self.apply_gesture(g)
        if self.tutorial:
            self.tutorial.saw(g)

    def apply_gesture(self, g: str):
        """Turn one frame's gesture into grabs and opens.

        Kept free of camera and canvas state so the timing can be exercised
        directly against flickery gesture streams — this is the part that made
        opening a panel feel like luck, and eyeballing it was how it shipped
        broken.
        """
        if g == "fist":
            self.fist_frames += 1
            self.pinch_grace = 0
            # A pinch passes through a fist-like frame on its way in. Grabbing
            # on the very first fist frame let that transient snatch a panel,
            # so the pinch that followed "opened" a panel you were now holding
            # rather than the one you were pointing at.
            if self.held is None and self.fist_frames >= FIST_HOLD:
                p = self.panel_at(self.px, self.py)
                if p:
                    self.grab(p)
            self.state = "fist"
        elif g == "pinch":
            self.fist_frames = 0
            self.pinch_frames += 1
            self.pinch_grace = PINCH_GRACE
            # Detection is deliberately eager — the old `== 3` fired on exactly
            # one frame, so a single dropped frame lost the whole gesture. But
            # eager detection must not mean an eager ACTION: a pinch only ARMS
            # what it is over, and the hold that follows is what commits. A
            # brushed pinch now costs nothing, where before it launched an app
            # and closed the workspace.
            # `pinch_fired` latches once this pinch has committed, and only
            # clears when the pinch is genuinely released. Without it a pinch
            # held past the commit simply re-arms and fires again: opening a
            # panel hid that behind `closing`, but launching a desktop item did
            # not, so keeping the pinch down started the app over and over.
            if self.pinch_frames >= PINCH_HOLD and not self.pinch_fired:
                target = (self.held or self.panel_at(self.px, self.py)
                          or self.item_at(self.px, self.py))
                if target is not self.arm:
                    self.arm = target
                    self.arm_t = 0.0
            self.state = "pinch"
        else:
            if self.state == "fist":
                self.drop()
            self.fist_frames = 0
            # Ride out a few dropped frames rather than restarting the pinch.
            if self.pinch_grace > 0:
                self.pinch_grace -= 1
                self.state = "pinch"
            else:
                self.pinch_frames = 0
                self.pinch_fired = False
                self.state = g

    def _advance_arm(self, dt: float):
        """Grow (or unwind) the hold that commits an action.

        Held to completion it opens; let go early and it rewinds, faster than it
        filled, so backing out is quicker than committing.
        """
        if self.state == "pinch" and self.arm is not None:
            self.arm_t += dt / self._arm_secs(self.arm)
            if self.arm_t >= 1.0:
                target, self.arm, self.arm_t = self.arm, None, 0.0
                self.pinch_fired = True      # nothing else until you let go
                if isinstance(target, DeskItem):
                    self.open_desk(target)
                else:
                    self.drop()
                    self.select(target)
        elif self.arm_t > 0.0:
            self.arm_t -= dt / (ARM_PANEL * 0.55)
            if self.arm_t <= 0.0:
                self.arm_t, self.arm = 0.0, None

    @staticmethod
    def _arm_secs(target) -> float:
        """How long the hold is, scaled to how bad the mistake would be.

        Opening a panel just focuses a window that is already running. Opening a
        desktop item LAUNCHES something, which is the mistake worth guarding."""
        return ARM_DESKTOP if isinstance(target, DeskItem) else ARM_PANEL

    def _toggle_shelf(self):
        """Fold the desktop away, and back.

        On Tab rather than on a pinch. As a gesture this was a trap: pinching
        empty space is the easiest thing to do by accident, and the desktop
        silently vanishing gives no hint of what happened or how to undo it."""
        if not self.items:
            return
        self.shelf_visible = not self.shelf_visible
        self.say("Desktop hidden — Tab to show" if not self.shelf_visible
                 else "Desktop", 1.4)

    # ── drawing ───────────────────────────────────────────────────────────────
    def draw(self, hovered):
        c = self.cv
        c.delete("all")
        if self.bg_img:
            c.create_image(0, 0, image=self.bg_img, anchor="nw")

        if not self.panels:
            c.create_text(self.sw / 2, self.sh * 0.34, text="Nothing open",
                          fill=TEXT, font=("Segoe UI Light", 30))
            c.create_text(self.sw / 2, self.sh * 0.34 + 40,
                          text="Your desktop is below — pinch anything to open it.",
                          fill=MUTED, font=("Segoe UI", 12))
        else:
            # a soft ground line, so panels feel like they're standing in a
            # space. It sits on the shelf rather than at a fixed height, or the
            # two draw straight through each other.
            gy = self.sh - max(self.shelf_h, self.sh * 0.16) - 8
            for i in range(6):
                t = i / 5
                c.create_line(0, gy + i * 2, self.sw, gy + i * 2,
                              fill=_hex(_blend((16, 26, 40), BG_BOT, t)))

            for p in self.panels:
                self._panel(c, p, p is hovered)

        # the shelf and the hands are drawn either way — with nothing open the
        # desktop IS the workspace, and an early return here used to take the
        # user's hands off screen with it
        self._draw_shelf(c)
        self._draw_hands(c)
        self._chrome()
        if self.tutorial:
            self.tutorial.draw(c)

    def _draw_shelf(self, c):
        """Your desktop items, magnified by how near your hand is."""
        if not self.items or not self.shelf_visible:
            return
        cell = self.shelf_cell
        top = self.sh - self.shelf_h
        # a hairline rather than a plinth: the shelf should read as a surface
        # without laying a slab of chrome over the backdrop
        c.create_line(self.sw * 0.04, top, self.sw * 0.96, top, fill="#16283A")

        # everything at rest, in one blit
        if getattr(self, "shelf_photo", None) is not None:
            c.create_image(0, top, image=self.shelf_photo, anchor="nw")

        # ...then only what your hand is near, least-magnified first so the
        # focused icon overlaps its neighbours
        near = [it for it in self.items if it.mag > 0.02]
        for it in sorted(near, key=lambda i: i.mag):
            l, t, r, b = it.rect(cell)
            cx, cy = (l + r) / 2, (t + b) / 2
            photo = self._icon_photo(it, it.size(cell))
            if photo is not None:
                c.create_image(cx, cy, image=photo)
            elif self.shelf_photo is None:
                c.create_rectangle(l, t, r, b, outline=MUTED)
            if it.hover > 0.04:
                # only the focused item names itself — at this density every
                # label at once is unreadable noise
                col = _hex(_blend(BG_BOT, (220, 233, 242), min(1.0, it.hover * 1.5)))
                c.create_text(cx, t - cell * 0.17, text=it.name[:30],
                              fill=col, font=("Segoe UI", 10))

    def _draw_hands(self, c):
        """Draw the user's hands as shaded 3D geometry, posed by real tracking.

        The pose is entirely theirs — 21 tracked landmarks, so every finger bends
        and the hand rotates exactly as it does in life. Only the SURFACE is
        synthetic, which avoids the background bleed, sensor noise and mismatched
        lighting that made raw camera pixels look pasted on top of the scene."""
        hands = getattr(self, "hands", None)
        if not hands:
            self.hand_imgs.clear()
            self._hand_cache.clear()
            return
        try:
            from PIL import Image, ImageTk
        except Exception:
            return

        fw, fh = self.reader.frame_size
        cam2scr = self.sw / max(fw, 1)
        keep = {}
        for i, hd in enumerate(hands):
            if hd.pts is None or hd.zs is None:
                continue
            # Mirror in LANDMARK space rather than flipping the finished image.
            # The view is mirrored so moving right moves right, and transposing
            # the render to achieve that cost a whole extra image copy a frame.
            pts = hd.pts.copy()
            pts[:, 0] = fw - pts[:, 0]

            # Size from the hand's REAL extent in camera pixels — the render's
            # own pixel size says nothing about how big the hand is — and then
            # render at exactly that size. Rendering at a fixed resolution and
            # resampling afterwards cost more than the render itself.
            try:
                off, extent = self.hand_renderer.extent(pts)
            except Exception:
                continue
            if extent[0] < 1.0 or extent[1] < 1.0:
                continue

            # Re-pose on alternate frames. Rendering the surface is by far the
            # most expensive thing in a frame, and a pose one frame stale is not
            # perceptible — while POSITION still updates every frame, because it
            # comes from the cursor, so the hand never lags where you point it.
            cached = self._hand_cache.get(i)
            if cached is not None and (self.tick & 1):
                photo, fx, fy = cached
                if i == 0:
                    cx, cy = self.px, self.py
                else:
                    cx, cy = hd.x * self.sw, hd.y * self.sh
                c.create_image(int(cx - fx * photo.width()),
                               int(cy - fy * photo.height()),
                               image=photo, anchor="nw")
                keep[(i, (photo.width(), photo.height()))] = photo
                continue

            # Quantised, so the same buffer can be reused frame to frame.
            # Allocating a fresh PhotoImage costs ~9ms; pasting into one that
            # already exists is a straight block copy, and a hand's size changes
            # slowly enough that the size repeats for many frames at a time.
            dw = max(24, int(extent[0] * cam2scr * HAND_SCALE) // 8 * 8)
            try:
                img, off, extent = self.hand_renderer.render(
                    pts, hd.zs, fw, fh, res_w=dw)
                key = (i, img.size)
                photo = self.hand_imgs.get(key)
                if photo is None:
                    photo = ImageTk.PhotoImage(img)
                else:
                    photo.paste(img)
            except Exception:
                continue

            # Put the PINCH POINT on the cursor. The control point is the
            # thumb-index midpoint; centring the whole image instead planted the
            # cursor in the middle of the palm, so the hand never looked like it
            # was touching the thing it was pointing at.
            ctrl_x = (pts[4][0] + pts[8][0]) * 0.5
            ctrl_y = (pts[4][1] + pts[8][1]) * 0.5
            fx = (ctrl_x - off[0]) / extent[0]
            fy = (ctrl_y - off[1]) / extent[1]

            if i == 0:
                cx, cy = self.px, self.py
            else:
                cx, cy = hd.x * self.sw, hd.y * self.sh
            c.create_image(int(cx - fx * img.size[0]),
                           int(cy - fy * img.size[1]),
                           image=photo, anchor="nw")
            self._hand_cache[i] = (photo, fx, fy)
            keep[key] = photo        # tkinter drops un-referenced PhotoImages
        # keep at most a couple of spare sizes so a hand moving toward the
        # camera doesn't accumulate a buffer for every width it passes through
        for k in list(self.hand_imgs)[:-4]:
            self.hand_imgs.pop(k, None)
        self.hand_imgs.update(keep)

    def _bg_at(self, x, y):
        """The backdrop colour under a point, for faking translucency."""
        pil = getattr(self, "bg_pil", None)
        if pil is None:
            return BG_BOT
        try:
            return pil.getpixel((min(max(int(x / 8), 0), pil.width - 1),
                                 min(max(int(y / 8), 0), pil.height - 1)))[:3]
        except Exception:
            return BG_BOT

    def _panel(self, c, p: Panel, is_hover: bool):
        l, t, r, b = p.rect(self.base_w, self.tick)   # hover lift is in rect()
        grabbed = p.grabbed
        accent = GRAB if grabbed else ACCENT
        tint = (255, 196, 107) if grabbed else (125, 219, 255)
        # Glow: concentric strokes fading into the backdrop. There is no alpha
        # in the tkinter canvas, so each ring blends toward whatever is actually
        # behind it — sampled from the backdrop, not assumed to be near-black.
        near = self._bg_at((l + r) / 2, t)
        far = self._bg_at((l + r) / 2, b)
        # A resting panel gets a thin halo; only the one you are looking at pays
        # for the full stack. Nine strokes on every panel was a third of the
        # frame spent on glow nobody was looking at.
        rings = 9 if (p.hover > 0.03 or grabbed) else 4
        strength = 0.22 + p.hover * 0.62
        for i in range(rings, 0, -1):
            k = i / rings
            # quadratic falloff reads as light spilling out; linear looks like a
            # stack of drawn boxes, which is what it was
            a = strength * (1.0 - k) * (1.0 - k)
            pad = i * 3
            c.create_rectangle(l - pad, t - pad, r + pad, b + pad,
                               outline=_hex(_blend(_blend(near, far, k), tint, a)),
                               width=2)

        # contact shadow, grounding the panel against the floor haze
        for i in range(4):
            sh = _blend(self._bg_at((l + r) / 2, b + 6 + i * 3), (0, 0, 0),
                        0.42 - i * 0.09)
            c.create_line(l + 10 + i * 4, b + 5 + i * 3,
                          r - 10 - i * 4, b + 5 + i * 3, fill=_hex(sh), width=3)

        # the live window itself
        p.mirror.place(l, t, r, b, opacity=255, visible=True)

        # frame + corner ticks
        c.create_rectangle(l - 1, t - 1, r + 1, b + 1,
                           outline=accent, width=2 if (is_hover or grabbed) else 1)
        if is_hover or grabbed:
            n = 22
            for cx, cy, dx, dy in ((l, t, 1, 1), (r, t, -1, 1),
                                   (l, b, 1, -1), (r, b, -1, -1)):
                c.create_line(cx + dx * n, cy, cx, cy, fill=accent, width=3)
                c.create_line(cx, cy, cx, cy + dy * n, fill=accent, width=3)

        # title, brightening as you look at it
        title = p.mirror.title
        if len(title) > 46:
            title = title[:44] + "…"
        lit = max(p.hover, 1.0 if grabbed else 0.0)
        fill = _hex(_blend(_blend(self._bg_at((l + r) / 2, b + 26), (150, 174, 196), 0.85),
                           (255, 236, 196) if grabbed else (226, 240, 250), lit))
        c.create_text((l + r) / 2, b + 26, text=title, fill=fill,
                      font=("Segoe UI Semilight", 13,
                            "bold" if is_hover else "normal"))
        if grabbed:
            c.create_text((l + r) / 2, b + 42, text="carrying", fill=GRAB,
                          font=("Segoe UI", 10))

    def _chrome(self):
        c = self.cv
        # pointer: a calm ring, brighter when it can do something
        act = {"fist": GRAB, "pinch": "#FFFFFF"}.get(self.state, ACCENT)
        rr = 15 if self.state != "pinch" else 9
        c.create_oval(self.px - rr, self.py - rr, self.px + rr, self.py + rr,
                      outline=act, width=2)
        c.create_oval(self.px - 3, self.py - 3, self.px + 3, self.py + 3,
                      fill=act, outline="")

        # The commit hold, drawn as it fills. A hold with no visible progress is
        # no better than a hair trigger: you cannot tell whether something is
        # about to happen, so you cannot stop it. The ring is what makes
        # "keep pinching to open, let go to cancel" legible without instructions.
        if self.arm is not None and self.arm_t > 0.015:
            t = min(1.0, self.arm_t)
            ar = 30
            c.create_oval(self.px - ar, self.py - ar, self.px + ar, self.py + ar,
                          outline=_hex(_blend(BG_BOT, (60, 92, 116), 0.9)), width=3)
            c.create_arc(self.px - ar, self.py - ar, self.px + ar, self.py + ar,
                         start=90, extent=-359.9 * t, style="arc",
                         outline=ACCENT, width=3)
            label = (self.arm.name if isinstance(self.arm, DeskItem)
                     else self.arm.mirror.title)
            c.create_text(self.px, self.py + ar + 16, text=label[:34],
                          fill=TEXT, font=("Segoe UI", 10))

        c.create_text(38, 34, text="SPATIAL WORKSPACE", anchor="w",
                      fill=ACCENT_SOFT, font=("Segoe UI", 11, "bold"))
        n = len(self.panels)
        bits = [f"{n} window{'s' if n != 1 else ''}"]
        if self.items:
            bits.append(f"{len(self.items)} on your desktop")
        bits += ["fist to carry", "pinch to open", "Esc to leave"]
        c.create_text(38, 56, anchor="w", text=" · ".join(bits),
                      fill=MUTED, font=("Segoe UI", 10))

        # the status line sits above the shelf, not on top of it
        if self.status and time.time() < self.status_until:
            c.create_text(self.sw / 2, self.sh - self.shelf_h - 26,
                          text=self.status, fill=TEXT, font=("Segoe UI", 13))

    # ── teardown ──────────────────────────────────────────────────────────────
    def quit(self):
        try:
            if self.reader:
                self.reader.stop()
        except Exception:
            pass
        for p in self.panels:
            try:
                p.mirror.close()
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass
        sys.exit(0)


class Tutorial:
    """Shown once. Each step unlocks only when the gesture is genuinely held,
    and nothing moves a real window until it's finished."""

    STEPS = [
        ("open", "Open your hand",
         "Your hand is the pointer. Move it — panels light up as you pass over."),
        ("fist", "Make a fist over a panel",
         "A fist picks the window up. Move your hand and it comes with you."),
        ("pinch", "Pinch your thumb and finger",
         "Pinch drops you into that window and closes the workspace."),
    ]
    HOLD = 16

    def __init__(self, ws):
        self.ws = ws
        self.i = 0
        self.n = 0
        self.done_at = 0.0

    def ready(self):
        return self.i >= len(self.STEPS)

    def saw(self, g):
        if self.ready():
            return
        want = self.STEPS[self.i][0]
        self.n = self.n + 1 if g == want else max(0, self.n - 1)
        if self.n >= self.HOLD:
            self.n = 0
            self.i += 1
            if self.ready():
                self.done_at = time.time()
                try:
                    TUTORIAL_FLAG.write_text("done", encoding="utf-8")
                except Exception:
                    pass

    def draw(self, c):
        ws = self.ws
        if self.ready():
            if time.time() - self.done_at > 2.0:
                ws.tutorial = None
                return
            c.create_text(ws.sw / 2, 92, text="Ready", fill=ACCENT,
                          font=("Segoe UI Light", 30))
            return
        want, title, why = self.STEPS[self.i]
        y = 78
        c.create_rectangle(ws.sw / 2 - 320, y - 34, ws.sw / 2 + 320, y + 74,
                           outline=ACCENT_SOFT, width=1)
        c.create_text(ws.sw / 2, y - 14,
                      text=f"{self.i + 1} of {len(self.STEPS)}",
                      fill=MUTED, font=("Segoe UI", 10))
        c.create_text(ws.sw / 2, y + 14, text=title, fill=TEXT,
                      font=("Segoe UI Light", 22))
        c.create_text(ws.sw / 2, y + 44, text=why, fill=MUTED,
                      font=("Segoe UI", 11))
        w = 260 * min(1.0, self.n / self.HOLD)
        c.create_rectangle(ws.sw / 2 - 130, y + 62, ws.sw / 2 + 130, y + 65,
                           outline=_hex(_blend(BG_BOT, (60, 90, 110), 1.0)))
        if w > 1:
            c.create_rectangle(ws.sw / 2 - 130, y + 62, ws.sw / 2 - 130 + w,
                               y + 65, outline="", fill=ACCENT)


if __name__ == "__main__":
    Workspace(use_hands="--nohands" not in sys.argv).root.mainloop()
