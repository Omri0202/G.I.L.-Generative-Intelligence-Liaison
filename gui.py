"""
gui.py — Project G.I.L.
A being that lives in your computer.
Frameless, transparent, floating — always present.
"""

import json
import math
import os
import random
import sys
import threading
import time
import winreg
import tkinter as tk
import customtkinter as ctk
from pathlib import Path
import datetime as _dt
import re
from gil_icons import icon as _icon


# ── Transparency color (pure black = see-through on Windows) ──────────────────
BG     = "#000000"   # transparent key
BG2    = "#010118"   # dark panel — NOT transparent
ACCENT = "#00BFFF"

# ── App icon (set once, reused across all windows) ────────────────────────────
_ICON_PATH = Path(__file__).parent / "data" / "gil.ico"
_LOGO_PATH = Path(__file__).parent / "data" / "gil_logo.png"
_HQ_PATH   = Path(__file__).parent / "data" / "gil_icon_hq.png"


def _hide_from_taskbar(win) -> None:
    """Remove a window from the Windows taskbar using extended window styles."""
    try:
        import ctypes as _ct
        hwnd = win.winfo_id()
        GWL_EXSTYLE      = -20
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_APPWINDOW  = 0x00040000
        style = _ct.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        _ct.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE,
            (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        )
    except Exception:
        pass


def _set_icon(win) -> None:
    """Apply the GIL icon via iconbitmap (title bar) and wm_iconphoto (taskbar)."""
    try:
        if _ICON_PATH.exists():
            win.iconbitmap(str(_ICON_PATH))
    except Exception:
        pass
    try:
        from PIL import Image as _PI, ImageTk as _ITK
        src = (_HQ_PATH   if _HQ_PATH.exists()  else
               _LOGO_PATH if _LOGO_PATH.exists() else None)
        if src:
            img   = _PI.open(str(src)).convert("RGBA").resize((128, 128), _PI.LANCZOS)
            photo = _ITK.PhotoImage(img)
            win.wm_iconphoto(True, photo)
            if not hasattr(win, "_icon_photos"):
                win._icon_photos = []
            win._icon_photos.append(photo)
    except Exception:
        pass


def _tray_image():
    """Return a PIL Image for the system tray (128×128 RGBA)."""
    try:
        from PIL import Image as _PilImg
        src = _LOGO_PATH if _LOGO_PATH.exists() else _ICON_PATH
        if src.exists():
            return _PilImg.open(str(src)).convert("RGBA").resize((128, 128))
    except Exception:
        pass
    # Fallback: plain circle (original tray icon)
    try:
        from PIL import Image as _PI, ImageDraw as _PID
        img  = _PI.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = _PID.Draw(img)
        draw.ellipse([2,  2,  62, 62], fill=(10,  10,  15))
        draw.ellipse([6,  6,  58, 58], outline=(0, 191, 255), width=3)
        draw.ellipse([24, 24, 40, 40], fill=(0, 191, 255))
        return img
    except Exception:
        return None

_ST = {
    "standby": {
        "color": "#2080B0", "glow": "#0A2848", "dim": "#0C1E30",
        "ring": "#0C2040", "label": "standby", "speed": 55, "energy": 0.30, "spin": 0.32,
    },
    "listening": {
        "color": "#00BFFF", "glow": "#001E30", "dim": "#001020",
        "ring": "#000D18", "label": "listening", "speed": 30, "energy": 0.55, "spin": 0.85,
    },
    "processing": {
        "color": "#FFB800", "glow": "#180E00", "dim": "#0E0800",
        "ring": "#0A0600", "label": "processing", "speed": 18, "energy": 1.0, "spin": 2.0,
    },
    "speaking": {
        "color": "#3A70FF", "glow": "#000820", "dim": "#000510",
        "ring": "#000310", "label": "speaking", "speed": 16, "energy": 0.95, "spin": 1.0,
    },
}

# ── Wave visualizer ───────────────────────────────────────────────────────────
# Floating ribbon bands: each layer is a closed polygon tracing top + bottom
# edges of a wave band. Dark/wide bands sit low; bright/thin bands sit high.
WIN_H  = 200
N_PTS  = 72

# (fill_color, center_y, amplitude, freq_cycles, speed)
# center_y counts from top of screen; ribbons overlap so the full height is covered.
_WLAYERS = [
    ("#010614", 175, 44, 2.0, 0.28),   # deep space — wide, slow, ultra-dark
    ("#020A36", 150, 42, 2.5, 0.44),
    ("#051680", 124, 38, 3.0, 0.66),
    ("#0C2CC8",  98, 32, 3.6, 0.92),
    ("#1454F0",  74, 24, 4.3, 1.28),
    ("#2090FF",  50, 16, 5.0, 1.68),
    ("#50D0FF",  28,  8, 5.8, 2.18),   # vivid cyan surface — thin, fast
]
# Half-thickness of each ribbon band in pixels
_WTHICK = [54, 46, 38, 30, 22, 15, 9]


# ── Startup registry ──────────────────────────────────────────────────────────
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_KEY = "ProjectGIL"


def _get_startup_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.QueryValueEx(k, _APP_KEY)
            return True
    except OSError:
        return False


def _set_startup(enable: bool) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enable:
                exe    = sys.executable.replace("python.exe", "pythonw.exe")
                exe    = exe if os.path.exists(exe) else sys.executable
                script = str(Path(__file__).parent / "gil.pyw")
                winreg.SetValueEx(k, _APP_KEY, 0, winreg.REG_SZ, f'"{exe}" "{script}"')
            else:
                try:
                    winreg.DeleteValue(k, _APP_KEY)
                except FileNotFoundError:
                    pass
    except OSError as exc:
        print(f"[G.I.L.] Startup toggle failed: {exc}")


