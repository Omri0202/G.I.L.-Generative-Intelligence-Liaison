"""
spatial_mirror.py — live mirrors of real windows, via DWM thumbnails.

This is the piece that makes a spatial workspace possible: DwmRegisterThumbnail
hands us a live, GPU-composited view of any window, which we can place anywhere
at any size. It is the same mechanism Alt-Tab and Task View use, so it costs us
nothing per frame — we never capture or blit a single pixel ourselves.

What it can't do is as important as what it can: destination rectangles are
axis-aligned, so there is no rotation, no perspective and no rounded corners on
the content itself. Everything decorative is therefore drawn AROUND the mirror.
"""

from __future__ import annotations

import ctypes
from ctypes import POINTER, Structure, byref, c_ubyte, c_void_p
from ctypes.wintypes import BOOL, DWORD, HWND, RECT

import win32con
import win32gui

_dwm = ctypes.windll.dwmapi


class _ThumbProps(Structure):
    _fields_ = [
        ("dwFlags", DWORD),
        ("rcDestination", RECT),
        ("rcSource", RECT),
        ("opacity", c_ubyte),
        ("fVisible", BOOL),
        ("fSourceClientAreaOnly", BOOL),
    ]


_TNP_RECTDESTINATION = 0x1
_TNP_RECTSOURCE = 0x2
_TNP_OPACITY = 0x4
_TNP_VISIBLE = 0x8
_TNP_SOURCECLIENTAREAONLY = 0x10

# restype is c_long, NOT ctypes.HRESULT: with HRESULT, ctypes raises OSError on
# any failure, so a single unmirrorable window (a protected or already-closing
# one) would take the whole workspace down instead of simply being skipped.
_dwm.DwmRegisterThumbnail.argtypes = [HWND, HWND, POINTER(c_void_p)]
_dwm.DwmRegisterThumbnail.restype = ctypes.c_long
_dwm.DwmUnregisterThumbnail.argtypes = [c_void_p]
_dwm.DwmUnregisterThumbnail.restype = ctypes.c_long
_dwm.DwmUpdateThumbnailProperties.argtypes = [c_void_p, POINTER(_ThumbProps)]
_dwm.DwmUpdateThumbnailProperties.restype = ctypes.c_long
_dwm.DwmQueryThumbnailSourceSize.argtypes = [c_void_p, POINTER(ctypes.c_long * 2)]
_dwm.DwmQueryThumbnailSourceSize.restype = ctypes.c_long


