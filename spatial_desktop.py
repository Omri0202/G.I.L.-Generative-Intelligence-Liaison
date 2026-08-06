"""
spatial_desktop.py — your real Windows desktop, as reachable objects.

Mirrored panels only ever show what is ALREADY running, which left the workspace
unable to reach anything you hadn't opened yet. This enumerates the actual
desktop — the same shortcuts, files and folders Explorer draws on your wallpaper
— so they can be hovered and opened with the same gestures.

Why items rather than a mirror of the desktop window:
The desktop IS mirrorable (Progman composites fine), but a mirror is a single
flat picture: you cannot hover one icon within it, because there are no separate
objects to hover. Enumerating the items gives each one its own hit-box, its own
hover, and its own depth.

Icons come from the shell association layer, so a .fbx shows its 3D-viewer icon
and a shortcut shows its target's icon — exactly what Explorer shows. An earlier
version used ExtractIconEx, which reads icons embedded IN the file itself; a
.lnk has none (its icon lives in the target) and a data file has none at all, so
it returned nothing for practically every desktop item.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import POINTER, byref, c_int, c_void_p, sizeof, wintypes
from pathlib import Path

# ── shell / GDI plumbing ─────────────────────────────────────────────────────
_shell32 = ctypes.windll.shell32
_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32
_comctl = ctypes.windll.comctl32

SHGFI_ICON = 0x000000100
SHGFI_LARGEICON = 0x000000000
SHGFI_SYSICONINDEX = 0x000004000
SHGFI_USEFILEATTRIBUTES = 0x000000010

#: system image lists, smallest to largest. JUMBO is 256px on modern Windows.
SHIL_LARGE, SHIL_EXTRALARGE, SHIL_JUMBO = 0, 2, 4

_ILD_TRANSPARENT = 0x1


class _SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HANDLE),
        ("iIcon", c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


class _ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER),
                ("bmiColors", wintypes.DWORD * 3)]


class _BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG),
                ("bmHeight", wintypes.LONG), ("bmWidthBytes", wintypes.LONG),
                ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                ("bmBits", c_void_p)]


_shell32.SHGetFileInfoW.restype = c_void_p
_shell32.SHGetFileInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                    POINTER(_SHFILEINFOW), wintypes.UINT,
                                    wintypes.UINT]

# Every handle-taking call below needs an explicit prototype. Without argtypes
# ctypes marshals a Python int as c_int, so any GDI handle that happens to land
# above 2**31 raised OverflowError and the icon was silently dropped. Handle
# values vary run to run, so this failed on a random handful of items each time
# rather than on any particular one.
_gdi32.GetObjectW.argtypes = [wintypes.HANDLE, c_int, c_void_p]
_gdi32.GetObjectW.restype = c_int
_gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HANDLE, wintypes.UINT,
                             wintypes.UINT, c_void_p, POINTER(_BITMAPINFO),
                             wintypes.UINT]
_gdi32.GetDIBits.restype = c_int
_user32.GetIconInfo.argtypes = [wintypes.HANDLE, POINTER(_ICONINFO)]
_user32.GetIconInfo.restype = wintypes.BOOL
_user32.DestroyIcon.argtypes = [wintypes.HANDLE]
_user32.DestroyIcon.restype = wintypes.BOOL
_user32.GetDC.argtypes = [wintypes.HWND]
_user32.GetDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.ReleaseDC.restype = c_int

# {46EB5926-582E-4017-9FDF-E8998DAA0950} — IID_IImageList
_IID_IImageList = (ctypes.c_byte * 16)(
    0x26, 0x59, 0xEB, 0x46, 0x2E, 0x58, 0x17, 0x40,
    0x9F, 0xDF, 0xE8, 0x99, 0x8D, 0xAA, 0x09, 0x50)


def _image_list(which: int):
    """A system image list as a raw COM pointer, or None."""
    try:
        p = c_void_p()
        hr = _shell32.SHGetImageList(which, byref(_IID_IImageList), byref(p))
        return p if hr == 0 and p else None
    except Exception:
        return None


def _il_get_icon(il, index: int):
    """IImageList::GetIcon — vtable slot 10 (3 IUnknown + 7 IImageList)."""
    try:
        vtbl = ctypes.cast(il, POINTER(c_void_p))[0]
        fn_addr = ctypes.cast(vtbl, POINTER(c_void_p))[10]
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, c_int,
                                   wintypes.UINT, POINTER(wintypes.HANDLE))
        hicon = wintypes.HANDLE()
        if proto(fn_addr)(il, index, _ILD_TRANSPARENT, byref(hicon)) == 0:
            return hicon.value
    except Exception:
        pass
    return None


def _icon_to_image(hicon):
    """HICON → PIL RGBA, alpha intact, or None."""
    try:
        from PIL import Image
    except Exception:
        return None

    info = _ICONINFO()
    if not _user32.GetIconInfo(wintypes.HANDLE(hicon), byref(info)):
        return None

    bm = _BITMAP()
    img = None
    try:
        if not info.hbmColor:
            return None
        _gdi32.GetObjectW(info.hbmColor, sizeof(_BITMAP), byref(bm))
        w, h = int(bm.bmWidth), int(bm.bmHeight)
        if w <= 0 or h <= 0:
            return None

        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h          # negative → top-down rows
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0      # BI_RGB

        hdc = _user32.GetDC(None)
        buf = ctypes.create_string_buffer(w * h * 4)
        got = _gdi32.GetDIBits(hdc, info.hbmColor, 0, h, buf, byref(bmi), 0)
        _user32.ReleaseDC(None, hdc)
        if not got:
            return None

        img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1)

        # Legacy 24-bit icons carry no alpha; their transparency lives in the
        # 1-bit mask (set bit = transparent). Without this they render as a
        # solid black tile.
        if not img.getchannel("A").getbbox():
            img = _apply_mask(img, info.hbmMask, w, h)
    finally:
        for h_ in (info.hbmColor, info.hbmMask):
            if h_:
                _gdi32.DeleteObject(h_)
    return img


def _apply_mask(img, hbm_mask, w, h):
    from PIL import Image
    if not hbm_mask:
        return img.convert("RGB").convert("RGBA")
    bmi = _BITMAPINFO()
    bmi.bmiHeader.biSize = sizeof(_BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    hdc = _user32.GetDC(None)
    mbuf = ctypes.create_string_buffer(w * h * 4)
    ok = _gdi32.GetDIBits(hdc, hbm_mask, 0, h, mbuf, byref(bmi), 0)
    _user32.ReleaseDC(None, hdc)
    if not ok:
        return img.convert("RGB").convert("RGBA")
    mask = Image.frombuffer("RGBA", (w, h), mbuf.raw, "raw", "BGRA", 0, 1)
    # mask is white where transparent → invert into an alpha channel
    alpha = mask.convert("L").point(lambda v: 0 if v > 127 else 255)
    out = img.convert("RGB").convert("RGBA")
    out.putalpha(alpha)
    return out


def _trim(img):
    """JUMBO entries centre a small icon in a 256px field — crop the padding."""
    box = img.getchannel("A").getbbox()
    if box and (box[2] - box[0]) > 4 and (box[3] - box[1]) > 4:
        return img.crop(box)
    return img


_icon_cache: dict[tuple, object] = {}


def icon_for(path: str, size: int = 96):
    """The icon Explorer draws for this path, as a PIL RGBA image, or None."""
    key = (path.lower(), size)
    if key in _icon_cache:
        return _icon_cache[key]

    img = None
    try:
        from PIL import Image
        info = _SHFILEINFOW()
        # SYSICONINDEX gives an index into the system image lists, which is the
        # only route to icons above 48px.
        res = _shell32.SHGetFileInfoW(path, 0, byref(info), sizeof(info),
                                      SHGFI_SYSICONINDEX)
        if res:
            for which in (SHIL_JUMBO, SHIL_EXTRALARGE, SHIL_LARGE):
                il = _image_list(which)
                if not il:
                    continue
                hicon = _il_get_icon(il, info.iIcon)
                if not hicon:
                    continue
                img = _icon_to_image(hicon)
                _user32.DestroyIcon(wintypes.HANDLE(hicon))
                if img is not None:
                    img = _trim(img)
                    # a JUMBO slot holding a 32px icon upscales to mush; drop to
                    # the next list instead
                    if img.size[0] >= 40 or which == SHIL_LARGE:
                        break
                    img = None

        if img is None:                       # last resort: plain 32px icon
            info2 = _SHFILEINFOW()
            if _shell32.SHGetFileInfoW(path, 0, byref(info2), sizeof(info2),
                                       SHGFI_ICON | SHGFI_LARGEICON):
                img = _icon_to_image(info2.hIcon)
                _user32.DestroyIcon(info2.hIcon)

        if img is not None:
            img = img.resize((size, size), Image.LANCZOS)
    except Exception:
        img = None

    _icon_cache[key] = img
    return img


# ── the desktop itself ───────────────────────────────────────────────────────
#: Explorer hides these; showing them would be surprising.
_HIDDEN = {"desktop.ini", "thumbs.db"}


def desktop_roots() -> list[Path]:
    """Both folders Explorer composites into one desktop."""
    out = []
    for env, sub in (("USERPROFILE", "Desktop"), ("PUBLIC", "Desktop")):
        base = os.environ.get(env)
        if base:
            p = Path(base) / sub
            if p.is_dir():
                out.append(p)
    return out


def display_name(path: Path) -> str:
    """What Explorer calls it — shortcuts lose their .lnk, files keep the rest."""
    if path.suffix.lower() == ".lnk":
        return path.stem
    return path.name


def list_desktop(limit: int = 60) -> list[dict]:
    """Every item on your desktop, ordered the way Explorer orders them."""
    items: dict[str, dict] = {}
    for root in desktop_roots():
        try:
            entries = list(root.iterdir())
        except (OSError, PermissionError):
            continue
        for p in entries:
            low = p.name.lower()
            if low in _HIDDEN or low in items:
                continue
            try:
                if p.stat().st_file_attributes & 0x2:    # FILE_ATTRIBUTE_HIDDEN
                    continue
            except (OSError, AttributeError):
                pass
            try:
                is_dir = p.is_dir()
            except OSError:
                is_dir = False
            items[low] = {
                "name": display_name(p),
                "path": str(p),
                "dir": is_dir,
                "kind": "folder" if is_dir else p.suffix.lower().lstrip(".") or "file",
            }

    ordered = sorted(items.values(), key=lambda i: (not i["dir"], i["name"].lower()))
    return ordered[:limit]


def open_item(path: str) -> bool:
    """Open it exactly as a double-click would."""
    try:
        os.startfile(path)
        return True
    except Exception:
        try:
            subprocess.Popen(["cmd", "/c", "start", "", path], shell=False)
            return True
        except Exception:
            return False