# ── Color helpers ─────────────────────────────────────────────────────────────
def _hex2rgb(h: str):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _blend(c1: str, c2: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    r1, g1, b1 = _hex2rgb(c1)
    r2, g2, b2 = _hex2rgb(c2)
    return f"#{int(r1+(r2-r1)*t):02x}{int(g1+(g2-g1)*t):02x}{int(b1+(b2-b1)*t):02x}"


# ── Trigger parser ────────────────────────────────────────────────────────────
import re as _re
_DOMAIN_RE = _re.compile(r'^[\w.-]+\.(com|org|net|io|co|uk|edu|gov|app|dev|me)(/.*)?$')


def _parse_trigger_actions(raw: str) -> list[dict]:
    actions = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        low = item.lower()
        if low.startswith("http"):
            actions.append({"type": "open_url",   "target": item})
        elif low.startswith("search:"):
            actions.append({"type": "web_search", "target": item[7:].strip()})
        elif _DOMAIN_RE.match(low):
            actions.append({"type": "open_url",   "target": "https://" + item})
        else:
            actions.append({"type": "open_app",   "target": item})
    return actions


# ── Settings window ───────────────────────────────────────────────────────────
_CFG_PATH = Path(__file__).parent / "data" / "gil_config.json"


class SettingsWindow(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("G.I.L. — Settings")
        self.geometry("540x720")
        self.resizable(False, False)
        ctk.set_appearance_mode("dark")
        self.configure(fg_color="#05050F")
        _set_icon(self)
        self.lift(); self.focus()
        self._build_ui()

    def _build_ui(self) -> None:
        hdr = ctk.CTkFrame(self, fg_color="#080818", corner_radius=0, height=54)
        hdr.pack(fill="x"); hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="G.I.L.  —  Settings",
                     font=ctk.CTkFont("Segoe UI", 16, "bold"),
                     text_color=ACCENT).pack(side="left", padx=22, pady=14)
        ctk.CTkFrame(self, height=1, fg_color=ACCENT).pack(fill="x")
        tabs = ctk.CTkTabview(self, fg_color="#080818",
                              segmented_button_fg_color="#05050F",
                              segmented_button_selected_color=ACCENT,
                              segmented_button_selected_hover_color="#0090CC",
                              segmented_button_unselected_color="#080818",
                              text_color="#E0E0E0")
        tabs.pack(fill="both", expand=True, padx=16, pady=12)
        self._build_voice_tab(tabs.add("  Voice & AI  "))
        self._build_credentials_tab(tabs.add("  Credentials  "))
        self._build_triggers_tab(tabs.add("  Triggers  "))
        self._build_system_tab(tabs.add("  System  "))
        self._build_gestures_tab(tabs.add("  Gestures  "))

    # ── Config helpers ────────────────────────────────────────────────────────

    def _cfg_get(self, key: str, default=None):
        try:
            import json as _json
            with open(_CFG_PATH) as f:
                return _json.load(f).get(key, default)
        except Exception:
            return default

    def _cfg_set(self, key: str, value) -> None:
        try:
            import json as _json
            _CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            try:
                with open(_CFG_PATH) as f:
                    data = _json.load(f)
            except Exception:
                pass
            data[key] = value
            with open(_CFG_PATH, "w") as f:
                _json.dump(data, f, indent=2)
        except Exception as exc:
            print(f"[G.I.L.] Config save failed: {exc}")

    # ── Voice & AI tab ────────────────────────────────────────────────────────

    def _build_voice_tab(self, parent) -> None:
        s = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        s.pack(fill="both", expand=True)

        # Wake phrase
        ctk.CTkLabel(s, text="Wake Phrase",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color="#4A4A6A").pack(anchor="w", pady=(8, 4))
        wake_row = ctk.CTkFrame(s, fg_color="#0A0A18", corner_radius=8, height=44)
        wake_row.pack(fill="x"); wake_row.pack_propagate(False)
        self._wake_var = ctk.StringVar(value=self._cfg_get("wake_phrase", "Hello G.I.L."))
        ctk.CTkEntry(wake_row, textvariable=self._wake_var, height=32,
                     fg_color="#0A0A18", border_color="#1A1A30",
                     text_color="#E0E0E0", font=ctk.CTkFont("Segoe UI", 13)
                     ).pack(side="left", fill="x", expand=True, padx=(10, 6), pady=6)
        ctk.CTkButton(wake_row, text="Save", width=64, height=30,
                      fg_color=ACCENT, hover_color="#0090CC",
                      text_color="#000", font=ctk.CTkFont("Segoe UI", 11, "bold"),
                      command=lambda: self._cfg_set("wake_phrase", self._wake_var.get().strip())
                      ).pack(side="right", padx=8, pady=6)
        ctk.CTkLabel(s, text="Restart G.I.L. for wake phrase changes to take effect.",
                     font=ctk.CTkFont("Segoe UI", 9), text_color="#2A2A4A").pack(anchor="w", pady=(3, 10))

        # TTS voice
        ctk.CTkFrame(s, height=1, fg_color="#181828").pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(s, text="TTS Voice",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color="#4A4A6A").pack(anchor="w", pady=(0, 6))
        _VOICES = [
            "en-GB-RyanNeural",
            "en-US-GuyNeural",
            "en-US-AriaNeural",
            "en-AU-WilliamNeural",
            "en-IN-PrabhatNeural",
            "en-CA-LiamNeural",
            "en-IE-ConnorNeural",
        ]
        self._voice_var = ctk.StringVar(value=self._cfg_get("tts_voice", "en-GB-RyanNeural"))
        ctk.CTkOptionMenu(s, variable=self._voice_var, values=_VOICES,
                          fg_color="#0A0A18", button_color="#0E0E24",
                          button_hover_color="#1A1A30",
                          dropdown_fg_color="#080818",
                          text_color="#E0E0E0",
                          font=ctk.CTkFont("Segoe UI", 12),
                          command=lambda v: self._cfg_set("tts_voice", v)
                          ).pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(s, text="Restart G.I.L. to switch voice.",
                     font=ctk.CTkFont("Segoe UI", 9), text_color="#2A2A4A").pack(anchor="w", pady=(0, 10))

        # AI model
        ctk.CTkFrame(s, height=1, fg_color="#181828").pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(s, text="AI Model",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color="#4A4A6A").pack(anchor="w", pady=(0, 6))
        _MODELS = [
            ("llama-3.1-8b-instant",    "llama-3.1-8b  (fast, default)"),
            ("llama-3.3-70b-versatile", "llama-3.3-70b  (smarter, slower)"),
            ("mixtral-8x7b-32768",      "Mixtral 8×7B  (multilingual)"),
            ("gemma2-9b-it",            "Gemma 2 9B    (lightweight)"),
        ]
        self._model_var = ctk.StringVar(value=self._cfg_get("ai_model", "llama-3.1-8b-instant"))
        for model_id, label in _MODELS:
            row = ctk.CTkFrame(s, fg_color="#0A0A18", corner_radius=6, height=38)
            row.pack(fill="x", pady=2); row.pack_propagate(False)
            ctk.CTkRadioButton(row, text=label, variable=self._model_var, value=model_id,
                               text_color="#C0C0D8", font=ctk.CTkFont("Segoe UI", 11),
                               fg_color=ACCENT, hover_color="#0090CC",
                               command=lambda m=model_id: self._cfg_set("ai_model", m)
                               ).pack(side="left", padx=14, pady=8)
        ctk.CTkLabel(s, text="Restart G.I.L. to switch model.",
                     font=ctk.CTkFont("Segoe UI", 9), text_color="#2A2A4A").pack(anchor="w", pady=(3, 10))

        # Feature toggles
        ctk.CTkFrame(s, height=1, fg_color="#181828").pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(s, text="Features",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color="#4A4A6A").pack(anchor="w", pady=(0, 6))
        for key, label, default in [
            ("proactive_on",   "Proactive suggestions",  True),
            ("clap_detect_on", "Clap-to-activate",        True),
            ("memory_on",      "Auto memory extraction",  True),
        ]:
            row = ctk.CTkFrame(s, fg_color="#0A0A18", corner_radius=8, height=44)
            row.pack(fill="x", pady=3); row.pack_propagate(False)
            ctk.CTkLabel(row, text=label, text_color="#D0D0D0",
                         font=ctk.CTkFont("Segoe UI", 12)).pack(side="left", padx=14)
            var = ctk.BooleanVar(value=self._cfg_get(key, default))
            ctk.CTkSwitch(row, variable=var, text="", width=46,
                          progress_color=ACCENT,
                          command=lambda k=key, v=var: self._cfg_set(k, v.get())
                          ).pack(side="right", padx=14)

    def _build_credentials_tab(self, parent) -> None:
        s = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        s.pack(fill="both", expand=True)
        ctk.CTkLabel(s, text="Stored Credentials",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color="#4A4A6A").pack(anchor="w", pady=(8, 6))
        self._cred_frame = ctk.CTkScrollableFrame(s, fg_color="#0A0A18", corner_radius=8, height=160)
        self._cred_frame.pack(fill="x", pady=(0, 12))
        self._refresh_credentials()
        ctk.CTkFrame(s, height=1, fg_color="#181828").pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(s, text="Add Credential",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color="#4A4A6A").pack(anchor="w", pady=(0, 6))
        self._svc = ctk.StringVar()
        self._usr = ctk.StringVar()
        self._pwd = ctk.StringVar()
        for lbl, var, hide in [("Service", self._svc, False),
                                ("Email / Username", self._usr, False),
                                ("Password", self._pwd, True)]:
            ctk.CTkLabel(s, text=lbl, font=ctk.CTkFont("Segoe UI", 10),
                         text_color="#6060A0").pack(anchor="w")
            ctk.CTkEntry(s, textvariable=var, height=36, fg_color="#0A0A18",
                         border_color="#1A1A30", text_color="#E0E0E0",
                         font=ctk.CTkFont("Segoe UI", 13),
                         show="*" if hide else "").pack(fill="x", pady=(2, 8))
        ctk.CTkButton(s, text="Save Credential", height=38, fg_color=ACCENT,
                      hover_color="#0090CC", text_color="#000",
                      font=ctk.CTkFont("Segoe UI", 12, "bold"),
                      command=self._add_credential).pack(fill="x", pady=(4, 8))

    def _refresh_credentials(self) -> None:
        for w in self._cred_frame.winfo_children():
            w.destroy()
        try:
            from credentials import list_services, get_credential
            services = list_services()
        except Exception:
            services = []
        if not services:
            ctk.CTkLabel(self._cred_frame, text="No credentials stored yet.",
                         text_color="#2A2A4A", font=ctk.CTkFont("Segoe UI", 11)).pack(pady=18)
            return
        for svc in services:
            cred = (lambda s: (lambda c: c)(__import__("credentials").get_credential(s)))(svc)
            usr  = cred[0] if cred else "—"
            row  = ctk.CTkFrame(self._cred_frame, fg_color="#0A0A18", corner_radius=6, height=40)
            row.pack(fill="x", padx=6, pady=3); row.pack_propagate(False)
            ctk.CTkLabel(row, text=svc, text_color="#E0E0E0",
                         font=ctk.CTkFont("Segoe UI", 12, "bold"),
                         width=120, anchor="w").pack(side="left", padx=12)
            ctk.CTkLabel(row, text=usr, text_color="#6060A0",
                         font=ctk.CTkFont("Segoe UI", 11)).pack(side="left", padx=6)
            ctk.CTkButton(row, text="✕", width=28, height=24,
                          fg_color="#160616", hover_color="#3A0A0A",
                          text_color="#FF5555", font=ctk.CTkFont("Segoe UI", 11, "bold"),
                          corner_radius=4,
                          command=lambda s=svc: self._delete_credential(s)).pack(side="right", padx=8)

    def _add_credential(self) -> None:
        svc, usr, pwd = self._svc.get().strip(), self._usr.get().strip(), self._pwd.get().strip()
        if svc and usr and pwd:
            from credentials import save_credential, initialize_credentials
            initialize_credentials()
            save_credential(svc, usr, pwd)
            for v in (self._svc, self._usr, self._pwd):
                v.set("")
            self._refresh_credentials()

    def _delete_credential(self, svc: str) -> None:
        from credentials import delete_credential
        delete_credential(svc)
        self._refresh_credentials()

    def _build_triggers_tab(self, parent) -> None:
        s = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        s.pack(fill="both", expand=True)
        ctk.CTkLabel(s, text="Active Triggers",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color="#4A4A6A").pack(anchor="w", pady=(8, 6))
        self._trig_list = ctk.CTkScrollableFrame(s, fg_color="#0A0A18", corner_radius=8, height=150)
        self._trig_list.pack(fill="x", pady=(0, 12))
        self._refresh_triggers()
        ctk.CTkFrame(s, height=1, fg_color="#181828").pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(s, text="Add Trigger",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color="#4A4A6A").pack(anchor="w", pady=(0, 6))
        self._trig_phrase   = ctk.StringVar()
        self._trig_actions  = ctk.StringVar()
        self._trig_followup = ctk.StringVar()
        fields = [
            ("Trigger phrase", self._trig_phrase, "e.g.  homework mode"),
            ("Actions (comma-separated)", self._trig_actions, "e.g.  spotify, classoos.com"),
            ("Follow-up question (optional)", self._trig_followup, "e.g.  What are you studying?"),
        ]
        for lbl, var, hint in fields:
            ctk.CTkLabel(s, text=lbl, font=ctk.CTkFont("Segoe UI", 10),
                         text_color="#6060A0").pack(anchor="w")
            ctk.CTkEntry(s, textvariable=var, height=36, fg_color="#0A0A18",
                         border_color="#1A1A30", text_color="#E0E0E0",
                         font=ctk.CTkFont("Segoe UI", 13),
                         placeholder_text=hint).pack(fill="x", pady=(2, 8))
        ctk.CTkButton(s, text="Add Trigger", height=38, fg_color=ACCENT,
                      hover_color="#0090CC", text_color="#000",
                      font=ctk.CTkFont("Segoe UI", 12, "bold"),
                      command=self._add_trigger).pack(fill="x", pady=(4, 8))

    def _refresh_triggers(self) -> None:
        for w in self._trig_list.winfo_children():
            w.destroy()
        try:
            from triggers import get_all
            trigs = get_all()
        except Exception:
            trigs = []
        if not trigs:
            ctk.CTkLabel(self._trig_list, text="No triggers yet. Add one below.",
                         text_color="#2A2A4A", font=ctk.CTkFont("Segoe UI", 11)).pack(pady=18)
            return
        for trig in trigs:
            parts = [a.get("target", "")[:20] for a in trig.get("actions", [])]
            acts  = "  →  ".join(parts) if parts else "(no actions)"
            row = ctk.CTkFrame(self._trig_list, fg_color="#0A0A18", corner_radius=6)
            row.pack(fill="x", padx=6, pady=3)
            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True, padx=(12, 4), pady=6)
            ctk.CTkLabel(info, text=f'"{trig["phrase"]}"',
                         text_color=ACCENT, font=ctk.CTkFont("Segoe UI", 12, "bold"),
                         anchor="w").pack(anchor="w")
            ctk.CTkLabel(info, text=acts, text_color="#505080",
                         font=ctk.CTkFont("Segoe UI", 10), anchor="w").pack(anchor="w")
            ctk.CTkButton(row, text="✕", width=28, height=24,
                          fg_color="#160616", hover_color="#3A0A0A",
                          text_color="#FF5555", font=ctk.CTkFont("Segoe UI", 11, "bold"),
                          corner_radius=4,
                          command=lambda tid=trig["id"]: self._delete_trigger(tid)
                          ).pack(side="right", padx=8, pady=8)

    def _add_trigger(self) -> None:
        phrase   = self._trig_phrase.get().strip()
        raw_acts = self._trig_actions.get().strip()
        followup = self._trig_followup.get().strip()
        if not phrase or not raw_acts:
            return
        actions = _parse_trigger_actions(raw_acts)
        if not actions:
            return
        from triggers import add_trigger
        add_trigger(phrase, actions, followup)
        for v in (self._trig_phrase, self._trig_actions, self._trig_followup):
            v.set("")
        self._refresh_triggers()

    def _delete_trigger(self, tid: str) -> None:
        from triggers import delete_trigger
        delete_trigger(tid)
        self._refresh_triggers()

    def _build_system_tab(self, parent) -> None:
        ctk.CTkLabel(parent, text="Startup",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color="#4A4A6A").pack(anchor="w", pady=(8, 8))
        row = ctk.CTkFrame(parent, fg_color="#0A0A18", corner_radius=8, height=48)
        row.pack(fill="x"); row.pack_propagate(False)
        ctk.CTkLabel(row, text="Start with Windows",
                     text_color="#D0D0D0", font=ctk.CTkFont("Segoe UI", 13)).pack(side="left", padx=16)
        self._sw = ctk.BooleanVar(value=_get_startup_enabled())
        ctk.CTkSwitch(row, variable=self._sw, text="", width=46,
                      progress_color=ACCENT,
                      command=lambda: _set_startup(self._sw.get())).pack(side="right", padx=16)
        ctk.CTkLabel(parent, text="System Info",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color="#4A4A6A").pack(anchor="w", pady=(20, 8))
        for lbl, val in [
            ("AI Model",    "Groq / llama-3.1-8b-instant"),
            ("Voice In",    "Google Speech Recognition"),
            ("Voice Out",   "edge-tts / en-GB-RyanNeural"),
            ("Wake phrase", "Hello G.I.L."),
            ("Hotkey",      "Ctrl + Shift + G"),
        ]:
            r = ctk.CTkFrame(parent, fg_color="#0A0A18", corner_radius=6, height=36)
            r.pack(fill="x", pady=2); r.pack_propagate(False)
            ctk.CTkLabel(r, text=lbl, text_color="#606080",
                         font=ctk.CTkFont("Segoe UI", 11),
                         width=130, anchor="w").pack(side="left", padx=12)
            ctk.CTkLabel(r, text=val, text_color="#A0A0C0",
                         font=ctk.CTkFont("Segoe UI", 11)).pack(side="left")


    # ── Gestures tab ─────────────────────────────────────────────────────────

    _GESTURE_CONFIG_FILE = Path(__file__).parent / "data" / "gesture_config.json"

    _GESTURE_ROWS = [
        ("thumbs_up",   "Thumbs Up   ☝"),
        ("thumbs_down", "Thumbs Down ↓"),
        ("peace",       "Peace  ✌"),
        ("fist",        "Fist  ✊"),
        ("open_hand",   "Open Hand  🖐"),
        ("rock_on",     "Rock On  🤘"),
        ("three_up",    "Three Fingers ☝☝☝"),
        ("call_me",     "Call Me  🤙"),
        ("index_point", "Index Point ☝"),
    ]

    _BUILTIN_OPTS = [
        ("Vol Up (+10%)",    "volume_up"),
        ("Vol Down (-10%)",  "volume_down"),
        ("Screenshot",       "screenshot"),
        ("Mute / Unmute",    "mute_toggle"),
        ("Next Track",       "next_track"),
        ("Prev Track",       "prev_track"),
        ("DND Toggle",       "dnd_toggle"),
        ("Announce Mode",    "announce"),
        ("Open App...",      "__open_app__"),
        ("Open URL...",      "__open_url__"),
    ]

    _DEFAULT_GESTURE_CONFIG = {
        "thumbs_up":   {"type": "builtin", "action": "volume_up",   "label": "Vol Up"},
        "thumbs_down": {"type": "builtin", "action": "volume_down", "label": "Vol Down"},
        "peace":       {"type": "builtin", "action": "screenshot",  "label": "Screenshot"},
        "fist":        {"type": "builtin", "action": "mute_toggle", "label": "Mute"},
        "open_hand":   {"type": "builtin", "action": "announce",    "label": "Announce"},
        "rock_on":     {"type": "builtin", "action": "dnd_toggle",  "label": "DND Mode"},
        "three_up":    {"type": "builtin", "action": "next_track",  "label": "Next Track"},
        "call_me":     {"type": "builtin", "action": "prev_track",  "label": "Prev Track"},
        "index_point": {"type": "builtin", "action": "announce",    "label": "Cursor"},
    }

    def _build_gestures_tab(self, parent) -> None:
        s = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        s.pack(fill="both", expand=True)

        ctk.CTkLabel(s, text="Gesture Bindings",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color="#4A4A6A").pack(anchor="w", pady=(8, 4))
        ctk.CTkLabel(s,
                     text="Hold each gesture 0.5 s to trigger.  Choose Open App or Open URL\n"
                          "to bind any gesture to an app name (e.g. chrome) or a URL.",
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color="#3A3A5A", justify="left").pack(anchor="w", padx=2, pady=(0, 10))

        try:
            cfg_data = json.loads(self._GESTURE_CONFIG_FILE.read_text(encoding="utf-8"))
            current  = cfg_data.get("gestures", {})
        except Exception:
            current  = {}

        opt_labels  = [o[0] for o in self._BUILTIN_OPTS]
        opt_by_act  = {act: lbl for lbl, act in self._BUILTIN_OPTS}

        self._gesture_vars    = {}   # gkey -> (ctk.StringVar action_label, ctk.StringVar target)
        self._gesture_entries = {}   # gkey -> CTkEntry

        for gkey, glabel in self._GESTURE_ROWS:
            gcfg   = current.get(gkey, self._DEFAULT_GESTURE_CONFIG.get(gkey, {}))
            gtype  = gcfg.get("type", "builtin")
            gact   = gcfg.get("action", "volume_up")
            gtgt   = gcfg.get("target", "")

            if gtype == "open_app":
                selected_label = "Open App..."
                tgt_val        = gtgt
            elif gtype == "open_url":
                selected_label = "Open URL..."
                tgt_val        = gtgt
            else:
                selected_label = opt_by_act.get(gact, opt_labels[0])
                tgt_val        = ""

            row = ctk.CTkFrame(s, fg_color="#0A0A18", corner_radius=8, height=52)
            row.pack(fill="x", pady=3)
            row.pack_propagate(False)
            row.columnconfigure(1, weight=1)

            ctk.CTkLabel(row, text=glabel, text_color="#C0C0D8",
                         font=ctk.CTkFont("Segoe UI", 11),
                         width=148, anchor="w").place(x=10, y=14)

            action_var = ctk.StringVar(value=selected_label)
            target_var = ctk.StringVar(value=tgt_val)
            self._gesture_vars[gkey] = (action_var, target_var)

            ent = ctk.CTkEntry(row, textvariable=target_var,
                               placeholder_text="app name or URL",
                               width=128, height=26,
                               fg_color="#05050F", text_color="#90D0FF",
                               border_color="#0A2030", font=ctk.CTkFont("Segoe UI", 10))
            ent.place(x=370, y=13)
            self._gesture_entries[gkey] = ent

            needs_target = selected_label in ("Open App...", "Open URL...")
            ent.configure(state="normal" if needs_target else "disabled",
                          text_color="#90D0FF" if needs_target else "#2A2A3A")

            def _on_action_change(choice, _ent=ent, _tgt=target_var, _gk=gkey):
                is_custom = choice in ("Open App...", "Open URL...")
                _ent.configure(state="normal" if is_custom else "disabled",
                               text_color="#90D0FF" if is_custom else "#2A2A3A")
                if not is_custom:
                    _tgt.set("")

            ctk.CTkOptionMenu(row, variable=action_var,
                              values=opt_labels,
                              width=196, height=26,
                              fg_color="#070714", button_color="#001530",
                              button_hover_color="#002040",
                              text_color="#A0C8E8",
                              dropdown_fg_color="#070714",
                              dropdown_text_color="#A0C8E8",
                              dropdown_hover_color="#001E30",
                              font=ctk.CTkFont("Segoe UI", 10),
                              command=_on_action_change).place(x=158, y=13)

        # Save / Reset buttons
        btn_row = ctk.CTkFrame(s, fg_color="transparent")
        btn_row.pack(fill="x", pady=(14, 4))

        ctk.CTkButton(btn_row, text="Save Gestures",
                      height=32, width=160,
                      fg_color=ACCENT, hover_color="#0090CC",
                      text_color="#000", font=ctk.CTkFont("Segoe UI", 12, "bold"),
                      command=self._save_gestures).pack(side="left", padx=(2, 8))

        ctk.CTkButton(btn_row, text="Reset Defaults",
                      height=32, width=130,
                      fg_color="#0A0A18", hover_color="#0A0A28",
                      text_color="#4A4A6A",
                      font=ctk.CTkFont("Segoe UI", 11),
                      command=self._reset_gestures).pack(side="left")

        self._gesture_status = ctk.CTkLabel(s, text="",
                                            font=ctk.CTkFont("Segoe UI", 10),
                                            text_color="#00AA44")
        self._gesture_status.pack(anchor="w", pady=(4, 2))

    def _save_gestures(self) -> None:
        act_lookup = {lbl: act for lbl, act in self._BUILTIN_OPTS}
        gestures   = {}
        for gkey, (action_var, target_var) in self._gesture_vars.items():
            choice = action_var.get()
            target = target_var.get().strip()
            if choice == "Open App...":
                gestures[gkey] = {"type": "open_app", "target": target,
                                  "label": target[:12] or "App"}
            elif choice == "Open URL...":
                gestures[gkey] = {"type": "open_url", "target": target,
                                  "label": target[:12] or "URL"}
            else:
                act = act_lookup.get(choice, "volume_up")
                gestures[gkey] = {"type": "builtin", "action": act, "label": choice[:12]}

        try:
            self._GESTURE_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._GESTURE_CONFIG_FILE.write_text(
                json.dumps({"gestures": gestures}, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            if hasattr(self, "_gesture_status"):
                self._gesture_status.configure(text="Saved.  Changes take effect immediately.")
        except Exception as exc:
            if hasattr(self, "_gesture_status"):
                self._gesture_status.configure(text=f"Error: {exc}", text_color="#FF4444")

    def _reset_gestures(self) -> None:
        try:
            self._GESTURE_CONFIG_FILE.write_text(
                json.dumps({"gestures": self._DEFAULT_GESTURE_CONFIG}, indent=2),
                encoding="utf-8"
            )
            if hasattr(self, "_gesture_status"):
                self._gesture_status.configure(text="Reset to defaults.")
        except Exception as exc:
            if hasattr(self, "_gesture_status"):
                self._gesture_status.configure(text=f"Error: {exc}", text_color="#FF4444")


# ── Tasks window (opened from right-click menu) ───────────────────────────────
class GILTasksWindow(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("G.I.L. — Tasks & Learning")
        self.geometry("380x560")
        self.resizable(False, True)
        self.configure(fg_color="#05050F")
        _set_icon(self)
        self.lift(); self.focus()
        self._build()

    def _build(self) -> None:
        hdr = ctk.CTkFrame(self, fg_color="#080818", corner_radius=0, height=50)
        hdr.pack(fill="x"); hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="Tasks & Learning",
                     font=ctk.CTkFont("Segoe UI", 15, "bold"),
                     text_color=ACCENT).pack(side="left", padx=20, pady=12)
        ctk.CTkFrame(self, height=1, fg_color=ACCENT).pack(fill="x")

        tab_row = ctk.CTkFrame(self, fg_color="#080818", height=36)
        tab_row.pack(fill="x"); tab_row.pack_propagate(False)
        self._tab = "tasks"
        self._tab_t = ctk.CTkButton(tab_row, text="Tasks", height=28, width=160,
                                     fg_color=ACCENT, hover_color="#0090CC",
                                     text_color="#000", font=ctk.CTkFont("Segoe UI", 11, "bold"),
                                     corner_radius=3, command=self._show_tasks)
        self._tab_t.pack(side="left", padx=(8, 2), pady=4)
        self._tab_l = ctk.CTkButton(tab_row, text="Learning", height=28, width=160,
                                     fg_color="#0A0A18", hover_color="#0A0A20",
                                     text_color="#1E3A5A", font=ctk.CTkFont("Segoe UI", 11, "bold"),
                                     corner_radius=3, command=self._show_learn)
        self._tab_l.pack(side="left", padx=2, pady=4)

        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent",
                                               scrollbar_button_color="#0E0E1E",
                                               scrollbar_button_hover_color="#1A1A2A")
        self._scroll.pack(fill="both", expand=True, padx=6, pady=6)
        self._show_tasks()

    def _show_tasks(self) -> None:
        self._tab = "tasks"
        self._tab_t.configure(fg_color=ACCENT, text_color="#000")
        self._tab_l.configure(fg_color="#0A0A18", text_color="#1E3A5A")
        self._rebuild_tasks()

    def _show_learn(self) -> None:
        self._tab = "learn"
        self._tab_l.configure(fg_color=ACCENT, text_color="#000")
        self._tab_t.configure(fg_color="#0A0A18", text_color="#1E3A5A")
        self._rebuild_learn()

    def _rebuild_tasks(self) -> None:
        for w in self._scroll.winfo_children():
            w.destroy()
        try:
            from tasks import get_all
            data = get_all()
        except Exception:
            data = {"projects": {}, "tasks": []}

        projects  = data.get("projects", {})
        all_tasks = data.get("tasks", [])

        if not projects:
            ctk.CTkLabel(self._scroll,
                         text='Say "create a project" or "add a task" to G.I.L.',
                         text_color="#1A1A30", font=ctk.CTkFont("Segoe UI", 11),
                         wraplength=300, justify="center").pack(pady=32)
            return

        for key, proj in projects.items():
            open_t = [t for t in all_tasks if t.get("project") == key and not t.get("done")]
            done_t = [t for t in all_tasks if t.get("project") == key and t.get("done")]

            pc = ctk.CTkFrame(self._scroll, fg_color="#0A0A18", corner_radius=8)
            pc.pack(fill="x", pady=(0, 8))
            pr = ctk.CTkFrame(pc, fg_color="transparent", height=30)
            pr.pack(fill="x", padx=12, pady=(8, 4)); pr.pack_propagate(False)
            ctk.CTkLabel(pr, text=proj["name"],
                         font=ctk.CTkFont("Segoe UI", 12, "bold"),
                         text_color=ACCENT, anchor="w").pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(pr, text=f"{len(open_t)} open",
                         font=ctk.CTkFont("Segoe UI", 9),
                         text_color="#1A1A30").pack(side="right")
            ctk.CTkFrame(pc, height=1, fg_color="#0D0D20").pack(fill="x", padx=8, pady=(0, 4))

            for task in open_t:
                self._task_row(pc, task, False)
            for task in done_t[-2:]:
                self._task_row(pc, task, True)
            ctk.CTkFrame(pc, height=6, fg_color="transparent").pack()

    def _task_row(self, parent, task: dict, done: bool) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent", height=24)
        row.pack(fill="x", padx=12, pady=1); row.pack_propagate(False)
        icon  = "✓" if done else "·"
        color = "#161628" if done else "#5060A0"
        txt   = task["text"][:38] + ("…" if len(task["text"]) > 38 else "")
        ctk.CTkLabel(row, text=f"{icon}  {txt}",
                     font=ctk.CTkFont("Segoe UI", 10), text_color=color,
                     anchor="w").pack(side="left", fill="x", expand=True)

    def _rebuild_learn(self) -> None:
        for w in self._scroll.winfo_children():
            w.destroy()
        try:
            from learning_projects import list_all
            projects = list_all()
        except Exception:
            projects = []

        if not projects:
            ctk.CTkLabel(self._scroll,
                         text="Tell G.I.L. what you're studying\nto start a learning project.",
                         text_color="#1A1A30", font=ctk.CTkFont("Segoe UI", 11),
                         justify="center").pack(pady=32)
            return

        for proj in projects:
            card = ctk.CTkFrame(self._scroll, fg_color="#0A0A18", corner_radius=8)
            card.pack(fill="x", pady=(0, 8))
            hdr = ctk.CTkFrame(card, fg_color="transparent", cursor="hand2")
            hdr.pack(fill="x", padx=12, pady=(10, 4))
            ctk.CTkLabel(hdr, text=proj["name"],
                         font=ctk.CTkFont("Segoe UI", 12, "bold"),
                         text_color=ACCENT, anchor="w", cursor="hand2").pack(side="left")
            meta = f"{proj['sessions']} sessions"
            ctk.CTkLabel(hdr, text=meta, font=ctk.CTkFont("Segoe UI", 9),
                         text_color="#1A2A3A").pack(side="right")
            last = proj.get("last_accessed", "")[:10]
            if last:
                ctk.CTkLabel(card, text=f"Last active: {last}",
                             font=ctk.CTkFont("Segoe UI", 9),
                             text_color="#0E1E2E", anchor="w").pack(fill="x", padx=12, pady=(0, 8))


# ── Project view window ───────────────────────────────────────────────────────
class ProjectViewWindow(ctk.CTkToplevel):
    def __init__(self, parent, project_name: str, on_show_3d=None):
        super().__init__(parent)
        self.title(f"G.I.L. — {project_name}")
        self.geometry("520x680")
        self.resizable(True, True)
        self.configure(fg_color="#05050F")
        self._on_show_3d   = on_show_3d
        self._project_name = project_name
        _set_icon(self)
        self._build(project_name)
        self.lift(); self.focus()

    def _build(self, name: str) -> None:
        try:
            from learning_projects import load
            data = load(name)
        except Exception:
            data = {"name": name, "sessions": [], "resources": [], "notes": []}

        hdr = ctk.CTkFrame(self, fg_color="#080818", corner_radius=0, height=52)
        hdr.pack(fill="x"); hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text=name,
                     font=ctk.CTkFont("Segoe UI", 15, "bold"),
                     text_color=ACCENT).pack(side="left", padx=18, pady=10)
        sessions  = data.get("sessions", [])
        resources = data.get("resources", [])
        ctk.CTkLabel(hdr, text=f"{len(sessions)} sessions  ·  {len(resources)} resources",
                     font=ctk.CTkFont("Segoe UI", 9), text_color="#1A2A3A").pack(side="right", padx=16)
        ctk.CTkFrame(self, height=1, fg_color=ACCENT).pack(fill="x")

        models  = [r for r in resources if r.get("type") == "3d_model"]
        studios = [r for r in resources if r.get("type") == "3d_studio"]
        urls    = [r for r in resources if r.get("type") in ("url", "video", "search")]
        if models or studios or urls:
            bar = ctk.CTkFrame(self, fg_color="#080818", height=42)
            bar.pack(fill="x"); bar.pack_propagate(False)
            import webbrowser as _wb
            for res in models[:3]:
                shape = res.get("url", "sphere")
                lbl   = res.get("title", f"3D: {shape}").replace("3D: ", "")
                ctk.CTkButton(bar, text=f"Show {lbl} in 3D", height=28,
                              fg_color="#001828", hover_color="#002A40",
                              text_color=ACCENT, font=ctk.CTkFont("Segoe UI", 9, "bold"),
                              corner_radius=3, command=lambda s=shape: self._show_3d(s),
                              ).pack(side="left", padx=(10, 4), pady=7)
            for res in studios[:3]:
                html_path = res.get("url", "")
                lbl = res.get("title", "3D Studio")
                ctk.CTkButton(bar, text=f"Open {lbl[:18]}", height=28,
                              fg_color="#001A10", hover_color="#002A18",
                              text_color="#00FF88", font=ctk.CTkFont("Segoe UI", 9, "bold"),
                              corner_radius=3, command=lambda p=html_path: self._reopen_studio(p),
                              ).pack(side="left", padx=4, pady=7)
            for res in urls[:3]:
                u = res.get("url", ""); t = res.get("title", u)[:22]
                ctk.CTkButton(bar, text=f"↗ {t}", height=28, fg_color="#0A0A18",
                              hover_color="#0A0A20", text_color="#1A3A5A",
                              font=ctk.CTkFont("Segoe UI", 9), corner_radius=3,
                              command=lambda url=u: _wb.open(url)).pack(side="left", padx=4, pady=7)
            ctk.CTkFrame(self, height=1, fg_color="#0A0A18").pack(fill="x")

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent",
                                         scrollbar_button_color="#0E0E1E",
                                         scrollbar_button_hover_color="#1A1A2A")
        scroll.pack(fill="both", expand=True, padx=8, pady=8)
        if not sessions:
            ctk.CTkLabel(scroll,
                         text="No conversations yet.\nAsk G.I.L. about this topic to start.",
                         text_color="#1A1A30", font=ctk.CTkFont("Segoe UI", 11),
                         justify="center").pack(pady=40)
            return
        for session in sessions:
            date_row = ctk.CTkFrame(scroll, fg_color="transparent", height=24)
            date_row.pack(fill="x", pady=(10, 4))
            ctk.CTkFrame(date_row, height=1, fg_color="#0A0A1A").pack(
                side="left", fill="x", expand=True, padx=(0, 8), pady=12)
            ctk.CTkLabel(date_row, text=session.get("date", ""),
                         font=ctk.CTkFont("Segoe UI", 8),
                         text_color="#1A1A30").pack(side="left")
            ctk.CTkFrame(date_row, height=1, fg_color="#0A0A1A").pack(
                side="left", fill="x", expand=True, padx=(8, 0), pady=12)
            for conv in session.get("conversations", []):
                if conv.get("user"):
                    u_frame = ctk.CTkFrame(scroll, fg_color="transparent")
                    u_frame.pack(fill="x", pady=(2, 1))
                    ctk.CTkFrame(u_frame, fg_color="transparent").pack(side="left", fill="x", expand=True)
                    bubble = ctk.CTkFrame(u_frame, fg_color="#0A1828", corner_radius=10)
                    bubble.pack(side="right", padx=(60, 4))
                    ctk.CTkLabel(bubble, text=conv["user"], font=ctk.CTkFont("Segoe UI", 11),
                                 text_color="#4A8AAA", wraplength=300, justify="right").pack(padx=12, pady=6)
                if conv.get("gil"):
                    g_frame = ctk.CTkFrame(scroll, fg_color="transparent")
                    g_frame.pack(fill="x", pady=(1, 4))
                    bubble = ctk.CTkFrame(g_frame, fg_color="#0A0A18", corner_radius=10)
                    bubble.pack(side="left", padx=(4, 60))
                    ctk.CTkFrame(bubble, width=3, fg_color=ACCENT, corner_radius=2).pack(
                        side="left", fill="y", padx=(6, 0), pady=6)
                    ctk.CTkLabel(bubble, text=conv["gil"], font=ctk.CTkFont("Segoe UI", 11),
                                 text_color="#7090A0", wraplength=300, justify="left").pack(
                                     side="left", padx=(8, 12), pady=6)

    def _show_3d(self, shape: str) -> None:
        if self._on_show_3d:
            self._on_show_3d(shape)
        self.lift()

    def _reopen_studio(self, html_path: str) -> None:
        try:
            from studio3d import reopen_studio
            threading.Thread(target=reopen_studio, args=(html_path,),
                             daemon=True, name="GIL-ReopenStudio").start()
        except Exception as exc:
            print(f"[G.I.L.] {exc}")
        self.lift()

    def refresh(self) -> None:
        for w in self.winfo_children():
            w.destroy()
        self._build(self._project_name)


