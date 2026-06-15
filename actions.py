"""
actions.py — Project G.I.L.
System automation: dynamic app launcher, credential-based sign-in,
web navigation, hardware diagnostics, and screen capture.
"""

import os
import glob
import json
import time
import ctypes
import winreg
import subprocess
import webbrowser
import urllib.parse
import psutil
from datetime import datetime
from pathlib import Path


# ── Secure clipboard helpers (avoids keystroke logging) ───────────────────────

def _set_clipboard(text: str) -> None:
    CF_UNICODETEXT = 13
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    encoded = (text + "\0").encode("utf-16-le")
    h = k32.GlobalAlloc(0x0042, len(encoded))
    ptr = k32.GlobalLock(h)
    ctypes.memmove(ptr, encoded, len(encoded))
    k32.GlobalUnlock(h)
    u32.OpenClipboard(None)
    u32.EmptyClipboard()
    u32.SetClipboardData(CF_UNICODETEXT, h)
    u32.CloseClipboard()


def _clear_clipboard() -> None:
    u32 = ctypes.windll.user32
    u32.OpenClipboard(None)
    u32.EmptyClipboard()
    u32.CloseClipboard()


# ── Personality helpers ───────────────────────────────────────────────────────

def _stubborn_speak(msg: str) -> None:
    """Speak a personality-driven error message without crashing if voice is unavailable."""
    try:
        from voice import speak
        speak(msg)
    except Exception:
        print(f"[G.I.L. ACTIONS] {msg}")


# ── Dynamic app index ─────────────────────────────────────────────────────────

_APP_INDEX: dict[str, str] = {}
_INDEX_BUILT = False


def _build_app_index() -> None:
    global _APP_INDEX, _INDEX_BUILT
    index = {}

    start_dirs = [
        os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%AppData%\Microsoft\Windows\Start Menu\Programs"),
    ]
    for base in start_dirs:
        for lnk in glob.glob(os.path.join(base, "**", "*.lnk"), recursive=True):
            name = os.path.splitext(os.path.basename(lnk))[0].lower()
            index[name] = lnk

    reg_key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, reg_key) as key:
                i = 0
                while True:
                    try:
                        sub  = winreg.EnumKey(key, i)
                        name = sub.lower().replace(".exe", "")
                        with winreg.OpenKey(key, sub) as sk:
                            path, _ = winreg.QueryValueEx(sk, "")
                            if path and os.path.exists(path):
                                index[name] = path
                        i += 1
                    except OSError:
                        break
        except OSError:
            pass

    unreg = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, unreg) as key:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(key, i)
                    with winreg.OpenKey(key, sub) as sk:
                        try:
                            name, _ = winreg.QueryValueEx(sk, "DisplayName")
                            loc,  _ = winreg.QueryValueEx(sk, "InstallLocation")
                            if name and loc and os.path.isdir(loc):
                                index[name.lower()] = loc
                        except OSError:
                            pass
                    i += 1
                except OSError:
                    break
    except OSError:
        pass

    _APP_INDEX   = index
    _INDEX_BUILT = True
    print(f"[G.I.L. ACTIONS] App index built: {len(index)} entries.")


def _find_app(target: str) -> str | None:
    global _INDEX_BUILT
    if not _INDEX_BUILT:
        _build_app_index()

    t = target.lower().strip()

    if t in _APP_INDEX:
        return _APP_INDEX[t]

    for name, path in _APP_INDEX.items():
        if name.startswith(t) or t.startswith(name):
            return path

    for name, path in _APP_INDEX.items():
        if t in name or name in t:
            return path

    words = t.split()
    for name, path in _APP_INDEX.items():
        if any(w in name for w in words):
            return path

    return None


# ── Web app fallbacks ─────────────────────────────────────────────────────────

WEB_FALLBACKS = {
    "whatsapp":  "https://web.whatsapp.com",
    "gmail":     "https://mail.google.com",
    "youtube":   "https://youtube.com",
    "spotify":   "https://open.spotify.com",
    "netflix":   "https://netflix.com",
    "instagram": "https://instagram.com",
    "twitter":   "https://twitter.com",
    "x":         "https://twitter.com",
    "discord":   "https://discord.com/app",
    "github":    "https://github.com",
    "reddit":    "https://reddit.com",
    "linkedin":  "https://linkedin.com",
}

