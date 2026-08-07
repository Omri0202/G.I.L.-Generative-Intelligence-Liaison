"""
auth.py — Project G.I.L.
Handles the login GUI and SQLite credential management.
"""

import customtkinter as ctk
import sqlite3
import hashlib
import os
import time

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
        # Sessions used to be permanent, which quietly disabled the whole front
        # door: once saved, GIL never looked at a face or asked for a password
        # again, on any later start. Existing rows have no timestamp, so they
        # are treated as expired — which is the safe reading.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(session)")}
        if "created_at" not in cols:
            conn.execute("ALTER TABLE session ADD COLUMN created_at REAL")
        conn.commit()


#: How long a saved session is honoured. This exists to survive an immediate
#: restart or a crash loop, NOT to remember you across the day — the face scan
#: takes a second and is the point of having a front door at all.
SESSION_TTL = 120.0


def save_session(username: str) -> None:
    """Persist the logged-in user so an immediate restart isn't challenged."""
    with _get_connection() as conn:
        conn.execute("""
            INSERT INTO session (id, username, created_at) VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET username = excluded.username,
                                          created_at = excluded.created_at
        """, (username, time.time()))
        conn.commit()


def get_saved_session(max_age: float | None = SESSION_TTL) -> str | None:
    """The remembered username, or None if there is none or it has expired.

    max_age=None returns it regardless of age, for callers that want to know
    WHO was last here rather than to skip authentication."""
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT username, created_at FROM session WHERE id = 1").fetchone()
    if not row:
        return None
    name, made = row[0], row[1]
    if max_age is None:
        return name
    if made is None or (time.time() - float(made)) > max_age:
        return None                     # stale: verify who is there
    return name


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


_LOGIN_PAGE = Path(__file__).parent / "web_chat" / "login.html"


def run_web_login(timeout: float = 300.0) -> str | None | bool:
    """The designed sign-in page, served locally and opened full-screen.

    Returns the username, None if cancelled, or False if the page could not be
    shown at all — which is the caller's cue to fall back to the desktop
    window. Never raises: being unable to draw a prettier login must not be
    able to lock anyone out of their own assistant.
    """
    import json as _json
    import queue
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    if not _LOGIN_PAGE.exists():
        return False
    try:
        html = _LOGIN_PAGE.read_text(encoding="utf-8")
    except Exception:
        return False

    result: queue.Queue = queue.Queue(maxsize=1)
    cancelled = threading.Event()
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, ctype, body: bytes):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.split("?", 1)[0] in ("/", "/login", "/login.html"):
                self._send(200, "text/html; charset=utf-8", html.encode("utf-8"))
            else:
                self._send(404, "text/plain", b"no")

        def do_POST(self):
            if self.path.split("?", 1)[0] != "/api":
                return self._send(404, "text/plain", b"no")
            n = int(self.headers.get("Content-Length", 0) or 0)
            try:
                req = _json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                req = {}
            method, args = req.get("method", ""), req.get("args") or []
            out = None
            if method == "login":
                who = authenticate(args[0] if args else "",
                                   args[1] if len(args) > 1 else "")
                # Deliberately the same message whether the name or the
                # password was wrong: saying which one is a free hint about
                # whose machine this is.
                out = ({"ok": True, "name": who} if who else
                       {"ok": False, "error": "That didn't match. Try again."})
                if who and not result.full():
                    result.put(who)
            elif method == "cancel":
                out = {"ok": True}
                cancelled.set()
            elif method == "done":
                out = {"ok": True}
                done.set()
            self._send(200, "application/json",
                       _json.dumps({"result": out}).encode("utf-8"))

    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    except Exception:
        return False
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name="GIL-Login").start()

    try:
        from face_login import _open_fullscreen
        last = get_saved_session(max_age=None) or ""
        proc = _open_fullscreen(f"http://127.0.0.1:{port}/login?who={last}")
    except Exception:
        try:
            httpd.shutdown(); httpd.server_close()
        except Exception:
            pass
        return False

    name = None
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cancelled.is_set():
                break
            try:
                name = result.get(timeout=0.25)
                break
            except queue.Empty:
                continue
        if name:
            done.wait(timeout=4.0)      # let the welcome land before we close
    finally:
        try:
            httpd.shutdown(); httpd.server_close()
        except Exception:
            pass
        try:
            from face_login import _close
            _close(proc)
        except Exception:
            pass
    return name


def run_login(force: bool = False) -> str | None:
    """
    Return the authenticated username.
    A saved session normally skips the GUI. `force` demands a real sign-in.

    force=True is passed when the user pressed "Use password instead" on the
    face scanner. Without it that button could never work: it cancels the scan,
    control returns here, the saved session is found, and they are signed in
    without ever being asked for anything — which is precisely the opposite of
    what the button says it does.
    """
    initialize_database()

    saved = get_saved_session()
    if saved and not force:
        print(f"[G.I.L.] Session restored: {saved}")
        return saved

    # The designed page first; the desktop window only if it cannot be shown.
    # False means "could not display", which is different from None ("the user
    # cancelled") — conflating them would either strand the user on a failure
    # or ignore their decision to back out.
    try:
        web = run_web_login()
    except Exception:
        web = False
    if web is not False:
        if web:
            save_session(web)
        return web or None

    app = LoginWindow()
    app.mainloop()

    if app.authenticated_user:
        save_session(app.authenticated_user)

    return app.authenticated_user