# ── Speaking bubble popup ─────────────────────────────────────────────────────
class _SpeakBubble(ctk.CTkToplevel):
    """
    Clean GIL speech overlay — Claude-style, no heavy gradients.
    Dark surface card with cyan top accent. Appears below the wave bar.
    """
    W = 700

    def __init__(self, parent):
        super().__init__(parent)
        self.transient(parent)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.0)
        self.configure(fg_color=BG)
        self.attributes("-transparentcolor", BG)
        self._alpha   = 0.0
        self._fade_id = None
        self._visible = False

        outer = ctk.CTkFrame(self, fg_color="#100E24", corner_radius=16,
                             border_width=1, border_color="#1E1840")
        outer.pack(fill="both", expand=True, padx=2, pady=2)

        # Cyan top stripe
        ctk.CTkFrame(outer, height=2, fg_color=ACCENT, corner_radius=0).pack(fill="x")

        content = ctk.CTkFrame(outer, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=16, pady=(12, 14))

        # Avatar column
        av_col = ctk.CTkFrame(content, fg_color="transparent")
        av_col.pack(side="left", padx=(0, 14))
        av = ctk.CTkFrame(av_col, fg_color="#1A1540", corner_radius=14,
                          width=46, height=46, border_width=1, border_color=ACCENT)
        av.pack(); av.pack_propagate(False)
        ctk.CTkLabel(av, text="◈", font=ctk.CTkFont("Segoe UI", 17, "bold"),
                     text_color=ACCENT, fg_color="transparent").pack(expand=True)
        ctk.CTkLabel(av_col, text="G.I.L.",
                     font=ctk.CTkFont("Segoe UI", 7, "bold"),
                     text_color="#1A4060", fg_color="transparent").pack(pady=(4, 0))

        # Text area
        text_area = ctk.CTkFrame(content, fg_color="transparent")
        text_area.pack(side="left", fill="both", expand=True)

        self._lbl = ctk.CTkLabel(
            text_area, text="",
            font=ctk.CTkFont("Segoe UI", 13),
            text_color="#EDE9FF", fg_color="transparent",
            wraplength=self.W - 130,
            justify="left", anchor="w",
        )
        self._lbl.pack(padx=0, pady=4, fill="both", expand=True)

        self.withdraw()
        self.after(50, lambda: _hide_from_taskbar(self))

    def show(self, text: str) -> None:
        self._lbl.configure(text=text)
        self.geometry(f"{self.W}x90")
        self.update_idletasks()
        h  = max(84, self.winfo_reqheight() + 6)
        sw = self.winfo_screenwidth()
        x  = (sw - self.W) // 2
        y  = WIN_H + 10
        self.geometry(f"{self.W}x{h}+{x}+{y}")
        if not self._visible:
            self._visible = True
            self.deiconify()
        self._fade_to(0.96)

    def hide(self) -> None:
        self._visible = False
        self._fade_to(0.0)

    def _fade_to(self, target: float) -> None:
        if self._fade_id:
            try: self.after_cancel(self._fade_id)
            except Exception: pass
        self._step(target)

    def _step(self, target: float) -> None:
        spd = 0.16 if target > self._alpha else 0.10
        if abs(self._alpha - target) < 0.02:
            self._alpha = target
            self.attributes("-alpha", self._alpha)
            if target == 0.0:
                self.withdraw()
            return
        self._alpha += (target - self._alpha) * spd
        self._alpha  = max(0.0, min(1.0, self._alpha))
        self.attributes("-alpha", self._alpha)
        self._fade_id = self.after(16, lambda: self._step(target))



# ── Floating chat launcher (bottom-left, always on screen) ───────────────────
class _FloatingChatButton(ctk.CTkToplevel):
    """
    Always-visible pill at the bottom-left corner.
    Does NOT use transient() so it stays visible even when GIL hides to tray.
    WS_EX_TOOLWINDOW keeps it off the taskbar.
    """
    W, H = 148, 52

    def __init__(self, parent, on_click: callable):
        super().__init__(parent)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.0)
        self.configure(fg_color=BG)
        self.attributes("-transparentcolor", BG)
        self._on_click = on_click
        self._alive    = True
        self._alpha    = 0.0
        self._pulse_t  = 0.0
        self._hidden   = False
        self._hovering = False
        self._last_active = time.time()   # drives idle ghost-fade

        # Offscreen placeholder — real position set in _place() after mapping
        self.geometry(f"{self.W}x{self.H}+16+900")

        cv = tk.Canvas(self, width=self.W, height=self.H,
                       bg=BG, highlightthickness=0)
        cv.pack()
        self._cv = cv

        # Clean pill: dark surface, thin cyan border, ◈ icon + label
        self._bg = cv.create_rectangle(3, 3, self.W - 3, self.H - 3,
                                        fill="#06061A", outline=ACCENT, width=1)
        # ◈ left circle
        cv.create_oval(9, 11, 37, 39, fill="#08082C", outline=ACCENT, width=1)
        cv.create_text(23, 25, text="◈", fill=ACCENT,
                       font=("Segoe UI", 13, "bold"))
        # Divider
        cv.create_line(44, 10, 44, self.H - 10, fill="#0A1428", width=1)
        # Labels
        self._lbl  = cv.create_text(94, 20, text="Chat",
                                     fill="#EEF4FF",
                                     font=("Segoe UI", 11, "bold"))
        self._hint = cv.create_text(94, 34, text="Ctrl+Shift+C",
                                     fill="#1A3050",
                                     font=("Segoe UI", 7))

        cv.bind("<Button-1>", lambda e: self._click())
        cv.bind("<Button-3>", lambda e: self.hide())   # right-click: hide for now
        cv.bind("<Enter>",    self._hover_in)
        cv.bind("<Leave>",    self._hover_out)
        cv.configure(cursor="hand2")

        # Dev mode badge (green dot — shown when developer mode is active)
        self._dev_dot = cv.create_oval(
            self.W - 18, 8, self.W - 6, 20,
            fill="#4ADE80", outline="#080D08", width=2,
            state="hidden",
        )

        # Position and show after the window is fully mapped
        self.after(100, self._place)
        self.after(500, self._update_dev_badge)

    def _place(self) -> None:
        """Pin to the bottom-left corner using the Windows work-area API.
        This is DPI-safe and accounts for taskbar height/position correctly."""
        import ctypes, ctypes.wintypes as _wt
        try:
            # SPI_GETWORKAREA (0x30) — returns usable screen rect excl. taskbar
            rc = _wt.RECT()
            ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rc), 0)
            x = rc.left + 16
            y = rc.bottom - self.H - 4   # 4 px above where taskbar starts
        except Exception:
            sh = self.winfo_screenheight()
            x, y = 16, sh - self.H - 52
        self.geometry(f"{self.W}x{self.H}+{x}+{y}")
        _hide_from_taskbar(self)
        self._fade_in()
        self._pulse()
        self._idle_loop()

    # ── Show / hide (called when chat opens / closes) ─────────────────────────

    def _update_dev_badge(self) -> None:
        """Show/hide the green dev-mode dot. Re-checks every 5 s."""
        try:
            from dev_config import is_enabled
            state = "normal" if is_enabled() else "hidden"
            self._cv.itemconfig(self._dev_dot, state=state)
        except Exception:
            pass
        self.after(5000, self._update_dev_badge)

    def show(self) -> None:
        if self._hidden:
            self._hidden = False
            self._alpha  = 0.0
            self._last_active = time.time()
            self.deiconify()
            _hide_from_taskbar(self)
            self._fade_in()

    def hide(self) -> None:
        if not self._hidden:
            self._hidden = True
            self.withdraw()

    # ── Interactions ──────────────────────────────────────────────────────────

    def _click(self) -> None:
        self._cv.itemconfig(self._bg, fill="#0A2044")
        self.after(130, lambda: self._cv.itemconfig(self._bg, fill="#04041A"))
        self._on_click()

    def _hover_in(self, _) -> None:
        self._hovering    = True
        self._last_active = time.time()
        self._alpha       = 0.95
        try:
            self.attributes("-alpha", 0.95)
        except Exception:
            pass
        self._cv.itemconfig(self._bg,  fill="#16124A", outline="#3FDDFA")
        self._cv.itemconfig(self._lbl, fill=ACCENT)

    def _hover_out(self, _) -> None:
        self._hovering    = False
        self._last_active = time.time()
        self._cv.itemconfig(self._bg,  fill="#0D0B2E", outline="#3FDDFA")
        self._cv.itemconfig(self._lbl, fill="#C0E8FF")

    def _idle_loop(self) -> None:
        """Ghost mode: after 5s of no interaction the pill fades to a faint
        watermark so it stops competing for attention; hover brings it back."""
        if not self._alive:
            return
        try:
            if (not self._hidden and not self._hovering
                    and time.time() - self._last_active > 5.0
                    and self._alpha > 0.30):
                self._alpha = max(0.30, self._alpha - 0.06)
                self.attributes("-alpha", self._alpha)
        except Exception:
            pass
        self.after(120, self._idle_loop)

    # ── Animations ────────────────────────────────────────────────────────────

    def _fade_in(self) -> None:
        self._alpha = min(0.95, self._alpha + 0.055)
        self.attributes("-alpha", self._alpha)
        if self._alpha < 0.95:
            self.after(18, self._fade_in)

    def _pulse(self) -> None:
        if not self._alive:
            return
        self._pulse_t += 0.06
        t = abs(math.sin(self._pulse_t))
        self._cv.itemconfig(self._bg,
                             outline=_blend(ACCENT, "#80EEFF", t * 0.5))
        self.after(45, self._pulse)

    def kill(self) -> None:
        self._alive = False
        try:
            self.destroy()
        except Exception:
            pass