SIGNIN_URLS = {
    "gmail":     "https://accounts.google.com/signin",
    "google":    "https://accounts.google.com/signin",
    "youtube":   "https://accounts.google.com/signin",
    "github":    "https://github.com/login",
    "discord":   "https://discord.com/login",
    "instagram": "https://www.instagram.com/accounts/login/",
    "twitter":   "https://twitter.com/login",
    "x":         "https://twitter.com/login",
    "netflix":   "https://www.netflix.com/login",
    "spotify":   "https://accounts.spotify.com/login",
    "outlook":   "https://login.live.com",
    "microsoft": "https://login.live.com",
    "linkedin":  "https://www.linkedin.com/login",
}


# ── Public dispatch ───────────────────────────────────────────────────────────

# ── LG TV control ─────────────────────────────────────────────────────────────
_TV_IP       = "192.168.68.65"
_TV_MAC      = "64:CB:E9:BA:3F:DE"
_TV_KEY_PATH = Path(__file__).parent / "data" / "lg_tv_key.json"


def _wake_on_lan(mac: str) -> None:
    import socket
    mac_bytes = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    magic     = b"\xff" * 6 + mac_bytes * 16
    targets   = ["<broadcast>", "192.168.68.255", _TV_IP]
    for port in (9, 7):
        for addr in targets:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    s.sendto(magic, (addr, port))
            except Exception:
                pass


async def _tv_exec(command: str) -> str:
    from aiowebostv import WebOsClient

    client_key = None
    if _TV_KEY_PATH.exists():
        with open(_TV_KEY_PATH) as f:
            client_key = json.load(f).get("client_key")

    client = WebOsClient(_TV_IP, client_key=client_key)
    await client.connect()

    if client.client_key and client.client_key != client_key:
        _TV_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_TV_KEY_PATH, "w") as f:
            json.dump({"client_key": client.client_key}, f)

    cmd = command.lower().strip()
    try:
        if cmd in ("on", "power on", "turn on", "wake"):
            await client.disconnect()
            _wake_on_lan(_TV_MAC)
            return "Waking the TV."

        if cmd in ("off", "power off", "turn off"):
            await client.power_off()
            return "TV is off."

        if cmd == "mute":
            await client.mute(True)
            return "TV muted."

        if cmd == "unmute":
            await client.mute(False)
            return "TV unmuted."

        if cmd == "volume up":
            await client.volume_up()
            return "Volume up."

        if cmd == "volume down":
            await client.volume_down()
            return "Volume down."

        if cmd.startswith("volume up ") or cmd.startswith("volume down "):
            parts = cmd.split()
            try:
                step      = int(parts[-1])
                direction = 1 if parts[1] == "up" else -1
                info      = await client.get_volume()
                current   = info.get("volume", 0) if isinstance(info, dict) else int(info)
                new_vol   = max(0, min(100, current + direction * step))
                await client.set_volume(new_vol)
                return f"Volume {'up' if direction > 0 else 'down'} to {new_vol}."
            except Exception as e:
                return f"Volume adjust failed: {e}"

        if cmd.startswith("volume "):
            try:
                vol = int(cmd.split()[-1])
                await client.set_volume(vol)
                return f"Volume set to {vol}."
            except ValueError:
                return "Couldn't parse volume level."

        if cmd.startswith("hdmi"):
            parts = cmd.split()
            num   = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
            inputs = await client.get_inputs()
            match  = next(
                (i for i in inputs
                 if f"HDMI_{num}" in i.get("appId", "") or
                    f"HDMI {num}" in i.get("label", "")),
                None
            )
            if match:
                await client.set_input(match["inputId"])
                return f"Switched to HDMI {num}."
            return f"HDMI {num} not found."

        if "netflix" in cmd:
            await client.launch_app("netflix")
            return "Opening Netflix."

        if "youtube" in cmd:
            await client.launch_app("youtube.leanback.v4")
            return "Opening YouTube."

        return f"Unknown TV command: {command}"
    finally:
        await client.disconnect()


