"""
dev_setup_wizard.py — G.I.L.
Developer mode setup wizard.
Terminal-themed design — dark green on black, monospace font, code aesthetic.
Collects: GitHub token, project directory, preferred editor, screen watcher settings.
"""

import threading
import tkinter as tk
import customtkinter as ctk
import webbrowser
from pathlib import Path
from logger import get as _get_log

log = _get_log("dev.wizard")

# ── Dev theme constants ───────────────────────────────────────────────────────
_BG      = "#080D08"      # near-black with green tint
_BG2     = "#0D150D"      # slightly lighter
_CARD    = "#111A11"      # card background
_ACCENT  = "#4ADE80"      # terminal green
_ACCENT2 = "#22D3EE"      # cyan highlight
_TEXT    = "#E2F0E2"      # light green-white
_MUTED   = "rgba(160,200,160,0.6)"
_BORDER  = "#1A2E1A"      # subtle green border
_MONO    = "Consolas"     # monospace font


def _muted_color() -> str:
    return "#6B9E6B"


# ── Main wizard window ────────────────────────────────────────────────────────

class DevSetupWizard(ctk.CTkToplevel):
    N_STEPS = 6

    def __init__(self, parent):
        super().__init__(parent)
        self.title("G.I.L. — Developer Mode Setup")
        self.geometry("660x740")
        self.resizable(False, False)
        ctk.set_appearance_mode("dark")
        self.configure(fg_color=_BG)
        self.completed = False
        self._step     = 0
        self._data: dict = {}
        self._cursor_visible = True

        # Set icon if available
        try:
            from gui import _set_icon
            _set_icon(self)
        except Exception:
            pass

        self._build()
        self._go(0)
        self.lift(); self.focus()
        self._blink_cursor()

    # ── Shell ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # Canvas header — terminal style
        self._hdr = tk.Canvas(self, width=660, height=160,
                               bg=_BG, highlightthickness=0)
        self._hdr.pack(fill="x")
        self.after(10, self._draw_header)

        # Progress dots
        self._dot_bar = ctk.CTkFrame(self, fg_color=_BG, height=28)
        self._dot_bar.pack(fill="x")
        self._dot_bar.pack_propagate(False)

        # Separator
        ctk.CTkFrame(self, height=1, fg_color=_BORDER).pack(fill="x")

        # Content
        self._body = ctk.CTkFrame(self, fg_color=_BG)
        self._body.pack(fill="both", expand=True, padx=44, pady=(18, 0))

        # Bottom nav
        ctk.CTkFrame(self, height=1, fg_color=_BORDER).pack(fill="x")
        nav = ctk.CTkFrame(self, fg_color=_BG2, height=72, corner_radius=0)
        nav.pack(fill="x"); nav.pack_propagate(False)

        self._btn_back = ctk.CTkButton(
            nav, text="← Back", width=100, height=42,
            fg_color="#0D1A0D", hover_color="#142814",
            text_color=_muted_color(), font=ctk.CTkFont(_MONO, 12),
            corner_radius=21, command=self._back,
        )
        self._btn_back.pack(side="left", padx=(22, 0), pady=15)

        self._btn_next = ctk.CTkButton(
            nav, text="CONTINUE  >", width=190, height=42,
            fg_color=_ACCENT, hover_color="#3AB86A",
            text_color="#000810", font=ctk.CTkFont(_MONO, 13, "bold"),
            corner_radius=21, command=self._next,
        )
        self._btn_next.pack(side="right", padx=(0, 22), pady=15)

    def _draw_header(self) -> None:
        cv = self._hdr
        W, H = 660, 160
        cv.delete("all")

        # Dark background
        cv.create_rectangle(0, 0, W, H, fill=_BG2, outline="")

        # Subtle scan-line effect (thin horizontal lines)
        for y in range(0, H, 4):
            cv.create_line(0, y, W, y, fill="#0A100A", width=1)

        # Top green line
        cv.create_rectangle(0, 0, W, 3, fill=_ACCENT, outline="")

        # Terminal prompt decoration
        cv.create_text(28, 28, text="$", fill=_ACCENT,
                       font=(_MONO, 14, "bold"), anchor="w")
        cv.create_text(44, 28, text="gil --developer-mode setup",
                       fill=_muted_color(), font=(_MONO, 12), anchor="w")

        # Main title
        cv.create_text(W // 2, 78, text="DEVELOPER MODE",
                       fill=_ACCENT, font=(_MONO, 22, "bold"), anchor="center")

        # Cursor (blinks)
        self._cursor_id = cv.create_rectangle(
            W // 2 + 86, 64, W // 2 + 98, 94,
            fill=_ACCENT, outline="",
        )

        # Subtitle
        cv.create_text(W // 2, 112, text="Configure GIL for your development workflow",
                       fill=_muted_color(), font=(_MONO, 10), anchor="center")

        # Version badges
        for i, badge in enumerate(["GIT", "GITHUB", "DOCKER", "CODE SEARCH", "SCREEN WATCH"]):
            x = 50 + i * 118
            cv.create_rectangle(x, 136, x + len(badge) * 7 + 14, 155,
                                fill=_CARD, outline=_BORDER, width=1)
            cv.create_text(x + 7, 145, text=badge, fill=_ACCENT2,
                           font=(_MONO, 8), anchor="w")

        # Bottom accent
        cv.create_rectangle(0, H - 2, W, H, fill=_ACCENT, outline="")

    def _blink_cursor(self) -> None:
        """Animate the header cursor."""
        try:
            if hasattr(self, "_cursor_id"):
                self._cursor_visible = not self._cursor_visible
                fill = _ACCENT if self._cursor_visible else _BG2
                self._hdr.itemconfig(self._cursor_id, fill=fill)
        except Exception:
            pass
        self.after(530, self._blink_cursor)

    def _draw_dots(self) -> None:
        for w in self._dot_bar.winfo_children():
            w.destroy()
        ctk.CTkFrame(self._dot_bar, fg_color="transparent").pack(
            side="left", fill="x", expand=True)
        for i in range(self.N_STEPS):
            if i == self._step:
                d = ctk.CTkFrame(self._dot_bar, fg_color=_ACCENT,
                                  corner_radius=5, width=28, height=10)
            elif i < self._step:
                d = ctk.CTkFrame(self._dot_bar, fg_color="#1E4D1E",
                                  corner_radius=5, width=10, height=10)
            else:
                d = ctk.CTkFrame(self._dot_bar, fg_color="#0D1A0D",
                                  corner_radius=5, width=10, height=10)
            d.pack(side="left", padx=4, pady=9)
            d.pack_propagate(False)
        ctk.CTkFrame(self._dot_bar, fg_color="transparent").pack(
            side="left", fill="x", expand=True)

    # ── Navigation ────────────────────────────────────────────────────────────

    def _go(self, step: int) -> None:
        self._step = step
        self._draw_dots()
        for w in self._body.winfo_children():
            w.destroy()
        [self._pg_welcome, self._pg_token, self._pg_directory,
         self._pg_editor, self._pg_screen, self._pg_done][step]()

        show_back = 0 < step < self.N_STEPS - 1
        self._btn_back.configure(
            state="normal" if show_back else "disabled",
            text_color=_muted_color() if show_back else "#1E2E1E",
        )
        labels = ["> INIT", "> CONTINUE", "> CONTINUE",
                  "> CONTINUE", "> CONTINUE", "> ACTIVATE DEV MODE"]
        self._btn_next.configure(text=labels[step])

    def _next(self) -> None:
        s = self._step
        if s == 1:
            t = getattr(self, "_token_var", ctk.StringVar()).get().strip()
            from dev_config import save
            save(github_token=t)
        elif s == 2:
            d = getattr(self, "_dir_var", ctk.StringVar()).get().strip()
            if d:
                from dev_config import save
                save(project_dir=d)
        elif s == 3:
            ed = getattr(self, "_editor_cmd", "code")
            from dev_config import save
            save(editor=ed)
        elif s == 4:
            sw  = getattr(self, "_watch_var", ctk.BooleanVar(value=True)).get()
            iv  = getattr(self, "_interval", 30)
            from dev_config import save
            save(screen_watch=sw, screen_interval=iv)
        elif s == self.N_STEPS - 1:
            from dev_config import enable
            enable()
            self.completed = True
            self.destroy()
            return
        self._go(s + 1)

    def _back(self) -> None:
        if 0 < self._step < self.N_STEPS - 1:
            self._go(self._step - 1)

    # ── Helper widgets ────────────────────────────────────────────────────────

    def _heading(self, parent, title: str, sub: str) -> None:
        ctk.CTkLabel(parent, text=f"> {title}",
                     font=ctk.CTkFont(_MONO, 20, "bold"),
                     text_color=_ACCENT).pack(anchor="w", pady=(4, 4))
        ctk.CTkLabel(parent, text=sub,
                     font=ctk.CTkFont(_MONO, 11),
                     text_color=_muted_color(), justify="left").pack(anchor="w", pady=(0, 20))

    def _card(self, parent, **kw) -> ctk.CTkFrame:
        return ctk.CTkFrame(parent, fg_color=_CARD, corner_radius=12,
                            border_width=1, border_color=_BORDER, **kw)

    def _entry(self, parent, var, placeholder="", password=False) -> ctk.CTkEntry:
        e = ctk.CTkEntry(
            parent, textvariable=var, placeholder_text=placeholder,
            font=ctk.CTkFont(_MONO, 12),
            fg_color="#0A100A", border_color=_BORDER, border_width=2,
            text_color=_ACCENT, placeholder_text_color="#2A4A2A",
            height=46, corner_radius=8,
            show="*" if password else "",
        )
        e.pack(fill="x", pady=(0, 6))
        e.bind("<FocusIn>",  lambda ev: e.configure(border_color=_ACCENT))
        e.bind("<FocusOut>", lambda ev: e.configure(border_color=_BORDER))
        return e

    # ── Pages ─────────────────────────────────────────────────────────────────

    def _pg_welcome(self) -> None:
        f = self._body
        self._heading(f, "DEVELOPER MODE",
                      "GIL gains powerful tools for your coding workflow.\nSetup takes under 2 minutes.")

        features = [
            ("GIT",      "Status, commit, push, pull, branches, stash"),
            ("GITHUB",   "Pull requests, issues, CI status via API"),
            ("RUNNER",   "Run tests, start servers, capture output"),
            ("SEARCH",   "Find definitions, TODOs, search codebase"),
            ("DOCKER",   "Container management, compose, logs"),
            ("SCREEN",   "Detects errors on screen, offers to fix them"),
        ]
        for code, desc in features:
            row = ctk.CTkFrame(f, fg_color=_CARD, corner_radius=10,
                               border_width=1, border_color=_BORDER)
            row.pack(fill="x", pady=4)
            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="x", padx=14, pady=10)
            ctk.CTkLabel(inner, text=f"[{code}]",
                         font=ctk.CTkFont(_MONO, 10, "bold"),
                         text_color=_ACCENT2, width=80, anchor="w").pack(side="left")
            ctk.CTkLabel(inner, text=desc,
                         font=ctk.CTkFont(_MONO, 10),
                         text_color=_muted_color(), anchor="w").pack(side="left")

    def _pg_token(self) -> None:
        f = self._body
        self._heading(f, "GITHUB TOKEN",
                      "Required for PRs, issues, and CI status.\nOnly needs 'repo' scope. Free to create.")

        # Step 1
        s1 = self._card(f); s1.pack(fill="x", pady=(0, 10))
        r1 = ctk.CTkFrame(s1, fg_color="transparent")
        r1.pack(fill="x", padx=14, pady=14)
        ctk.CTkLabel(r1, text="[1]", font=ctk.CTkFont(_MONO, 12, "bold"),
                     text_color=_ACCENT, width=36).pack(side="left", padx=(0, 10))
        col = ctk.CTkFrame(r1, fg_color="transparent")
        col.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(col, text="Create a token at GitHub",
                     font=ctk.CTkFont(_MONO, 11, "bold"),
                     text_color=_TEXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(col, text="Settings → Developer settings → Personal access tokens",
                     font=ctk.CTkFont(_MONO, 9),
                     text_color=_muted_color(), anchor="w").pack(anchor="w")
        ctk.CTkButton(r1, text="Open GitHub  >", width=130, height=32,
                      fg_color="#0D1A0D", hover_color="#142814",
                      text_color=_ACCENT2, font=ctk.CTkFont(_MONO, 10, "bold"),
                      corner_radius=8,
                      command=lambda: webbrowser.open(
                          "https://github.com/settings/tokens/new?scopes=repo&description=GIL+Assistant"
                      )).pack(side="right")

        # Step 2
        s2 = self._card(f); s2.pack(fill="x")
        r2 = ctk.CTkFrame(s2, fg_color="transparent")
        r2.pack(fill="x", padx=14, pady=(14, 6))
        ctk.CTkLabel(r2, text="[2]", font=ctk.CTkFont(_MONO, 12, "bold"),
                     text_color=_ACCENT, width=36).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(r2, text="Paste your token",
                     font=ctk.CTkFont(_MONO, 11, "bold"),
                     text_color=_TEXT).pack(side="left")

        from dev_config import get as _cfg_get
        self._token_var = ctk.StringVar(value=_cfg_get("github_token", ""))
        self._entry(s2, self._token_var, "ghp_...", password=True)

        self._token_status = ctk.CTkLabel(s2, text="",
                                           font=ctk.CTkFont(_MONO, 9),
                                           text_color=_ACCENT)
        self._token_status.pack(anchor="w", padx=14, pady=(0, 4))

        test_row = ctk.CTkFrame(s2, fg_color="transparent")
        test_row.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkButton(test_row, text="Test connection",
                      width=140, height=30,
                      fg_color="#0A100A", hover_color="#111A11",
                      text_color=_muted_color(), font=ctk.CTkFont(_MONO, 9),
                      corner_radius=6, command=self._test_token).pack(side="right")

        ctk.CTkLabel(f, text="# Skip this step if you don't need GitHub features",
                     font=ctk.CTkFont(_MONO, 9),
                     text_color="#2A4A2A").pack(anchor="w", pady=(10, 0))

    def _test_token(self) -> None:
        token = self._token_var.get().strip()
        if not token:
            self._token_status.configure(text="Paste your token first.", text_color="#E05050")
            return
        self._token_status.configure(text="Testing...", text_color=_muted_color())
        def _do():
            try:
                import requests
                r = requests.get("https://api.github.com/user",
                                 headers={"Authorization": f"Bearer {token}",
                                          "Accept": "application/vnd.github.v3+json"},
                                 timeout=8)
                if r.status_code == 200:
                    login = r.json().get("login", "")
                    self.after(0, lambda: self._token_status.configure(
                        text=f"Connected as @{login}", text_color=_ACCENT))
                else:
                    self.after(0, lambda: self._token_status.configure(
                        text="Invalid token — try again.", text_color="#E05050"))
            except Exception as exc:
                self.after(0, lambda: self._token_status.configure(
                    text="No internet connection.", text_color="#E07030"))
        threading.Thread(target=_do, daemon=True).start()

    def _pg_directory(self) -> None:
        f = self._body
        self._heading(f, "PROJECT DIRECTORY",
                      "Where do you keep your code projects?\nGIL will search here when you say 'open my project'.")

        from dev_config import get as _cfg_get
        default = _cfg_get("project_dir", str(Path.home() / "Desktop"))
        self._dir_var = ctk.StringVar(value=default)

        card = self._card(f); card.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(card, text="Project root directory:",
                     font=ctk.CTkFont(_MONO, 10, "bold"),
                     text_color=_ACCENT).pack(anchor="w", padx=14, pady=(12, 4))

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(0, 12))
        e = ctk.CTkEntry(row, textvariable=self._dir_var,
                          placeholder_text="C:/Users/You/Projects",
                          font=ctk.CTkFont(_MONO, 11),
                          fg_color="#0A100A", border_color=_BORDER, border_width=2,
                          text_color=_ACCENT, height=40, corner_radius=6)
        e.pack(side="left", fill="x", expand=True, padx=(0, 8))
        e.bind("<FocusIn>",  lambda ev: e.configure(border_color=_ACCENT))
        e.bind("<FocusOut>", lambda ev: e.configure(border_color=_BORDER))
        ctk.CTkButton(row, text="Browse", width=80, height=40,
                      fg_color="#0D1A0D", hover_color="#142814",
                      text_color=_ACCENT2, font=ctk.CTkFont(_MONO, 10),
                      corner_radius=6, command=self._browse_dir).pack(side="right")

        # Common shortcuts
        ctk.CTkLabel(f, text="# Common locations:",
                     font=ctk.CTkFont(_MONO, 9), text_color="#2A4A2A").pack(anchor="w", pady=(8, 4))
        shortcuts = [
            ("Desktop",   str(Path.home() / "Desktop")),
            ("Documents", str(Path.home() / "Documents")),
            ("~/Projects", str(Path.home() / "Projects")),
            ("~/dev",     str(Path.home() / "dev")),
        ]
        row2 = ctk.CTkFrame(f, fg_color="transparent")
        row2.pack(fill="x")
        for label, path in shortcuts:
            ctk.CTkButton(row2, text=label, width=0, height=28,
                          fg_color="#0D1A0D", hover_color="#142814",
                          text_color=_ACCENT2, font=ctk.CTkFont(_MONO, 9),
                          corner_radius=6,
                          command=lambda p=path: self._dir_var.set(p)
                          ).pack(side="left", padx=(0, 6))

    def _browse_dir(self) -> None:
        import tkinter.filedialog as fd
        d = fd.askdirectory(title="Select your projects directory")
        if d:
            self._dir_var.set(d.replace("/", "\\"))

    def _pg_editor(self) -> None:
        f = self._body
        self._heading(f, "CODE EDITOR",
                      "Which editor do you use?\nGIL will open projects here when you ask.")

        from dev_config import detect_editors, get as _cfg_get
        editors = detect_editors()
        saved   = _cfg_get("editor", "code")
        self._editor_cmd = saved

        self._editor_btns = {}
        grid = ctk.CTkFrame(f, fg_color="transparent")
        grid.pack(fill="x")

        all_editors = [
            {"name": "VS Code",   "cmd": "code"},
            {"name": "Cursor",    "cmd": "cursor"},
            {"name": "WebStorm",  "cmd": "webstorm"},
            {"name": "PyCharm",   "cmd": "pycharm"},
            {"name": "Sublime",   "cmd": "subl"},
            {"name": "Vim",       "cmd": "vim"},
        ]

        found_cmds = {e["cmd"] for e in editors}

        for i, ed in enumerate(all_editors):
            is_found  = ed["cmd"] in found_cmds
            is_active = ed["cmd"] == saved
            col = i % 2
            r   = i // 2

            btn = ctk.CTkButton(
                grid,
                text=f"  {ed['name']}" + ("  ✓" if is_found else ""),
                width=250, height=52,
                fg_color=_ACCENT if is_active else _CARD,
                hover_color="#3AB86A" if is_active else "#142814",
                text_color="#000" if is_active else (_ACCENT if is_found else _muted_color()),
                font=ctk.CTkFont(_MONO, 12, "bold" if is_active else "normal"),
                corner_radius=10, border_width=1,
                border_color=_ACCENT if is_active else _BORDER,
                command=lambda cmd=ed["cmd"]: self._select_editor(cmd),
            )
            btn.grid(row=r, column=col, padx=6, pady=6, sticky="ew")
            self._editor_btns[ed["cmd"]] = btn

        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        self._editor_note = ctk.CTkLabel(
            f, text=f"# Using: {saved}",
            font=ctk.CTkFont(_MONO, 9), text_color=_muted_color(),
        )
        self._editor_note.pack(anchor="w", pady=(12, 0))

    def _select_editor(self, cmd: str) -> None:
        self._editor_cmd = cmd
        from dev_config import detect_editors
        all_cmds = ["code","cursor","webstorm","pycharm","subl","vim"]
        for c in all_cmds:
            if c in self._editor_btns:
                is_sel = c == cmd
                b = self._editor_btns[c]
                b.configure(
                    fg_color=_ACCENT if is_sel else _CARD,
                    hover_color="#3AB86A" if is_sel else "#142814",
                    text_color="#000" if is_sel else _muted_color(),
                    border_color=_ACCENT if is_sel else _BORDER,
                )
        try:
            self._editor_note.configure(text=f"# Using: {cmd}")
        except Exception:
            pass

    def _pg_screen(self) -> None:
        f = self._body
        self._heading(f, "SCREEN WATCHER",
                      "GIL monitors your screen for errors and bugs.\nWhen it sees one, it asks if you want help.")

        from dev_config import get as _cfg_get
        self._watch_var = ctk.BooleanVar(value=_cfg_get("screen_watch", True))
        self._interval  = _cfg_get("screen_interval", 30)

        # Toggle card
        toggle_card = self._card(f); toggle_card.pack(fill="x", pady=(0, 12))
        tr = ctk.CTkFrame(toggle_card, fg_color="transparent")
        tr.pack(fill="x", padx=16, pady=16)
        col = ctk.CTkFrame(tr, fg_color="transparent")
        col.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(col, text="Enable screen watching",
                     font=ctk.CTkFont(_MONO, 13, "bold"),
                     text_color=_TEXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(col, text="Detects errors, failed tests, crashes",
                     font=ctk.CTkFont(_MONO, 9),
                     text_color=_muted_color(), anchor="w").pack(anchor="w")
        ctk.CTkSwitch(tr, variable=self._watch_var, text="",
                      progress_color=_ACCENT, button_color=_BG,
                      button_hover_color="#3AB86A",
                      width=52).pack(side="right")

        # Interval
        iv_card = self._card(f); iv_card.pack(fill="x")
        ctk.CTkLabel(iv_card, text="Check interval:",
                     font=ctk.CTkFont(_MONO, 10, "bold"),
                     text_color=_ACCENT).pack(anchor="w", padx=16, pady=(12, 6))
        iv_row = ctk.CTkFrame(iv_card, fg_color="transparent")
        iv_row.pack(fill="x", padx=16, pady=(0, 14))
        for secs, label in [(20, "20s  fast"), (30, "30s  balanced"), (60, "60s  quiet")]:
            is_sel = secs == self._interval
            b = ctk.CTkButton(
                iv_row, text=label, width=0, height=36,
                fg_color=_ACCENT if is_sel else "#0D1A0D",
                hover_color="#3AB86A" if is_sel else "#142814",
                text_color="#000" if is_sel else _ACCENT2,
                font=ctk.CTkFont(_MONO, 10, "bold" if is_sel else "normal"),
                corner_radius=8,
                command=lambda s=secs: self._set_interval(s),
            )
            b.pack(side="left", padx=(0, 8))

        ctk.CTkLabel(f,
                     text="# Requires Groq API key for vision analysis\n"
                          "# Compatible with: Groq llama-4-scout vision model",
                     font=ctk.CTkFont(_MONO, 9), text_color="#2A4A2A",
                     justify="left").pack(anchor="w", pady=(12, 0))

    def _set_interval(self, secs: int) -> None:
        self._interval = secs

    def _pg_done(self) -> None:
        f = self._body

        # Big green check
        av = ctk.CTkFrame(f, fg_color=_CARD, corner_radius=40,
                          width=80, height=80, border_width=2, border_color=_ACCENT)
        av.pack(pady=(10, 16)); av.pack_propagate(False)
        ctk.CTkLabel(av, text="✓", font=ctk.CTkFont(_MONO, 32, "bold"),
                     text_color=_ACCENT).pack(expand=True)

        ctk.CTkLabel(f, text="> DEVELOPER MODE READY",
                     font=ctk.CTkFont(_MONO, 20, "bold"),
                     text_color=_ACCENT).pack(pady=(0, 6))
        ctk.CTkLabel(f, text="GIL is now configured for development.\nAll tools activate immediately.",
                     font=ctk.CTkFont(_MONO, 11),
                     text_color=_muted_color(), justify="center").pack(pady=(0, 20))

        # Summary
        from dev_config import _load
        cfg = _load()
        card = self._card(f); card.pack(fill="x")
        items = [
            (bool(cfg.get("github_token")), "GitHub integration connected"),
            (bool(cfg.get("project_dir")),  f"Projects: {cfg.get('project_dir','Desktop')}"),
            (bool(cfg.get("editor")),        f"Editor: {cfg.get('editor','code')}"),
            (cfg.get("screen_watch", True),  "Screen watcher enabled"),
        ]
        for ok, label in items:
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=7)
            ctk.CTkLabel(row, text="+" if ok else "-",
                         font=ctk.CTkFont(_MONO, 13, "bold"),
                         text_color=_ACCENT if ok else "#2A4A2A",
                         width=24).pack(side="left", padx=(0, 10))
            ctk.CTkLabel(row, text=label,
                         font=ctk.CTkFont(_MONO, 10),
                         text_color=_TEXT if ok else _muted_color(),
                         anchor="w").pack(side="left")


# ── Public entry point ────────────────────────────────────────────────────────

def run_dev_wizard(parent) -> bool:
    """Show the developer setup wizard. Returns True if completed."""
    w = DevSetupWizard(parent)
    w.wait_window()
    return w.completed
