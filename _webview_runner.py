"""
_webview_runner.py — Project G.I.L.
Opens the 3D studio HTML as a standalone app window (no browser chrome).
Uses Edge/Chrome in --app mode: dedicated window, no tabs, no address bar.
Called by studio3d.py as: python _webview_runner.py <file_url> <title> [width] [height]
"""
import sys
import subprocess

def main():
    if len(sys.argv) < 2:
        sys.exit(1)

    file_url = sys.argv[1]
    w        = sys.argv[3] if len(sys.argv) > 3 else "1400"
    h        = sys.argv[4] if len(sys.argv) > 4 else "900"

    # Edge/Chrome --app mode: standalone window, no browser UI at all
    for exe in [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "msedge",
        "chrome",
    ]:
        try:
            subprocess.Popen(
                [exe, f"--app={file_url}", f"--window-size={w},{h}",
                 "--disable-extensions", "--no-default-browser-check"],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return
        except (FileNotFoundError, OSError):
            continue

    # Last resort: default browser
    import webbrowser
    webbrowser.open(file_url)

if __name__ == "__main__":
    main()