# ── Chat window ───────────────────────────────────────────────────────────────
class ChatWindow(ctk.CTkToplevel):
    """
    Claude-inspired G.I.L. chat — sidebar + context header + copy buttons.

    Layout:  [220px sidebar] | [main: header + messages + input]
    Palette: Deep space purple #0D0B1E, elevated #131028, royal blue #0C1D42
    """

    _THEMES = {
        "dark": dict(
            PAGE="#0D0B1E", SIDE="#100E24", SURF2="#131028", USERBG="#0C1D42",
            BORDER="#1E1840", UBORDER="#1A3870", TXT="#EDE9FF", USERTXT="#A8D0FF",
            NAME_G="#3FDDFA", NAME_U="#4A90E2", MUTED="#4A3A7A", INPUT="#16123A",
            ACCENT="#3FDDFA", PURPLE="#A78BFA", DIMMED="#8A7AAA",
            AVATAR_BG="#1A1540", SEP="#0A0820", SCROLL="#0A0820",
            BTN_TXT="#020810", VERSION_TXT="#28204C", WELCOME_BG="#1C1848",
            WELCOME_BORDER="#2E2870",
        ),
        # Claude-style warm cream light theme (#FAF9F5 family, not blue-white)
        "light": dict(
            PAGE="#FAF9F5", SIDE="#F0EEE5", SURF2="#F4F2EB", USERBG="#FFFFFF",
            BORDER="#E5E3D9", UBORDER="#DAD7C9", TXT="#141413", USERTXT="#3D3929",
            NAME_G="#0B9CB8", NAME_U="#C96442", MUTED="#8D8A7F", INPUT="#FFFFFF",
            ACCENT="#0B9CB8", PURPLE="#7C5CDB", DIMMED="#6E6B60",
            AVATAR_BG="#EFEDE3", SEP="#E5E3D9", SCROLL="#F0EEE5",
            BTN_TXT="#FFFFFF", VERSION_TXT="#C9C6B8", WELCOME_BG="#F0EEE5",
            WELCOME_BORDER="#DDD9CB",
        ),
    }

    @staticmethod
    def _load_theme_pref() -> str:
        try:
            import json
            cfg = json.loads((Path(__file__).parent / "data" / "gil_config.json")
                             .read_text(encoding="utf-8"))
            return cfg.get("chat_theme", "dark")
        except Exception:
            return "dark"

    @staticmethod
    def _save_theme_pref(theme: str) -> None:
        try:
            import json
            p = Path(__file__).parent / "data" / "gil_config.json"
            cfg = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
            cfg["chat_theme"] = theme
            p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        except Exception:
            pass

    def __init__(self, parent, on_send: callable):
        super().__init__(parent)
        self._theme_name = self._load_theme_pref()
        for key, val in self._THEMES[self._theme_name].items():
            setattr(self, f"_{key}", val)

        self.title("G.I.L. — Chat")
        self.geometry("980x840")
        self.minsize(700, 560)
        ctk.set_appearance_mode("dark")
        self.configure(fg_color=self._PAGE)
        _set_icon(self)
        self._gil_window         = parent   # for looking up engine callbacks
        self._on_send           = on_send
        self._typing_id         = None
        self._typing_lbl        = None
        self._typing_frame_ref  = None
        self._typing_phase      = 0
        self._current_session   = ""
        self._session_name_var  = ctk.StringVar(value="New conversation")
        self._last_user_text    = ""   # for regenerate
        self._last_gil_frames   = []   # frames of last GIL response
        # Live activity feed (Claude-style tool cards)
        self._act_card      = None    # current group card frame
        self._act_body      = None    # rows container inside the card
        self._act_header    = None    # header label (updates to "Done")
        self._act_rows      = {}      # activity id -> row widget dict
        self._act_running   = set()   # ids still running (drives spinner)
        self._act_group     = ""      # group label of current card
        self._act_spin_id   = None    # after() handle for spinner animation
        self._act_spin_ph   = 0
        self._build()
        self.update_idletasks()   # resolve geometry so winfo_width is accurate on first render
        self._refresh_sidebar()
        self._load_current()
        try:
            from chat_history import set_session_name_callback, backfill_unnamed_sessions
            set_session_name_callback(self._on_session_auto_named)
            backfill_unnamed_sessions()
        except Exception:
            pass
        self.lift(); self.focus()

    def _on_session_auto_named(self, session_id: str, title: str) -> None:
        """Called (from a background thread) when chat_history finishes auto-naming a session."""
        def _do():
            try:
                if not self.winfo_exists():
                    return
            except Exception:
                return
            if session_id == self._current_session:
                self._session_name_var.set(title)
            self._refresh_sidebar()
        self.after(0, _do)

    # ── Build shell ───────────────────────────────────────────────────────────

    def _build(self) -> None:
        root = ctk.CTkFrame(self, fg_color=self._PAGE, corner_radius=0)
        root.pack(fill="both", expand=True)

        # ── Left sidebar ──────────────────────────────────────────────────────
        self._sidebar = ctk.CTkFrame(root, fg_color=self._SIDE, width=220,
                                      corner_radius=0,
                                      border_width=0)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)

        # GIL logo row
        lr = ctk.CTkFrame(self._sidebar, fg_color="transparent", height=54)
        lr.pack(fill="x"); lr.pack_propagate(False)
        av_s = ctk.CTkFrame(lr, fg_color=self._AVATAR_BG, corner_radius=9,
                            width=30, height=30, border_width=1, border_color=self._ACCENT)
        av_s.pack(side="left", padx=(12, 8), pady=12); av_s.pack_propagate(False)
        ctk.CTkLabel(av_s, text="\u25c8",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=self._ACCENT).pack(expand=True)
        ctk.CTkLabel(lr, text="G.I.L.",
                     font=ctk.CTkFont("Segoe UI", 13, "bold"),
                     text_color=self._TXT).pack(side="left")
        ctk.CTkFrame(self._sidebar, height=1, fg_color=self._BORDER).pack(fill="x", padx=8)

        # Search bar
        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *a: self._refresh_sidebar())
        search_wrap = ctk.CTkFrame(self._sidebar, fg_color=self._PAGE,
                                   corner_radius=8, border_width=1,
                                   border_color=self._BORDER)
        search_wrap.pack(fill="x", padx=10, pady=(8, 4))
        ctk.CTkLabel(search_wrap, image=_icon("search", self._MUTED, 13), text="",
                     ).pack(side="left", padx=(8, 2))
        ctk.CTkEntry(search_wrap, textvariable=self._search_var,
                     placeholder_text="Search chats...",
                     font=ctk.CTkFont("Segoe UI", 11),
                     fg_color="transparent", border_width=0,
                     text_color=self._TXT,
                     placeholder_text_color=self._MUTED,
                     height=30).pack(side="left", fill="x", expand=True, padx=(0, 6), pady=4)

        # New chat button
        ctk.CTkButton(
            self._sidebar, text="+ New Chat", height=38,
            fg_color=self._ACCENT, hover_color="#00B8D4",
            text_color=self._BTN_TXT, font=ctk.CTkFont("Segoe UI", 12, "bold"),
            corner_radius=10, command=self._new_chat,
        ).pack(fill="x", padx=12, pady=(14, 10))

        ctk.CTkFrame(self._sidebar, height=1, fg_color=self._BORDER).pack(fill="x", padx=8)

        # Session list (scrollable)
        self._session_list = ctk.CTkScrollableFrame(
            self._sidebar, fg_color="transparent",
            scrollbar_button_color=self._BORDER,
            scrollbar_button_hover_color="#2A2460",
        )
        self._session_list.pack(fill="both", expand=True, pady=(6, 0))

        # Bottom info strip
        ctk.CTkFrame(self._sidebar, height=1, fg_color=self._BORDER).pack(fill="x", padx=8)
        # Export + Starred quick actions
        actions_row = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        actions_row.pack(fill="x", padx=8, pady=4)
        ctk.CTkButton(actions_row, text=" Export", image=_icon("export", self._MUTED, 13),
                      compound="left", width=90, height=28,
                      fg_color="transparent", hover_color="#1A1640",
                      text_color=self._MUTED, font=ctk.CTkFont("Segoe UI", 10),
                      corner_radius=6, command=self._export_chat,
                      ).pack(side="left", padx=(0, 4))
        ctk.CTkButton(actions_row, text=" Starred", image=_icon("star_outline", self._MUTED, 13),
                      compound="left", width=90, height=28,
                      fg_color="transparent", hover_color="#1A1640",
                      text_color=self._MUTED, font=ctk.CTkFont("Segoe UI", 10),
                      corner_radius=6, command=self._show_starred,
                      ).pack(side="left")
        ctk.CTkFrame(self._sidebar, height=1, fg_color=self._BORDER).pack(fill="x", padx=8)
        info_bar = ctk.CTkFrame(self._sidebar, fg_color="transparent", height=44)
        info_bar.pack(fill="x"); info_bar.pack_propagate(False)
        try:
            from version import VERSION as _V
            ver = _V
        except Exception:
            ver = "1.x"
        ctk.CTkLabel(info_bar, text=f"G.I.L. v{ver}  •  llama-3.1",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color=self._VERSION_TXT).pack(anchor="w", padx=12, pady=14)

        # ── Separator ─────────────────────────────────────────────────────────
        ctk.CTkFrame(root, width=1, fg_color=self._BORDER).pack(side="left", fill="y")

        # ── Main pane ─────────────────────────────────────────────────────────
        main = ctk.CTkFrame(root, fg_color=self._PAGE, corner_radius=0)
        main.pack(side="left", fill="both", expand=True)

        # ── Context header ────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(main, fg_color=self._SIDE, corner_radius=0, height=60)
        hdr.pack(fill="x"); hdr.pack_propagate(False)

        # Avatar
        av_h = ctk.CTkFrame(hdr, fg_color=self._AVATAR_BG, corner_radius=10,
                            width=36, height=36,
                            border_width=1, border_color=self._ACCENT)
        av_h.pack(side="left", padx=(14, 8), pady=12); av_h.pack_propagate(False)
        ctk.CTkLabel(av_h, text="\u25c8",
                     font=ctk.CTkFont("Segoe UI", 13, "bold"),
                     text_color=self._ACCENT).pack(expand=True)
        # Session name column
        nc = ctk.CTkFrame(hdr, fg_color="transparent")
        nc.pack(side="left", fill="y", pady=8)
        name_entry = ctk.CTkEntry(
            nc, textvariable=self._session_name_var,
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            fg_color="transparent", border_width=0,
            text_color=self._TXT, width=260,
        )
        name_entry.pack(anchor="w")
        name_entry.bind("<Return>",   lambda e: self._save_session_name())
        name_entry.bind("<FocusOut>", lambda e: self._save_session_name())
        ctk.CTkLabel(nc, text="G.I.L.  \u2022  Generative Intelligence Liaison",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color=self._MUTED, anchor="w").pack(anchor="w")

        # Theme toggle (sun/moon)
        theme_icon = "sun" if self._theme_name == "dark" else "moon"
        ctk.CTkButton(hdr, text="", image=_icon(theme_icon, self._MUTED, 15),
                      width=32, height=32,
                      fg_color="transparent", hover_color=self._BORDER,
                      corner_radius=8, command=self._toggle_theme,
                      ).pack(side="right", padx=(0, 4), pady=10)

        # Context badges
        self._badge_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        self._badge_frame.pack(side="right", padx=14, pady=10)
        self._update_context_badges()

        ctk.CTkFrame(main, height=1, fg_color=self._SEP).pack(fill="x")

        # ── Scroll area ───────────────────────────────────────────────────────
        self._scroll = ctk.CTkScrollableFrame(
            main, fg_color=self._SCROLL,
            scrollbar_button_color=self._BORDER,
            scrollbar_button_hover_color=self._UBORDER,
        )
        self._scroll.pack(fill="both", expand=True)
        try:
            from tkinterdnd2 import DND_FILES
            self._textbox.drop_target_register(DND_FILES)
            self._textbox.dnd_bind("<<Drop>>", self._on_drop)
            self._scroll.drop_target_register(DND_FILES)
            self._scroll.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

        # ── Input bar ─────────────────────────────────────────────────────────
        ctk.CTkFrame(main, height=1, fg_color=self._SEP).pack(fill="x")
        bar = ctk.CTkFrame(main, fg_color=self._SIDE, corner_radius=0)
        bar.pack(fill="x")

        self._input_wrap = ctk.CTkFrame(
            bar, fg_color=self._INPUT,
            corner_radius=14, border_width=1, border_color=self._BORDER,
        )
        self._input_wrap.pack(fill="x", padx=18, pady=14)
        self._input_wrap.bind("<Button-1>", self._activate_input)

        # Multi-line textbox (like Claude)
        self._textbox = ctk.CTkTextbox(
            self._input_wrap,
            font=ctk.CTkFont("Segoe UI", 13),
            fg_color="transparent",
            border_width=0,
            text_color=self._TXT,
            height=70,
            wrap="word",
            activate_scrollbars=False,
        )
        self._textbox.pack(side="left", fill="x", expand=True, padx=(14, 6), pady=10)
        self._textbox.bind("<Return>",       self._on_enter)
        self._textbox.bind("<Shift-Return>", self._on_shift_enter)
        self._textbox.bind("<Button-1>",     self._activate_input)
        self._textbox.bind("<FocusOut>",     self._restore_placeholder)
        self._textbox.bind("<KeyRelease>",   self._on_textbox_keyrelease)
        self._textbox.bind("<Up>",           self._on_textbox_up)
        self._textbox.bind("<Down>",         self._on_textbox_down)
        self._textbox.bind("<Escape>",       self._on_textbox_escape)
        self._slash_menu = None
        self._placeholder_active = True
        self._textbox.insert("0.0", "Message G.I.L.  •  Enter sends  •  Shift+Enter = new line")
        self._textbox.configure(text_color=self._MUTED, state="disabled")

        # File upload button
        ctk.CTkButton(
            self._input_wrap, text="", image=_icon("attach", self._MUTED, 16),
            width=36, height=36,
            fg_color="transparent", hover_color="#1A1640",
            corner_radius=8, command=self._upload_file,
        ).pack(side="right", padx=(0, 2), pady=8)
        send_col = ctk.CTkFrame(self._input_wrap, fg_color="transparent")
        send_col.pack(side="right", padx=(0, 8), pady=8)
        ctk.CTkButton(
            send_col, text="", image=_icon("send", "#020810", 18),
            fg_color=self._ACCENT, hover_color="#00B8D4",
            text_color="#020810", font=ctk.CTkFont("Segoe UI", 18, "bold"),
            corner_radius=10, command=self._send,
        ).pack()

    # ── Placeholder handling ──────────────────────────────────────────────────

    def _activate_input(self, event=None) -> None:
        """Clear placeholder, enable textbox, give focus."""
        if self._placeholder_active:
            self._textbox.configure(state="normal", text_color=self._TXT)
            self._textbox.delete("0.0", "end")
            self._placeholder_active = False
            self._input_wrap.configure(border_color=self._ACCENT)
        else:
            self._textbox.configure(state="normal")
        self._textbox.focus()

    def _restore_placeholder(self, event=None) -> None:
        self._textbox.configure(state="normal")
        content = self._textbox.get("0.0", "end-1c").strip()
        if not content:
            self._textbox.delete("0.0", "end")
            self._textbox.insert("0.0", "Message G.I.L.  •  Enter sends  •  Shift+Enter = new line")
            self._textbox.configure(text_color=self._MUTED, state="disabled")
            self._placeholder_active = True
            self._input_wrap.configure(border_color=self._BORDER)

    def _on_enter(self, event) -> str:
        if self._slash_menu and self._slash_menu.is_open():
            self._slash_menu.pick_selected()
            return "break"
        if not self._placeholder_active:
            self._textbox.configure(state="normal")
            self._send()
        return "break"

    def _on_shift_enter(self, event) -> None:
        pass   # allow default newline insert

    # ── Slash command menu wiring ────────────────────────────────────────────

    def _on_textbox_keyrelease(self, event) -> None:
        if self._placeholder_active:
            return
        content = self._textbox.get("0.0", "end-1c")
        if content.startswith("/") and "\n" not in content and " " not in content:
            query = content[1:]
            if self._slash_menu is None:
                self._slash_menu = _SlashMenu(self, self._on_slash_pick)
            has_matches = self._slash_menu.filter(query)
            if has_matches:
                x = self._input_wrap.winfo_rootx()
                y = self._input_wrap.winfo_rooty()
                w = self._input_wrap.winfo_width()
                self._slash_menu.show_at(x, y, w)
            else:
                self._slash_menu.hide()
        elif self._slash_menu:
            self._slash_menu.hide()

    def _on_textbox_up(self, event):
        if self._slash_menu and self._slash_menu.is_open():
            self._slash_menu.move(-1)
            return "break"
        return None

    def _on_textbox_down(self, event):
        if self._slash_menu and self._slash_menu.is_open():
            self._slash_menu.move(1)
            return "break"
        return None

    def _on_textbox_escape(self, event):
        if self._slash_menu and self._slash_menu.is_open():
            self._slash_menu.hide()
            return "break"
        return None

    def _on_slash_pick(self, item) -> None:
        cmd, _desc, expansion, auto_send = item
        if self._slash_menu:
            self._slash_menu.hide()
        if cmd == "/clear":
            self._new_chat()
            return
        if auto_send and expansion:
            # Complete command — do the action immediately, no review step.
            # The user already knows what they asked for; don't make them
            # re-read and press Enter on text they just selected.
            self._clear_input()
            self._send_text(expansion)
            return
        # Template command — populate input so the user can finish typing
        self._textbox.configure(state="normal", text_color=self._TXT)
        self._textbox.delete("0.0", "end")
        if expansion:
            self._textbox.insert("0.0", expansion)
        self._placeholder_active = False
        self._input_wrap.configure(border_color=self._ACCENT)
        self._textbox.focus()
        self._textbox.mark_set("insert", "end")

    def _get_input(self) -> str:
        if self._placeholder_active:
            return ""
        self._textbox.configure(state="normal")
        return self._textbox.get("0.0", "end").strip()

    def _clear_input(self) -> None:
        self._textbox.configure(state="normal")
        self._textbox.delete("0.0", "end")
        self._restore_placeholder()

    # ── Context badges ────────────────────────────────────────────────────────

    def _update_context_badges(self) -> None:
        try:
            if not self.winfo_exists(): return
        except Exception: return
        for w in self._badge_frame.winfo_children():
            w.destroy()

        badges = []

        # Git branch — runs in a background thread (subprocess spawn cost
        # was blocking window open for 1+ second). Appended async below.
        threading.Thread(target=self._fetch_git_branch_badge, daemon=True,
                         name="GIL-GitBadge").start()

        # Model (clickable to cycle)
        try:
            from gil_brain import GROQ_MODEL
            badges.append(("robot", GROQ_MODEL[:18], self._ACCENT, "#0D0B2E", "#1E1840", True))
        except Exception:
            badges.append(("robot", "llama-3.1", self._ACCENT, "#0D0B2E", "#1E1840", True))

        # Dev mode
        try:
            from dev_config import is_enabled
            if is_enabled():
                badges.append((None, "● DEV", "#22C55E", "#0A2820", "#1E4030", False))
        except Exception:
            pass

        # Token usage indicator
        try:
            tok = getattr(self, "_engine_ref", None)
            tokens = tok.brain.last_tokens_used if tok else 0
            if tokens > 0:
                pct = min(100, int(tokens / 32_768 * 100))
                col = "#22C55E" if pct < 60 else ("#F59E0B" if pct < 85 else "#EF4444")
                badges.append(("activity", f"{tokens:,} tok", col, "#0D0B2E", "#1E1840", False))
        except Exception:
            pass

        for icon_name, badge_text, fg, bg, border, clickable in badges:
            b = ctk.CTkFrame(self._badge_frame, fg_color=bg,
                             corner_radius=7, border_width=1,
                             border_color=border)
            b.pack(side="left", padx=(0, 6))
            row = ctk.CTkFrame(b, fg_color="transparent")
            row.pack(padx=8, pady=3)
            if icon_name:
                isz = 14 if icon_name == "robot" else 11
                ctk.CTkLabel(row, image=_icon(icon_name, fg, isz), text="",
                             ).pack(side="left", padx=(0, 4))
            lbl = ctk.CTkLabel(row, text=badge_text,
                               font=ctk.CTkFont("Segoe UI", 10, "bold"),
                               text_color=fg)
            lbl.pack(side="left")
            if clickable:
                lbl.bind("<Button-1>", lambda e: self._cycle_model())
                b.configure(cursor="hand2")
                row.bind("<Button-1>", lambda e: self._cycle_model())

        # Schedule next refresh
        self.after(10_000, self._update_context_badges)

    def _fetch_git_branch_badge(self) -> None:
        """Background thread: spawning git is slow (~1s); never block window open on it."""
        try:
            import subprocess
            r = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if r.returncode == 0 and r.stdout.strip():
                branch = r.stdout.strip()
                self.after(0, lambda: self._add_branch_badge(branch))
        except Exception:
            pass

    def _add_branch_badge(self, branch: str) -> None:
        try:
            if not self.winfo_exists() or not self._badge_frame.winfo_exists():
                return
        except Exception:
            return
        b = ctk.CTkFrame(self._badge_frame, fg_color="#0D0B2E",
                         corner_radius=7, border_width=1, border_color="#1E1840")
        b.pack(side="left", padx=(0, 6), before=self._badge_frame.winfo_children()[0]
               if self._badge_frame.winfo_children() else None)
        ctk.CTkLabel(b, text="⎇ " + branch,
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color=self._PURPLE).pack(padx=8, pady=3)

    # ── Session name ──────────────────────────────────────────────────────────

    def _save_session_name(self) -> None:
        name = self._session_name_var.get().strip()
        if name and self._current_session:
            try:
                from chat_history import rename_session
                rename_session(self._current_session, name)
                self._refresh_sidebar()
            except Exception:
                pass

    # ── Sidebar session list ──────────────────────────────────────────────────

    def _refresh_sidebar(self) -> None:
        try:
            if not self.winfo_exists(): return
        except Exception: return
        for w in self._session_list.winfo_children():
            w.destroy()

        try:
            from chat_history import list_sessions
            sessions = list_sessions(30)
        except Exception:
            sessions = []

        if not sessions:
            ctk.CTkLabel(self._session_list, text="No previous chats",
                         font=ctk.CTkFont("Segoe UI", 10),
                         text_color=self._MUTED).pack(pady=20)
            return

        # Group by date
        import datetime as _dt
        today     = _dt.date.today()
        yesterday = today - _dt.timedelta(days=1)
        groups    = {}

        for s in sessions:
            d = _dt.datetime.fromtimestamp(s["started_at"]).date()
            if d == today:
                grp = "Today"
            elif d == yesterday:
                grp = "Yesterday"
            elif (today - d).days < 7:
                grp = d.strftime("%A")
            else:
                grp = d.strftime("%B %d")
            groups.setdefault(grp, []).append(s)

        for grp_name, items in groups.items():
            ctk.CTkLabel(self._session_list, text=grp_name,
                         font=ctk.CTkFont("Segoe UI", 9, "bold"),
                         text_color=self._MUTED,
                         anchor="w").pack(fill="x", padx=10, pady=(10, 4))

            for s in items:
                display = s["name"] or (s["preview"] or "Chat")[:32]
                is_cur  = s["id"] == self._current_session
                btn = ctk.CTkFrame(
                    self._session_list,
                    fg_color=self._AVATAR_BG if is_cur else "transparent",
                    corner_radius=8,
                    cursor="hand2",
                )
                btn.pack(fill="x", padx=4, pady=1)

                row_top = ctk.CTkFrame(btn, fg_color="transparent")
                row_top.pack(fill="x", padx=(10, 4), pady=(6, 0))
                ctk.CTkLabel(row_top,
                             text=display,
                             font=ctk.CTkFont("Segoe UI", 11,
                                              "bold" if is_cur else "normal"),
                             text_color=self._TXT if is_cur else self._DIMMED,
                             anchor="w",
                             wraplength=150).pack(side="left", fill="x", expand=True)
                sid = s["id"]
                del_btn = ctk.CTkButton(
                    row_top, text="", image=_icon("trash", self._MUTED, 12),
                    width=22, height=20, fg_color="transparent",
                    hover_color="#5A1A2A", corner_radius=4,
                    command=lambda i=sid, b=btn: self._confirm_delete_session(i, b),
                )
                del_btn.pack(side="right")

                preview_raw = s.get("preview") or ""
                preview_txt = (preview_raw[:40] + "…") if len(preview_raw) > 40 else preview_raw
                ctk.CTkLabel(btn,
                             text=preview_txt or f"{s['msg_count']} messages",
                             font=ctk.CTkFont("Segoe UI", 9),
                             text_color=self._MUTED, anchor="w",
                             wraplength=180).pack(
                                 fill="x", padx=10, pady=(0, 6))

                btn.bind("<Button-1>", lambda e, i=sid, n=display: self._open_session(i, n))
                open_handler = lambda e, i=sid, n=display: self._open_session(i, n)
                for child in btn.winfo_children():
                    if child is del_btn:
                        continue
                    child.bind("<Button-1>", open_handler)
                    for grandchild in child.winfo_children():
                        if grandchild is del_btn:
                            continue
                        grandchild.bind("<Button-1>", open_handler)

    def _confirm_delete_session(self, session_id: str, row_frame) -> None:
        """
        Inline confirm — replaces the row's content with Yes/No for a few
        seconds instead of deleting immediately or popping a modal dialog.
        """
        try:
            if not row_frame.winfo_exists():
                return
        except Exception:
            return
        for w in row_frame.winfo_children():
            w.destroy()
        confirm_row = ctk.CTkFrame(row_frame, fg_color="transparent")
        confirm_row.pack(fill="x", padx=10, pady=8)
        ctk.CTkLabel(confirm_row, text="Delete this chat?",
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color=self._TXT).pack(side="left")
        ctk.CTkButton(confirm_row, text="Yes", width=40, height=22,
                      fg_color="#7A1F2E", hover_color="#9A2838",
                      text_color="#FFD8D8", font=ctk.CTkFont("Segoe UI", 9, "bold"),
                      corner_radius=5,
                      command=lambda: self._do_delete_session(session_id),
                      ).pack(side="right", padx=(4, 0))
        ctk.CTkButton(confirm_row, text="No", width=40, height=22,
                      fg_color="transparent", hover_color=self._BORDER,
                      text_color=self._MUTED, font=ctk.CTkFont("Segoe UI", 9),
                      corner_radius=5,
                      command=self._refresh_sidebar,
                      ).pack(side="right")

    def _do_delete_session(self, session_id: str) -> None:
        try:
            from chat_history import delete_session
            delete_session(session_id)
        except Exception:
            pass
        if session_id == self._current_session:
            self._new_chat()
        else:
            self._refresh_sidebar()

    def _toggle_theme(self) -> None:
        """
        Switch dark/light IN PLACE: swap the palette, rebuild the window's
        content, and restore the open session — the Toplevel itself never
        closes, so there's no flicker of the window disappearing/reappearing.
        """
        new_theme = "light" if self._theme_name == "dark" else "dark"
        self._save_theme_pref(new_theme)
        self._theme_name = new_theme
        for key, val in self._THEMES[new_theme].items():
            setattr(self, f"_{key}", val)

        current_session = self._current_session
        # Cancel animations and drop references into widgets we're destroying
        for handle in ("_typing_id", "_act_spin_id"):
            h = getattr(self, handle, None)
            if h:
                try:
                    self.after_cancel(h)
                except Exception:
                    pass
                setattr(self, handle, None)
        self._typing_lbl = self._typing_frame_ref = None
        self._act_card = self._act_body = self._act_header = None
        self._act_rows = {}; self._act_running = set(); self._act_group = ""
        self._last_gil_frames = []
        self._slash_menu = None

        for w in self.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass
        self.configure(fg_color=self._PAGE)
        self._build()
        self.update_idletasks()
        self._refresh_sidebar()
        try:
            from chat_history import list_sessions
            sessions = {s["id"]: s for s in list_sessions(30)}
            if current_session in sessions:
                s = sessions[current_session]
                self._open_session(current_session, s.get("name") or "Conversation")
            else:
                self._load_current()
        except Exception:
            self._load_current()

    def _new_chat(self) -> None:
        try:
            from chat_history import new_chat_session
            self._current_session = new_chat_session()
        except Exception:
            import uuid
            self._current_session = str(uuid.uuid4())
        self._session_name_var.set("New conversation")
        for w in self._scroll.winfo_children():
            w.destroy()
        self._show_welcome()
        self._refresh_sidebar()

    def _open_session(self, session_id: str, name: str) -> None:
        self._current_session = session_id
        self._session_name_var.set(name)
        for w in self._scroll.winfo_children():
            w.destroy()
        try:
            from chat_history import load_session, set_current_session
            set_current_session(session_id)   # new messages append to this session now
            messages = load_session(session_id)
        except Exception:
            messages = []
        for msg in messages:
            self._render_bubble(msg["content"], msg["sender"], msg["ts"], save=False)
        if not messages:
            self._show_welcome()
        self.after(100, lambda: self._scroll._parent_canvas.yview_moveto(1.0))
        self._refresh_sidebar()

    # ── Load current session ──────────────────────────────────────────────────

    def _load_current(self) -> None:
        try:
            from chat_history import get_current_session, load_recent, list_sessions
            self._current_session = get_current_session()
            sessions = list_sessions(1)
            if sessions:
                name = sessions[0].get("name") or "Conversation"
                self._session_name_var.set(name)
            messages = load_recent(20)   # keep initial paint fast — see _finish() note above
        except Exception:
            messages = []

        if not messages:
            self._show_welcome()
            return

        import datetime as _dt
        prev_day = prev_session = None
        for msg in messages:
            day_str = _dt.datetime.fromtimestamp(msg["ts"]).strftime("%Y-%m-%d")
            if day_str != prev_day:
                prev_day = day_str
                self._date_divider(self._friendly_date(msg["ts"]))
            elif msg["is_session_start"] and prev_session is not None:
                self._session_divider()
            prev_session = msg["session_id"]
            self._render_bubble(msg["content"], msg["sender"], msg["ts"], save=False)
        self._session_divider("Now")
        self.after(100, lambda: self._scroll._parent_canvas.yview_moveto(1.0))

    # ── Dividers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _friendly_date(ts: float) -> str:
        import datetime as _dt
        d     = _dt.datetime.fromtimestamp(ts).date()
        today = _dt.date.today()
        delta = (today - d).days
        if delta == 0:   return "Today"
        if delta == 1:   return "Yesterday"
        if delta < 7:    return d.strftime("%A")
        return d.strftime("%B %d")

    def _date_divider(self, label: str) -> None:
        row = ctk.CTkFrame(self._scroll, fg_color="transparent")
        row.pack(fill="x", pady=(14, 6))
        ctk.CTkFrame(row, height=1, fg_color=self._BORDER).pack(
            side="left", fill="x", expand=True, pady=8, padx=(28, 8))
        ctk.CTkLabel(row, text=label,
                     font=ctk.CTkFont("Segoe UI", 9, "bold"),
                     text_color=self._MUTED, fg_color=self._SCROLL).pack(side="left")
        ctk.CTkFrame(row, height=1, fg_color=self._BORDER).pack(
            side="left", fill="x", expand=True, pady=8, padx=(8, 28))

    def _session_divider(self, label: str = "Earlier") -> None:
        row = ctk.CTkFrame(self._scroll, fg_color="transparent")
        row.pack(fill="x", pady=(8, 4))
        ctk.CTkFrame(row, height=1, fg_color=self._SEP).pack(
            side="left", fill="x", expand=True, pady=6, padx=(28, 8))
        ctk.CTkLabel(row, text=label,
                     font=ctk.CTkFont("Segoe UI", 8),
                     text_color=self._DIMMED, fg_color=self._SCROLL).pack(side="left")
        ctk.CTkFrame(row, height=1, fg_color=self._SEP).pack(
            side="left", fill="x", expand=True, pady=6, padx=(8, 28))

    # ── Welcome ───────────────────────────────────────────────────────────────

    def _show_welcome(self) -> None:
        wrap = ctk.CTkFrame(self._scroll, fg_color="transparent")
        wrap.pack(fill="x", padx=32, pady=(40, 24))
        card = ctk.CTkFrame(wrap, fg_color=self._WELCOME_BG, corner_radius=20,
                            border_width=1, border_color=self._WELCOME_BORDER)
        card.pack(fill="x")
        # Top accent
        ctk.CTkFrame(card, height=2, fg_color=self._ACCENT,
                     corner_radius=0).pack(fill="x")
        hrow = ctk.CTkFrame(card, fg_color="transparent")
        hrow.pack(fill="x", padx=22, pady=(22, 16))
        av = ctk.CTkFrame(hrow, fg_color=self._AVATAR_BG, corner_radius=14,
                          width=50, height=50, border_width=1, border_color=self._ACCENT)
        av.pack(side="left", padx=(0, 18)); av.pack_propagate(False)
        ctk.CTkLabel(av, text="◈", font=ctk.CTkFont("Segoe UI", 20, "bold"),
                     text_color=self._ACCENT).pack(expand=True)
        tc = ctk.CTkFrame(hrow, fg_color="transparent")
        tc.pack(side="left")
        ctk.CTkLabel(tc, text="G.I.L. is ready",
                     font=ctk.CTkFont("Segoe UI", 18, "bold"),
                     text_color=self._TXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(tc, text="Generative Intelligence Liaison  •  Always on",
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color=self._MUTED, anchor="w").pack(anchor="w")
        ctk.CTkFrame(card, height=1, fg_color=self._BORDER).pack(fill="x", padx=16)
        cf = ctk.CTkFrame(card, fg_color="transparent")
        cf.pack(fill="x", padx=16, pady=(14, 18))
        for label, color in [("Voice", "#3FDDFA"), ("Hebrew", "#A78BFA"),
                              ("Git", "#22C55E"), ("Docker", "#F59E0B"),
                              ("Images", "#3FDDFA"), ("Search", "#A78BFA")]:
            ch = ctk.CTkFrame(cf, fg_color=self._PAGE, corner_radius=10,
                              border_width=1, border_color=self._BORDER)
            ch.pack(side="left", padx=(0, 7))
            ctk.CTkLabel(ch, text=f" {label} ",
                         font=ctk.CTkFont("Segoe UI", 10, "bold"),
                         text_color=color).pack(pady=6, padx=2)

    # ── Typing indicator ──────────────────────────────────────────────────────

    def show_typing(self) -> None:
        def _do():
            if self._typing_frame_ref:
                return
            frame = ctk.CTkFrame(self._scroll, fg_color=self._SURF2, corner_radius=0)
            frame.pack(fill="x")
            row = ctk.CTkFrame(frame, fg_color="transparent")
            row.pack(fill="x", padx=28, pady=(12, 12))
            av = ctk.CTkFrame(row, fg_color=self._AVATAR_BG, corner_radius=8,
                              width=26, height=26, border_width=1,
                              border_color=self._ACCENT)
            av.pack(side="left", anchor="n", padx=(0, 10), pady=(2, 0))
            av.pack_propagate(False)
            ctk.CTkLabel(av, text="◈", font=ctk.CTkFont("Segoe UI", 9, "bold"),
                         text_color=self._ACCENT).pack(expand=True)
            lbl = ctk.CTkLabel(row, text="  ●  ·  ·  ",
                               font=ctk.CTkFont("Segoe UI", 16, "bold"),
                               text_color="#2A2060", anchor="w")
            lbl.pack(side="left")
            self._typing_lbl       = lbl
            self._typing_frame_ref = frame
            self._typing_phase     = 0
            self._animate_dots()
            try:
                self._scroll._parent_canvas.yview_moveto(1.0)
            except Exception:
                pass
        self.after(0, _do)

    def hide_typing(self) -> None:
        def _do():
            if self._typing_id:
                try: self.after_cancel(self._typing_id)
                except Exception: pass
                self._typing_id = None
            if self._typing_frame_ref:
                try: self._typing_frame_ref.destroy()
                except Exception: pass
                self._typing_frame_ref = None
                self._typing_lbl       = None
        self.after(0, _do)

    def _animate_dots(self) -> None:
        if not self._typing_lbl:
            return
        patterns = ["  ●  ·  ·  ", "  ·  ●  ·  ", "  ·  ·  ●  ", "  ·  ●  ·  "]
        try:
            self._typing_lbl.configure(text=patterns[self._typing_phase % 4])
        except Exception:
            return
        self._typing_phase += 1
        self._typing_id = self.after(300, self._animate_dots)

    # ── Live activity feed (Claude-style tool cards) ──────────────────────────
    #
    # Every action GIL performs is published on the activity bus and rendered
    # here as a row inside a grouped "working" card: spinner while running,
    # ✓/✕ with duration when finished. Missions get their own titled card.

    _ACT_SPIN  = ["◐", "◓", "◑", "◒"]
    _ACT_OK    = "#22C55E"
    _ACT_FAIL  = "#EF4444"

    def on_activity(self, entry: dict) -> None:
        """Main-thread entry point — called via GILWindow.after(0, ...)."""
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        aid = entry["id"]
        if aid in self._act_rows:
            self._update_activity_row(aid, entry)
            return
        self._ensure_activity_card(entry.get("group") or "")
        self._add_activity_row(aid, entry)
        try:
            self._scroll._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _card_alive(self) -> bool:
        card = self._act_card
        if card is None:
            return False
        try:
            return bool(card.winfo_exists())
        except Exception:
            return False

    def _ensure_activity_card(self, group: str) -> None:
        # Reuse the open card while the group matches; _maybe_finish_card
        # clears _act_card after 4s of quiet, which forces a fresh card.
        if self._card_alive() and self._act_group == group:
            return
        # Start a fresh card
        self._act_rows    = {}
        self._act_running = set()
        self._act_group   = group

        holder = ctk.CTkFrame(self._scroll, fg_color="transparent")
        holder.pack(fill="x")
        card = ctk.CTkFrame(holder, fg_color=self._INPUT, corner_radius=12,
                            border_width=1, border_color=self._BORDER)
        card.pack(anchor="w", padx=28, pady=(6, 6), fill="x")

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(8, 2))
        ctk.CTkLabel(head, text="", image=_icon("activity", self._ACCENT, 13),
                     width=16).pack(side="left", padx=(0, 7))
        title = group if group else "G.I.L. is working"
        hdr = ctk.CTkLabel(head, text=title,
                           font=ctk.CTkFont("Segoe UI", 10, "bold"),
                           text_color=self._DIMMED, anchor="w")
        hdr.pack(side="left")

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=14, pady=(0, 8))

        self._act_card   = holder
        self._act_body   = body
        self._act_header = hdr

    def _add_activity_row(self, aid: int, entry: dict) -> None:
        row = ctk.CTkFrame(self._act_body, fg_color="transparent")
        row.pack(fill="x", pady=1)
        running = entry["status"] == "running"
        st = ctk.CTkLabel(row, text=self._ACT_SPIN[0] if running else
                          ("✓" if entry["status"] == "done" else "✕"),
                          width=18,
                          font=ctk.CTkFont("Segoe UI", 11, "bold"),
                          text_color=self._ACCENT if running else
                          (self._ACT_OK if entry["status"] == "done"
                           else self._ACT_FAIL))
        st.pack(side="left")
        lbl = ctk.CTkLabel(row, text=entry["title"],
                           font=ctk.CTkFont("Segoe UI", 11),
                           text_color=self._TXT, anchor="w")
        lbl.pack(side="left", padx=(6, 0))
        dur = ctk.CTkLabel(row, text="",
                           font=ctk.CTkFont("Segoe UI", 9),
                           text_color=self._MUTED, anchor="e")
        dur.pack(side="right")
        det = ctk.CTkLabel(row, text=(entry.get("detail") or "")[:80],
                           font=ctk.CTkFont("Segoe UI", 9),
                           text_color=self._MUTED, anchor="w")
        det.pack(side="left", padx=(10, 6))
        self._act_rows[aid] = {"status": st, "label": lbl, "detail": det,
                               "dur": dur}
        if running:
            self._act_running.add(aid)
            self._start_act_spinner()
        else:
            self._maybe_finish_card()

    def _update_activity_row(self, aid: int, entry: dict) -> None:
        w = self._act_rows.get(aid)
        if not w:
            return
        try:
            if not w["status"].winfo_exists():
                return
        except Exception:
            return
        status = entry["status"]
        if status == "running":
            w["detail"].configure(text=(entry.get("detail") or "")[:80])
            return
        w["status"].configure(
            text="✓" if status == "done" else "✕",
            text_color=self._ACT_OK if status == "done" else self._ACT_FAIL)
        detail = (entry.get("detail") or "")[:80]
        if detail:
            w["detail"].configure(text=detail)
        d = entry.get("duration") or 0.0
        if d >= 0.05:
            w["dur"].configure(text=f"{d:.1f}s" if d < 60 else f"{d/60:.1f}m")
        self._act_running.discard(aid)
        self._maybe_finish_card()

    def _maybe_finish_card(self) -> None:
        if self._act_running:
            return
        if self._act_spin_id:
            try:
                self.after_cancel(self._act_spin_id)
            except Exception:
                pass
            self._act_spin_id = None
        hdr, rows = self._act_header, self._act_rows
        if hdr is not None and rows:
            try:
                fails = 0   # recompute from row glyphs
                for w in rows.values():
                    if w["status"].cget("text") == "✕":
                        fails += 1
                n = len(rows)
                base = self._act_group or "Worked"
                txt = (f"{base} — {n - fails}/{n} steps done" if self._act_group
                       else (f"Done — {n} step{'s' if n > 1 else ''}" if not fails
                             else f"Finished — {n - fails}/{n} ok"))
                hdr.configure(text=txt)
            except Exception:
                pass
        # After a quiet period the card is considered closed; the next
        # activity burst opens a fresh card instead of appending here.
        def _close():
            if not self._act_running:
                self._act_group = ""
                self._act_card  = None
        self.after(4000, _close)

    def _start_act_spinner(self) -> None:
        if self._act_spin_id:
            return
        self._animate_act_spinner()

    def _animate_act_spinner(self) -> None:
        if not self._act_running:
            self._act_spin_id = None
            return
        self._act_spin_ph = (self._act_spin_ph + 1) % len(self._ACT_SPIN)
        glyph = self._ACT_SPIN[self._act_spin_ph]
        for aid in list(self._act_running):
            w = self._act_rows.get(aid)
            if not w:
                continue
            try:
                w["status"].configure(text=glyph)
            except Exception:
                self._act_running.discard(aid)
        self._act_spin_id = self.after(160, self._animate_act_spinner)

    # ── Messages ──────────────────────────────────────────────────────────────

    @staticmethod
    def _has_markdown(text: str) -> bool:
        """True when text contains markdown that should be rendered."""
        return bool(re.search(
            r'\*\*[^*]+\*\*'  # **bold**
            r'|\*[^*]+\*'       # *italic*
            r'|^#{1,6}\s'        # headers
            r'|^[-*]\s'          # bullets
            r'|^\d+\.\s'       # numbered list
            r'|`[^`]+`',          # inline code
            text, re.MULTILINE
        ))

    def _render_markdown(self, parent, text: str, text_color: str, wraplength: int) -> None:
        """
        Render markdown-formatted text as a sequence of CTk widgets.
        Handles: headers (h1-h3), **bold**, *italic*, `code`, bullet lists, numbered lists.
        Falls back to plain CTkLabel for unformatted lines.
        """
        lines = text.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]

            # Blank line → small spacer
            if not line.strip():
                ctk.CTkFrame(parent, height=6, fg_color="transparent").pack()
                i += 1
                continue

            # Headers
            m = re.match(r"^(#{1,6})\s(.+)$", line)
            if m:
                sizes = {1: 20, 2: 17, 3: 15, 4: 14, 5: 13, 6: 13}
                sz = sizes.get(len(m.group(1)), 13)
                ctk.CTkLabel(
                    parent, text=m.group(2),
                    font=ctk.CTkFont("Segoe UI", sz, "bold"),
                    text_color=text_color,
                    wraplength=wraplength, justify="left", anchor="w",
                ).pack(fill="x", pady=(8 if sz >= 17 else 4, 2))
                i += 1
                continue

            # Bullet list
            if re.match(r"^[-*\u2022]\s", line):
                bullet_frame = ctk.CTkFrame(parent, fg_color="transparent")
                bullet_frame.pack(fill="x", pady=1)
                ctk.CTkLabel(bullet_frame, text="\u2022",
                             font=ctk.CTkFont("Segoe UI", 13),
                             text_color=self._ACCENT, width=18).pack(side="left", anchor="n", pady=2)
                body = line[2:].strip()
                self._render_inline(bullet_frame, body, text_color, wraplength - 22)
                i += 1
                continue

            # Numbered list
            m = re.match(r"^(\d+)\.\s(.+)$", line)
            if m:
                row = ctk.CTkFrame(parent, fg_color="transparent")
                row.pack(fill="x", pady=1)
                ctk.CTkLabel(row, text=m.group(1) + ".",
                             font=ctk.CTkFont("Segoe UI", 13),
                             text_color=self._MUTED, width=26).pack(side="left", anchor="n")
                self._render_inline(row, m.group(2), text_color, wraplength - 30)
                i += 1
                continue

            # Normal line with possible inline formatting
            self._render_inline(parent, line, text_color, wraplength)
            i += 1

    def _render_inline(self, parent, text: str, text_color: str, wraplength: int) -> None:
        """
        Render a single line that may contain **bold**, *italic*, `code`.
        Uses a single CTkLabel for plain text (common case, fast).
        Uses a flex row for mixed formatting.
        """
        if not re.search(r"\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`", text):
            ctk.CTkLabel(parent, text=text,
                         font=ctk.CTkFont("Segoe UI", 13),
                         text_color=text_color,
                         wraplength=wraplength, justify="left", anchor="w").pack(fill="x")
            return

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")
        parts = re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)", text)
        for part in parts:
            if not part:
                continue
            if part.startswith("**") and part.endswith("**"):
                ctk.CTkLabel(row, text=part[2:-2],
                             font=ctk.CTkFont("Segoe UI", 13, "bold"),
                             text_color=text_color, anchor="w").pack(side="left")
            elif part.startswith("*") and part.endswith("*"):
                ctk.CTkLabel(row, text=part[1:-1],
                             font=ctk.CTkFont("Segoe UI", 13),
                             text_color="#BEB0E8", anchor="w").pack(side="left")
            elif part.startswith("`") and part.endswith("`"):
                wrap = ctk.CTkFrame(row, fg_color="#080620", corner_radius=4)
                wrap.pack(side="left", padx=(2, 2))
                ctk.CTkLabel(wrap, text=part[1:-1],
                             font=ctk.CTkFont("Consolas", 11),
                             text_color="#7ECEA8").pack(padx=5, pady=1)
            else:
                ctk.CTkLabel(row, text=part,
                             font=ctk.CTkFont("Segoe UI", 13),
                             text_color=text_color, anchor="w").pack(side="left")

    def _regenerate(self) -> None:
        """Re-run the last user message, removing the previous GIL response."""
        if not self._last_user_text:
            return
        # Remove last GIL response from the UI
        for frame in self._last_gil_frames:
            try:
                frame.destroy()
            except Exception:
                pass
        self._last_gil_frames.clear()
        # Trim brain history so the re-run is fresh
        try:
            fn = getattr(self._gil_window, "_trim_history_fn", None)
            if fn:
                fn()
        except Exception:
            pass
        # Re-run
        self.show_typing()
        threading.Thread(target=self._on_send, args=(self._last_user_text,),
                         daemon=True, name="GIL-Regen").start()

    def _edit_message(self, text: str, ts: float) -> None:
        """
        Edit an earlier message — forks the conversation from that point
        instead of overwriting it. The original chat stays intact and
        reachable from the sidebar; a new branch continues from here.
        """
        try:
            from chat_history import fork_session, set_current_session
            new_sid = fork_session(self._current_session, ts)
            set_current_session(new_sid)
            self._current_session = new_sid
        except Exception:
            pass

        # Reset brain's live context to match the forked branch
        try:
            fn = getattr(self._gil_window, "_trim_history_to_fn", None)
            if fn:
                # Brain history stores user+assistant pairs flattened;
                # forked session message count maps 1:1 to history entries.
                from chat_history import load_session
                kept = len(load_session(self._current_session))
                fn(kept)
        except Exception:
            pass

        # Re-render the chat view to show only the forked (pre-edit) messages
        for w in self._scroll.winfo_children():
            w.destroy()
        try:
            from chat_history import load_session
            messages = load_session(self._current_session)
        except Exception:
            messages = []
        for msg in messages:
            self._render_bubble(msg["content"], msg["sender"], msg["ts"], save=False)
        self.after(50, lambda: self._scroll._parent_canvas.yview_moveto(1.0))
        self._refresh_sidebar()

        # Put the edited text in the input box, ready to send
        self._textbox.configure(state="normal", text_color=self._TXT)
        self._textbox.delete("0.0", "end")
        self._textbox.insert("0.0", text)
        self._placeholder_active = False
        self._input_wrap.configure(border_color=self._ACCENT)
        self._textbox.focus()

    def add_message(self, text: str, sender: str) -> None:
        if not text.strip():
            return
        import time as _t
        ts = _t.time()
        def _do():
            self.hide_typing()
            self._render_bubble(text, sender, ts, save=True)
            try:
                self._scroll._parent_canvas.yview_moveto(1.0)
            except Exception:
                pass
        self.after(0, _do)

    @staticmethod
    def _open_file(path) -> None:
        import os
        try:
            os.startfile(str(path))
        except Exception:
            import subprocess
            subprocess.Popen(["explorer", str(path)])

    def add_image_message(self, path) -> None:
        """Render GIL-style bubble with an embedded image thumbnail + open button."""
        from pathlib import Path as _Path
        import time as _t
        path = _Path(path)
        tag  = f"[IMAGE:{path}]"

        def _do():
            self.hide_typing()
            # Persist so it reloads on next open
            threading.Thread(target=self._persist, args=(tag, "gil"),
                             daemon=True, name="GIL-HistSave").start()
            self._render_rich_bubble(path, "image")
            try:
                self._scroll._parent_canvas.yview_moveto(1.0)
            except Exception:
                pass
        self.after(0, _do)

    def add_link_message(self, path) -> None:
        """Render GIL-style bubble with a clickable website file card."""
        from pathlib import Path as _Path
        path = _Path(path)
        tag  = f"[WEBSITE:{path}]"

        def _do():
            self.hide_typing()
            threading.Thread(target=self._persist, args=(tag, "gil"),
                             daemon=True, name="GIL-HistSave").start()
            self._render_rich_bubble(path, "website")
            try:
                self._scroll._parent_canvas.yview_moveto(1.0)
            except Exception:
                pass
        self.after(0, _do)

    def _render_rich_bubble(self, path, kind: str) -> None:
        """Render a GIL-avatar message row containing an image or website link card."""
        from pathlib import Path as _Path
        path = _Path(path)

        row   = ctk.CTkFrame(self._scroll, fg_color=self._SURF2, corner_radius=0)
        row.pack(fill="x")
        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=28, pady=(10, 10))

        # Avatar + name row
        name_row = ctk.CTkFrame(inner, fg_color="transparent")
        name_row.pack(fill="x", pady=(0, 8))
        av = ctk.CTkFrame(name_row, fg_color=self._AVATAR_BG, corner_radius=8,
                          width=26, height=26, border_width=1, border_color=self._ACCENT)
        av.pack(side="left", padx=(0, 10)); av.pack_propagate(False)
        ctk.CTkLabel(av, text="◈", font=ctk.CTkFont("Segoe UI", 9, "bold"),
                     text_color=self._ACCENT).pack(expand=True)
        ctk.CTkLabel(name_row, text="G.I.L.",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=self._NAME_G).pack(side="left")

        if kind == "image" and path.exists():
            try:
                from PIL import Image as _PILImage
                img = _PILImage.open(path)
                max_w = min(460, max(200, (self._scroll.winfo_width() or 600) - 120))
                img.thumbnail((max_w, 340), _PILImage.LANCZOS)
                photo = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)

                img_card = ctk.CTkFrame(inner, fg_color=self._BORDER,
                                        corner_radius=12, cursor="hand2")
                img_card.pack(anchor="w", pady=(0, 6))
                img_lbl  = ctk.CTkLabel(img_card, image=photo, text="")
                img_lbl.pack(padx=4, pady=4)
                for w in (img_card, img_lbl):
                    w.bind("<Button-1>", lambda e, p=path: self._open_file(p))
            except Exception:
                pass

            fn_row = ctk.CTkFrame(inner, fg_color="transparent")
            fn_row.pack(anchor="w")
            ctk.CTkLabel(fn_row, text=path.name,
                         font=ctk.CTkFont("Segoe UI", 10),
                         text_color=self._MUTED).pack(side="left")
            ctk.CTkButton(fn_row, text="Open", width=56, height=22,
                          fg_color=self._AVATAR_BG, hover_color=self._BORDER,
                          text_color=self._ACCENT, font=ctk.CTkFont("Segoe UI", 10),
                          corner_radius=6,
                          command=lambda p=path: self._open_file(p),
                          ).pack(side="left", padx=(8, 0))

        elif kind == "website":
            card = ctk.CTkFrame(inner, fg_color=self._AVATAR_BG,
                                corner_radius=12, border_width=1,
                                border_color=self._BORDER, cursor="hand2")
            card.pack(anchor="w", fill="x", pady=(0, 4))
            title = path.parent.name.replace("-", " ").replace("_", " ").title()
            ctk.CTkLabel(card, text=title,
                         font=ctk.CTkFont("Segoe UI", 13, "bold"),
                         text_color=self._TXT, anchor="w").pack(
                             padx=14, pady=(10, 2), fill="x")
            uri = path.as_uri()
            short = uri if len(uri) <= 55 else uri[:52] + "..."
            ctk.CTkLabel(card, text=short,
                         font=ctk.CTkFont("Segoe UI", 9),
                         text_color=self._ACCENT, anchor="w").pack(
                             padx=14, pady=(0, 10), fill="x")
            card.bind("<Button-1>", lambda e, p=path: self._open_file(p))
            for child in card.winfo_children():
                child.bind("<Button-1>", lambda e, p=path: self._open_file(p))

    @staticmethod
    def _has_code(text: str) -> bool:
        import re
        return bool(re.search(r"```|`[^`]+`|\bdef \w+\(|\bfunction \w+\(", text))

    @staticmethod
    def _copy_to_clipboard(root, text: str) -> None:
        try:
            root.clipboard_clear()
            root.clipboard_append(text)
        except Exception:
            pass

    def _render_code_block(self, parent, code: str, lang: str = "") -> None:
        frame = ctk.CTkFrame(parent, fg_color="#080620",
                             corner_radius=10, border_width=1,
                             border_color="#1E1840")
        frame.pack(fill="x", pady=(6, 6))
        top = ctk.CTkFrame(frame, fg_color="#0D0B22", corner_radius=0, height=30)
        top.pack(fill="x"); top.pack_propagate(False)
        ctk.CTkLabel(top, text=f" {lang or 'code'}",
                     font=ctk.CTkFont("Consolas", 9),
                     text_color="#4A3A7A").pack(side="left", padx=10, pady=4)
        ctk.CTkButton(top, text="⎘ Copy", width=60, height=22,
                      fg_color="#1A1540", hover_color="#241E5A",
                      text_color=self._ACCENT,
                      font=ctk.CTkFont("Segoe UI", 9, "bold"),
                      corner_radius=5,
                      command=lambda c=code: self._copy_to_clipboard(self, c),
                      ).pack(side="right", padx=8, pady=4)
        ctk.CTkLabel(frame, text=code,
                     font=ctk.CTkFont("Consolas", 11),
                     text_color="#7ECEA8",
                     wraplength=max(200, self.winfo_width() - 180),
                     justify="left", anchor="w").pack(padx=16, pady=(6, 12), fill="x")

    # ── Streaming animation ───────────────────────────────────────────────────

    def _animate_stream(self, label, full_text: str, on_complete=None,
                        speed_ms: int = 10, chars_per_tick: int = 3) -> None:
        """
        Progressively reveal text in a CTkLabel to simulate token streaming.
        Groq's REST API isn't streamed, so this fakes the effect client-side
        once the full response is already in hand.
        """
        state = {"i": 0}
        def _tick():
            state["i"] = min(len(full_text), state["i"] + chars_per_tick)
            try:
                label.configure(text=full_text[:state["i"]])
            except Exception:
                return   # widget destroyed mid-animation (window closed)
            try:
                self._scroll._parent_canvas.yview_moveto(1.0)
            except Exception:
                pass
            if state["i"] < len(full_text):
                self.after(speed_ms, _tick)
            elif on_complete:
                on_complete()
        _tick()

    # ── GIL message content + actions (shared by streamed and history render) ──

    def _render_gil_content(self, parent, text: str, wl: int) -> None:
        """Render code blocks + markdown for a GIL message body."""
        has_code = "```" in text
        if has_code:
            parts = re.split(r"(```(?:\w+)?\n?[\s\S]*?```)", text)
            for part in parts:
                if part.startswith("```") and part.endswith("```"):
                    m    = re.match(r"```(\w*)\n?([\s\S]*?)```", part)
                    lang = m.group(1) if m else ""
                    code = m.group(2).strip() if m else part[3:-3].strip()
                    self._render_code_block(parent, code, lang)
                elif part.strip():
                    if self._has_markdown(part):
                        self._render_markdown(parent, part.strip(), self._TXT, wl)
                    else:
                        ctk.CTkLabel(parent, text=part.strip(),
                                     font=ctk.CTkFont("Segoe UI", 13),
                                     text_color=self._TXT,
                                     wraplength=wl, justify="left",
                                     anchor="w").pack(fill="x", pady=(0, 4))
        elif self._has_markdown(text):
            self._render_markdown(parent, text, self._TXT, wl)
        else:
            ctk.CTkLabel(parent, text=text,
                         font=ctk.CTkFont("Segoe UI", 13),
                         text_color=self._TXT,
                         wraplength=wl, justify="left",
                         anchor="w").pack(fill="x")

    def _render_gil_actions(self, row, text: str):
        """Copy + Thumbs + Star + Regenerate action row beneath a GIL message."""
        act = ctk.CTkFrame(row, fg_color="transparent")
        act.pack(fill="x", padx=28, pady=(0, 6))
        ctk.CTkButton(act, text=" Copy", image=_icon("copy", self._MUTED, 12),
                      compound="left", width=72, height=24,
                      fg_color="transparent", hover_color="#1A1640",
                      text_color=self._MUTED, font=ctk.CTkFont("Segoe UI", 10),
                      corner_radius=6,
                      command=lambda t=text: self._copy_to_clipboard(self, t),
                      ).pack(side="left", padx=(0, 2))
        ctk.CTkButton(act, text="", image=_icon("thumbs_up", self._MUTED, 14),
                      width=30, height=24,
                      fg_color="transparent", hover_color="#1A2040",
                      corner_radius=6,
                      command=lambda: self._rate_last(1),
                      ).pack(side="left", padx=(0, 2))
        ctk.CTkButton(act, text="", image=_icon("thumbs_down", self._MUTED, 14),
                      width=30, height=24,
                      fg_color="transparent", hover_color="#201020",
                      corner_radius=6,
                      command=lambda: self._rate_last(-1),
                      ).pack(side="left", padx=(0, 4))
        star_btn = ctk.CTkButton(act, text="", image=_icon("star_outline", self._MUTED, 14),
                      width=30, height=24,
                      fg_color="transparent", hover_color="#1A1040",
                      corner_radius=6,
                      command=lambda sb=None: self._pin_last(sb),
                      )
        star_btn.pack(side="left", padx=(0, 4))
        star_btn.configure(command=lambda b=star_btn: self._pin_last(b))
        ctk.CTkButton(act, text=" Regenerate", image=_icon("regenerate", self._MUTED, 12),
                      compound="left", width=96, height=24,
                      fg_color="transparent", hover_color="#1A1640",
                      text_color=self._MUTED, font=ctk.CTkFont("Segoe UI", 10),
                      corner_radius=6, command=self._regenerate,
                      ).pack(side="left")
        return act

    # ── Suggested follow-up questions ────────────────────────────────────────

    def _maybe_show_followups(self, row, gil_text: str) -> None:
        """Fetch 2-3 short follow-up questions in the background and show them as chips."""
        user_text = self._last_user_text
        if not user_text or not gil_text:
            return
        threading.Thread(target=self._fetch_followups, args=(row, user_text, gil_text),
                         daemon=True, name="GIL-Followups").start()

    def _fetch_followups(self, row, user_text: str, gil_text: str) -> None:
        try:
            import os, requests
            key = os.getenv("GROQ_API_KEY", "") or os.getenv("GROQ_API_KEY_2", "")
            if not key:
                return
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [
                        {"role": "system", "content":
                            "Given this exchange, suggest exactly 3 short, natural follow-up "
                            "questions the user might ask next. One per line, no numbering, "
                            "no quotes, each under 8 words."},
                        {"role": "user", "content": f"User: {user_text}\nAssistant: {gil_text[:400]}"},
                    ],
                    "max_tokens": 60,
                    "temperature": 0.6,
                },
                timeout=8,
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            chips = [l.strip("-• \t") for l in raw.splitlines() if l.strip()][:3]
            if chips:
                self.after(0, lambda: self._render_followup_chips(row, chips))
        except Exception:
            pass

    def _render_followup_chips(self, row, chips: list[str]) -> None:
        try:
            if not row.winfo_exists():
                return
        except Exception:
            return
        wrap = ctk.CTkFrame(row, fg_color="transparent")
        wrap.pack(fill="x", padx=28, pady=(0, 12))
        for chip_text in chips:
            chip = ctk.CTkButton(
                wrap, text=chip_text, height=28,
                fg_color="#161236", hover_color="#221C50",
                text_color=self._ACCENT, font=ctk.CTkFont("Segoe UI", 10),
                corner_radius=14, border_width=1, border_color="#2A2060",
                command=lambda t=chip_text: self._send_text(t),
            )
            chip.pack(side="left", padx=(0, 6), pady=2)

    def _send_text(self, text: str) -> None:
        """Send arbitrary text as if the user typed and submitted it (used by follow-up chips)."""
        if not text.strip():
            return
        self._last_user_text = text
        self._last_gil_frames.clear()
        self.add_message(text, "user")
        self.show_typing()
        threading.Thread(target=self._on_send, args=(text,),
                         daemon=True, name="GIL-ChatSend").start()

    def _render_bubble(self, text: str, sender: str, ts: float,
                       save: bool = True) -> None:
        # Rich-content tags from image/website generation — render as cards
        if sender == "gil" and text.startswith("[IMAGE:") and text.endswith("]"):
            from pathlib import Path as _Path
            self._render_rich_bubble(_Path(text[7:-1]), "image")
            return
        if sender == "gil" and text.startswith("[WEBSITE:") and text.endswith("]"):
            from pathlib import Path as _Path
            self._render_rich_bubble(_Path(text[9:-1]), "website")
            return

        import datetime as _dt
        ts_str = _dt.datetime.fromtimestamp(ts).strftime("%H:%M")
        # Use scroll frame width directly — it's the actual message container,
        # so we avoid the sidebar offset and scrollbar errors that hit window width.
        sw = self._scroll.winfo_width()
        if sw <= 1:
            sw = max(600, self.winfo_width() - 240)  # fallback: window minus sidebar+margin
        wl = max(400, sw - 80)   # 28px each side of inner frame + 24px buffer

        if sender == "gil":
            row = ctk.CTkFrame(self._scroll, fg_color=self._SURF2, corner_radius=0)
            row.pack(fill="x")
            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="x", padx=28, pady=(10, 8))

            name_row = ctk.CTkFrame(inner, fg_color="transparent")
            name_row.pack(fill="x", pady=(0, 6))
            av = ctk.CTkFrame(name_row, fg_color=self._AVATAR_BG,
                              corner_radius=8, width=26, height=26,
                              border_width=1, border_color=self._ACCENT)
            av.pack(side="left", padx=(0, 10)); av.pack_propagate(False)
            ctk.CTkLabel(av, text="◈", font=ctk.CTkFont("Segoe UI", 9, "bold"),
                         text_color=self._ACCENT).pack(expand=True)
            ctk.CTkLabel(name_row, text="G.I.L.",
                         font=ctk.CTkFont("Segoe UI", 11, "bold"),
                         text_color=self._NAME_G, anchor="w").pack(side="left")
            ctk.CTkLabel(name_row, text=ts_str,
                         font=ctk.CTkFont("Segoe UI", 9),
                         text_color=self._MUTED).pack(side="left", padx=(10, 0))
            ctk.CTkButton(name_row, text="⎘", width=28, height=22,
                          fg_color="transparent", hover_color="#1A1640",
                          text_color=self._MUTED,
                          font=ctk.CTkFont("Segoe UI", 10),
                          corner_radius=5,
                          command=lambda t=text: self._copy_to_clipboard(self, t),
                          ).pack(side="right")

            content_holder = ctk.CTkFrame(inner, fg_color="transparent")
            content_holder.pack(fill="x")

            def _finish(row=row, inner=inner, content_holder=content_holder,
                       text=text, wl=wl, ts=ts):
                self._render_gil_content(content_holder, text, wl)
                # Skip the heavy 5-icon action row for historical loads —
                # rendering it for every message in a long chat history is
                # what made opening the chat window take 6-30+ seconds.
                # The name-row Copy button above still works for old messages.
                if save:
                    act = self._render_gil_actions(row, text)
                    self._last_gil_frames = [row, act]
                    self._maybe_show_followups(row, text)

            # Animate (stream) only fresh live responses, not history loads
            if save and not self._has_markdown(text) and "```" not in text:
                stream_lbl = ctk.CTkLabel(content_holder, text="",
                                          font=ctk.CTkFont("Segoe UI", 13),
                                          text_color=self._TXT,
                                          wraplength=wl, justify="left", anchor="w")
                stream_lbl.pack(fill="x")
                self._animate_stream(stream_lbl, text,
                                     on_complete=lambda: (stream_lbl.destroy(), _finish()))
            else:
                _finish()

        else:
            spacer = ctk.CTkFrame(self._scroll, fg_color="transparent")
            spacer.pack(fill="x", pady=(6, 4))
            inner = ctk.CTkFrame(spacer, fg_color="transparent")
            inner.pack(fill="x", padx=28)
            ctk.CTkFrame(inner, fg_color="transparent").pack(
                side="left", fill="x", expand=True)
            col = ctk.CTkFrame(inner, fg_color="transparent")
            col.pack(side="right", anchor="e")
            meta = ctk.CTkFrame(col, fg_color="transparent")
            meta.pack(anchor="e", pady=(0, 6))
            ctk.CTkButton(meta, text="", image=_icon("edit", self._MUTED, 12),
                          width=26, height=22,
                          fg_color="transparent", hover_color="#1A1640",
                          corner_radius=5,
                          command=lambda t=text, ets=ts: self._edit_message(t, ets),
                          ).pack(side="left", padx=(0, 6))
            ctk.CTkLabel(meta, text="You",
                         font=ctk.CTkFont("Segoe UI", 11, "bold"),
                         text_color=self._NAME_U).pack(side="left", padx=(0, 10))
            ctk.CTkLabel(meta, text=ts_str,
                         font=ctk.CTkFont("Segoe UI", 9),
                         text_color=self._MUTED).pack(side="left")
            card = ctk.CTkFrame(col, fg_color=self._USERBG,
                                corner_radius=16,
                                border_width=1, border_color=self._UBORDER)
            card.pack(anchor="e")
            ctk.CTkLabel(card, text=text,
                         font=ctk.CTkFont("Segoe UI", 13),
                         text_color=self._USERTXT,
                         wraplength=min(wl, int(sw * 0.52)),
                         justify="right", anchor="e").pack(padx=16, pady=10)

        if save:
            threading.Thread(target=self._persist, args=(text, sender),
                             daemon=True, name="GIL-HistSave").start()

    @staticmethod
    def _persist(text: str, sender: str) -> None:
        try:
            from chat_history import save_message
            save_message(sender, text)
        except Exception:
            pass

    # ── Send ──────────────────────────────────────────────────────────────────

    # ── Feature methods ──────────────────────────────────────────────────────

    def _export_chat(self) -> None:
        """Export current session as a .txt file."""
        import tkinter.filedialog as fd
        name = self._session_name_var.get().strip() or "GIL_Chat"
        filename = fd.asksaveasfilename(
            parent=self,
            defaultextension=".txt",
            filetypes=[("Text file", "*.txt"), ("All files", "*.*")],
            initialfile=f"{name}.txt",
        )
        if not filename:
            return
        try:
            from chat_history import export_session
            content = export_session(self._current_session)
            with open(filename, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as exc:
            pass

    def _show_starred(self) -> None:
        """Load and display starred messages in the main area."""
        try:
            from chat_history import load_pinned
            pinned = load_pinned()
        except Exception:
            pinned = []

        for w in self._scroll.winfo_children():
            w.destroy()

        if not pinned:
            empty = ctk.CTkFrame(self._scroll, fg_color="transparent")
            empty.pack(expand=True, pady=60)
            ctk.CTkLabel(empty, image=_icon("star_outline", self._MUTED, 28), text="",
                         ).pack(pady=(0, 10))
            ctk.CTkLabel(empty, text="No starred messages yet.\nStar any G.I.L. message to save it here.",
                         font=ctk.CTkFont("Segoe UI", 12),
                         text_color=self._MUTED, justify="center").pack()
            return

        self._session_divider("Starred Messages")
        for msg in pinned:
            self._render_bubble(msg["content"], msg["sender"], msg["ts"], save=False)

    def _on_drop(self, event) -> None:
        """Handle files/images dropped onto the chat area."""
        import re
        raw   = event.data.strip()
        paths = re.findall(r"\{([^}]+)\}|([^\s{}]+)", raw)
        files = [a or b for a, b in paths if a or b]
        image_exts = {"png","jpg","jpeg","gif","bmp","webp","tiff","svg"}
        for path in files[:4]:
            ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
            if ext in image_exts:
                self._attach_image(path)
            else:
                self._activate_input()
                cur = self._get_input()
                self._textbox.insert("end", ("\n" if cur else "") + f"[File: {path}]")

    def _attach_image(self, path: str) -> None:
        """Show thumbnail for a dropped or uploaded image, queue it for brain analysis."""
        self._pending_image = path
        try:
            from PIL import Image as _PI, ImageTk as _ITK
            img = _PI.open(path).convert("RGBA")
            img.thumbnail((90, 90), _PI.LANCZOS)
            photo = _ITK.PhotoImage(img)
            self._img_photo_ref = photo
            attach = ctk.CTkFrame(self._scroll, fg_color="transparent")
            attach.pack(fill="x", pady=(4, 0))
            thumb = ctk.CTkFrame(attach, fg_color="#0D0B2E", corner_radius=10,
                                 border_width=1, border_color=self._BORDER)
            thumb.pack(side="right", padx=28, pady=4)
            ctk.CTkLabel(thumb, image=photo, text="",
                         width=90, height=90).pack(padx=6, pady=6)
            fname = path.replace("\\", "/").rsplit("/", 1)[-1]
            ctk.CTkLabel(thumb, text=fname[:22],
                         font=ctk.CTkFont("Segoe UI", 9),
                         text_color=self._MUTED).pack(padx=6, pady=(0, 6))
            ctk.CTkButton(attach, text=" Remove", image=_icon("close", self._MUTED, 9),
                          compound="left",
                          fg_color="transparent", hover_color="#1A1030",
                          text_color=self._MUTED, font=ctk.CTkFont("Segoe UI", 9),
                          width=64, height=20, corner_radius=4,
                          command=lambda f=attach: [
                              f.destroy(), setattr(self, "_pending_image", None)],
                          ).pack(side="right", padx=4)
            try: self._scroll._parent_canvas.yview_moveto(1.0)
            except Exception: pass
        except Exception: pass
        self._textbox.configure(state="normal", text_color=self._TXT)
        self._textbox.delete("0.0", "end")
        self._textbox.insert("0.0", "What do you see in this image?")
        self._placeholder_active = False
        self._input_wrap.configure(border_color=self._ACCENT)
        self._textbox.focus()

    def _upload_file(self) -> None:
        """Open image picker, show thumbnail preview, then analyze."""
        import tkinter.filedialog as fd
        path = fd.askopenfilename(
            parent=self,
            title="Choose image to analyze",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        self._pending_image = path
        # Show thumbnail in the chat area
        try:
            from PIL import Image as _PI, ImageTk as _ITK
            img = _PI.open(path).convert("RGBA")
            img.thumbnail((90, 90), _PI.LANCZOS)
            photo = _ITK.PhotoImage(img)
            self._img_photo_ref = photo
            attach = ctk.CTkFrame(self._scroll, fg_color="transparent")
            attach.pack(fill="x", pady=(4, 0))
            ctk.CTkLabel(attach, image=photo, text="",
                         fg_color="#0D0B2E", corner_radius=10,
                         width=90, height=90).pack(side="right", padx=28, pady=4)
            ctk.CTkButton(attach, text="× Remove",
                          fg_color="transparent", hover_color="#1A1030",
                          text_color=self._MUTED, font=ctk.CTkFont("Segoe UI", 9),
                          width=60, height=20, corner_radius=4,
                          command=lambda f=attach: [f.destroy(), setattr(self, "_pending_image", None)],
                          ).pack(side="right", padx=4)
            try: self._scroll._parent_canvas.yview_moveto(1.0)
            except Exception: pass
        except Exception: pass
        # Clean prompt in input box
        self._textbox.configure(state="normal", text_color=self._TXT)
        self._textbox.delete("0.0", "end")
        self._textbox.insert("0.0", "What do you see in this image?")
        self._placeholder_active = False
        self._input_wrap.configure(border_color=self._ACCENT)
        self._textbox.focus()

    def _rate_last(self, rating: int) -> None:
        """Rate the last GIL message (1=up, -1=down)."""
        try:
            from chat_history import get_last_message_id, rate_message
            msg_id = get_last_message_id()
            if msg_id:
                rate_message(msg_id, rating)
        except Exception:
            pass

    def _pin_last(self, star_btn=None) -> None:
        """Star/unstar the last GIL message."""
        try:
            from chat_history import get_last_message_id, pin_message
            msg_id = get_last_message_id()
            if msg_id:
                pin_message(msg_id, True)
                if star_btn:
                    star_btn.configure(image=_icon("star_filled", "#F59E0B", 14))
        except Exception:
            pass

    def _cycle_model(self) -> None:
        """Cycle through available Groq models."""
        import json
        from pathlib import Path
        _MODELS = [
            "llama-3.1-8b-instant",
            "llama-3.3-70b-versatile",
            "gemma2-9b-it",
        ]
        cfg_path = Path(__file__).parent / "data" / "gil_config.json"
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
        current = cfg.get("ai_model", _MODELS[0])
        try:
            idx = _MODELS.index(current)
            next_model = _MODELS[(idx + 1) % len(_MODELS)]
        except ValueError:
            next_model = _MODELS[0]
        cfg["ai_model"] = next_model
        cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        # Refresh badges to show new model
        self._update_context_badges()


    def _send(self) -> None:
        text = self._get_input()
        if not text:
            return
        pending_img = getattr(self, "_pending_image", None)
        self._pending_image = None
        send_text = (f"Analyze this image file path: {pending_img}\nQuestion: {text}"
                     if pending_img else text)
        self._last_user_text = text
        self._last_gil_frames.clear()
        self._clear_input()
        self.add_message(text, "user")
        self.show_typing()
        threading.Thread(target=self._on_send, args=(send_text,),
                         daemon=True, name="GIL-ChatSend").start()


# ── Slash command menu ──────────────────────────────────────────────────────────
class _SlashMenu(ctk.CTkToplevel):
    """
    Claude-style "/" command popup. Appears above the chat input the moment
    the user types "/" as the very first character, filters live as they type.
    (cmd, description, expansion) — expansion=None means a direct UI action.
    """

    # (cmd, description, expansion, auto_send)
    # auto_send=True  -> selecting it sends the expansion immediately, no review step
    # auto_send=False -> expansion is a prefix/template; user finishes typing and sends manually
    COMMANDS = [
        ("/summarize", "Summarize this conversation", "Summarize our conversation so far.", True),
        ("/explain",   "Explain the last response in more detail", "Explain that in more detail.", True),
        ("/code",      "Write code for…", "Write code for ", False),
        ("/image",     "Generate an image of…", "Generate an image of ", False),
        ("/website",   "Build a website for…", "Build a website for ", False),
        ("/search",    "Search the web for…", "Search the web for ", False),
        ("/git",       "Check git status", "What's my git status?", True),
        ("/docker",    "List running containers", "Show me running docker containers", True),
        ("/clear",     "Start a new chat", None, True),
        ("/help",      "Show what G.I.L. can do", "What can you help me with? List your main capabilities briefly.", True),
    ]

    def __init__(self, parent_window, on_select):
        super().__init__(parent_window)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self._on_select = on_select
        self._items     = list(self.COMMANDS)
        self._selected  = 0

        self._frame = ctk.CTkFrame(self, fg_color="#100E24", corner_radius=12,
                                   border_width=1, border_color="#2A2060")
        self._frame.pack(fill="both", expand=True)
        self._build_rows()
        self.withdraw()

    def _build_rows(self) -> None:
        for w in self._frame.winfo_children():
            w.destroy()
        for i, (cmd, desc, _expansion, _auto_send) in enumerate(self._items):
            is_sel = i == self._selected
            row = ctk.CTkFrame(self._frame, fg_color="#1A1640" if is_sel else "transparent",
                               corner_radius=8)
            row.pack(fill="x", padx=6, pady=2)
            ctk.CTkLabel(row, text=cmd, font=ctk.CTkFont("Consolas", 12, "bold"),
                        text_color="#3FDDFA", anchor="w", width=92).pack(
                            side="left", padx=(10, 4), pady=7)
            ctk.CTkLabel(row, text=desc, font=ctk.CTkFont("Segoe UI", 10),
                        text_color="#8A7AAA", anchor="w").pack(
                            side="left", padx=(0, 10), pady=7)
            row.bind("<Button-1>", lambda e, idx=i: self._pick(idx))
            for child in row.winfo_children():
                child.bind("<Button-1>", lambda e, idx=i: self._pick(idx))

    def filter(self, query: str) -> bool:
        q = query.lower()
        self._items = [c for c in self.COMMANDS if c[0][1:].lower().startswith(q)]
        self._selected = 0
        self._build_rows()
        return len(self._items) > 0

    def move(self, delta: int) -> None:
        if not self._items:
            return
        self._selected = (self._selected + delta) % len(self._items)
        self._build_rows()

    def pick_selected(self) -> None:
        self._pick(self._selected)

    def _pick(self, idx: int) -> None:
        if 0 <= idx < len(self._items):
            self._on_select(self._items[idx])

    def show_at(self, x: int, y: int, width: int) -> None:
        h = min(280, 44 * len(self._items) + 8)
        self.geometry(f"{width}x{h}+{x}+{y - h - 6}")
        self.deiconify()
        self.lift()

    def hide(self) -> None:
        self.withdraw()

    def is_open(self) -> bool:
        try:
            return bool(self.winfo_viewable())
        except Exception:
            return False


# ── Main window — top wave bar ────────────────────────────────────────────────
class GILWindow(ctk.CTk):

    def __init__(self, username: str):
        super().__init__()
        self.username   = username
        self._state     = "standby"
        self._alive     = True
        self._after_id  = None
        self._on_activate: callable | None = None
        self._start_t   = time.time()
        self._tick_n    = 0
        self._said_text  = ""
        self._alpha_cur  = 0.0
        self._alpha_tgt  = 0.0
        self._speak_bubble: _SpeakBubble | None = None

        ctk.set_appearance_mode("dark")
        self.configure(fg_color=BG)
        self.title("G.I.L.")
        _set_icon(self)

        # Initialise drag-and-drop support for the entire app
        try:
            from tkinterdnd2 import TkinterDnD
            TkinterDnD._require(self)
            self._dnd_ready = True
        except Exception:
            self._dnd_ready = False

        self.overrideredirect(True)
        self.attributes("-transparentcolor", BG)
        self.attributes("-topmost", True)
        self.after(100, lambda: _hide_from_taskbar(self))
        self.attributes("-alpha", 0.0)

        self.update_idletasks()
        self._screen_w = self.winfo_screenwidth()
        self._cx       = self._screen_w // 2

        # Per-layer random starting phase
        self._wph = [random.uniform(0, math.tau) for _ in _WLAYERS]

        self.geometry(f"{self._screen_w}x{WIN_H}+0+0")

        self._build_ui()
        self._schedule_tick()
        self.after(150, self._create_speak_bubble)

        # Live activity feed — forward events to the chat window (if open)
        try:
            import activity as _activity
            _activity.subscribe(self._on_activity_event)
        except Exception:
            pass

    def _on_activity_event(self, entry: dict) -> None:
        """Called from worker threads by the activity bus — marshal to UI."""
        def _do():
            try:
                win = getattr(self, "_chat_win", None)
                if win and win.winfo_exists():
                    win.on_activity(entry)
            except Exception:
                pass
        try:
            self.after(0, _do)
        except Exception:
            pass

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.canvas = tk.Canvas(
            self, width=self._screen_w, height=WIN_H,
            bg=BG, highlightthickness=0,
        )
        self.canvas.pack()
        self._init_canvas_items()

        # Click anywhere in wave zone to activate
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        # Right-click menu
        self.canvas.bind("<Button-3>", self._show_menu)
        # Cursor change on hover for the chat button
        self.canvas.bind("<Motion>", self._on_canvas_motion)

    def _create_speak_bubble(self) -> None:
        self._speak_bubble = _SpeakBubble(self)

    def _init_canvas_items(self) -> None:
        cx = self._cx
        sw = self._screen_w

        # ── Wave polygons — dark/back first, bright/front last ──
        self._wave_polys = []
        for li, (col, cy, *_) in enumerate(_WLAYERS):
            # Initialize as a flat zero-height ribbon at center_y
            pts = []
            for xi in range(N_PTS):
                xn = xi / (N_PTS - 1)
                pts.extend([xn * sw, cy])
            for xi in range(N_PTS - 1, -1, -1):
                xn = xi / (N_PTS - 1)
                pts.extend([xn * sw, cy])
            p = self.canvas.create_polygon(pts, fill=BG, outline="", smooth=True)
            self._wave_polys.append(p)

        # ── Thin top-edge glow line ──────────────────────────────────────────
        self._top_glow = self.canvas.create_rectangle(0, 0, sw, 2, fill=BG, outline="")

        # ── Response text ────────────────────────────────────────────────────
        self._c_txt = self.canvas.create_text(
            cx, WIN_H // 2, anchor="center",
            text="", fill="#C8DDFF",
            font=("Segoe UI", 10),
            width=min(sw - 80, 900),
        )

        # ── Chat button (right side, always visible) ──────────────────────────
        _BW, _BH = 80, 26
        _bx1 = sw - _BW - 18
        _bx2 = sw - 18
        _by1 = (WIN_H - _BH) // 2
        _by2 = (WIN_H + _BH) // 2
        self._chat_btn_bounds = (_bx1, _by1, _bx2, _by2)
        self.canvas.create_rectangle(_bx1, _by1, _bx2, _by2,
                                     fill="#040418", outline=ACCENT, width=1,
                                     tags="chat_btn")
        self.canvas.create_text((_bx1 + _bx2) // 2, WIN_H // 2,
                                 text="⌨  Chat", fill=ACCENT,
                                 font=("Segoe UI", 9, "bold"),
                                 tags="chat_btn")

    # ── Interaction ────────────────────────────────────────────────────────────

    def _on_canvas_click(self, event) -> None:
        if hasattr(self, "_chat_btn_bounds"):
            x1, y1, x2, y2 = self._chat_btn_bounds
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                self._open_chat_window()
                return
        if event.y < WIN_H - 20:
            self._on_activate_click()

    def _on_canvas_motion(self, event) -> None:
        if hasattr(self, "_chat_btn_bounds"):
            x1, y1, x2, y2 = self._chat_btn_bounds
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                self.canvas.configure(cursor="hand2")
                return
        self.canvas.configure(cursor="")

    # ── Context menu ──────────────────────────────────────────────────────────

    def _show_menu(self, event) -> None:
        menu = tk.Menu(self, tearoff=0,
                       bg="#07071A", fg="#A0C0E0",
                       activebackground="#0D1A2E",
                       activeforeground="#00BFFF",
                       font=("Segoe UI", 10),
                       relief="flat", bd=0)
        menu.add_command(label="  Activate G.I.L.",  command=self._on_activate_click)
        menu.add_separator()
        menu.add_command(label="  Chat with G.I.L.", command=self._open_chat_window)
        menu.add_command(label="  Tasks & Learning", command=self._open_tasks_window)
        menu.add_command(label="  Settings",         command=self.open_settings)
        menu.add_command(label="  Open Log File",    command=self._open_log)
        menu.add_separator()
        menu.add_command(label="  Exit",             command=self._do_quit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _open_chat_window(self) -> None:
        send_fn = getattr(self, "_chat_send_fn", None)
        if not send_fn:
            return
        if hasattr(self, "_chat_win") and self._chat_win.winfo_exists():
            self._chat_win.lift()
            self._chat_win.focus()
            self._chat_win.state("zoomed")
        else:
            self._chat_win = ChatWindow(self, send_fn)
            self._chat_win.after(80, lambda: self._chat_win.state("zoomed"))
            # Show button again when chat is closed
            self._chat_win.protocol("WM_DELETE_WINDOW", self._on_chat_close)
        # Hide floating button while chat is open
        self._hide_float_btn()

    def _hide_float_btn(self) -> None:
        if hasattr(self, "_float_btn") and self._float_btn.winfo_exists():
            self._float_btn.hide()

    def _show_float_btn(self) -> None:
        if hasattr(self, "_float_btn") and self._float_btn.winfo_exists():
            self._float_btn.show()

    def _on_chat_close(self) -> None:
        """Called when the user closes the chat window."""
        try:
            if hasattr(self, "_chat_win") and self._chat_win.winfo_exists():
                self._chat_win.destroy()
        except Exception:
            pass
        self._show_float_btn()

    def add_chat_message(self, text: str, sender: str) -> None:
        """
        Thread-safe. When the chat window is open the message renders there
        (which also persists it). When it's closed, save straight to the
        history DB — previously these messages were silently lost, so voice
        conversations never showed up when the chat was opened later.
        """
        def _save_offline():
            try:
                from chat_history import save_message
                save_message(sender, text)
            except Exception:
                pass
        def _do():
            if hasattr(self, "_chat_win") and self._chat_win.winfo_exists():
                self._chat_win.add_message(text, sender)
            else:
                threading.Thread(target=_save_offline, daemon=True,
                                 name="GIL-HistSave").start()
        self.after(0, _do)

    def chat_show_typing(self) -> None:
        def _do():
            if hasattr(self, "_chat_win") and self._chat_win.winfo_exists():
                self._chat_win.show_typing()
        self.after(0, _do)

    def chat_hide_typing(self) -> None:
        def _do():
            if hasattr(self, "_chat_win") and self._chat_win.winfo_exists():
                self._chat_win.hide_typing()
        self.after(0, _do)

    def _create_floating_chat_button(self) -> None:
        if not hasattr(self, "_float_btn") or not self._float_btn.winfo_exists():
            self._float_btn = _FloatingChatButton(self, self._open_chat_window)

    def _open_log(self) -> None:
        import subprocess
        log_path = Path(__file__).parent / "data" / "gil.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if not log_path.exists():
            log_path.write_text("No log entries yet.\n", encoding="utf-8")
        subprocess.Popen(["notepad.exe", str(log_path)])

    def _open_tasks_window(self) -> None:
        if hasattr(self, "_tasks_win"):
            try:
                self._tasks_win.lift(); self._tasks_win.focus(); return
            except Exception:
                pass
        self._tasks_win = GILTasksWindow(self)

    # ── Animation ─────────────────────────────────────────────────────────────

    def _schedule_tick(self) -> None:
        if not self._alive:
            return
        ms = _ST.get(self._state, _ST["standby"])["speed"]
        self._after_id = self.after(ms, self._tick)

    def _tick(self) -> None:
        if not self._alive:
            return
        self._tick_n += 1
        t  = time.time() - self._start_t
        sw = self._screen_w

        # ── Smooth alpha fade ─────────────────────────────────────────────────
        if abs(self._alpha_cur - self._alpha_tgt) > 0.005:
            spd = 0.18 if self._alpha_tgt > self._alpha_cur else 0.06
            self._alpha_cur += (self._alpha_tgt - self._alpha_cur) * spd
            self._alpha_cur  = max(0.0, min(1.0, self._alpha_cur))
            self.attributes("-alpha", self._alpha_cur)

        speaking = self._state == "speaking"

        if not speaking:
            for p in self._wave_polys:
                self.canvas.itemconfig(p, fill=BG)
            self.canvas.itemconfig(self._top_glow, fill=BG)
            self._schedule_tick()
            return

        # ── Global breath — ribbons swell and recede together ─────────────────
        breath = 0.70 + 0.30 * abs(math.sin(t * 0.74))

        # ── Update each ribbon band ───────────────────────────────────────────
        for li, (poly, (col, cy, amp, freq_cyc, spd)) in enumerate(
                zip(self._wave_polys, _WLAYERS)):
            ph  = self._wph[li]
            eff = amp * breath
            thk = _WTHICK[li]

            top_pts: list[tuple] = []
            bot_pts: list[tuple] = []
            for xi in range(N_PTS):
                xn    = xi / (N_PTS - 1)
                angle = xn * math.pi * 2 * freq_cyc + t * spd + ph
                wave  = (math.sin(angle)                          * 0.58 +
                         math.sin(angle * 1.87 + t * 0.68 + ph)  * 0.28 +
                         math.sin(angle * 0.42 + t * 0.33)       * 0.14)
                cy_now = cy + eff * wave
                top_pts.append((xn * sw, cy_now - thk))
                bot_pts.append((xn * sw, cy_now + thk))

            # Close ribbon: top edge L→R, bottom edge R→L
            pts_all = top_pts + list(reversed(bot_pts))
            flat    = [c for pt in pts_all for c in pt]
            self.canvas.coords(poly, flat)
            self.canvas.itemconfig(poly, fill=col)

        # ── Top-edge glow ─────────────────────────────────────────────────────
        gt = 0.55 + 0.45 * abs(math.sin(t * 1.3))
        self.canvas.itemconfig(self._top_glow, fill=_blend(BG, "#90BEFF", gt))

        self._schedule_tick()

    # ── State API ─────────────────────────────────────────────────────────────

    def set_state(self, state: str,
                  heard: str | None = None,
                  said:  str | None = None) -> None:
        self.after(0, lambda: self._apply_state(state, heard, said))

    def _apply_state(self, state: str,
                     heard: str | None, said: str | None) -> None:
        self._state     = state
        self._alpha_tgt = 0.0  # wave bar stays hidden; bubble handles speaking
        if state == "speaking" and said is not None:
            self._said_text = said
            self.canvas.itemconfig(self._c_txt, text=said)
            if self._speak_bubble:
                self._speak_bubble.show(said)
        elif state != "speaking" and self._speak_bubble:
            self._speak_bubble.hide()

    # ── Public API (called by main.py) ────────────────────────────────────────

    def refresh_tasks(self) -> None:
        pass

    def open_project_view(self, project_name: str) -> None:
        def _open():
            if not hasattr(self, "_proj_views"):
                self._proj_views = {}
            key = project_name
            if key in self._proj_views:
                try:
                    self._proj_views[key].lift()
                    self._proj_views[key].focus()
                    return
                except Exception:
                    pass
            win = ProjectViewWindow(self, project_name,
                                    on_show_3d=lambda s: self.show_3d(s))
            sw  = self.winfo_screenwidth()
            win.geometry(f"520x680+{(sw - 520) // 2}+{WIN_H + 10}")
            self._proj_views[key] = win
        self.after(0, _open)

    def show_3d(self, shape: str) -> None:
        def _show():
            try:
                from viewer3d import GIL3DPanel
                if hasattr(self, "_panel3d") and self._panel3d.winfo_exists():
                    self._panel3d.set_shape(shape)
                    self._panel3d.lift()
                else:
                    self._panel3d = GIL3DPanel(self, shape)
                    self._panel3d.position_beside(self)
            except Exception as exc:
                print(f"[G.I.L. 3D] {exc}")
        self.after(0, _show)

    def hide_3d(self) -> None:
        def _hide():
            if hasattr(self, "_panel3d") and self._panel3d.winfo_exists():
                self._panel3d._close()
        self.after(0, _hide)

    def show_webgen_progress(self) -> None:
        def _show():
            if not hasattr(self, "_webgen_panel") or not self._webgen_panel.winfo_exists():
                self._webgen_panel = _WebGenPanel(self)
            else:
                self._webgen_panel.deiconify()
        self.after(0, _show)

    def close_webgen_progress(self) -> None:
        def _close():
            if hasattr(self, "_webgen_panel") and self._webgen_panel.winfo_exists():
                self._webgen_panel.finish()
        self.after(0, _close)

    def send_rich_to_chat(self, kind: str, path) -> None:
        """Forward an image or website path into the chat window as a rich message."""
        try:
            win = getattr(self, "_chat_win", None)
            if win and win.winfo_exists():
                if kind == "image":
                    win.add_image_message(path)
                elif kind == "website":
                    win.add_link_message(path)
        except Exception:
            pass

    def show_proactive_suggestion(self, message: str) -> None:
        """
        Disabled — floating proactive toasts (screen-watch alerts, idle
        check-ins, reminder nudges) were too intrusive. Kept as a no-op
        rather than removing call sites in dev_screen/proactive/reminders,
        so this stays the single switch if proactive UI is ever revisited.
        """
        pass

    def show_update_toast(self, info: dict) -> None:
        """Show the update notification on screen AND post it into the chat."""
        # 1. Floating toast at the bottom of the screen
        if hasattr(self, "_update_toast"):
            try:
                self._update_toast.destroy()
            except Exception:
                pass
        self._update_toast = _UpdateToast(self, info, on_update=None)
        self._update_toast.deiconify()

        # 2. Message inside the chat window (if open)
        notes = info.get("notes", "").strip()
        chat_msg = (
            f"Update available: G.I.L. v{info['version']}.\n"
            f"{notes + chr(10) if notes else ''}"
            "Click the notification at the bottom of your screen to install."
        )
        self.add_chat_message(chat_msg, "gil")

    def show_window(self) -> None:
        self.deiconify()
        self.attributes("-topmost", True)
        self.lift()
        self.after(0,   lambda: _hide_from_taskbar(self))
        self.after(300, lambda: self.attributes("-topmost", True))

    def open_settings(self) -> None:
        if not hasattr(self, "_settings_win") or not self._settings_win.winfo_exists():
            self._settings_win = SettingsWindow(self)
        else:
            self._settings_win.focus(); self._settings_win.lift()

    def register_activate_callback(self, fn: callable) -> None:
        self._on_activate = fn

    def _on_activate_click(self) -> None:
        if self._on_activate:
            threading.Thread(target=self._on_activate, daemon=True,
                             name="GIL-ManualActivate").start()

    def _do_quit(self) -> None:
        self._alive = False
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
        self.destroy()


# ── Website generation progress panel ────────────────────────────────────────
class _WebGenPanel(ctk.CTkToplevel):
    """
    Floating progress panel shown while GIL generates a website.
    Asymptotic fill so the bar never reaches 100% until finish() is called.
    """
    _PHASES = [
        "Designing layout and color palette…",
        "Writing content for each section…",
        "Adding animations and hover effects…",
        "Wiring up interactivity…",
        "Finalising and polishing…",
    ]

    def __init__(self, parent):
        import math, time as _t
        super().__init__(parent)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.0)
        self._alpha    = 0.0
        self._parent   = parent
        self._start    = _t.time()
        self._phase    = 0
        self._done     = False
        self._tick_id  = None
        self._phase_id = None

        W, H = 520, 108
        sw = parent.winfo_screenwidth()
        self.geometry(f"{W}x{H}+{(sw - W) // 2}+{WIN_H + 10}")
        self.configure(fg_color="#030318")

        border = ctk.CTkFrame(self, fg_color="#0A1A30", corner_radius=12)
        border.pack(fill="both", expand=True, padx=1, pady=1)
        inner  = ctk.CTkFrame(border, fg_color="#030318", corner_radius=11)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 0))
        ctk.CTkLabel(top, text="◈", font=("Segoe UI", 13, "bold"),
                     text_color=ACCENT, fg_color="transparent").pack(side="left", padx=(0, 8))
        ctk.CTkLabel(top, text="BUILDING WEBSITE", font=("Segoe UI", 10, "bold"),
                     text_color=ACCENT, fg_color="transparent").pack(side="left")
        self._phase_lbl = ctk.CTkLabel(top, text=self._PHASES[0],
                                        font=("Segoe UI", 9), text_color="#608090",
                                        fg_color="transparent")
        self._phase_lbl.pack(side="right")

        self._bar = ctk.CTkProgressBar(inner, height=5,
                                        progress_color=ACCENT,
                                        fg_color="#0A1A2A",
                                        corner_radius=3)
        self._bar.pack(fill="x", padx=16, pady=(10, 14))
        self._bar.set(0)

        self._fade_in()
        self._tick()
        self.after(5000, self._next_phase)

    def _fade_in(self):
        self._alpha = min(1.0, self._alpha + 0.12)
        self.attributes("-alpha", self._alpha)
        if self._alpha < 0.95:
            self.after(16, self._fade_in)

    def _tick(self):
        import math, time as _t
        if self._done:
            return
        elapsed = _t.time() - self._start
        # Asymptotic: approaches 0.92 over ~35 s, never reaches 1.0 until finish()
        self._bar.set(0.92 * (1 - math.exp(-elapsed / 28)))
        self._tick_id = self.after(150, self._tick)

    def _next_phase(self):
        if self._done:
            return
        self._phase = min(self._phase + 1, len(self._PHASES) - 1)
        self._phase_lbl.configure(text=self._PHASES[self._phase])
        if self._phase < len(self._PHASES) - 1:
            self._phase_id = self.after(6000, self._next_phase)

    def finish(self):
        """Snap bar to 100 %, show final message, then fade out."""
        self._done = True
        for id_ in (self._tick_id, self._phase_id):
            if id_:
                try:
                    self.after_cancel(id_)
                except Exception:
                    pass
        self._phase_lbl.configure(text="Opening in your browser…")
        self._bar.set(1.0)
        self.after(1400, self._fade_out)

    def _fade_out(self):
        self._alpha = max(0.0, self._alpha - 0.10)
        self.attributes("-alpha", self._alpha)
        if self._alpha > 0.01:
            self.after(16, self._fade_out)
        else:
            self.withdraw()