def tv_control(command: str) -> str:
    import asyncio
    try:
        return asyncio.run(_tv_exec(command))
    except Exception as e:
        return f"TV error: {e}"


def execute_action(action: str, target: str) -> str | None:
    handlers = {
        "open_app":        lambda: open_app(target),
        "open_url":        lambda: open_url(target),
        "system_vitals":   lambda: _handle_vitals(),
        "web_search":      lambda: open_web_search(target),
        "sign_in":         lambda: sign_in(target),
        "take_screenshot": lambda: _handle_screenshot(),
        "build":           lambda: build_project(*_split_target(target)),
        "open_terminal":   lambda: open_terminal(target),
        # Window management
        "focus_window":    lambda: focus_window(target),
        "arrange_windows": lambda: arrange_windows(*_split_target(target)),
        "close_window":    lambda: close_window(target),
        "minimize_all":    lambda: minimize_all(),
        "maximize_window": lambda: maximize_window(target),
        # File system
        "open_file":       lambda: open_file(target),
        "read_file":       lambda: read_file(target),
        "list_directory":  lambda: list_directory(target),
        "find_file":       lambda: find_file(target),
        # Clipboard
        "set_clipboard":   lambda: set_clipboard_text(target),
        "get_clipboard":   lambda: get_clipboard_text(),
        # LG TV
        "tv":              lambda: tv_control(target),
        # Modes
        "set_mode":        lambda: _mode_control(target),
        # PC power & volume
        "pc":              lambda: _pc_power(target),
        "pc_sleep":        lambda: _pc_power("sleep"),
        "pc_lock":         lambda: _pc_power("lock"),
        "pc_restart":      lambda: _pc_power("restart"),
        "pc_shutdown":     lambda: _pc_power("shutdown"),
        "pc_volume":       lambda: _pc_vol(target),
        # Weather
        "weather":         lambda: _weather(target),
        # Reminders
        "reminder":        lambda: _reminder(target),
        "list_reminders":  lambda: _list_reminders(),
        # Notes & clipboard history
        "note":            lambda: _save_note(target),
        "list_notes":      lambda: _list_notes(),
        "clip_history":    lambda: _clip_history(),
        # Spotify
        "spotify":         lambda: _spotify(target),
        # Morning briefing
        "briefing":        lambda: _briefing(target),
        # Location & nearby
        "nearby":          lambda: _nearby(target),
        "directions":      lambda: _directions(target),
        "my_location":     lambda: _my_location(),
        "food_delivery":   lambda: _food_delivery(target),
        # News
        "news":            lambda: _news(target),
        "open_article":    lambda: _open_article(target),
        # Calendar
        "calendar":        lambda: _calendar(target),
        "add_event":       lambda: _add_event(target),
        # Camera vision
        "look":            lambda: _look(target),
    }
    handler = handlers.get(action)
    if handler:
        return handler()
    print(f"[G.I.L. ACTIONS] Unknown action: {action}")
    return None


def _split_target(target: str) -> tuple[str, str]:
    parts = [p.strip() for p in target.split("|", 1)]
    return parts[0], (parts[1] if len(parts) > 1 else "")


# ── App launcher ──────────────────────────────────────────────────────────────

