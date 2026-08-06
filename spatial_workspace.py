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
        """Screen rectangle, including a slow idle drift so it feels afloat."""
        drift = 0.0 if self.grabbed else math.sin(tick / 62.0 + self.seed) * 3.0
        w = base_w * self.s
        h = w / max(self.mirror.aspect, 0.35)
        return (self.x - w / 2, self.y - h / 2 + drift,
                self.x + w / 2, self.y + h / 2 + drift)


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

        # Hands are the point, but the mouse must work too: hand tracking can
        # fail (camera busy, bad light) and being stuck in a fullscreen mode you
        # can't drive is the exact trap we just fixed elsewhere.
        self.reader = None
        if use_hands:
            try:
                from spatial_overlay import HandReader
                self.reader = HandReader()
                self.reader.start()
            except Exception:
                self.reader = None
        self.cv.bind("<Motion>", self._mouse_move)
        self.cv.bind("<Button-1>", self._mouse_down)
        self.cv.bind("<ButtonRelease-1>", self._mouse_up)
        self.root.bind_all("<Escape>", lambda e: self.quit())
        self.root.focus_force()

        self.tutorial = Tutorial(self) if not TUTORIAL_FLAG.exists() else None
        self.loop()

    # ── setup ─────────────────────────────────────────────────────────────────
    def _backdrop(self):
        """A quiet deep-space gradient with a few stars, rendered once."""
        try:
            from PIL import Image, ImageDraw, ImageFilter, ImageTk
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
        # faint aurora bloom behind everything, so panels sit in light
        glow = Image.new("RGB", (w, h), (0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.ellipse([w * 0.18, -h * 0.42, w * 0.82, h * 0.55], fill=(20, 44, 66))
        glow = glow.filter(ImageFilter.GaussianBlur(w // 12))
        img = Image.blend(img, Image.blend(img, glow, 0.55), 0.75)
        d = ImageDraw.Draw(img)
        rnd = random.Random(7)
        for _ in range(150):
            x, y = rnd.randrange(w), rnd.randrange(h)
            v = rnd.randint(28, 92)
            r = 1 if rnd.random() < 0.86 else 2
            d.ellipse([x, y, x + r, y + r], fill=(v, v + 8, v + 16))
        self.bg_img = ImageTk.PhotoImage(img)

    def _collect(self):
        for h, title in sm.list_windows(exclude_hwnd=self.hwnd):
            m = sm.Mirror(self.hwnd, h, title)
            if m.handle:
                self.panels.append(Panel(m, self.sw / 2, self.sh / 2, 0.35))

    def _layout(self):
        """Lay panels on a gentle arc — depth suggested by scale, not perspective.

        DWM mirrors are axis-aligned rectangles, so real perspective is off the
        table. Scaling and a curved baseline read as depth honestly, instead of
        pretending to a 3D we can't render."""
        n = len(self.panels)
        if not n:
            return
        cols = min(n, 4 if n > 6 else 3 if n > 2 else n)
        rows = math.ceil(n / cols)
        cw = self.sw / (cols + 0.5)
        base_w = min(cw * 0.86, self.sw * 0.34)
        self.base_w = base_w
        top = self.sh * (0.40 if rows > 1 else 0.48)
        vgap = base_w * 0.62

        for i, p in enumerate(self.panels):
            r, c = divmod(i, cols)
            in_row = min(cols, n - r * cols)
            spread = (c - (in_row - 1) / 2)
            x = self.sw / 2 + spread * cw
            # arc: outer panels sit slightly lower and smaller
            off = abs(spread) / max(in_row / 2, 1)
            y = top + r * vgap + off * off * 26
            p.tx, p.home_x = x, x
            p.ty, p.home_y = y, y
            p.ts = 1.0 - off * 0.09

    # ── input ─────────────────────────────────────────────────────────────────
    def _mouse_move(self, e):
        self.px, self.py = e.x, e.y

    def _mouse_down(self, e):
        self.px, self.py = e.x, e.y
        p = self.panel_at(e.x, e.y)
        if p:
            self.select(p)

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
        if gone:
            self._layout()

        hovered = self.panel_at(self.px, self.py) if not self.held else self.held
        for p in self.panels:
            want = 1.0 if p is hovered else 0.0
            p.hover += (want - p.hover) * min(1.0, dt * 11.0)
            if p is self.held:
                p.tx = self.px + self.hold_off[0]
                p.ty = self.py + self.hold_off[1]
            p.step(dt, self.tick)

        self.draw(hovered)
        self.root.after(16, self.loop)

    def _hands(self):
        s = self.reader.state
        if s.get("err"):
            self.say("hand tracking unavailable — use the mouse", 0.4)
            return
        if not s.get("hands"):
            if self.state == "fist":
                self.drop()
            self.state = "idle"
            return

        tx, ty = s["x"] * self.sw, s["y"] * self.sh
        d = math.hypot(tx - self.px, ty - self.py)
        a = min(0.5, 0.14 + d / 1100.0)
        self.px += (tx - self.px) * a
        self.py += (ty - self.py) * a

        g = s.get("gesture", "none")
        if self.tutorial and not self.tutorial.ready():
            self.tutorial.saw(g)
            self.state = g
            return

        if g == "fist":
            if self.held is None:
                p = self.panel_at(self.px, self.py)
                if p:
                    self.grab(p)
            self.state = "fist"
        elif g == "pinch":
            self.pinch_frames += 1
            if self.pinch_frames == 3:          # brief hold, avoids twitch-selects
                p = self.held or self.panel_at(self.px, self.py)
                self.drop()
                if p:
                    self.select(p)
            self.state = "pinch"
        else:
            if self.state == "fist":
                self.drop()
            self.pinch_frames = 0
            self.state = g
        if self.tutorial:
            self.tutorial.saw(g)

    # ── drawing ───────────────────────────────────────────────────────────────
    def draw(self, hovered):
        c = self.cv
        c.delete("all")
        if self.bg_img:
            c.create_image(0, 0, image=self.bg_img, anchor="nw")

        if not self.panels:
            c.create_text(self.sw / 2, self.sh / 2 - 10, text="Nothing open",
                          fill=TEXT, font=("Segoe UI Light", 30))
            c.create_text(self.sw / 2, self.sh / 2 + 30,
                          text="Open an app, then come back.",
                          fill=MUTED, font=("Segoe UI", 12))
            self._chrome()
            return

        # a soft ground line, so panels feel like they're standing in a space
        gy = self.sh * 0.84
        for i in range(6):
            t = i / 5
            c.create_line(0, gy + i * 2, self.sw, gy + i * 2,
                          fill=_hex(_blend((16, 26, 40), BG_BOT, t)))

        for p in self.panels:
            self._panel(c, p, p is hovered)

        self._chrome()
        if self.tutorial:
            self.tutorial.draw(c)

    def _panel(self, c, p: Panel, is_hover: bool):
        l, t, r, b = p.rect(self.base_w, self.tick)
        lift = p.hover * 12.0
        t -= lift
        b -= lift

        grabbed = p.grabbed
        accent = GRAB if grabbed else ACCENT
        # glow: concentric strokes fading into the backdrop. No alpha in tkinter,
        # so blend toward the known background instead of faking transparency.
        strength = 0.30 + p.hover * 0.70
        for i in range(7, 0, -1):
            k = (i / 7.0)
            col = _blend(BG_BOT,
                         (125, 219, 255) if not grabbed else (255, 196, 107),
                         strength * (1.0 - k) * 0.75)
            pad = i * 3
            c.create_rectangle(l - pad, t - pad, r + pad, b + pad,
                               outline=_hex(col), width=2)

        # drop shadow, grounding the panel
        c.create_rectangle(l + 5, b + 3, r + 5, b + 9,
                           outline="", fill=_hex(_blend(BG_BOT, (0, 0, 0), 0.5)))

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

        # title, only when it's the one you're looking at
        title = p.mirror.title
        if len(title) > 46:
            title = title[:44] + "…"
        fill = TEXT if (is_hover or grabbed) else MUTED
        c.create_text((l + r) / 2, b + 22, text=title, fill=fill,
                      font=("Segoe UI", 12, "bold" if is_hover else "normal"))
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

        c.create_text(38, 34, text="SPATIAL WORKSPACE", anchor="w",
                      fill=ACCENT_SOFT, font=("Segoe UI", 11, "bold"))
        n = len(self.panels)
        c.create_text(38, 56, anchor="w",
                      text=f"{n} window{'s' if n != 1 else ''} · "
                           "fist to carry · pinch to open · Esc to leave",
                      fill=MUTED, font=("Segoe UI", 10))

        if self.status and time.time() < self.status_until:
            c.create_text(self.sw / 2, self.sh - 44, text=self.status,
                          fill=TEXT, font=("Segoe UI", 13))

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