# ── Update toast ─────────────────────────────────────────────────────────────
class _UpdateToast(ctk.CTkToplevel):
    """Non-intrusive update notification shown at the bottom of the screen."""

    W = 420

    def __init__(self, parent, info: dict, on_update: callable):
        super().__init__(parent)
        self.transient(parent)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.0)
        self.configure(fg_color=BG)
        self.attributes("-transparentcolor", BG)
        self._alpha    = 0.0
        self._fade_id  = None
        self._info     = info
        self._on_update = on_update
        self._downloading = False

        sw = parent.winfo_screenwidth()
        sh = parent.winfo_screenheight()
        self.geometry(f"{self.W}x90+{(sw - self.W) // 2}+{sh - 120}")

        # Card
        card = ctk.CTkFrame(self, fg_color="#040420", corner_radius=14,
                            border_width=1, border_color=ACCENT)
        card.pack(fill="both", expand=True, padx=2, pady=2)

        # Top stripe
        ctk.CTkFrame(card, height=2, fg_color=ACCENT, corner_radius=0).pack(fill="x")

        content = ctk.CTkFrame(card, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=14, pady=10)

        left = ctk.CTkFrame(content, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True)

        ctk.CTkLabel(left, text=f"G.I.L. v{info['version']} available",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=ACCENT, anchor="w").pack(anchor="w")
        self._status = ctk.CTkLabel(left, text="New features and improvements.",
                                     font=ctk.CTkFont("Segoe UI", 9),
                                     text_color="#3A6A88", anchor="w")
        self._status.pack(anchor="w")

        # Progress bar (hidden until download starts)
        self._bar = ctk.CTkProgressBar(card, height=3, progress_color=ACCENT,
                                        fg_color="#040420", corner_radius=0)
        self._bar.set(0)

        # Buttons
        btn_col = ctk.CTkFrame(content, fg_color="transparent")
        btn_col.pack(side="right", padx=(10, 0))

        self._update_btn = ctk.CTkButton(
            btn_col, text="Update", width=76, height=30,
            fg_color=ACCENT, hover_color="#00A0D8",
            text_color="#000810", font=ctk.CTkFont("Segoe UI", 10, "bold"),
            corner_radius=15, command=self._start_update,
        )
        self._update_btn.pack(pady=(0, 4))

        ctk.CTkButton(
            btn_col, text="Later", width=76, height=26,
            fg_color="transparent", hover_color="#07071E",
            text_color="#2A5070", font=ctk.CTkFont("Segoe UI", 9),
            corner_radius=13, command=self._dismiss,
        ).pack()

        self._fade_in()

    def _start_update(self) -> None:
        if self._downloading:
            return
        self._downloading = True
        self._update_btn.configure(state="disabled", text="Downloading…")
        self._status.configure(text="Downloading update…")
        self._bar.pack(fill="x", side="bottom")
        self._bar.set(0)
        threading.Thread(target=self._download, daemon=True,
                         name="GIL-Update").start()

    def _download(self) -> None:
        import updater as _u
        ok, msg = _u.download_and_install(
            self._info["download_url"],
            on_progress=lambda f: self.after(0, lambda v=f: self._bar.set(v)),
        )
        def _done():
            if ok:
                self._bar.set(1.0)
                self._status.configure(text="Done! Restart GIL to apply.",
                                        text_color="#3AE870")
                self._update_btn.configure(
                    state="normal", text="Restart now",
                    command=self._restart,
                )
            else:
                self._status.configure(text=msg, text_color="#E05050")
                self._update_btn.configure(state="normal", text="Retry",
                                            command=self._start_update)
                self._downloading = False
        self.after(0, _done)

    def _restart(self) -> None:
        import subprocess
        exe = sys.executable
        subprocess.Popen([exe] + sys.argv)
        sys.exit(0)

    def _dismiss(self) -> None:
        self._fade_to(0.0)

    def _fade_in(self) -> None:
        self._alpha = min(0.96, self._alpha + 0.07)
        self.attributes("-alpha", self._alpha)
        if self._alpha < 0.96:
            self._fade_id = self.after(16, self._fade_in)
        else:
            self.deiconify()

    def _fade_to(self, target: float) -> None:
        if self._fade_id:
            try: self.after_cancel(self._fade_id)
            except Exception: pass
        self._step(target)

    def _step(self, target: float) -> None:
        spd = 0.14 if target > self._alpha else 0.09
        if abs(self._alpha - target) < 0.02:
            self._alpha = target
            self.attributes("-alpha", self._alpha)
            if target == 0.0:
                self.destroy()
            return
        self._alpha += (target - self._alpha) * spd
        self._alpha  = max(0.0, min(1.0, self._alpha))
        self.attributes("-alpha", self._alpha)
        self._fade_id = self.after(16, lambda: self._step(target))