def open_app(target: str) -> str:
    path = _find_app(target)

    if path:
        try:
            os.startfile(path)
            print(f"[G.I.L. ACTIONS] Opened: {path}")
            return f"Launched {target}."
        except Exception as exc:
            print(f"[G.I.L. ACTIONS] startfile failed: {exc}")
            try:
                subprocess.Popen(path, shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
                return f"Launched {target}."
            except Exception as exc2:
                print(f"[G.I.L. ACTIONS] Shell launch failed: {exc2}")

    key = target.lower().strip()
    if key in WEB_FALLBACKS:
        webbrowser.open(WEB_FALLBACKS[key])
        return f"Opened {target} in browser."

    try:
        subprocess.Popen(target, shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
        return f"Attempted to launch {target}."
    except Exception as exc:
        print(f"[G.I.L. ACTIONS] All launch methods failed: {exc}")
        _stubborn_speak(f"The system is being stubborn with {target}. I'll keep trying.")
        return f"Could not find {target}."


# ── URL opener ────────────────────────────────────────────────────────────────

def open_url(target: str) -> str:
    """Open a URL. Tries webbrowser first, then os.startfile, then shell as fallback."""
    try:
        result = webbrowser.open(target)
        if result:
            print(f"[G.I.L. ACTIONS] Opened URL: {target}")
            return f"Opened {target}"
        raise RuntimeError("webbrowser.open returned False")
    except Exception as exc:
        print(f"[G.I.L. ACTIONS] webbrowser failed ({exc}), trying shell...")
    try:
        os.startfile(target)
        return f"Opened {target}"
    except Exception as exc2:
        print(f"[G.I.L. ACTIONS] os.startfile failed ({exc2}), trying subprocess...")
    try:
        subprocess.Popen(["cmd", "/c", "start", "", target], creationflags=subprocess.CREATE_NO_WINDOW)
        return f"Opened {target}"
    except Exception as exc3:
        print(f"[G.I.L. ACTIONS] All URL open methods failed: {exc3}")
        _stubborn_speak(f"Browser failed to launch. Trying an alternative method now.")
        return f"Failed to open {target}"


# ── Sign-in ───────────────────────────────────────────────────────────────────

def sign_in(service: str) -> str:
    from credentials import get_credential
    import pyautogui

    cred = get_credential(service)
    if not cred:
        _stubborn_speak(f"I don't have credentials stored for {service}. Add them via settings or just tell me.")
        return f"No credentials stored for {service}."

    username, password = cred
    key = service.lower().strip()

    url = SIGNIN_URLS.get(key)
    if url:
        webbrowser.open(url)
    else:
        app_path = _find_app(service)
        if app_path:
            os.startfile(app_path)
        else:
            webbrowser.open(
                f"https://www.google.com/search?q={urllib.parse.quote(service + ' login')}"
            )

    print(f"[G.I.L. ACTIONS] Waiting for {service} to load...")
    time.sleep(3.5)

    try:
        _set_clipboard(username)
        pyautogui.hotkey("ctrl", "a")
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.15)
        pyautogui.press("tab")
        time.sleep(0.3)
        _set_clipboard(password)
        pyautogui.hotkey("ctrl", "a")
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.15)
        pyautogui.press("enter")
    finally:
        _clear_clipboard()

    return f"Signed in to {service}."


# ── System vitals ─────────────────────────────────────────────────────────────

def get_vitals() -> dict:
    cpu     = psutil.cpu_percent(interval=1)
    mem     = psutil.virtual_memory()
    disk    = psutil.disk_usage("/")
    battery = psutil.sensors_battery()
    return {
        "cpu_percent":     cpu,
        "ram_used_gb":     round(mem.used  / (1024 ** 3), 1),
        "ram_total_gb":    round(mem.total / (1024 ** 3), 1),
        "ram_percent":     mem.percent,
        "disk_used_gb":    round(disk.used  / (1024 ** 3), 1),
        "disk_total_gb":   round(disk.total / (1024 ** 3), 1),
        "disk_percent":    disk.percent,
        "battery_percent": battery.percent      if battery else "N/A",
        "charging":        battery.power_plugged if battery else "N/A",
        "timestamp":       datetime.now().strftime("%H:%M:%S"),
    }


def _handle_vitals() -> str:
    v = get_vitals()
    report = (
        f"\n{'=' * 42}\n"
        f"  G.I.L. SYSTEM DIAGNOSTIC — {v['timestamp']}\n"
        f"{'=' * 42}\n"
        f"  CPU        : {v['cpu_percent']}%\n"
        f"  RAM        : {v['ram_used_gb']} / {v['ram_total_gb']} GB  ({v['ram_percent']}%)\n"
        f"  Disk       : {v['disk_used_gb']} / {v['disk_total_gb']} GB  ({v['disk_percent']}%)\n"
        f"  Battery    : {v['battery_percent']}%  |  Charging: {v['charging']}\n"
        f"{'=' * 42}\n"
    )
    print(report)
    return report


