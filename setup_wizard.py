"""
setup_wizard.py - G.I.L. first-run setup.
Runs automatically the first time GIL launches.
Collects name, Groq API key, and optional Google auth.
"""

import os
import sys
import json
import threading
import webbrowser
import tkinter as tk
import customtkinter as ctk
from pathlib import Path

# ── Paths & colors ────────────────────────────────────────────────────────────
ROOT        = Path(__file__).parent
ENV_PATH    = ROOT / ".env"
PROFILE     = ROOT / "data" / "user_profile.json"
ICON_PATH   = ROOT / "data" / "gil.ico"
LOGO_PATH   = ROOT / "data" / "gil_icon_hq.png"
GCREDS      = ROOT / "data" / "gmail_credentials.json"

ACCENT  = "#00BFFF"
BG      = "#030312"
CARD    = "#060624"
INPUT   = "#07071E"
TXT     = "#C8E8FF"
MUTED   = "#4A7898"
DIM     = "#1A3050"


# ── Config helpers ────────────────────────────────────────────────────────────

def is_setup_complete() -> bool:
    """True if a Groq key is already stored."""
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("GROQ_API_KEY="):
                k = line.split("=", 1)[1].strip().strip('"').strip("'")
                if k and len(k) > 10:
                    return True
    k = os.environ.get("GROQ_API_KEY", "")
    return bool(k and len(k) > 10)


