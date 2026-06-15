"""
gui.py — Project G.I.L.
A being that lives in your computer.
Frameless, transparent, floating — always present.
"""

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


# ── Transparency color (pure black = see-through on Windows) ──────────────────
BG     = "#000000"   # transparent key
BG2    = "#010118"   # dark panel — NOT transparent
ACCENT = "#00BFFF"

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
    ("#010A30", 175, 40, 2.0, 0.30),   # deep ocean — wide, slow, dark
    ("#030F4A", 150, 38, 2.5, 0.48),
    ("#071E80", 124, 34, 3.0, 0.72),
    ("#1030C0",  98, 28, 3.6, 1.00),
    ("#1C52E4",  74, 22, 4.3, 1.34),
    ("#3070FF",  50, 16, 5.0, 1.76),
    ("#78AEFF",  28,  9, 5.8, 2.24),   # bright surface ribbon — thin, fast
]
# Half-thickness of each ribbon band in pixels
_WTHICK = [52, 45, 37, 30, 23, 16, 10]


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


# ── Tasks window (opened from right-click menu) ───────────────────────────────
class GILTasksWindow(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("G.I.L. — Tasks & Learning")
        self.geometry("380x560")
        self.resizable(False, True)
        self.configure(fg_color="#05050F")
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
    """Box-and-bubble overlay shown when G.I.L. is speaking."""

    W = 620

    def __init__(self, parent):
        super().__init__(parent)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.0)
        self.configure(fg_color=BG)
        self.attributes("-transparentcolor", BG)
        self._alpha    = 0.0
        self._fade_id  = None
        self._visible  = False

        # ── Outer card ───────────────────────────────────────────────────────
        outer = ctk.CTkFrame(self, fg_color="#050F25", corner_radius=14)
        outer.pack(fill="both", expand=True, padx=2, pady=2)

        # Top accent stripe
        ctk.CTkFrame(outer, height=2, fg_color=ACCENT,
                     corner_radius=0).pack(fill="x")

        content = ctk.CTkFrame(outer, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=14, pady=(10, 12))

        # ── G.I.L. identifier box (left) ─────────────────────────────────────
        gil_box = ctk.CTkFrame(content, fg_color="#020A18", corner_radius=10,
                               border_width=1, border_color="#0A2040")
        gil_box.pack(side="left", padx=(0, 14), pady=0, ipadx=10, ipady=6)
        ctk.CTkLabel(gil_box, text="◈",
                     font=ctk.CTkFont("Segoe UI", 24, "bold"),
                     text_color=ACCENT, fg_color="transparent").pack()
        ctk.CTkLabel(gil_box, text="G.I.L.",
                     font=ctk.CTkFont("Segoe UI", 8),
                     text_color="#336688", fg_color="transparent").pack()

        # ── Speech bubble (right) ────────────────────────────────────────────
        bubble = ctk.CTkFrame(content, fg_color="#020B1C", corner_radius=10,
                              border_width=1, border_color="#081830")
        bubble.pack(side="left", fill="both", expand=True)

        self._lbl = ctk.CTkLabel(
            bubble, text="",
            font=ctk.CTkFont("Segoe UI", 11),
            text_color="#C0D8F8", fg_color="transparent",
            wraplength=self.W - 160,
            justify="left", anchor="w",
        )
        self._lbl.pack(padx=14, pady=10, fill="both", expand=True)

        self.withdraw()

    def show(self, text: str) -> None:
        self._lbl.configure(text=text)
        self.geometry(f"{self.W}x90")
        self.update_idletasks()
        h  = max(90, self.winfo_reqheight() + 4)
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
        spd = 0.14 if target > self._alpha else 0.08
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

        self.overrideredirect(True)
        self.attributes("-transparentcolor", BG)
        self.attributes("-topmost", True)
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

    # ── Interaction ────────────────────────────────────────────────────────────

    def _on_canvas_click(self, event) -> None:
        if event.y < WIN_H - 20:
            self._on_activate_click()

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
        menu.add_command(label="  Tasks & Learning", command=self._open_tasks_window)
        menu.add_command(label="  Settings",         command=self.open_settings)
        menu.add_separator()
        menu.add_command(label="  Exit",             command=self._do_quit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

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

    def show_proactive_suggestion(self, message: str) -> None:
        if not hasattr(self, "_toast") or not self._toast.winfo_exists():
            self._toast = _ProactiveToast(self)
        self._toast.show(message)

    def show_window(self) -> None:
        self.deiconify()
        self.attributes("-topmost", True)
        self.lift()
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