# ── Proactive toast ───────────────────────────────────────────────────────────
class _ProactiveToast(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.overrideredirect(True)
        self.attributes("-alpha", 0.0)
        self.attributes("-topmost", True)
        self.configure(fg_color="#030318")
        self.geometry("380x64")
        self._parent   = parent
        self._after_id = None
        self._fade_id  = None
        self._alpha    = 0.0

        border = ctk.CTkFrame(self, fg_color="#0A1A30", corner_radius=10)
        border.pack(fill="both", expand=True, padx=1, pady=1)
        inner = ctk.CTkFrame(border, fg_color="#030318", corner_radius=9)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="both", expand=True, padx=14, pady=10)
        ctk.CTkLabel(row, text="◈", font=("Segoe UI", 14, "bold"),
                     text_color=ACCENT, fg_color="transparent",
                     width=20).pack(side="left", padx=(0, 10))
        self._msg_lbl = ctk.CTkLabel(row, text="", font=("Segoe UI", 10),
                                      text_color="#A0C8E0", fg_color="transparent",
                                      wraplength=300, justify="left")
        self._msg_lbl.pack(side="left", fill="both", expand=True)
        ctk.CTkButton(row, text="✕", width=18, height=18,
                      fg_color="transparent", hover_color="#0A0A20",
                      text_color="#335566", font=("Segoe UI", 9),
                      command=self._dismiss).pack(side="right")

    def show(self, message: str) -> None:
        self._msg_lbl.configure(text=message)
        self.update_idletasks()
        sw = self._parent.winfo_screenwidth()
        x  = (sw - 380) // 2
        y  = WIN_H + 8   # just below the GIL bar
        self.geometry(f"380x64+{x}+{y}")
        self.deiconify()
        for attr in (self._after_id, self._fade_id):
            if attr:
                try: self.after_cancel(attr)
                except Exception: pass
        self._alpha = 0.0
        self._fade_in()
        self._after_id = self.after(8000, self._dismiss)

    def _fade_in(self) -> None:
        self._alpha = min(1.0, self._alpha + 0.10)
        self.attributes("-alpha", self._alpha)
        if self._alpha < 0.95:
            self._fade_id = self.after(16, self._fade_in)

    def _dismiss(self) -> None:
        self._fade_out()

    def _fade_out(self) -> None:
        self._alpha = max(0.0, self._alpha - 0.08)
        self.attributes("-alpha", self._alpha)
        if self._alpha > 0.01:
            self.after(16, self._fade_out)
        else:
            self.withdraw()