def _save_key(key: str) -> None:
    lines, found = [], False
    if ENV_PATH.exists():
        for ln in ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            if ln.startswith("GROQ_API_KEY="):
                lines.append(f"GROQ_API_KEY={key}"); found = True
            else:
                lines.append(ln)
    if not found:
        lines.append(f"GROQ_API_KEY={key}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ["GROQ_API_KEY"] = key


def _save_name(name: str) -> None:
    PROFILE.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if PROFILE.exists():
        try:
            data = json.loads(PROFILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    data["name"] = name
    data["address_as"] = name
    data.setdefault("fluent_in", ["English"])
    data.setdefault("native_language", "English")
    PROFILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Wizard window ─────────────────────────────────────────────────────────────

class SetupWizard(ctk.CTk):
    N_STEPS = 5   # welcome, name, groq, google, done

    def __init__(self):
        super().__init__()
        self.title("G.I.L. Setup")
        ctk.set_appearance_mode("dark")
        self.configure(fg_color=BG)
        self.resizable(False, False)

        W, H = 600, 680
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{W}x{H}+{(sw-W)//2}+{(sh-H)//2}")

        try:
            if ICON_PATH.exists():
                self.iconbitmap(str(ICON_PATH))
        except Exception:
            pass
        try:
            from PIL import Image as _PI, ImageTk as _ITK
            if LOGO_PATH.exists():
                _img = _PI.open(str(LOGO_PATH)).convert("RGBA").resize((48, 48), _PI.LANCZOS)
                _ph  = _ITK.PhotoImage(_img)
                self.wm_iconphoto(True, _ph)
                self._ph = _ph
        except Exception:
            pass

        self._step      = 0
        self._name      = ""
        self._key       = ""
        self._google_ok = False
        self.completed  = False

        self._build()
        self._go(0)

    # ── Shell ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # Canvas header — logo + title
        self._hdr = tk.Canvas(self, width=600, height=176,
                               bg=BG, highlightthickness=0)
        self._hdr.pack(fill="x")
        self.after(10, self._draw_header)

        # Step-progress dots
        self._dot_bar = ctk.CTkFrame(self, fg_color=BG, height=32)
        self._dot_bar.pack(fill="x")
        self._dot_bar.pack_propagate(False)

        # Thin divider
        ctk.CTkFrame(self, height=1, fg_color="#0C1832").pack(fill="x", padx=24)

        # Content area
        self._body = ctk.CTkFrame(self, fg_color=BG)
        self._body.pack(fill="both", expand=True, padx=44, pady=(18, 0))

        # Bottom nav bar
        ctk.CTkFrame(self, height=1, fg_color="#0C1832").pack(fill="x", padx=24)
        nav = ctk.CTkFrame(self, fg_color="#040418", height=72, corner_radius=0)
        nav.pack(fill="x")
        nav.pack_propagate(False)

        self._btn_back = ctk.CTkButton(
            nav, text="Back", width=100, height=42,
            fg_color="#07071C", hover_color="#0A0A28",
            text_color=MUTED, font=ctk.CTkFont("Segoe UI", 12),
            corner_radius=21, command=self._back,
        )
        self._btn_back.pack(side="left", padx=(22, 0), pady=15)

        self._btn_next = ctk.CTkButton(
            nav, text="Get Started  ->", width=190, height=42,
            fg_color=ACCENT, hover_color="#00A8E8",
            text_color="#000810", font=ctk.CTkFont("Segoe UI", 13, "bold"),
            corner_radius=21, command=self._next,
        )
        self._btn_next.pack(side="right", padx=(0, 22), pady=15)

    def _draw_header(self) -> None:
        cv, W, H = self._hdr, 600, 176
        cv.delete("all")

        # Background
        cv.create_rectangle(0, 0, W, H, fill="#040418", outline="")
        # Glow orbs (decorative)
        cv.create_oval(W-180, -70, W+60, H+30, fill="#05051E", outline="")
        cv.create_oval(W-130, -40, W+20, H,    fill="#06062A", outline="")
        cv.create_oval(-60, -50, 160, H+30,    fill="#05051A", outline="")

        # Logo — use real image if available, else draw ◈ with glow rings
        cx, cy = W // 2, 76
        try:
            from PIL import Image as _PI, ImageTk as _ITK
            if LOGO_PATH.exists() and not hasattr(self, "_hdr_logo"):
                img = _PI.open(str(LOGO_PATH)).convert("RGBA").resize((70, 70), _PI.LANCZOS)
                self._hdr_logo = _ITK.PhotoImage(img)
            if hasattr(self, "_hdr_logo"):
                cv.create_image(cx, cy, image=self._hdr_logo, anchor="center")
        except Exception:
            # Fallback: drawn rings + symbol
            for r, col in [(38, "#07082E"), (34, "#0B0D3A"), (29, "#111648")]:
                cv.create_oval(cx-r, cy-r, cx+r, cy+r, fill=col, outline="")
            cv.create_oval(cx-24, cy-24, cx+24, cy+24,
                           fill="#05051E", outline=ACCENT, width=2)
            cv.create_text(cx, cy, text="◈", fill=ACCENT,
                           font=("Segoe UI", 22, "bold"))

        # Title text
        cv.create_text(cx, 130, text="G.I.L.",
                       fill="#F0F8FF", font=("Segoe UI", 17, "bold"))
        cv.create_text(cx, 152, text="Generative Intelligence Liaison",
                       fill="#3A6888", font=("Segoe UI", 9))

        # Bottom accent stripe
        cv.create_rectangle(0, H-2, W, H, fill=ACCENT, outline="")

    def _draw_dots(self) -> None:
        for w in self._dot_bar.winfo_children():
            w.destroy()
        # Center the dots
        ctk.CTkFrame(self._dot_bar, fg_color="transparent").pack(
            side="left", fill="x", expand=True)
        for i in range(self.N_STEPS):
            if i == self._step:
                d = ctk.CTkFrame(self._dot_bar, fg_color=ACCENT,
                                  corner_radius=5, width=28, height=10)
            elif i < self._step:
                d = ctk.CTkFrame(self._dot_bar, fg_color="#1E5070",
                                  corner_radius=5, width=10, height=10)
            else:
                d = ctk.CTkFrame(self._dot_bar, fg_color="#0A1428",
                                  corner_radius=5, width=10, height=10)
            d.pack(side="left", padx=4, pady=11)
            d.pack_propagate(False)
        ctk.CTkFrame(self._dot_bar, fg_color="transparent").pack(
            side="left", fill="x", expand=True)

    # ── Navigation ────────────────────────────────────────────────────────────

    def _go(self, step: int) -> None:
        self._step = max(0, min(step, self.N_STEPS - 1))
        self._draw_dots()
        for w in self._body.winfo_children():
            w.destroy()

        builders = [
            self._page_welcome,
            self._page_name,
            self._page_groq,
            self._page_google,
            self._page_done,
        ]
        builders[self._step]()

        # Nav labels
        show_back = 0 < self._step < self.N_STEPS - 1
        self._btn_back.configure(
            state="normal" if show_back else "disabled",
            text_color=MUTED if show_back else DIM,
        )
        labels = ["Get Started  ->", "Continue  ->",
                  "Continue  ->", "Skip  ->", "Launch G.I.L.  ->"]
        self._btn_next.configure(text=labels[self._step])

    def _next(self) -> None:
        s = self._step

        if s == 1:                          # save name
            val = self._name_var.get().strip()
            if not val:
                self._flash(self._name_entry, "Enter your name first.")
                return
            self._name = val
            _save_name(val)

        elif s == 2:                        # save key
            key = self._key_var.get().strip()
            if not key:
                self._key_status.configure(text="Paste your key first.", text_color="#E05050")
                return
            self._key = key
            _save_key(key)

        elif s == self.N_STEPS - 1:        # done — launch
            self.completed = True
            self.destroy()
            return

        self._go(s + 1)

    def _back(self) -> None:
        if 0 < self._step < self.N_STEPS - 1:
            self._go(self._step - 1)

    def _flash(self, widget, msg: str) -> None:
        """Briefly highlight a widget border in red."""
        try:
            widget.configure(border_color="#E05050")
            self.after(1800, lambda: widget.configure(border_color="#0C1630"))
        except Exception:
            pass

    # ── Shared UI helpers ─────────────────────────────────────────────────────

    def _heading(self, parent, title: str, subtitle: str) -> None:
        ctk.CTkLabel(parent, text=title,
                     font=ctk.CTkFont("Segoe UI", 26, "bold"),
                     text_color="#EEF8FF").pack(anchor="w", pady=(4, 4))
        ctk.CTkLabel(parent, text=subtitle,
                     font=ctk.CTkFont("Segoe UI", 12),
                     text_color=MUTED, justify="left").pack(anchor="w", pady=(0, 20))

    def _card(self, parent, icon: str, title: str, desc: str,
              btn_text: str = "", btn_cmd=None) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14,
                            border_width=1, border_color="#0C1C3A")
        card.pack(fill="x", pady=5)
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=14)
        ctk.CTkLabel(row, text=icon, font=ctk.CTkFont("Segoe UI", 20, "bold"),
                     text_color=ACCENT, width=36).pack(side="left", padx=(0, 14))
        col = ctk.CTkFrame(row, fg_color="transparent")
        col.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(col, text=title,
                     font=ctk.CTkFont("Segoe UI", 12, "bold"),
                     text_color=TXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(col, text=desc,
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color=MUTED, anchor="w").pack(anchor="w")
        if btn_text and btn_cmd:
            ctk.CTkButton(row, text=btn_text, width=120, height=32,
                          fg_color="#071E3C", hover_color="#0A2848",
                          text_color=ACCENT, font=ctk.CTkFont("Segoe UI", 10, "bold"),
                          corner_radius=16, command=btn_cmd).pack(side="right")
        return card

    def _entry(self, parent, var, placeholder="", password=False) -> ctk.CTkEntry:
        e = ctk.CTkEntry(
            parent, textvariable=var,
            placeholder_text=placeholder,
            font=ctk.CTkFont("Segoe UI", 13),
            fg_color=INPUT, border_color="#0C1630", border_width=2,
            text_color=TXT, placeholder_text_color="#3A6080",
            height=50, corner_radius=25,
            show="*" if password else "",
        )
        e.pack(fill="x", pady=(0, 6))
        e.bind("<FocusIn>",  lambda ev: e.configure(border_color=ACCENT))
        e.bind("<FocusOut>", lambda ev: e.configure(border_color="#0C1630"))
        return e

    # ── Pages ─────────────────────────────────────────────────────────────────

    def _page_welcome(self) -> None:
        f = self._body
        ctk.CTkLabel(f, text="Welcome.",
                     font=ctk.CTkFont("Segoe UI", 34, "bold"),
                     text_color="#EEF8FF").pack(anchor="w", pady=(8, 4))
        ctk.CTkLabel(f, text="Your personal AI is almost ready.\nSetup takes under 2 minutes.",
                     font=ctk.CTkFont("Segoe UI", 13),
                     text_color=MUTED, justify="left").pack(anchor="w", pady=(0, 24))

        self._card(f, "◈", "Smart AI Brain",
                   "Powered by Groq — free, fast, always available")
        self._card(f, "◎", "Understands You",
                   "Voice or text — English and Hebrew both supported")
        self._card(f, "⊕", "Controls Your PC",
                   "Open apps, play music, web search, reminders, and more")

    def _page_name(self) -> None:
        f = self._body
        self._heading(f, "What's your name?",
                      "I'll use this whenever I talk to you.")
        self._name_var = ctk.StringVar(value=self._name)
        self._name_entry = self._entry(f, self._name_var, "Your name…")
        self._name_entry.bind("<Return>", lambda e: self._next())
        self._name_entry.focus()

    def _page_groq(self) -> None:
        f = self._body
        self._heading(f, "Connect your brain.",
                      "G.I.L. uses Groq for AI — completely free.\nGet a key in 30 seconds, no credit card needed.")

        # Step 1 — open browser
        s1 = ctk.CTkFrame(f, fg_color=CARD, corner_radius=14,
                          border_width=1, border_color="#0C1C3A")
        s1.pack(fill="x", pady=(0, 8))
        r1 = ctk.CTkFrame(s1, fg_color="transparent")
        r1.pack(fill="x", padx=16, pady=14)
        num1 = ctk.CTkFrame(r1, fg_color="#07071E", corner_radius=14,
                            width=28, height=28)
        num1.pack(side="left", padx=(0, 12))
        num1.pack_propagate(False)
        ctk.CTkLabel(num1, text="1", font=ctk.CTkFont("Segoe UI", 12, "bold"),
                     text_color=ACCENT).pack(expand=True)
        c1 = ctk.CTkFrame(r1, fg_color="transparent")
        c1.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(c1, text="Get your free key",
                     font=ctk.CTkFont("Segoe UI", 12, "bold"),
                     text_color=TXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(c1, text="Sign up at groq.com — takes 30 seconds",
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color=MUTED, anchor="w").pack(anchor="w")
        ctk.CTkButton(r1, text="Open ->", width=90, height=32,
                      fg_color="#071E3C", hover_color="#0A2848",
                      text_color=ACCENT, font=ctk.CTkFont("Segoe UI", 10, "bold"),
                      corner_radius=16,
                      command=lambda: webbrowser.open(
                          "https://console.groq.com/keys")
                      ).pack(side="right")

        # Step 2 — paste key
        s2 = ctk.CTkFrame(f, fg_color=CARD, corner_radius=14,
                          border_width=1, border_color="#0C1C3A")
        s2.pack(fill="x")
        r2 = ctk.CTkFrame(s2, fg_color="transparent")
        r2.pack(fill="x", padx=16, pady=(14, 6))
        num2 = ctk.CTkFrame(r2, fg_color="#07071E", corner_radius=14,
                            width=28, height=28)
        num2.pack(side="left", padx=(0, 12))
        num2.pack_propagate(False)
        ctk.CTkLabel(num2, text="2", font=ctk.CTkFont("Segoe UI", 12, "bold"),
                     text_color=ACCENT).pack(expand=True)
        ctk.CTkLabel(r2, text="Paste your key below",
                     font=ctk.CTkFont("Segoe UI", 12, "bold"),
                     text_color=TXT).pack(side="left")

        self._key_var = ctk.StringVar(value=self._key)
        ke = ctk.CTkEntry(
            s2, textvariable=self._key_var,
            placeholder_text="gsk_...",
            font=ctk.CTkFont("Segoe UI", 12),
            fg_color=INPUT, border_color="#0C1630", border_width=2,
            text_color=TXT, placeholder_text_color="#3A6080",
            height=44, corner_radius=22, show="*",
        )
        ke.pack(fill="x", padx=16, pady=(0, 8))
        ke.bind("<FocusIn>",  lambda e: ke.configure(border_color=ACCENT))
        ke.bind("<FocusOut>", lambda e: ke.configure(border_color="#0C1630"))

        test_row = ctk.CTkFrame(s2, fg_color="transparent")
        test_row.pack(fill="x", padx=16, pady=(0, 12))
        self._key_status = ctk.CTkLabel(test_row, text="",
                                         font=ctk.CTkFont("Segoe UI", 10),
                                         text_color="#3AE870")
        self._key_status.pack(side="left")
        ctk.CTkButton(test_row, text="Test connection",
                      width=136, height=30,
                      fg_color="#060618", hover_color="#09092A",
                      text_color=MUTED, font=ctk.CTkFont("Segoe UI", 10),
                      corner_radius=15, command=self._test_groq
                      ).pack(side="right")

    def _test_groq(self) -> None:
        key = self._key_var.get().strip()
        if not key:
            self._key_status.configure(text="Paste your key first.", text_color="#E05050")
            return
        self._key_status.configure(text="Testing...", text_color=MUTED)
        def _do():
            try:
                import requests as _r
                resp = _r.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"},
                    json={"model": "llama-3.1-8b-instant",
                          "messages": [{"role": "user", "content": "hi"}],
                          "max_tokens": 5},
                    timeout=8,
                )
                if resp.status_code == 200:
                    self.after(0, lambda: self._key_status.configure(
                        text="Connected!", text_color="#3AE870"))
                else:
                    self.after(0, lambda: self._key_status.configure(
                        text="Invalid key — try again.", text_color="#E05050"))
            except Exception:
                self.after(0, lambda: self._key_status.configure(
                    text="No internet — check your connection.", text_color="#E07030"))
        threading.Thread(target=_do, daemon=True).start()

    def _page_google(self) -> None:
        f = self._body
        self._heading(f, "Connect Google.",
                      "Optional — gives G.I.L. access to your Gmail\nand Calendar. You can skip and add it later.")

        self._card(f, "G", "Gmail",
                   "G.I.L. can summarize your unread emails on request")
        self._card(f, "C", "Calendar",
                   "G.I.L. tells you what's on your schedule today")

        self._g_status = ctk.CTkLabel(f, text="",
                                       font=ctk.CTkFont("Segoe UI", 11),
                                       text_color="#3AE870")
        self._g_status.pack(anchor="w", pady=(14, 6))

        btn_row = ctk.CTkFrame(f, fg_color="transparent")
        btn_row.pack(fill="x")
        ctk.CTkButton(btn_row, text="Connect Google  ->",
                      width=200, height=44,
                      fg_color="#071E3C", hover_color="#0A2848",
                      text_color=ACCENT,
                      font=ctk.CTkFont("Segoe UI", 12, "bold"),
                      corner_radius=22,
                      command=self._do_google).pack(side="left")
        ctk.CTkButton(btn_row, text="Skip for now",
                      width=120, height=44,
                      fg_color="transparent", hover_color="#07071E",
                      text_color=DIM,
                      font=ctk.CTkFont("Segoe UI", 11),
                      corner_radius=22,
                      command=lambda: self._go(4)).pack(side="left", padx=12)

    def _do_google(self) -> None:
        if not GCREDS.exists():
            self._g_status.configure(
                text="Google credentials not bundled in this version.",
                text_color="#E07030")
            return
        self._g_status.configure(text="Opening browser...", text_color=MUTED)
        def _auth():
            try:
                sys.path.insert(0, str(ROOT))
                from gcalendar import get_service
                get_service()
                self.after(0, lambda: (
                    self._g_status.configure(
                        text="Google connected!", text_color="#3AE870"),
                    setattr(self, "_google_ok", True)
                ))
            except Exception as exc:
                self.after(0, lambda: self._g_status.configure(
                    text=f"Couldn't connect: {exc}", text_color="#E05050"))
        threading.Thread(target=_auth, daemon=True).start()

    def _page_done(self) -> None:
        f = self._body
        name = self._name or "there"

        # Big avatar
        av_wrap = ctk.CTkFrame(f, fg_color="transparent")
        av_wrap.pack(pady=(4, 16))
        av = ctk.CTkFrame(av_wrap, fg_color="#060628", corner_radius=42,
                          width=84, height=84, border_width=2, border_color=ACCENT)
        av.pack()
        av.pack_propagate(False)
        ctk.CTkLabel(av, text="◈",
                     font=ctk.CTkFont("Segoe UI", 34, "bold"),
                     text_color=ACCENT).pack(expand=True)

        ctk.CTkLabel(f, text=f"You're all set, {name}!",
                     font=ctk.CTkFont("Segoe UI", 24, "bold"),
                     text_color="#EEF8FF").pack(pady=(0, 4))
        ctk.CTkLabel(f, text="G.I.L. is ready to assist you.",
                     font=ctk.CTkFont("Segoe UI", 12),
                     text_color=MUTED).pack(pady=(0, 22))

        # Summary card
        card = ctk.CTkFrame(f, fg_color=CARD, corner_radius=14,
                            border_width=1, border_color="#0C1C3A")
        card.pack(fill="x")
        items = [
            (bool(self._name),      f"Name: {self._name}"),
            (bool(self._key),        "Groq AI brain connected"),
            (self._google_ok,        "Google services connected"),
        ]
        for ok, label in items:
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=9)
            ctk.CTkLabel(row, text="+" if ok else "-",
                         font=ctk.CTkFont("Segoe UI", 14, "bold"),
                         text_color="#3AE870" if ok else "#2A4060",
                         width=24).pack(side="left", padx=(0, 12))
            ctk.CTkLabel(row, text=label,
                         font=ctk.CTkFont("Segoe UI", 11),
                         text_color=TXT if ok else DIM,
                         anchor="w").pack(side="left")

        ctk.CTkLabel(f, text="You can change all of this later in Settings.",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color=DIM).pack(pady=(14, 0))


# ── Public entry point ────────────────────────────────────────────────────────

def run_wizard() -> bool:
    """Show the wizard. Returns True if the user completed it."""
    w = SetupWizard()
    w.mainloop()
    return w.completed


if __name__ == "__main__":
    run_wizard()
