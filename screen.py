"""
screen.py — Project G.I.L.
Windows screen awareness — active window + running apps.
No extra dependencies (ctypes is stdlib, psutil already installed).
"""

import ctypes
import psutil

_SYSTEM_PROCS = {
    "system", "registry", "smss", "csrss", "wininit", "services", "lsass",
    "svchost", "fontdrvhost", "dwm", "winlogon", "spoolsv", "msiexec",
    "conhost", "dllhost", "sihost", "taskhostw", "runtimebroker",
    "searchindexer", "wmiprvse", "ctfmon", "securityhealthservice",
    "antimalware service executable", "searchhost", "startmenuexperiencehost",
    "textinputhost", "shellexperiencehost", "explorer",
}


def get_active_window_title() -> str:
    """Title of the window currently in focus."""
    try:
        hwnd   = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception:
        return ""


def get_running_apps() -> list[str]:
    """User-facing processes — system noise filtered out."""
    seen: set[str] = set()
    apps: list[str] = []
    try:
        for proc in psutil.process_iter(["name"]):
            raw  = proc.info.get("name") or ""
            name = raw.replace(".exe", "").strip()
            low  = name.lower()
            if name and len(name) > 2 and low not in _SYSTEM_PROCS and low not in seen:
                seen.add(low)
                apps.append(name)
    except Exception:
        pass
    return sorted(apps)[:20]


def get_desktop_projects() -> list[str]:
    """Folders on the Desktop — these are the user's known projects."""
    from pathlib import Path
    desktop = Path.home() / "Desktop"
    try:
        return sorted(
            d.name for d in desktop.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        )
    except Exception:
        return []


def get_screen_context() -> str:
    """Formatted string injected into every LLM call so GIL knows what's running."""
    active   = get_active_window_title()
    apps     = get_running_apps()
    projects = get_desktop_projects()

    lines = []
    if active:
        lines.append(f"Active window: {active}")
    if apps:
        lines.append(f"Running apps: {', '.join(apps[:14])}")
    if projects:
        lines.append(f"Desktop projects: {', '.join(projects)}")

    return "\n".join(lines) if lines else ""
