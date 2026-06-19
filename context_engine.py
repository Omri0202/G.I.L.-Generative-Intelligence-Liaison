"""
context_engine.py — Project G.I.L.
Deep screen & system context awareness.
Polls every 2s in a background thread, parses active window title
for app/file/URL/CWD and feeds into every brain call.
"""

import ctypes
import ctypes.wintypes
import re
import threading
import time
from pathlib import Path
from typing import Callable

import psutil

# ── Constants ───────────────────────────────────────────────────────────────────

_POLL_INTERVAL = 2.0   # seconds between polls

_BROWSER_APPS = {"chrome", "msedge", "firefox", "opera", "brave", "vivaldi"}
_TERMINAL_APPS = {"windowsterminal", "cmd", "powershell", "pwsh", "alacritty", "wt"}
_EDITOR_APPS = {
    "code":           "VS Code",
    "pycharm64":      "PyCharm",
    "idea64":         "IntelliJ IDEA",
    "devenv":         "Visual Studio",
    "notepad++":      "Notepad++",
    "sublime_text":   "Sublime Text",
    "atom":           "Atom",
    "cursor":         "Cursor",
}
_BROWSER_NAMES = {
    "chrome": "Chrome",
    "msedge": "Edge",
    "firefox": "Firefox",
    "opera": "Opera",
    "brave": "Brave",
}

_SYSTEM_PROCS = {
    "system","registry","smss","csrss","wininit","services","lsass",
    "svchost","fontdrvhost","dwm","winlogon","spoolsv","msiexec",
    "conhost","dllhost","sihost","taskhostw","runtimebroker",
    "searchindexer","wmiprvse","ctfmon","securityhealthservice",
    "startmenuexperiencehost","textinputhost","shellexperiencehost","explorer",
}


# ── State ────────────────────────────────────────────────────────────────────────

_state: dict = {
    "app":          "",   # human-readable app name
    "app_proc":     "",   # process name
    "title":        "",   # raw window title
    "file":         "",   # active file (extracted)
    "project":      "",   # project folder name
    "url":          "",   # browser URL / tab title
    "cwd":          "",   # terminal CWD
    "open_apps":    [],   # running user-facing apps
    "clipboard":    "",   # last clipboard text
    "updated_at":   0.0,
}
_prev_title = ""
_lock        = threading.Lock()
_listeners: list[Callable] = []    # called when context changes


# ── Win32 helpers ─────────────────────────────────────────────────────────────

_user32 = ctypes.windll.user32

def _get_foreground_title() -> str:
    hwnd   = _user32.GetForegroundWindow()
    length = _user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _get_foreground_pid() -> int:
    hwnd  = _user32.GetForegroundWindow()
    pid   = ctypes.wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _get_proc_name(pid: int) -> str:
    try:
        return psutil.Process(pid).name().replace(".exe", "").lower()
    except Exception:
        return ""


def _read_clipboard() -> str:
    CF_UNICODETEXT = 13
    try:
        if not _user32.OpenClipboard(None):
            return ""
        h = ctypes.windll.kernel32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            _user32.CloseClipboard()
            return ""
        ptr = ctypes.windll.kernel32.GlobalLock(h)
        if not ptr:
            _user32.CloseClipboard()
            return ""
        try:
            text = ctypes.wstring_at(ptr)
        finally:
            ctypes.windll.kernel32.GlobalUnlock(h)
        _user32.CloseClipboard()
        return text[:400]   # cap at 400 chars for safety
    except Exception:
        try:
            _user32.CloseClipboard()
        except Exception:
            pass
        return ""


def _get_running_user_apps() -> list[str]:
    seen: set[str] = set()
    apps: list[str] = []
    try:
        for proc in psutil.process_iter(["name"]):
            raw  = (proc.info.get("name") or "").replace(".exe", "").strip()
            low  = raw.lower()
            if raw and len(raw) > 2 and low not in _SYSTEM_PROCS and low not in seen:
                seen.add(low)
                apps.append(raw)
    except Exception:
        pass
    return sorted(apps)[:22]


# ── Title parsers ─────────────────────────────────────────────────────────────

def _parse_vscode(title: str) -> dict:
    # "● filename.py — folder — Visual Studio Code"
    # "filename.py — folder — Visual Studio Code"
    title = title.replace("●", "").strip()
    parts = re.split(r"\s[—–-]{1,2}\s", title)
    result = {"file": "", "project": ""}
    if len(parts) >= 2:
        result["file"]    = parts[0].strip()
        result["project"] = parts[1].strip() if len(parts) >= 3 else parts[1].strip()
    elif parts:
        result["file"] = parts[0].strip()
    return result


def _parse_browser(title: str, app_proc: str) -> dict:
    # "Page Title - App Name" or "Page Title | App Name"
    result = {"url": "", "file": ""}
    # strip browser suffix
    for suffix in (" - Google Chrome", " - Microsoft Edge", " — Mozilla Firefox",
                   " - Firefox", " - Opera", " - Brave"):
        if title.endswith(suffix):
            title = title[:-len(suffix)].strip()
            break
    result["url"] = title
    return result


def _parse_terminal(title: str) -> dict:
    result = {"cwd": "", "file": ""}
    # Windows Terminal / PowerShell shows CWD as title or "PS C:\path>"
    cwd_match = re.search(r"[A-Za-z]:\\[^\r\n>]*", title)
    if cwd_match:
        result["cwd"] = cwd_match.group(0).rstrip()
    elif title and title not in {"Windows PowerShell", "Command Prompt", "PowerShell"}:
        result["cwd"] = title.strip()
    return result


def _parse_notepad_plus(title: str) -> dict:
    # "filename.py - Notepad++"
    result = {"file": ""}
    parts = title.split(" - Notepad++")
    if parts:
        result["file"] = parts[0].strip().lstrip("*").strip()
    return result