# ── Screenshot ────────────────────────────────────────────────────────────────

def _handle_screenshot() -> str:
    try:
        import pyautogui
        pics = os.path.join(os.path.expanduser("~"), "Pictures")
        os.makedirs(pics, exist_ok=True)
        filename = f"GIL_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        path     = os.path.join(pics, filename)
        img      = pyautogui.screenshot()
        img.save(path)
        print(f"[G.I.L. ACTIONS] Screenshot saved: {path}")
        return filename
    except Exception as exc:
        print(f"[G.I.L. ACTIONS] Screenshot failed: {exc}")
        return ""


# ── Project builder ──────────────────────────────────────────────────────────

def build_project(description: str, project_name: str = "") -> bool:
    """Create a Desktop folder and build the project.
    Website/web requests are handled by webgen (no CMD ever opens for those).
    Code/app projects spawn claude -p in a terminal."""
    import re
    from pathlib import Path

    desktop = Path.home() / "Desktop"

    if not project_name:
        words = re.sub(r"[^a-z0-9 ]", "", description.lower()).split()
        skip  = {"a","the","an","me","build","create","make","for","with","and",
                 "to","of","i","want","need","new","app","web","page","site","that"}
        meaningful = [w for w in words if w not in skip][:5]
        project_name = "-".join(meaningful) or "gil-project"

    project_dir = desktop / project_name
    project_dir.mkdir(parents=True, exist_ok=True)

    task_file = project_dir / "TASK.md"
    if not task_file.exists():
        task_file.write_text(
            f"# Task\n\n{description}\n\n"
            "Build this completely. If it's a web app make it visually polished and fully functional.\n"
        )

    # ── Website guard: if this looks like a website/page request, never open CMD.
    # Use webgen directly so the browser opens silently with the finished site.
    _WEB_WORDS = {
        "website", "web site", "webpage", "web page", "landing page", "landing",
        "web app", "web application", "html", "frontend", "front-end", "site",
        "homepage", "home page", "front end",
    }
    _desc_check = (description + " " + project_name).lower()
    if any(w in _desc_check for w in _WEB_WORDS):
        print(f"[G.I.L. BUILD] Website detected in '{project_name}' — redirecting to webgen (no CMD).")
        try:
            from webgen import generate_for_project
            result = generate_for_project(project_dir)
            print(f"[G.I.L. BUILD] Webgen complete: {result}")
        except Exception as exc:
            print(f"[G.I.L. BUILD] Webgen failed: {exc}")
        return True

    dir_str  = str(project_dir)
    cmd_body = f'cd /d "{dir_str}" && type TASK.md | claude -p --dangerously-skip-permissions'

    for launcher in [
        ["wt", "-d", dir_str, "cmd", "/k", cmd_body],
        ["cmd", "/c", "start", "cmd", "/k", cmd_body],
    ]:
        try:
            subprocess.Popen(launcher)
            print(f"[G.I.L. BUILD] Spawned terminal for '{project_name}'.")
            return True
        except FileNotFoundError:
            continue
        except Exception as exc:
            print(f"[G.I.L. BUILD] Launch failed: {exc}")
            break

    return False


def open_terminal(command: str = "") -> bool:
    """Open a new terminal window, optionally running a command."""
    for launcher in (
        ["wt", "cmd", "/k", command] if command else ["wt"],
        ["cmd", "/c", "start", "cmd", "/k", command] if command else ["cmd", "/c", "start", "cmd"],
    ):
        try:
            subprocess.Popen(launcher)
            return True
        except FileNotFoundError:
            continue
        except Exception as exc:
            print(f"[G.I.L. ACTIONS] open_terminal failed: {exc}")
            break
    return False


