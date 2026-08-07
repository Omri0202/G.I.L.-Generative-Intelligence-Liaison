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
    """Register a new user. False if the name is taken, in ANY capitalisation.

    The UNIQUE constraint on the column is case-sensitive, so "omri" happily
    registered alongside an existing "Omri" — two accounts, two passwords, one
    person, and no way to tell which one a login attempt was hitting."""
    name = (username or "").strip()
    if not name:
        return False
    try:
        with _get_connection() as conn:
            taken = conn.execute(
                "SELECT 1 FROM users WHERE lower(username) = lower(?)", (name,)
            ).fetchone()
            if taken:
                return False
            conn.execute(
                "INSERT INTO users (username, password) VALUES (?, ?)",
                (name, _hash_password(password))
            )
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def _check_one(stored_name: str, stored_hash: str, password: str) -> bool:
    """Verify against one stored row, upgrading a legacy hash on success."""
    # Bcrypt hashes start with $2b$ or $2a$
    if stored_hash.startswith("$2"):
        try:
            return bcrypt.checkpw(password.encode(), stored_hash.encode())
        except Exception:
            return False

    # Legacy SHA-256 hash — verify then silently upgrade to bcrypt
    if stored_hash == hashlib.sha256(password.encode()).hexdigest():
        with _get_connection() as conn:
            conn.execute("UPDATE users SET password = ? WHERE username = ?",
                         (_hash_password(password), stored_name))
            conn.commit()
        return True
    return False


def authenticate(username: str, password: str) -> str | None:
    """Return the STORED spelling of the user on success, else None.

    Matching the username is CASE-INSENSITIVE. It used to be an exact `=`,
    which is case-sensitive in SQLite, so typing your own name in lower case
    reached a different account — or none. On this machine that meant "Omri"
    signed in and "omri" did not, because a stale second account existed under
    the lower-case spelling with an older password. The password looked broken
    when the account was simply the wrong one.

    Nobody types their name the same way twice, and a login is the worst place
    to be strict about it.
    """
    name = (username or "").strip()
    if not name or not password:
        return None
    with _get_connection() as conn:
        rows = conn.execute(
            "SELECT username, password FROM users "
            "WHERE lower(username) = lower(?)", (name,)
        ).fetchall()
    # Duplicates that differ only by case can exist from before this fix. Try
    # the modern hash first, but try them all: refusing to check the older row
    # would lock out anyone whose password lives on it.
    rows.sort(key=lambda r: (not str(r[1]).startswith("$2"),
                             str(r[0]) != name))
    for stored_name, stored_hash in rows:
        if _check_one(stored_name, str(stored_hash), password):
            return stored_name
    return None


def verify_credentials(username: str, password: str) -> bool:
    """True if the username/password pair is valid (username case-insensitive)."""
    return authenticate(username, password) is not None


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
            # Sign in under the STORED spelling, not what was typed: sessions,
            # face enrolment and chat history are all keyed on this name, and
            # "omri" and "Omri" must not become two different people.
            who = authenticate(username, password)
            if who:
                self.authenticated_user = who
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
