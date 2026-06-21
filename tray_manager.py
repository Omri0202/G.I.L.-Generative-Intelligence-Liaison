"""
tray_manager.py — G.I.L.
System tray icon setup. Hides window on close instead of quitting.
"""

import threading


def start_tray(window) -> None:
    """
    Launch the system-tray icon in a daemon thread.
    Requires: pip install pystray Pillow
    Gracefully skips if unavailable.
    """
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        print("[G.I.L.] pystray/Pillow not installed — tray icon disabled.")
        return

    from gui import _tray_image
    img = _tray_image()
    if img is None:
        img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([2,  2,  62, 62], fill=(10, 10, 15))
        draw.ellipse([6,  6,  58, 58], outline=(0, 191, 255), width=3)
        draw.ellipse([24, 24, 40, 40], fill=(0, 191, 255))

    def _show(icon, _):
        window.after(0, window.show_window)

    def _settings(icon, _):
        window.after(0, window.open_settings)

    def _exit(icon, _):
        icon.stop()
        window.after(0, window._do_quit)

    menu = pystray.Menu(
        pystray.MenuItem("Show G.I.L.", _show, default=True),
        pystray.MenuItem("Settings",    _settings),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit",        _exit),
    )

    icon = pystray.Icon("GIL", img, "G.I.L. — Online", menu)
    threading.Thread(target=icon.run, daemon=True, name="GIL-Tray").start()

    # Closing the window hides to tray rather than quitting
    window.protocol("WM_DELETE_WINDOW", window.withdraw)
    print("[G.I.L.] Tray icon active. Close button now hides to tray.")