# ── Web search ────────────────────────────────────────────────────────────────

def get_spotify_now_playing() -> str | None:
    """Read the currently playing track from Spotify's window title."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "$p = Get-Process | Where-Object { $_.Name -like '*spotify*' -and $_.MainWindowTitle -ne '' };"
             "if ($p) { $p[0].MainWindowTitle }"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        title = result.stdout.strip()
        print(f"[G.I.L. SPOTIFY] Window title: '{title}'")
        if title and title.lower() not in ("spotify", "spotify premium", ""):
            return title
    except Exception as exc:
        print(f"[G.I.L. SPOTIFY] {exc}")
    return None


def open_web_search(query: str) -> str:
    # Route video requests to YouTube directly
    lower = query.lower()
    if any(w in lower for w in ("video", "youtube", "watch", "tutorial", "explained", "lecture")):
        url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
    else:
        url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
    webbrowser.open(url)
    return f"Search opened: {query}"


# ── Song identification (Shazam) ──────────────────────────────────────────────

def identify_song(on_result: callable) -> None:
    """
    Record ~10 seconds of audio from the mic, identify the song via Shazam,
    and call on_result(text) with the result. Runs synchronously — call from a thread.
    """
    import wave, tempfile, asyncio
    import numpy as np
    import sounddevice as sd

    SAMPLERATE = 44100
    DURATION   = 15

    try:
        # Pick the microphone explicitly
        mic_index = None
        devices   = sd.query_devices()
        for i, dev in enumerate(devices):
            if dev["max_input_channels"] > 0 and "microphone" in dev["name"].lower():
                mic_index = i
                print(f"[G.I.L. SONG ID] Using mic: {dev['name']}")
                break
        if mic_index is None:
            mic_index = sd.default.device[0]
            print(f"[G.I.L. SONG ID] Using default input: {devices[mic_index]['name']}")

        audio = sd.rec(int(DURATION * SAMPLERATE), samplerate=SAMPLERATE,
                       channels=1, dtype="float32", device=mic_index)
        sd.wait()

        # Normalize — boost quiet recordings to full volume
        samples = audio.flatten()
        peak    = np.max(np.abs(samples))
        if peak > 0.001:
            samples = samples / peak * 0.95
        pcm = (samples * 32767).astype("int16")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLERATE)
            wf.writeframes(pcm.tobytes())

        import requests
        print(f"[G.I.L. SONG ID] Sending {DURATION}s of audio to audd.io...")
        with open(wav_path, "rb") as f:
            resp = requests.post(
                "https://api.audd.io/",
                files={"file": ("audio.wav", f, "audio/wav")},
                data={"api_token": os.getenv("AUDD_API_KEY", "test"), "return": "apple_music,spotify"},
                timeout=20,
            )
        os.unlink(wav_path)

        print(f"[G.I.L. SONG ID] audd.io response: {resp.text[:200]}")
        data  = resp.json()
        track = data.get("result") or {}
        title  = track.get("title", "")
        artist = track.get("artist", "")

        if title:
            on_result(f"That's {title} by {artist}." if artist else f"That's {title}.")
        else:
            on_result("Couldn't identify that one — hold the phone closer and try again.")

    except Exception as exc:
        print(f"[G.I.L. SONG ID] Error: {exc}")
        on_result("Song identification failed. Make sure the audio is playing clearly.")


# ── Window management ─────────────────────────────────────────────────────────

def focus_window(app_name: str) -> str:
    """Bring a named app window to the foreground."""
    import ctypes
    user32 = ctypes.windll.user32
    found  = [None]

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def enum_cb(hwnd, lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if app_name.lower() in buf.value.lower():
                found[0] = hwnd
                return False
        return True

    user32.EnumWindows(enum_cb, 0)
    if found[0]:
        user32.ShowWindow(found[0], 9)    # SW_RESTORE
        user32.SetForegroundWindow(found[0])
        return f"Focused: {app_name}"
    return f"Window not found: {app_name}"


def arrange_windows(app1: str, app2: str = "") -> str:
    """Tile two apps side by side using Win+Left / Win+Right."""
    import pyautogui
    if app1:
        focus_window(app1)
        time.sleep(0.4)
        pyautogui.hotkey("win", "left")
        time.sleep(0.3)
    if app2:
        focus_window(app2)
        time.sleep(0.4)
        pyautogui.hotkey("win", "right")
    return f"Arranged windows: {app1} | {app2 or 'other'}"


def close_window(app_name: str) -> str:
    """Close a named app window gracefully."""
    for proc in psutil.process_iter(["name", "pid"]):
        if app_name.lower() in (proc.info.get("name") or "").lower():
            try:
                proc.terminate()
                return f"Closed: {app_name}"
            except Exception as exc:
                return f"Could not close {app_name}: {exc}"
    return f"Process not found: {app_name}"


def minimize_all() -> str:
    """Show desktop (Win+D)."""
    import pyautogui
    pyautogui.hotkey("win", "d")
    return "Showing desktop."


def maximize_window(app_name: str) -> str:
    """Maximize a named app window."""
    import ctypes
    user32 = ctypes.windll.user32

    if not app_name:
        hwnd = user32.GetForegroundWindow()
    else:
        focus_window(app_name)
        time.sleep(0.2)
        hwnd = user32.GetForegroundWindow()

    user32.ShowWindow(hwnd, 3)   # SW_MAXIMIZE
    return f"Maximized: {app_name or 'active window'}"


# ── File system ───────────────────────────────────────────────────────────────

def open_file(path: str) -> str:
    """Open a file with its default application."""
    import os
    expanded = os.path.expandvars(os.path.expanduser(path))
    if not os.path.exists(expanded):
        # try home / desktop search
        from pathlib import Path
        candidates = list(Path.home().glob(f"**/{path}"))
        if candidates:
            expanded = str(candidates[0])
        else:
            return f"File not found: {path}"
    try:
        os.startfile(expanded)
        return f"Opened: {expanded}"
    except Exception as exc:
        return f"Could not open {path}: {exc}"


def read_file(path: str) -> str:
    """Read a text file and return its content (capped at 3000 chars)."""
    import os
    expanded = os.path.expandvars(os.path.expanduser(path))
    if not os.path.exists(expanded):
        return f"File not found: {path}"
    try:
        with open(expanded, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(3000)
        lines = len(content.splitlines())
        return f"[{path}  —  {lines} lines]\n{content}"
    except Exception as exc:
        return f"Could not read {path}: {exc}"


def list_directory(path: str = "") -> str:
    """List directory contents."""
    from pathlib import Path
    import os
    target = Path(os.path.expandvars(os.path.expanduser(path or "~")))
    if not target.exists():
        return f"Directory not found: {path}"
    try:
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        lines   = []
        for e in entries[:40]:
            icon = "📁" if e.is_dir() else "📄"
            lines.append(f"{icon} {e.name}")
        return f"{target}\n" + "\n".join(lines)
    except Exception as exc:
        return f"Could not list {path}: {exc}"


def find_file(name: str) -> str:
    """Search for a file by name pattern in user's home directory."""
    from pathlib import Path
    home = Path.home()
    try:
        matches = list(home.glob(f"**/{name}"))[:8]
        if matches:
            return "\n".join(str(m) for m in matches)
        return f"No files matching '{name}' found in home directory."
    except Exception as exc:
        return f"Search failed: {exc}"