class Mirror:
    """One live window, rendered into our host window."""

    __slots__ = ("src", "title", "handle", "size", "_visible", "_state")

    def __init__(self, host_hwnd: int, src_hwnd: int, title: str):
        self.src = src_hwnd
        self.title = title
        self.handle = None
        self.size = (16, 9)
        self._visible = True
        self._state = None          # last rect pushed to DWM; see place()
        th = c_void_p()
        try:
            hr = _dwm.DwmRegisterThumbnail(host_hwnd, src_hwnd, byref(th))
        except Exception:
            hr = -1
        if hr == 0 and th:
            self.handle = th
            self.size = self._source_size()

    def _source_size(self):
        arr = (ctypes.c_long * 2)()
        try:
            if _dwm.DwmQueryThumbnailSourceSize(self.handle, byref(arr)) == 0:
                w, h = int(arr[0]), int(arr[1])
                if w > 0 and h > 0:
                    return (w, h)
        except Exception:
            pass
        return (16, 9)

    @property
    def aspect(self) -> float:
        w, h = self.size
        return (w / h) if h else 1.777

    def place(self, left: int, top: int, right: int, bottom: int,
              opacity: int = 255, visible: bool = True) -> None:
        """Position this mirror.

        Skips the call when nothing actually moved. Every update is a DWM round
        trip that makes the compositor re-scale the source window, and the
        workspace calls this for each panel on each frame — including the many
        frames where a panel is sitting perfectly still."""
        if not self.handle:
            return
        state = (int(left), int(top), int(right), int(bottom),
                 max(0, min(255, int(opacity))), bool(visible))
        if state == self._state:
            return
        self._state = state
        p = _ThumbProps()
        p.dwFlags = (_TNP_RECTDESTINATION | _TNP_VISIBLE | _TNP_OPACITY
                     | _TNP_SOURCECLIENTAREAONLY)
        p.rcDestination = RECT(int(left), int(top), int(right), int(bottom))
        p.opacity = max(0, min(255, int(opacity)))
        p.fVisible = bool(visible)
        p.fSourceClientAreaOnly = True
        try:
            _dwm.DwmUpdateThumbnailProperties(self.handle, byref(p))
            self._visible = visible
        except Exception:
            pass

    def hide(self) -> None:
        if self.handle and self._visible:
            self.place(0, 0, 1, 1, 0, False)

    def alive(self) -> bool:
        try:
            return bool(win32gui.IsWindow(self.src)
                        and win32gui.IsWindowVisible(self.src))
        except Exception:
            return False

    def focus(self) -> None:
        """Bring the real window forward — what 'selecting' a panel means."""
        try:
            if win32gui.IsIconic(self.src):
                win32gui.ShowWindow(self.src, win32con.SW_RESTORE)
            # Windows blocks a background process from stealing focus unless it
            # briefly shares the foreground thread's input queue.
            fg = win32gui.GetForegroundWindow()
            import win32process
            cur = win32process.GetWindowThreadProcessId(fg)[0]
            tgt = win32process.GetWindowThreadProcessId(self.src)[0]
            user32 = ctypes.windll.user32
            if cur != tgt:
                user32.AttachThreadInput(cur, tgt, True)
                win32gui.SetForegroundWindow(self.src)
                user32.AttachThreadInput(cur, tgt, False)
            else:
                win32gui.SetForegroundWindow(self.src)
        except Exception:
            try:
                win32gui.SetForegroundWindow(self.src)
            except Exception:
                pass

    def close(self) -> None:
        if self.handle:
            try:
                _dwm.DwmUnregisterThumbnail(self.handle)
            except Exception:
                pass
            self.handle = None


# ── discovering what's worth showing ──────────────────────────────────────────

_SKIP_CLASSES = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Windows.UI.Core.CoreWindow",
    "ApplicationFrameWindow_Ghost", "Windows.Internal.Shell.TabProxyWindow",
}
_SKIP_TITLES = {
    "Program Manager", "Windows Input Experience", "Windows Shell Experience Host",
    "Setup", "Microsoft Text Input Application",
}


def list_windows(exclude_hwnd: int = 0, min_w: int = 220, min_h: int = 160):
    """Real, user-facing windows worth mirroring — newest/topmost first."""
    found = []

    def _cb(h, _):
        if h == exclude_hwnd or not win32gui.IsWindowVisible(h):
            return True
        title = win32gui.GetWindowText(h)
        if not title or title in _SKIP_TITLES:
            return True
        try:
            cls = win32gui.GetClassName(h)
        except Exception:
            cls = ""
        if cls in _SKIP_CLASSES:
            return True
        # skip tool windows and cloaked UWP shells
        ex = win32gui.GetWindowLong(h, win32con.GWL_EXSTYLE)
        if ex & win32con.WS_EX_TOOLWINDOW:
            return True
        try:
            cloaked = ctypes.c_int(0)
            # DWMWA_CLOAKED = 14
            ctypes.windll.dwmapi.DwmGetWindowAttribute(
                HWND(h), 14, byref(cloaked), ctypes.sizeof(cloaked))
            if cloaked.value:
                return True
        except Exception:
            pass
        l, t, r, b = win32gui.GetWindowRect(h)
        if (r - l) < min_w or (b - t) < min_h:
            return True
        found.append((h, title))
        return True

    win32gui.EnumWindows(_cb, None)
    return found
