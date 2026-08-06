"""
spatial_scan.py — teach G.I.L. your hands.

Samples the colour of your skin from well inside the tracked silhouette and
stores it, so the hands drawn in the workspace are yours rather than a generic
default. Runs as its own small window; the result lives in
data/hand_calibration.json and is picked up by every spatial mode.
"""

from __future__ import annotations

import math
import sys
import time
import tkinter as tk

import spatial_handrender as hr
import spatial_hands as sh
from spatial_reader import SpatialReader

BG = "#070B12"
ACCENT = "#7DDBFF"
OK = "#6BE8A6"
TEXT = "#DCE9F2"
MUTED = "#5A7186"
TARGET = 34                     # frames of good samples before we're satisfied


class ScanWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("G.I.L. — Hand Scan")
        self.root.configure(bg=BG)
        self.w, self.h = 660, 470
        sw = self.root.winfo_screenwidth()
        sh_ = self.root.winfo_screenheight()
        self.root.geometry(f"{self.w}x{self.h}+{(sw - self.w) // 2}+{(sh_ - self.h) // 2}")
        self.root.attributes("-topmost", True)
        self.cv = tk.Canvas(self.root, width=self.w, height=self.h, bg=BG,
                            highlightthickness=0)
        self.cv.pack()

        self.reader = SpatialReader(cutouts=False)
        self.reader.start()
        self.reader.begin_scan()
        self.renderer = hr.HandRenderer()
        self.done = False
        self.done_at = 0.0
        self.tick = 0
        self.photo = None
        self.root.bind_all("<Escape>", lambda e: self.quit())
        self.loop()

    def loop(self):
        self.tick += 1
        n = self.reader.scan_progress()

        if not self.done and n >= TARGET:
            calib = self.reader.finish_scan()
            if calib:
                self.skin = hr.skin_from_calibration(calib)
                self.renderer = hr.HandRenderer(self.skin)
                self.done = True
                self.done_at = time.time()

        self.draw(n)
        if self.done and time.time() - self.done_at > 2.6:
            return self.quit()
        self.root.after(33, self.loop)

    def draw(self, n):
        c = self.cv
        c.delete("all")
        hands = self.reader.snapshot()

        c.create_text(self.w / 2, 46, text="Hand scan", fill=TEXT,
                      font=("Segoe UI Light", 26))
        if self.reader.err:
            c.create_text(self.w / 2, 110, text=self.reader.err, fill="#FFB44C",
                          font=("Segoe UI", 12))
            c.create_text(self.w / 2, 140, text="Esc to close", fill=MUTED,
                          font=("Segoe UI", 11))
            return

        if self.done:
            c.create_text(self.w / 2, 84, text="Got it", fill=OK,
                          font=("Segoe UI", 15))
            sk = getattr(self, "skin", hr.DEFAULT_SKIN)
            c.create_text(self.w / 2, 112,
                          text="Your hands will be drawn in this tone from now on.",
                          fill=MUTED, font=("Segoe UI", 11))
            c.create_rectangle(self.w / 2 - 34, 136, self.w / 2 + 34, 168,
                               fill="#%02x%02x%02x" % sk, outline=ACCENT)
        else:
            c.create_text(self.w / 2, 84,
                          text="Hold your open hand up to the camera",
                          fill=TEXT, font=("Segoe UI", 14))
            c.create_text(self.w / 2, 110,
                          text="Turn it slowly — palm, then back — in even light.",
                          fill=MUTED, font=("Segoe UI", 11))

        # live preview of the hand as it will be drawn
        if hands and hands[0].pts is not None:
            hd = hands[0]
            try:
                img, _ = self.renderer.render(hd.pts, hd.zs, *self.reader.frame_size)
                from PIL import Image, ImageTk
                box = 210
                sc = min(box / img.size[0], box / img.size[1])
                img = img.resize((max(8, int(img.size[0] * sc)),
                                  max(8, int(img.size[1] * sc))), Image.LANCZOS)
                img = img.transpose(Image.FLIP_LEFT_RIGHT)
                self.photo = ImageTk.PhotoImage(img)
                c.create_image(self.w / 2, 272, image=self.photo, anchor="center")
            except Exception:
                pass
        else:
            r = 46 + math.sin(self.tick / 8) * 4
            c.create_oval(self.w / 2 - r, 272 - r, self.w / 2 + r, 272 + r,
                          outline=MUTED, width=2)
            c.create_text(self.w / 2, 272, text="looking…", fill=MUTED,
                          font=("Segoe UI", 11))

        # progress
        pct = min(1.0, n / TARGET)
        bw = 320
        x0 = (self.w - bw) / 2
        c.create_rectangle(x0, 402, x0 + bw, 408, outline=MUTED)
        if pct > 0:
            c.create_rectangle(x0, 402, x0 + bw * pct, 408, outline="",
                               fill=OK if self.done else ACCENT)
        c.create_text(self.w / 2, 430,
                      text="Esc to cancel" if not self.done else "",
                      fill=MUTED, font=("Segoe UI", 10))

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


if __name__ == "__main__":
    ScanWindow().root.mainloop()
