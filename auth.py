"""
auth.py — Project G.I.L.
Handles the login GUI and SQLite credential management.
"""

import customtkinter as ctk
import sqlite3
import hashlib
import os
import bcrypt
from pathlib import Path


# ── Database configuration ───────────────────────────────────────────────────

DB_PATH = Path(__file__).parent / "database" / "gil.db"


def _get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def initialize_database() -> None:
    """Create the users and session tables if they do not exist."""
    with _get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT    NOT NULL UNIQUE,
                password TEXT    NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session (
                id       INTEGER PRIMARY KEY CHECK (id = 1),
                username TEXT    NOT NULL
            )
        """)
        conn.commit()


def save_session(username: str) -> None:
    """Persist the logged-in user so the login screen is skipped next run."""
    with _get_connection() as conn:
        conn.execute("""
            INSERT INTO session (id, username) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET username = excluded.username
        """, (username,))
        conn.commit()


def get_saved_session() -> str | None:
    """Return the remembered username, or None if no session is saved."""
    with _get_connection() as conn:
        row = conn.execute("SELECT username FROM session WHERE id = 1").fetchone()
    return row[0] if row else None


def clear_session() -> None:
    """Forget the saved session (forces login screen on next run)."""
    with _get_connection() as conn:
        conn.execute("DELETE FROM session WHERE id = 1")
        conn.commit()


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def register_user(username: str, password: str) -> bool:
    """Register a new user. Returns False if username already exists."""
    try:
        with _get_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, password) VALUES (?, ?)",
                (username.strip(), _hash_password(password))
            )
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def verify_credentials(username: str, password: str) -> bool:
    """Return True if the username/password pair is valid. Migrates SHA-256 hashes to bcrypt on first login."""
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT password FROM users WHERE username = ?",
            (username.strip(),)
        ).fetchone()
    if row is None:
        return False

    stored = row[0]

    # Bcrypt hashes start with $2b$ or $2a$
    if stored.startswith("$2"):
        try:
            return bcrypt.checkpw(password.encode(), stored.encode())
        except Exception:
            return False

    # Legacy SHA-256 hash — verify then silently upgrade to bcrypt
    if stored == hashlib.sha256(password.encode()).hexdigest():
        new_hash = _hash_password(password)
        with _get_connection() as conn:
            conn.execute(
                "UPDATE users SET password = ? WHERE username = ?",
                (new_hash, username.strip())
            )
            conn.commit()
        return True

    return False


# ── Login GUI ─────────────────────────────────────────────────────────────────

class LoginWindow(ctk.CTk):
    """
    Dark-themed CustomTkinter login window.
    Sets self.authenticated_user on success, then closes itself.
    """

    ACCENT   = "#00BFFF"
    BG_DARK  = "#0A0A0F"
    BG_PANEL = "#12121A"
    FG_TEXT  = "#E0E0E0"

    def __init__(self):
        super().__init__()

        self._seed_default_user()

        self.authenticated_user: str | None = None
        self._mode = "login"

        self.title("G.I.L. — System Access")
        self.geometry("480x560")
        self.resizable(False, False)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.configure(fg_color=self.BG_DARK)

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _seed_default_user(self) -> None:
        """Create default 'Omri' account on first run."""
        register_user("Omri", "gill2024")

    def _build_ui(self) -> None:
        header_frame = ctk.CTkFrame(self, fg_color=self.BG_PANEL, corner_radius=0)
        header_frame.pack(fill="x", pady=(0, 2))

        ctk.CTkLabel(
            header_frame,
            text="G . I . L .",
            font=ctk.CTkFont(family="Courier New", size=36, weight="bold"),
            text_color=self.ACCENT
        ).pack(pady=(30, 4))

        ctk.CTkLabel(
            header_frame,
            text="GENERATIVE INTELLIGENCE LIAISON",
            font=ctk.CTkFont(size=10, weight="normal"),
            text_color="#5A5A7A"
        ).pack(pady=(0, 24))

        ctk.CTkFrame(self, height=1, fg_color=self.ACCENT).pack(fill="x")

        form = ctk.CTkFrame(self, fg_color=self.BG_DARK)
        form.pack(expand=True, fill="both", padx=48, pady=32)

        ctk.CTkLabel(
            form, text="SYSTEM AUTHENTICATION",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#5A5A7A"
        ).pack(anchor="w", pady=(0, 20))

        ctk.CTkLabel(form, text="USERNAME", font=ctk.CTkFont(size=10),
                     text_color="#8888AA").pack(anchor="w")
        self.username_entry = ctk.CTkEntry(
            form, height=44, corner_radius=6,
            fg_color=self.BG_PANEL, border_color="#2A2A3A",
            text_color=self.FG_TEXT, font=ctk.CTkFont(size=14)
        )
        self.username_entry.pack(fill="x", pady=(4, 16))

        ctk.CTkLabel(form, text="ACCESS CODE", font=ctk.CTkFont(size=10),
                     text_color="#8888AA").pack(anchor="w")
        self.password_entry = ctk.CTkEntry(
            form, height=44, corner_radius=6, show="*",
            fg_color=self.BG_PANEL, border_color="#2A2A3A",
            text_color=self.FG_TEXT, font=ctk.CTkFont(size=14)
        )
        self.password_entry.pack(fill="x", pady=(4, 24))
        self.password_entry.bind("<Return>", lambda _: self._handle_login())

        self.status_label = ctk.CTkLabel(
            form, text="", font=ctk.CTkFont(size=11),
            text_color="#FF4444"
        )
        self.status_label.pack(pady=(0, 12))

        self.action_btn = ctk.CTkButton(
            form, text="INITIALIZE SYSTEM",
            height=48, corner_radius=6,
            fg_color=self.ACCENT, hover_color="#0090CC",
            text_color="#000000",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._handle_login
        )
        self.action_btn.pack(fill="x")

        self.toggle_label = ctk.CTkLabel(
            form, text="First time? Register an account",
            font=ctk.CTkFont(size=10), text_color="#5A5A7A",
            cursor="hand2"
        )
        self.toggle_label.pack(pady=(16, 0))
        self.toggle_label.bind("<Button-1>", lambda _: self._toggle_mode())

    def _handle_login(self) -> None:
        username = self.username_entry.get().strip()
        password = self.password_entry.get()

        if not username or not password:
            self._set_status("Access denied. Credentials incomplete.")
            return

        if self._mode == "login":
            if verify_credentials(username, password):
                self.authenticated_user = username
                self.destroy()
            else:
                self._set_status("Authentication failed. Invalid credentials.")
        else:
            if register_user(username, password):
                self._set_status("User registered. You may now log in.", success=True)
                self._toggle_mode()
            else:
                self._set_status("Username already exists in the system.")

    def _toggle_mode(self) -> None:
        if self._mode == "login":
            self._mode = "register"
            self.action_btn.configure(text="REGISTER IDENTITY")
            self.toggle_label.configure(text="Already registered? Log in")
            self._set_status("")
        else:
            self._mode = "login"
            self.action_btn.configure(text="INITIALIZE SYSTEM")
            self.toggle_label.configure(text="First time? Register an account")
            self._set_status("")

    def _set_status(self, message: str, success: bool = False) -> None:
        color = "#00FF88" if success else "#FF4444"
        self.status_label.configure(text=message, text_color=color)

    def _on_close(self) -> None:
        self.authenticated_user = None
        self.destroy()


def run_login() -> str | None:
    """
    Return the authenticated username.
    If a session is saved from a previous login, skip the GUI entirely.
    Otherwise show the login window and save the session on success.
    """
    initialize_database()

    saved = get_saved_session()
    if saved:
        print(f"[G.I.L.] Session restored: {saved}")
        return saved

    app = LoginWindow()
    app.mainloop()

    if app.authenticated_user:
        save_session(app.authenticated_user)

    return app.authenticated_user