def _parse_pycharm(title: str) -> dict:
    # "filename.py [project] - PyCharm"  or  "project - [file.py] - PyCharm"
    result = {"file": "", "project": ""}
    title = re.sub(r"\s[-–—]\s*(PyCharm|IntelliJ IDEA|PyCharm Professional).*$", "", title).strip()
    # [project_name]
    bracket = re.search(r"\[([^\]]+)\]", title)
    if bracket:
        result["project"] = bracket.group(1)
        title = title.replace(bracket.group(0), "").strip(" -–—")
    result["file"] = title.strip()
    return result


def _parse_generic(title: str, app_name: str) -> dict:
    """For apps we don't specifically handle — try to strip app name suffix."""
    result = {"file": "", "project": ""}
    # strip " — AppName" suffix
    stripped = re.sub(r"\s[-–—]\s*" + re.escape(app_name) + r".*$", "", title, flags=re.I).strip()
    if stripped and stripped != title:
        result["file"] = stripped
    return result


# ── Main poll ─────────────────────────────────────────────────────────────────

def _poll() -> None:
    global _prev_title

    title    = _get_foreground_title()
    pid      = _get_foreground_pid()
    proc     = _get_proc_name(pid)
    apps     = _get_running_user_apps()

    # Determine app category
    app_name = ""
    file_    = ""
    project_ = ""
    url_     = ""
    cwd_     = ""

    if proc in _EDITOR_APPS:
        app_name = _EDITOR_APPS[proc]
        if proc == "code" or proc == "cursor":
            parsed   = _parse_vscode(title)
        elif proc in ("pycharm64", "idea64"):
            parsed   = _parse_pycharm(title)
        else:
            parsed   = _parse_generic(title, app_name)
        file_    = parsed.get("file", "")
        project_ = parsed.get("project", "")

    elif proc in _BROWSER_APPS:
        app_name = _BROWSER_NAMES.get(proc, proc.capitalize())
        parsed   = _parse_browser(title, proc)
        url_     = parsed.get("url", "")

    elif proc in _TERMINAL_APPS or "terminal" in proc or "powershell" in proc:
        app_name = "Terminal"
        parsed   = _parse_terminal(title)
        cwd_     = parsed.get("cwd", "")

    elif proc == "notepad++":
        app_name = "Notepad++"
        parsed   = _parse_notepad_plus(title)
        file_    = parsed.get("file", "")

    else:
        # Generic: human-readable from proc name
        app_name = proc.replace("_", " ").replace("-", " ").title() if proc else ""
        if " — " in title or " - " in title:
            parsed = _parse_generic(title, app_name)
            file_  = parsed.get("file", "")

    # Clipboard (only re-read if title changed, to avoid hammering)
    clip = _state.get("clipboard", "")
    if title != _prev_title:
        try:
            clip = _read_clipboard()
        except Exception:
            pass

    changed = (title != _prev_title)
    _prev_title = title

    with _lock:
        _state.update({
            "app":        app_name,
            "app_proc":   proc,
            "title":      title,
            "file":       file_,
            "project":    project_,
            "url":        url_,
            "cwd":        cwd_,
            "open_apps":  apps,
            "clipboard":  clip,
            "updated_at": time.time(),
        })

    if changed:
        for fn in list(_listeners):
            try:
                fn(dict(_state))
            except Exception:
                pass


def _run_loop() -> None:
    while True:
        try:
            _poll()
        except Exception as exc:
            print(f"[G.I.L. CONTEXT] Poll error: {exc}")
        time.sleep(_POLL_INTERVAL)


# ── Public API ────────────────────────────────────────────────────────────────

_thread: threading.Thread | None = None


def start() -> None:
    """Start background polling thread (idempotent)."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_run_loop, daemon=True, name="GIL-Context")
    _thread.start()
    print("[G.I.L. CONTEXT] Background context engine started.")


def on_context_changed(fn: Callable) -> None:
    """Register callback fired when active window changes."""
    _listeners.append(fn)


def get_active_context() -> dict:
    """Return a copy of the latest context snapshot."""
    with _lock:
        return dict(_state)


def get_screen_context() -> str:
    """
    Formatted context string injected into every LLM brain call.
    Replaces the old screen.get_screen_context().
    """
    with _lock:
        s = dict(_state)

    lines = []

    if s["title"]:
        lines.append(f"Active window: {s['title']}")
    if s["app"]:
        lines.append(f"App: {s['app']}")
    if s["file"]:
        lines.append(f"Open file: {s['file']}")
    if s["project"]:
        lines.append(f"Project: {s['project']}")
    if s["url"]:
        lines.append(f"Browser tab: {s['url']}")
    if s["cwd"]:
        lines.append(f"Terminal CWD: {s['cwd']}")
    if s["open_apps"]:
        lines.append(f"Running: {', '.join(s['open_apps'][:12])}")
    if s["clipboard"] and len(s["clipboard"]) > 4:
        clip_preview = s["clipboard"][:120].replace("\n", " ")
        lines.append(f"Clipboard: {clip_preview}")

    return "\n".join(lines) if lines else "No context available."


def get_active_window_title() -> str:
    """Compatibility shim for code that imports this from screen.py."""
    with _lock:
        return _state.get("title", "")


def get_running_apps() -> list[str]:
    """Compatibility shim."""
    with _lock:
        return list(_state.get("open_apps", []))


def get_desktop_projects() -> list[str]:
    desktop = Path.home() / "Desktop"
    try:
        return sorted(d.name for d in desktop.iterdir()
                      if d.is_dir() and not d.name.startswith("."))
    except Exception:
        return []