# ── New feature wrappers ─────────────────────────────────────────────────────

def _mode_control(mode_name: str) -> str:
    try:
        from modes import set_mode
        return set_mode(mode_name)
    except Exception:
        return "Couldn't switch modes right now."


def _pc_power(command: str) -> str:
    try:
        from pc_control import pc_power_control
        return pc_power_control(command)
    except Exception:
        return "PC power control failed."


def _pc_vol(command: str) -> str:
    try:
        from pc_control import pc_volume_control
        return pc_volume_control(command)
    except Exception:
        return "Couldn't adjust the volume."


def _weather(location: str = "") -> str:
    try:
        from weather import get_weather
        return get_weather(location)
    except Exception:
        return "Couldn't fetch weather right now."


def _reminder(text: str) -> str:
    try:
        from reminders import add_reminder
        return add_reminder(text)
    except Exception:
        return "Couldn't set the reminder."


def _list_reminders() -> str:
    try:
        from reminders import list_reminders
        return list_reminders()
    except Exception:
        return "Couldn't retrieve reminders."


def _save_note(text: str) -> str:
    try:
        from notes import save_note
        return save_note(text)
    except Exception:
        return "Couldn't save the note."


def _list_notes() -> str:
    try:
        from notes import list_notes
        return list_notes()
    except Exception:
        return "Couldn't retrieve notes."


def _clip_history() -> str:
    try:
        from notes import get_clip_history
        return get_clip_history()
    except Exception:
        return "Couldn't read clipboard history."


def _spotify(command: str) -> str:
    try:
        from spotify_control import spotify_control
        return spotify_control(command)
    except Exception:
        return "Spotify isn't responding right now."


def _briefing(location: str = "") -> str:
    try:
        from briefing import build_briefing
        return build_briefing(location)
    except Exception as exc:
        return f"Briefing error: {exc}"


def _nearby(query: str) -> str:
    try:
        from location import open_nearby
        return open_nearby(query or "things to do")
    except Exception as exc:
        return f"Map error: {exc}"


def _directions(destination: str) -> str:
    try:
        from location import open_directions
        mode = "walking" if any(w in destination.lower() for w in ("walk", "walking", "on foot")) else "driving"
        dest = destination.lower().replace("walking", "").replace("walk", "").replace("driving", "").strip()
        return open_directions(dest or destination, mode=mode)
    except Exception as exc:
        return f"Directions error: {exc}"


def _my_location() -> str:
    try:
        from location import get_location_string
        loc = get_location_string()
        return f"You're in {loc}."
    except Exception as exc:
        return f"Location error: {exc}"


def _food_delivery(query: str = "") -> str:
    try:
        from location import open_food_delivery
        return open_food_delivery(query)
    except Exception as exc:
        return f"Food delivery error: {exc}"


def _news(category: str = "") -> str:
    try:
        from news import get_news, build_news_speech
        articles = get_news(force=True)
        return build_news_speech(articles, count=3)
    except Exception as exc:
        return f"News error: {exc}"


def _open_article(index_str: str = "0") -> str:
    try:
        from news import open_news_article
        idx = int(index_str) if index_str.isdigit() else 0
        return open_news_article(idx)
    except Exception as exc:
        return f"Article error: {exc}"


def _calendar(period: str = "today") -> str:
    try:
        from gcalendar import get_todays_events, get_upcoming_events
        from gcalendar import build_today_speech, build_upcoming_speech
        period = period.lower().strip()
        if period in ("week", "upcoming", "next week", "this week"):
            return build_upcoming_speech(get_upcoming_events(7))
        if period in ("tomorrow",):
            return build_upcoming_speech(get_upcoming_events(2))
        return build_today_speech(get_todays_events())
    except Exception as exc:
        return f"Calendar error: {exc}"


def _add_event(target: str) -> str:
    try:
        from gcalendar import parse_event_target, add_event
        title, start_iso, duration = parse_event_target(target)
        return add_event(title, start_iso, duration)
    except Exception as exc:
        return f"Add event error: {exc}"


def _look(question: str = "") -> str:
    try:
        from eyes import look
        return look(question=question)
    except Exception as exc:
        return f"Camera error: {exc}"


# ── Clipboard public API ──────────────────────────────────────────────────────

def set_clipboard_text(text: str) -> str:
    """Put text on the clipboard."""
    try:
        _set_clipboard(text)
        return f"Clipboard set ({len(text)} chars)."
    except Exception as exc:
        return f"Clipboard set failed: {exc}"


def get_clipboard_text() -> str:
    """Read and return current clipboard content."""
    try:
        from context_engine import _read_clipboard
        return _read_clipboard() or "(clipboard empty)"
    except Exception:
        return "(could not read clipboard)"
