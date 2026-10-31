"""
vigil_chat.py — the Vigil chat: G.I.L. texting you, WhatsApp-style.

vigil.py keeps watch; this is where it talks to you. One conversation with
GIL in its own window. Updates arrive as messages — quiet ones silently, the
rest with a Windows notification, urgent ones marked — and when you're away
from the PC the ones worth a ping come as voice messages in GIL's voice. You
answer in the same chat: "stop", "go", "watch my inbox for anything from the
army", "less like this" (replying to a message), or a voice message of your
own, and GIL changes what he's watching.

Its own small server on 127.0.0.1. Deliberately NOT a page of the main chat:
that server is replaced every time the main chat window is reopened, and a
Vigil window attached to it would silently stop receiving messages — the one
failure a watch must never have.

One process hosts Vigil at a time (a named mutex decides): GIL while it's
running, the always-on watcher (watcher.py) while it isn't, so the watches
keep going with GIL closed. The other process is a "client": opening Vigil
there asks the host to open its window.

A phone on the same Wi-Fi can use the chat too, once the user turns that on
and scans the QR code: the server then also listens on the network, and every
request from off this PC must carry the pairing token. Turning it off forgets
the token, so an old link stops working.

Messages live in data/vigil_chat.db, voice messages in data/vigil_voice/.
Everything stays on this PC; the only traffic is the checks and replies GIL
makes through Groq, as everywhere else.
"""

from __future__ import annotations

import base64
import ctypes
import hmac
import json
import queue
import re
import secrets
import sqlite3
import sys
import threading
import time
import uuid
from ctypes import wintypes
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

import vigil
from logger import get as _get_log

log = _get_log("vigil.chat")

ROOT = Path(__file__).parent
WEB = ROOT / "web_chat"
PAGE = WEB / "vigil.html"
DB_PATH = ROOT / "data" / "vigil_chat.db"
VOICE_DIR = ROOT / "data" / "vigil_voice"
PROFILE = ROOT / "data" / "_vigil_profile"
PORT_FILE = ROOT / "data" / "_vigil_port.txt"
#: One process hosts Vigil — GIL, or the always-on watcher while GIL is closed.
HOST_MUTEX = "ProjectGIL_VigilHost"
#: Clicking a Vigil notification opens gil-vigil:open, which focuses this chat.
PROTOCOL = "gil-vigil"
#: The window's title; how an open Vigil window is found and brought forward.
WINDOW_TITLE = "Vigil — G.I.L."

PORTS = (8741, 8742, 8743, 0)
AWAY_SECS = 600          # no keyboard or mouse for this long: away from the PC
BACK_SECS = 30           # ...and input again within this: back
REMIND_SECS = 3 * 3600   # "remind me later"
PAGE_SIZE = 80
#: The conversation wants a quick, plain reply — no hidden reasoning first.
MODELS = ("qwen/qwen3.8-27b", "openai/gpt-oss-20b")
WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
WHISPER_MODEL = "whisper-large-v3-turbo"

_VOICE_FILE = re.compile(r"^(gil|me)-[a-f0-9]{12}\.(mp3|webm|ogg|wav)$")
_AUDIO_TYPES = {".mp3": "audio/mpeg", ".webm": "audio/webm",
                ".ogg": "audio/ogg", ".wav": "audio/wav"}
_STATIC = {"gil_icon.png": "image/png", "favicon.ico": "image/x-icon",
           "gil_icon.ico": "image/x-icon"}

_db_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_clients: list[queue.Queue] = []
_local_clients: set = set()          # the subset that are windows on this PC
_clients_lock = threading.Lock()
_inbox: queue.Queue = queue.Queue()
_stop = threading.Event()
_httpd: ThreadingHTTPServer | None = None
_port = 0
_user = ""
#: What each part of GIL is doing right now; the header shows the most direct.
_activity: dict[str, str] = {}
#: Whether the user is looking at the chat, as the page reports it.
_view = {"visible": False, "focused": False, "at": 0.0}
#: Voice messages sent while the user was away, for "welcome back".
_away = {"since": 0.0, "voice": []}
#: "host" runs Vigil here; "client" (GIL while the watcher hosts) only opens it.
_role = ""
_mutex = None
_protocol_ok = False


# ── message store ─────────────────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript("""
            CREATE TABLE IF NOT EXISTS messages(
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                at       REAL    NOT NULL,
                sender   TEXT    NOT NULL,              -- gil | me | system
                kind     TEXT    NOT NULL,              -- text | voice | card | system
                text     TEXT    NOT NULL DEFAULT '',
                audio    TEXT,                          -- file in data/vigil_voice
                urgency  TEXT,                          -- low | normal | urgent
                order_id TEXT,
                reply_to INTEGER,
                status   TEXT    NOT NULL DEFAULT 'sent',  -- gil: unread|read; me: sent|read
                meta     TEXT    NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS reminders(      -- "remind me later" on a message
                message_id INTEGER PRIMARY KEY,
                due        REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS later(          -- reminders GIL set himself
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                due  REAL NOT NULL,
                text TEXT NOT NULL);
        """)
        _conn.commit()
    return _conn


def _public(row) -> dict:
    """A message as the page sees it: meta decoded, audio as a URL, and the
    message it replies to quoted inline."""
    msg = dict(row)
    msg["meta"] = json.loads(msg.get("meta") or "{}")
    msg["audio"] = f"/voice/{msg['audio']}" if msg.get("audio") else None
    if msg.get("reply_to"):
        q = _db().execute("SELECT id, sender, kind, text FROM messages WHERE id=?",
                          (msg["reply_to"],)).fetchone()
        if q:
            msg["quote"] = {"id": q["id"], "sender": q["sender"], "kind": q["kind"],
                            "text": (q["text"] or "")[:180]}
    return msg


def get_message(mid: int) -> dict | None:
    with _db_lock:
        row = _db().execute("SELECT * FROM messages WHERE id=?", (int(mid),)).fetchone()
        return _public(row) if row else None


def add_message(sender: str, text: str = "", kind: str = "text", urgency: str | None = None,
                order_id: str | None = None, reply_to: int | None = None,
                audio: str | None = None, meta: dict | None = None,
                status: str | None = None) -> dict:
    status = status or ("unread" if sender == "gil" else "sent" if sender == "me" else "read")
    with _db_lock:
        cur = _db().execute(
            "INSERT INTO messages(at, sender, kind, text, audio, urgency, order_id,"
            " reply_to, status, meta) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (time.time(), sender, kind, text or "", audio, urgency, order_id,
             reply_to, status, json.dumps(meta or {}, ensure_ascii=False)))
        _db().commit()
        msg = get_message(cur.lastrowid)
    _push("message", msg)
    return msg


def update_message(mid: int, text: str | None = None, status: str | None = None,
                   **meta) -> dict | None:
    """Change a message in place (its ticks, its transcript, a card's outcome)
    and let the page redraw it."""
    with _db_lock:
        row = _db().execute("SELECT text, status, meta FROM messages WHERE id=?",
                            (int(mid),)).fetchone()
        if row is None:
            return None
        merged = {**json.loads(row["meta"] or "{}"), **meta}
        _db().execute("UPDATE messages SET text=?, status=?, meta=? WHERE id=?",
                      (row["text"] if text is None else text,
                       row["status"] if status is None else status,
                       json.dumps(merged, ensure_ascii=False), int(mid)))
        _db().commit()
        msg = get_message(mid)
    _push("update", msg)
    return msg


def messages(limit: int = PAGE_SIZE, before: int | None = None) -> list[dict]:
    """The newest `limit` messages (older than `before`), oldest first."""
    with _db_lock:
        if before:
            rows = _db().execute("SELECT * FROM messages WHERE id<? ORDER BY id DESC LIMIT ?",
                                 (int(before), int(limit))).fetchall()
        else:
            rows = _db().execute("SELECT * FROM messages ORDER BY id DESC LIMIT ?",
                                 (int(limit),)).fetchall()
        return [_public(r) for r in reversed(rows)]


def unread_count() -> int:
    with _db_lock:
        return _db().execute("SELECT COUNT(*) FROM messages WHERE sender='gil'"
                             " AND status='unread'").fetchone()[0]


def mark_read() -> int:
    with _db_lock:
        n = _db().execute("UPDATE messages SET status='read' WHERE sender='gil'"
                          " AND status='unread'").rowcount
        _db().commit()
    if n:
        _push("unread", 0)
    return n


# ── live push ─────────────────────────────────────────────────────────────────

def _push(fn: str, arg=None) -> None:
    msg = json.dumps({"fn": fn, "arg": arg}, ensure_ascii=False)
    with _clients_lock:
        clients = list(_clients)
    for q in clients:
        try:
            q.put_nowait(msg)
        except Exception:
            pass


def window_open() -> bool:
    """A Vigil window is open on this PC (a phone's page doesn't count)."""
    with _clients_lock:
        return any(q in _local_clients for q in _clients)


def _looking() -> bool:
    """The user has the chat in front of them: a ping would only interrupt."""
    return (window_open() and _view["visible"] and _view["focused"]
            and time.time() - _view["at"] < 90)


def set_activity(source: str, text: str | None) -> None:
    """source "reply" (typing…, recording audio…) or "check" (checking your
    inbox…). None clears it. The header shows the reply activity first: it's
    the one the user is waiting on."""
    if text:
        _activity[source] = text
    else:
        _activity.pop(source, None)
    _push("status", status_line())


def status_line() -> str:
    for source in ("reply", "check"):
        if _activity.get(source):
            return _activity[source]
    return "online" if vigil.master_on() else "paused · say “go” to start again"


# ── presence: is the user at the PC? ──────────────────────────────────────────

class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds() -> float:
    """Seconds since the last keyboard or mouse input anywhere on Windows."""
    try:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.GetLastInputInfo.argtypes = [ctypes.POINTER(_LASTINPUTINFO)]
        user32.GetLastInputInfo.restype = ctypes.c_int
        kernel32.GetTickCount.restype = ctypes.c_uint
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        return ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return 0.0


def _presence_loop() -> None:
    while not _stop.wait(10):
        try:
            idle = idle_seconds()
            if idle >= AWAY_SECS and not _away["since"]:
                _away.update(since=time.time() - idle, voice=[])
            elif _away["since"] and idle < BACK_SECS:
                _welcome_back()
            _due_reminders()
        except Exception:
            log.error("vigil presence loop failed", exc_info=True)


def _welcome_back() -> None:
    unheard = [m for m in (get_message(i) for i in _away["voice"])
               if m and not m["meta"].get("played")]
    _away.update(since=0.0, voice=[])
    if not unheard:
        return
    n = len(unheard)
    msg = add_message("gil", f"Welcome back 👋 You've got {n} voice message"
                             f"{'s' if n != 1 else ''} from me.", urgency="normal")
    _ping(msg)


# ── voice ─────────────────────────────────────────────────────────────────────

_URL = re.compile(r"https?://\S+")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]")


def synthesize(text: str) -> str | None:
    """GIL's voice saying `text`, saved as a voice message; returns its file
    name. Hebrew text gets GIL's Hebrew voice, like everywhere else."""
    try:
        import asyncio
        import edge_tts
        import voice
        spoken = _URL.sub("the link in the chat", text or "")
        spoken = voice._clean(_EMOJI.sub("", spoken))
        if not spoken:
            return None
        name = voice._HE_VOICE if voice._is_hebrew(spoken) else voice.EDGE_TTS_VOICE
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        fname = f"gil-{uuid.uuid4().hex[:12]}.mp3"
        asyncio.run(edge_tts.Communicate(spoken, name).save(str(VOICE_DIR / fname)))
        return fname
    except Exception:
        log.warning("voice message synthesis failed", exc_info=True)
        return None


def transcribe(raw: bytes, filename: str, mime: str) -> str:
    """The user's voice message as text (Groq Whisper, any language)."""
    for key in vigil._keys():
        try:
            r = requests.post(WHISPER_URL, timeout=60,
                              headers={"Authorization": f"Bearer {key}"},
                              files={"file": (filename, raw, mime)},
                              data={"model": WHISPER_MODEL, "response_format": "json"})
        except requests.RequestException:
            continue
        if r.status_code == 429:
            continue
        if r.ok:
            return (r.json().get("text") or "").strip()
        log.warning("whisper answered %s: %.160s", r.status_code, r.text)
    return ""


# ── what Vigil found: deliver it ──────────────────────────────────────────────

_NEEDS_BUTTONS = [{"id": "done", "label": "Done"},
                  {"id": "later", "label": "Remind me later"},
                  {"id": "stop", "label": "Stop these"}]


def _say(text: str, urgency: str = "normal", voice: bool = False, **fields) -> dict:
    """GIL sends a message: as a voice message when asked to, or when the user
    is away from the PC and it's worth a ping."""
    away = idle_seconds() >= AWAY_SECS
    audio = None
    if voice or (away and urgency != "low"):
        set_activity("reply", "recording audio…")
        try:
            audio = synthesize(text)
        finally:
            set_activity("reply", None)
    msg = add_message("gil", text, kind="voice" if audio else fields.pop("kind", "text"),
                      urgency=urgency, audio=audio, **fields)
    if audio and _away["since"]:
        _away["voice"].append(msg["id"])
    return msg


def deliver(notice: dict) -> None:
    """vigil.py found something: text it to the user — a voice message when
    they're away or the order is a voice routine — then deal with any action
    the order proposed."""
    urgency = notice.get("urgency") or "normal"
    meta = {"watch": notice.get("title", "")}
    if notice.get("keep"):
        meta.update(needs_id=notice.get("id"), buttons=_NEEDS_BUTTONS)
    msg = _say(notice["message"], urgency=urgency, voice=bool(notice.get("voice")),
               order_id=notice.get("order_id"), meta=meta)
    _ping(msg)
    if notice.get("action"):
        _propose(notice["action"], notice.get("order_id"), msg["id"])


def _propose(action: dict, order_id: str | None, reply_to: int | None) -> None:
    """An action an order came up with on its own. It has passed the order's
    own list in vigil.py; here it meets Sentinel, then the user's level."""
    import vigil_actions as va
    order = vigil.get(order_id or "")
    if order is None:
        return
    clean, why = va.validate(action)
    if clean is None:
        log.info("vigil action dropped: %s", why)
        return
    ok, reason = va.sentinel(clean, order)
    if not ok:
        add_message("system", f"Blocked: “{va.describe(clean)}”. “{order['title']}” wasn't "
                              f"asked to do that{f' — {reason}' if reason else ''}.")
        return
    level = va.level(clean["type"])
    if level == "auto":
        done, result = va.execute(clean)
        msg = _say(f"{'Done' if done else 'I tried'}: {va.describe(clean)}. {result}",
                   order_id=order_id, reply_to=reply_to, meta={"watch": order["title"]})
    elif level == "never":
        msg = _say(f"You might want to: {va.describe(clean)}. (I won't do this kind of "
                   "thing myself — you set it to “never”.)",
                   order_id=order_id, reply_to=reply_to, meta={"watch": order["title"]})
    else:
        msg = _action_card(clean, str(action.get("why") or ""), order_id, reply_to,
                           order["title"])
    _ping(msg)


def _action_card(clean: dict, why: str, order_id: str | None = None,
                 reply_to: int | None = None, watch: str = "") -> dict:
    """“Can I?” — the card for an action waiting on the user's yes."""
    import vigil_actions as va
    return add_message(
        "gil", va.describe(clean), kind="card", urgency="normal", order_id=order_id,
        reply_to=reply_to,
        meta={"card": "action", "watch": watch, "action": clean, "why": why[:200],
              "buttons": [{"id": "approve", "label": "Do it"},
                          {"id": "deny", "label": "Don't"},
                          {"id": "always", "label": "Do it, and don't ask again"}]})


def _ping(msg: dict) -> None:
    """A Windows notification, like a phone's — unless it's a quiet message
    or the user is already looking at the chat."""
    if msg.get("urgency") == "low" or _looking():
        return
    try:
        import notify
        body = msg["text"] if msg["kind"] != "voice" else f"🎤 Voice message · {msg['text']}"
        if msg["kind"] == "card" and msg["meta"].get("card") == "action":
            body = f"Can I? {msg['text']}"
        notify.toast("G.I.L.", body, msg.get("urgency") or "normal", launch=_launch_url())
    except Exception:
        log.warning("vigil chat toast failed", exc_info=True)
    _push("unread", unread_count())


def _launch_url() -> str:
    """What a notification click opens: this chat's own window when the
    gil-vigil: link is registered, the chat's address in a browser if not."""
    return f"{PROTOCOL}:open" if _protocol_ok else url()


# ── reminders ─────────────────────────────────────────────────────────────────

def _due_reminders() -> None:
    now = time.time()
    with _db_lock:
        due = [r["message_id"] for r in _db().execute(
            "SELECT message_id FROM reminders WHERE due<=?", (now,)).fetchall()]
        if due:
            _db().executemany("DELETE FROM reminders WHERE message_id=?",
                              [(m,) for m in due])
            _db().commit()
    for mid in due:
        original = get_message(mid)
        if not original:
            continue
        meta = {k: v for k, v in original["meta"].items() if k in ("watch", "needs_id")}
        if meta.get("needs_id"):
            meta["buttons"] = _NEEDS_BUTTONS
        msg = add_message("gil", f"Reminder: {original['text']}", urgency="normal",
                          order_id=original.get("order_id"), reply_to=mid, meta=meta)
        _ping(msg)
    with _db_lock:
        later = _db().execute("SELECT id, text FROM later WHERE due<=?", (now,)).fetchall()
        if later:
            _db().executemany("DELETE FROM later WHERE id=?", [(r["id"],) for r in later])
            _db().commit()
    for row in later:
        _ping(_say(f"⏰ {row['text']}", meta={"reminder": True}))


def schedule_reminder(text: str, when_iso: str) -> None:
    """A reminder GIL set — from an order's action or the user's own request.
    It arrives here as a message at that time (spoken if they're away)."""
    due = datetime.fromisoformat(when_iso).timestamp()
    with _db_lock:
        _db().execute("INSERT INTO later(due, text) VALUES (?,?)", (due, text))
        _db().commit()


# ── the conversation ──────────────────────────────────────────────────────────

_STOP = {"stop", "pause", "pause for now", "stop watching", "stop it", "quiet", "shh", "enough",
         "עצור", "תעצור", "תפסיק", "די", "עצור את זה", "שקט"}
_GO = {"go", "start", "resume", "continue", "go on", "start watching", "keep going",
       "המשך", "תמשיך", "יאללה", "תתחיל", "סע"}
_LIST = {"what are you watching", "what are you watching?", "list", "watches",
         "status", "מה אתה בודק", "על מה אתה שומר"}
_VOICE_ASK = re.compile(r"voice (message|note)|in (a|your) voice|say it out loud"
                        r"|הודעה קולית|הקלטה", re.I)


def _norm(text: str) -> str:
    return re.sub(r"[\s!.?,…]+$", "", (text or "").strip().lower())


def _source_names(order: dict) -> list[str]:
    from urllib.parse import urlparse
    names = []
    for s in order.get("sources", []):
        if s.get("type") == "url":
            names.append(urlparse(s.get("url", "")).hostname or "a page")
        else:
            names.append({"gmail": "Gmail", "calendar": "Calendar"}.get(
                s.get("type"), str(s.get("type"))))
    return names


def _when(order: dict) -> str:
    if order.get("at"):
        return f"every day at {order['at']}"
    start, end = (order.get("active_hours") or [0, 0])[:2]
    hours = "" if start == end else f", {start:02d}:00–{end:02d}:00"
    m = int(order["every_min"])
    every = ("every hour" if m == 60 else f"every {m // 60} hours" if m % 60 == 0
             else f"every {m} min")
    return f"{every}{hours}"


def _watch_line(order: dict) -> str:
    state = "" if order.get("enabled", True) else " (paused)"
    return f"{order['title']}{state} — {', '.join(_source_names(order))}, {_when(order)}"


def _quick(text: str, quote: dict | None) -> str | None:
    """Commands that must work instantly and without a model: stop and go."""
    t = _norm(text)
    watch = vigil.get(quote["order_id"]) if quote and quote.get("order_id") else None
    if t in _STOP:
        if watch:
            vigil.set_enabled(watch["id"], False)
            return f"Okay, I've stopped watching “{watch['title']}”. Reply “go” to it to bring it back."
        vigil.set_master(False)
        set_activity("check", None)
        return "Okay, I'm off watch. Nothing from me until you say “go” 🤐"
    if t in _GO:
        if watch:
            vigil.set_enabled(watch["id"], True)
            return f"Back on “{watch['title']}” 👀"
        vigil.set_master(True)
        n = sum(1 for o in vigil.orders() if o.get("enabled", True))
        return ("On it 👀 I'm watching again." if n else
                "I'm on 👀 Tell me what to keep an eye on.")
    if t in _LIST:
        orders = vigil.orders()
        if not orders:
            return "Nothing yet. Tell me what to keep an eye on — like “let me know if the army emails me”."
        lines = "\n".join(f"• {_watch_line(o)}" for o in orders)
        state = "" if vigil.master_on() else "\n\n(I'm paused right now — say “go” to start again.)"
        return f"Here's what I'm watching:\n{lines}{state}"
    return None


def _live_question(text: str) -> bool:
    """The same rule the main chat uses: current facts come from a search,
    never from a model's memory."""
    try:
        import fast_path_handler as fp
        lower = text.lower()
        return (fp._looks_like_question(text, lower)
                and not any(w in lower for w in fp._LIVE_EXCLUDE)
                and any(s in lower for s in fp._LIVE_SIGNALS))
    except Exception:
        return False


def _look_up(question: str, reply_to: int | None) -> None:
    """A live question, answered the way you'd text it: the short spoken answer
    research already writes, and the top couple of sources — not the long
    write-up meant for the main chat, whose extra research rounds also pull in
    loosely related news that doesn't belong under a two-line answer."""
    set_activity("reply", "looking it up…")
    try:
        import research
        chat_md, spoken = research.run(question)
    except Exception:
        log.warning("vigil chat lookup failed", exc_info=True)
        chat_md, spoken = "", "I couldn't look that up just now — try me again in a minute?"
    sources = re.findall(r"^- \[[^\]]+\]\(https?://[^)\s]+\)", chat_md or "", re.M)[:2]
    text = (spoken or chat_md).strip()
    if sources:
        text += "\n\n" + "\n".join(sources)
    add_message("gil", text, reply_to=reply_to, status="read" if _looking() else None)


_CHAT_SYSTEM = """You are G.I.L., {name}'s personal AI assistant, texting with them in your Vigil chat — the part of you that keeps watch while they're away: "watches" you check in the background, texting them when something matters.

How you text:
- Like a person in a messaging app: short, warm, natural. One to three sentences. No headings, no bullet lists unless they ask for a list. An emoji now and then is fine.
- Reply in the language they wrote in (Hebrew or English).

What you can watch — nothing else; say so plainly if they ask for more:
- Gmail: their unread mail.
- Google Calendar: their upcoming events.
- Any public web page, by its address (not pages behind a login).
- Their needs-you list (what you've flagged and they haven't cleared) — for a morning brief.
You can't watch WhatsApp, their phone, or sites that need a login yet. Don't pretend you can.
You can also set reminders, add events to their Google Calendar, and open pages on their PC.
You can't send email, pay, delete anything, or touch passwords.

What you can do: put ops in your reply.
 {{"op":"create","title":"short name","brief":"what to keep an eye on","sources":[{{"type":"gmail"}} or {{"type":"calendar","days":2}} or {{"type":"url","url":"https://..."}} or {{"type":"needs"}}],"attention":"exactly what is worth telling them","every_min":30,"at":"","change_only":true,"voice":false,"actions":[]}}
   every_min: how often to check, 5 to 1440. A daily routine ("every morning at 8") sets at:"08:00" instead.
   change_only: false only when the watch is about time passing ("the night before a meeting").
   voice: true to send its updates as voice messages (daily routines are spoken anyway).
   actions: what the watch may DO by itself — "reminder", "calendar_event", "open_page" — ONLY
   when their own words ask for it ("…and put exam dates in my calendar"). Otherwise [].
 {{"op":"act","action":{{"type":"reminder","text":"call grandma","when":"2026-11-10T09:00"}}}}
   does it now, because they just asked. Other actions: {{"type":"calendar_event","title":"...","start":"2026-11-12T09:00","minutes":60}}
   {{"type":"open_page","url":"https://..."}}. Times are local; work them out from NOW.
 {{"op":"allow","type":"reminder","level":"auto"}}  how freely watches act on their own:
   "auto" just do it, "ask" ask first, "never" only tell them.
 {{"op":"pause","id":"<watch id>"}}  {{"op":"resume","id":"<watch id>"}}  {{"op":"delete","id":"<watch id>"}}
 {{"op":"tune","id":"<watch id>","attention":"the whole new rule","every_min":30}}
   For "less like this", "more like this", "only tell me if…": rewrite the rule to include the change. every_min is optional.
 {{"op":"check_now","id":"<watch id>"}}
 {{"op":"master","on":false}} pauses all of Vigil; {{"op":"master","on":true}} resumes it.
 {{"op":"done","item":"<needs-you item id>"}}
 {{"op":"lookup","question":"..."}}  searches the web and texts them the answer. Use it for anything current (prices, weather, news, scores) — never state a current fact yourself.

Rules:
- Only change what they asked for. If they're just chatting, ops is [].
- If a request is ambiguous (which watch? which page?), ask one short question instead of guessing.
- When you create a watch, your reply says in one line what you'll watch and when you'll text them.
- "voice": true only when they asked for a voice message.

Reply with JSON only:
{{"reply": "your text message", "voice": false, "ops": []}}"""


def _context(user_msg: dict, quote: dict | None) -> str:
    orders = vigil.orders()
    watches = "\n".join(
        f"- id {o['id']}: {_watch_line(o)}. Tells them: {o.get('attention') or o.get('brief')}"
        for o in orders) or "(nothing yet)"
    needs = "\n".join(f"- item {n['id']}: {n['message']}" for n in vigil.needs_you()) or "(empty)"
    recent = [m for m in messages(limit=14) if m["id"] != user_msg["id"]][-12:]
    convo = "\n".join(
        f"{'GIL' if m['sender'] == 'gil' else 'THEM' if m['sender'] == 'me' else 'NOTE'}: "
        f"{(m['text'] or '')[:300]}" for m in recent) or "(this is the start)"
    import vigil_actions as va
    levels = ", ".join(f"{a['type']}: {a['level']}" for a in va.abilities())
    lines = [f"VIGIL IS: {'on' if vigil.master_on() else 'paused'}",
             f"WHAT WATCHES MAY DO ON THEIR OWN: {levels}",
             f"YOUR WATCHES:\n{watches}",
             f"THEIR NEEDS-YOU LIST:\n{needs}",
             f"NOW: {datetime.now().strftime('%A %d %B %Y, %H:%M')}",
             f"RECENT CHAT (oldest first):\n{convo}"]
    if quote:
        ref = f" (from the watch with id {quote['order_id']})" if quote.get("order_id") else ""
        lines.append(f"THEY'RE REPLYING TO: “{quote['text'][:300]}”{ref}")
    lines.append(f"THEIR NEW MESSAGE: {user_msg['text']}")
    return "\n\n".join(lines)


def _apply(ops: list, user_msg: dict | None = None) -> tuple[list[dict], list[str], list[dict]]:
    """Carry out the model's ops. Returns (watches created, problems to admit,
    cards to put after the reply — things to confirm with a tap)."""
    import vigil_actions as va
    created, problems, cards = [], [], []
    said = (user_msg or {}).get("text") or ""
    for op in (ops or [])[:6]:
        if not isinstance(op, dict):
            continue
        kind = op.get("op")
        try:
            wid = op.get("id")
            if kind in ("pause", "resume", "delete", "tune", "check_now") and not vigil.get(wid or ""):
                problems.append("I couldn't find that watch")
                continue
            if kind == "create":
                created.append(vigil.add(
                    op.get("title") or "Watch", op.get("brief") or op.get("title") or "",
                    op.get("sources") or [], attention=op.get("attention") or "",
                    every_min=int(op.get("every_min") or 30),
                    change_only=op.get("change_only", True) is not False,
                    at=op.get("at") or "",
                    voice=True if op.get("voice") is True else None,
                    actions=op.get("actions") or [], asked=said))
            elif kind in ("pause", "resume"):
                vigil.set_enabled(wid, kind == "resume")
            elif kind == "delete":
                vigil.remove(wid)
            elif kind == "tune":
                vigil.update(wid, attention=op.get("attention") or None,
                             every_min=op.get("every_min") or None, at=op.get("at") or None)
            elif kind == "check_now":
                threading.Thread(target=vigil.run_order, args=(wid, True),
                                 daemon=True, name="GIL-VigilCheck").start()
            elif kind == "master":
                vigil.set_master(bool(op.get("on")))
            elif kind == "done":
                vigil.clear(op.get("item") or "")
            elif kind == "act":
                clean, why = va.validate(op.get("action") or {})
                if clean is None:
                    problems.append(why)
                elif va.asked_for(clean, said):
                    ok, result = va.execute(clean)
                    if not ok:
                        problems.append(result)
                else:
                    cards.append({"action": clean})
            elif kind == "allow":
                typ, lvl = op.get("type"), op.get("level")
                if typ not in va.ACTIONS or lvl not in va.LEVELS:
                    problems.append("I can't change that setting")
                elif lvl == "auto" and va.level(typ) != "auto":
                    cards.append({"allow": typ})      # loosening: a tap confirms it
                else:
                    va.set_level(typ, lvl)
            elif kind == "lookup":
                question = (op.get("question") or "").strip()
                if question:
                    threading.Thread(target=_look_up, args=(question, None),
                                     daemon=True, name="GIL-VigilLookup").start()
        except (ValueError, TypeError) as exc:
            problems.append(str(exc))
    return created, problems, cards


def _watch_card(order: dict) -> None:
    tells = order.get("attention") or order.get("brief")
    add_message("gil", f"{' + '.join(_source_names(order))} · {_when(order)}\n{tells}",
                kind="card", order_id=order["id"],
                meta={"card": "watch", "watch": order["title"],
                      "buttons": [{"id": "check_now", "label": "Check now"},
                                  {"id": "cancel_watch", "label": "Cancel"}]})


def _converse(user_msg: dict, quote: dict | None) -> None:
    set_activity("reply", "typing…")
    obj, _tokens, why = vigil.call_json(
        [{"role": "system", "content": _CHAT_SYSTEM.format(name=_name() or "them")},
         {"role": "user", "content": _context(user_msg, quote)}],
        "reply", models=MODELS, max_tokens=900, temperature=0.4, reserve=False)
    if obj is None:
        log.warning("vigil chat reply failed: %s", why)
        add_message("gil", "Sorry, I lost my train of thought there — say that again?")
        return
    created, problems, cards = _apply(obj.get("ops") or [], user_msg)
    reply = str(obj.get("reply") or "").strip()
    if problems:
        reply = (reply + "\n\n" if reply else "") + "Hmm — " + "; ".join(problems) + "."
    if reply:
        audio = None
        if obj.get("voice") is True or _VOICE_ASK.search(user_msg["text"] or ""):
            set_activity("reply", "recording audio…")
            audio = synthesize(reply)
        add_message("gil", reply, kind="voice" if audio else "text",
                    status="read" if _looking() else None)
    for card in cards:
        if card.get("action"):
            _action_card(card["action"], "Just making sure that's what you meant.",
                         reply_to=user_msg["id"])
        else:
            import vigil_actions as va
            label = va.ACTIONS[card["allow"]]["label"].lower()
            add_message("gil", f"Let your watches {label} without asking you first?",
                        kind="card", reply_to=user_msg["id"],
                        meta={"card": "allow", "type": card["allow"],
                              "buttons": [{"id": "allow_yes", "label": "Yes"},
                                          {"id": "allow_no", "label": "No"}]})
    for order in created:
        _watch_card(order)
        # A first look straight away, so the watch is seen working at once.
        threading.Thread(target=vigil.run_order, args=(order["id"], True),
                         daemon=True, name="GIL-VigilFirstCheck").start()


def _handle(user_msg: dict) -> None:
    update_message(user_msg["id"], status="read")          # blue ticks: GIL has it
    quote = get_message(user_msg["reply_to"]) if user_msg.get("reply_to") else None
    text = (user_msg.get("text") or "").strip()
    if not text:
        return
    quick = _quick(text, quote)
    if quick:
        add_message("gil", quick, status="read" if _looking() else None)
        _push("state", _state_extras())
        return
    if _live_question(text):
        _look_up(text, user_msg["id"])
        return
    _converse(user_msg, quote)
    _push("state", _state_extras())


def _worker() -> None:
    while not _stop.is_set():
        try:
            user_msg = _inbox.get(timeout=1)
        except queue.Empty:
            continue
        try:
            _handle(user_msg)
        except Exception:
            log.error("vigil chat reply failed", exc_info=True)
            add_message("gil", "Sorry, something went wrong on my side. Try that again?")
        finally:
            set_activity("reply", None)


# ── the page's API ────────────────────────────────────────────────────────────

def _theme() -> str:
    try:
        return json.loads((ROOT / "data" / "gil_config.json").read_text(
            encoding="utf-8")).get("chat_theme", "dark")
    except Exception:
        return "dark"


def _watch_rows() -> list[dict]:
    rows = []
    for o in vigil.orders():
        last = (o.get("history") or [{}])[-1]
        rows.append({"id": o["id"], "title": o["title"], "line": _watch_line(o),
                     "enabled": o.get("enabled", True), "attention": o.get("attention", ""),
                     "last_run": o.get("last_run", 0), "last_outcome": last.get("outcome", ""),
                     "sources": [s.get("type") for s in o.get("sources", [])]})
    return rows


def _state_extras() -> dict:
    return {"master": vigil.master_on(), "watches": _watch_rows(),
            "needs": vigil.needs_you(), "spent": vigil.spent_today(),
            "allowance": vigil.DAILY_TOKENS, "status": status_line(),
            "suggestions": _suggestions(), "abilities": _abilities(),
            "phone": {"on": _phone()["on"]}}


def _abilities() -> list[dict]:
    import vigil_actions as va
    return [{**a, "levels": [{"id": lvl, "label": va.LEVEL_LABELS[lvl]} for lvl in va.LEVELS]}
            for a in va.abilities()]


def _suggestions() -> list[str]:
    """Quick replies that fit the moment: how to start, how to resume, or the
    everyday questions once he's watching."""
    if not vigil.master_on():
        return ["Go", "What are you watching?"]
    if not vigil.orders():
        return ["Let me know if the army emails me", "Brief me every morning at 8",
                "What can you watch?"]
    return ["What are you watching?", "Anything new?", "Pause for now"]


def api_state() -> dict:
    return {"messages": messages(), "name": _name(), "theme": _theme(),
            "unread": unread_count(), **_state_extras()}


def api_older(before: int) -> list[dict]:
    return messages(before=before)


def api_send(text: str = "", reply_to: int | None = None) -> dict | None:
    text = (text or "").strip()[:4000]
    if not text:
        return None
    msg = add_message("me", text, reply_to=reply_to or None)
    _inbox.put(msg)
    return msg


def api_send_voice(data: str = "", mime: str = "audio/webm",
                   reply_to: int | None = None) -> dict | None:
    """The user's voice message: kept as audio, transcribed, then answered."""
    try:
        raw = base64.b64decode(data.split(",", 1)[-1])
    except Exception:
        return None
    if not raw or len(raw) > 12_000_000:
        return None
    ext = ".ogg" if "ogg" in mime else ".wav" if "wav" in mime else ".webm"
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"me-{uuid.uuid4().hex[:12]}{ext}"
    (VOICE_DIR / fname).write_bytes(raw)
    msg = add_message("me", "", kind="voice", audio=fname, reply_to=reply_to or None,
                      meta={"transcribing": True})

    def _go():
        text = transcribe(raw, fname, mime.split(";")[0] or "audio/webm")
        done = update_message(msg["id"], text=text, transcribing=False)
        if text and done:
            _inbox.put(done)
        else:
            add_message("gil", "I couldn't make that one out — mind typing it?")
    threading.Thread(target=_go, daemon=True, name="GIL-VigilTranscribe").start()
    return msg


def api_read() -> int:
    return mark_read()


def api_view(visible: bool = False, focused: bool = False) -> bool:
    _view.update(visible=bool(visible), focused=bool(focused), at=time.time())
    return True


def api_master(on: bool = True) -> dict:
    vigil.set_master(bool(on))
    add_message("system", "Vigil is on" if on else "Vigil is paused")
    set_activity("check", None)
    _push("state", _state_extras())
    return _state_extras()


def api_watch(wid: str = "", action: str = "") -> dict:
    """The contact-info panel's switches: on, off, delete, check."""
    order = vigil.get(wid)
    if order is None:
        return _state_extras()
    if action in ("on", "off"):
        vigil.set_enabled(wid, action == "on")
    elif action == "delete":
        vigil.remove(wid)
        add_message("system", f"Stopped watching “{order['title']}”")
    elif action == "check":
        threading.Thread(target=vigil.run_order, args=(wid, True),
                         daemon=True, name="GIL-VigilCheck").start()
    _push("state", _state_extras())
    return _state_extras()


def api_needs_done(item_id: str = "") -> dict:
    vigil.clear(item_id)
    _push("state", _state_extras())
    return _state_extras()


def api_played(mid: int = 0) -> bool:
    return bool(update_message(mid, played=True))


def api_button(mid: int = 0, button: str = "") -> dict | None:
    """A card's buttons. Each one settles the card ("✓ Done") so it can't be
    pressed twice, except Check now, which can run again."""
    with _button_lock:                  # two quick taps must not do it twice
        msg = get_message(mid)
        if msg is None:
            return None
        if msg["meta"].get("resolved") and button != "check_now":
            return msg
        return _press(msg, mid, button)


_button_lock = threading.Lock()


def _press(msg: dict, mid: int, button: str) -> dict | None:
    import vigil_actions as va
    meta, oid = msg["meta"], msg.get("order_id")
    title = meta.get("watch") or "that"
    if button in ("approve", "always") and meta.get("card") == "action":
        clean, why = va.validate(meta.get("action") or {})   # its time may have passed
        if clean is None:
            result = update_message(mid, resolved=f"Not done — {why}")
        else:
            if button == "always":
                va.set_level(clean["type"], "auto")
            ok, said = va.execute(clean)
            result = update_message(mid, resolved=f"Done ✓ {said}" if ok else f"Didn't work — {said}")
    elif button == "deny" and meta.get("card") == "action":
        result = update_message(mid, resolved="Not done")
    elif button in ("allow_yes", "allow_no") and meta.get("card") == "allow":
        if button == "allow_yes" and meta.get("type") in va.ACTIONS:
            va.set_level(meta["type"], "auto")
            result = update_message(mid, resolved="Okay — I won't ask first")
        else:
            result = update_message(mid, resolved="Okay — I'll keep asking")
    elif button == "done":
        vigil.clear(meta.get("needs_id") or "")
        result = update_message(mid, resolved="Done ✓")
    elif button == "later":
        due = time.time() + REMIND_SECS
        with _db_lock:
            _db().execute("INSERT OR REPLACE INTO reminders(message_id, due) VALUES (?,?)",
                          (mid, due))
            _db().commit()
        result = update_message(mid, resolved=f"I'll remind you at {datetime.fromtimestamp(due):%H:%M}")
    elif button == "stop" and oid:
        vigil.set_enabled(oid, False)
        result = update_message(mid, resolved=f"Stopped “{title}”")
    elif button == "cancel_watch" and oid:
        vigil.remove(oid)
        result = update_message(mid, resolved="Cancelled")
    elif button == "check_now" and oid and vigil.get(oid):
        threading.Thread(target=vigil.run_order, args=(oid, True),
                         daemon=True, name="GIL-VigilCheck").start()
        result = msg
    else:
        result = msg
    _push("state", _state_extras())
    return result


def api_ability(kind: str = "", level: str = "") -> dict:
    """The contact page's “What G.I.L. can do” switches."""
    import vigil_actions as va
    try:
        va.set_level(kind, level)
    except ValueError:
        pass
    _push("state", _state_extras())
    return _state_extras()


def api_phone(on: bool = True) -> dict:
    """“Use on your phone”: open the chat to this Wi-Fi behind a fresh pairing
    token and hand back the link as a QR code — or close it again."""
    import mobile
    if on:
        token = _phone()["token"] or secrets.token_urlsafe(18)
        vigil.set_pref("phone", {"on": True, "token": token})
        _rebind("0.0.0.0")
    else:
        vigil.set_pref("phone", {"on": False, "token": ""})
        _rebind("127.0.0.1")
        _drop_phones()
    _push("state", _state_extras())
    view = {"on": bool(on)}
    if on:
        ip = mobile.lan_ip()
        link = mobile.pairing_url(ip, _port, _phone()["token"])
        view.update(url=link, ip=ip, qr=mobile.qr_svg(link, box=6))
    return view


def _drop_phones() -> None:
    """Close the live stream of every phone still connected, so turning phone
    access off cuts them off now, not when they next ask for something."""
    with _clients_lock:
        remote = [q for q in _clients if q not in _local_clients]
    for q in remote:
        q.put_nowait(None)


def api_open() -> bool:
    """Another process (GIL, or a notification click) wants the window."""
    return open_window()


_API = {"state": api_state, "older": api_older, "send": api_send,
        "send_voice": api_send_voice, "read": api_read, "view": api_view,
        "master": api_master, "watch": api_watch, "needs_done": api_needs_done,
        "played": api_played, "button": api_button, "ability": api_ability,
        "phone": api_phone, "open": api_open}
#: Only a window on this PC may do these — never a phone, even a paired one.
_LOCAL_ONLY = {"phone", "open"}


# ── the server ────────────────────────────────────────────────────────────────

_MANIFEST = {"name": "Vigil — G.I.L.", "short_name": "Vigil", "start_url": "/",
             "display": "standalone", "background_color": "#0B0D11",
             "theme_color": "#15171D",
             "icons": [{"src": "/gil_icon.png", "sizes": "256x256", "type": "image/png"}]}


class _Server(ThreadingHTTPServer):
    daemon_threads = True            # an open chat's event stream never blocks exit

    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], (ConnectionAbortedError, ConnectionResetError,
                                          BrokenPipeError)):
            return                   # a window closing mid-request is normal, not an error
        log.debug("vigil chat request failed", exc_info=True)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _local(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def _host_ok(self) -> bool:
        """The Host header names this server — not a website's own domain
        pointed at 127.0.0.1 (DNS rebinding), which would otherwise read the
        chat from inside the user's browser."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        if host in ("127.0.0.1", "localhost", "::1"):
            return True
        if _phone()["on"]:
            import mobile
            return host == mobile.lan_ip()
        return False

    def _token_ok(self) -> bool:
        """This PC needs no token; a phone needs phone access on and the token,
        as a header or as ?t= (an <audio> or an event stream can't send headers)."""
        if self._local():
            return True
        phone = _phone()
        if not phone["on"] or not phone["token"]:
            return False
        tok = self.headers.get("X-GIL-Token", "")
        if not tok:
            tok = (parse_qs(urlparse(self.path).query).get("t") or [""])[0]
        return bool(tok) and hmac.compare_digest(tok.encode(), phone["token"].encode())

    def _public_ok(self) -> bool:
        """The page itself and its icons hold nothing private: a phone may load
        them (to show its sign-in error) whenever phone access is on."""
        return self._local() or _phone()["on"]

    def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403)
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html", "/vigil.html", "/vigil.webmanifest") or \
                route.lstrip("/") in _STATIC:
            if not self._public_ok():
                return self._send(403)
        elif not self._token_ok():
            return self._send(403)
        if route == "/vigil.webmanifest":
            manifest = dict(_MANIFEST)
            if not self._local() and self._token_ok():
                # a phone's home-screen copy opens with its key
                manifest["start_url"] = "/?t=" + _phone()["token"]
            return self._send(200, json.dumps(manifest).encode("utf-8"),
                              "application/manifest+json")
        if route in ("/", "/index.html", "/vigil.html"):
            try:
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            except OSError:
                return self._send(500, b"vigil.html is missing")
        if route == "/events":
            return self._events()
        if route.startswith("/voice/"):
            name = route[len("/voice/"):]
            path = VOICE_DIR / name
            if _VOICE_FILE.match(name) and path.is_file():
                return self._send(200, path.read_bytes(),
                                  _AUDIO_TYPES.get(path.suffix, "application/octet-stream"))
            return self._send(404)
        name = route.lstrip("/")
        if name in _STATIC and (WEB / name).is_file():
            return self._send(200, (WEB / name).read_bytes(), _STATIC[name])
        return self._send(404)

    def _events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q: queue.Queue = queue.Queue()
        with _clients_lock:
            _clients.append(q)
            if self._local():
                _local_clients.add(q)
        try:
            while not _stop.is_set():
                try:
                    msg = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                if msg is None:                  # phone access was turned off
                    break
                self.wfile.write(f"data: {msg}\n\n".encode("utf-8"))
                self.wfile.flush()
        except Exception:
            pass
        finally:
            with _clients_lock:
                if q in _clients:
                    _clients.remove(q)
                _local_clients.discard(q)
            # The stream said keep-alive; ending it must really hang up, or a
            # dropped phone would sit on an open socket thinking it's live.
            self.close_connection = True

    def do_POST(self):
        # X-Vigil: a header a web page can't add to a cross-site request
        # without asking first (and this server never says yes) — so no other
        # site open in the user's browser can post to the chat.
        if not (self._host_ok() and self._token_ok() and self.headers.get("X-Vigil") == "1"):
            return self._send(403)
        if self.path.split("?", 1)[0] != "/api":
            return self._send(404)
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            method = req.get("method", "")
            if method in _LOCAL_ONLY and not self._local():
                return self._send(403)
            fn = _API.get(method)
            result = fn(*req.get("args", [])) if fn else None
        except Exception:
            log.error("vigil chat api error", exc_info=True)
            result = None
        self._send(200, json.dumps({"result": result}, ensure_ascii=False).encode("utf-8"),
                   "application/json")


def url() -> str:
    return f"http://127.0.0.1:{_port}/" if _port else ""


def _start_server() -> None:
    global _httpd, _port
    if _httpd is not None:
        return
    bind = "0.0.0.0" if _phone()["on"] else "127.0.0.1"
    for port in PORTS:
        try:
            _httpd = _Server((bind, port), _Handler)
            break
        except OSError:
            continue
    if _httpd is None:
        log.error("vigil chat server failed to start")
        return
    _port = _httpd.server_address[1]
    threading.Thread(target=_httpd.serve_forever, daemon=True, name="GIL-VigilHTTP").start()
    log.info("vigil chat serving on %s:%d", bind, _port)


def _rebind(bind: str) -> None:
    """Listen on the network (phone on) or on this PC only, on the same port.
    Open windows keep their event stream; it's only the listening socket."""
    global _httpd
    old = _httpd
    if old is None or old.server_address[0] == bind:
        return
    old.shutdown()
    old.server_close()
    try:
        _httpd = _Server((bind, _port), _Handler)
    except OSError:
        log.error("vigil chat couldn't listen on %s:%d — back to this PC only",
                  bind, _port, exc_info=True)
        _httpd = _Server(("127.0.0.1", _port), _Handler)
    threading.Thread(target=_httpd.serve_forever, daemon=True, name="GIL-VigilHTTP").start()
    log.info("vigil chat now on %s:%d", _httpd.server_address[0], _port)


def _phone() -> dict:
    saved = vigil.pref("phone") or {}
    return {"on": bool(saved.get("on")), "token": str(saved.get("token") or "")}


def open_window() -> bool:
    """Open the Vigil chat in its own app window — or bring it forward if it's
    already open, rather than stacking a second copy. In a process that isn't
    hosting Vigil, asks the host to."""
    if _role != "host":
        return open_from_outside()
    if not _port:
        return False
    if window_open():
        _push("focus")
        _raise_window()
        return True
    import subprocess
    for exe in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                "msedge", "chrome"):
        try:
            subprocess.Popen(
                [exe, f"--app={url()}", "--window-size=480,860",
                 f"--user-data-dir={PROFILE}", "--no-first-run",
                 "--no-default-browser-check", "--disable-extensions"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return True
        except (FileNotFoundError, OSError):
            continue
    import webbrowser
    return webbrowser.open(url())


_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _find_window() -> int:
    """The open Vigil window, found by its title; 0 if there isn't one."""
    user32 = ctypes.windll.user32
    found: list[int] = []

    def _each(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if WINDOW_TITLE in buf.value:
                    found.append(hwnd)
                    return False
        return True
    try:
        user32.EnumWindows(_WNDENUMPROC(_each), 0)
    except Exception:
        return 0
    return found[0] if found else 0


def _raise_window() -> bool:
    hwnd = _find_window()
    if not hwnd:
        return False
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)                      # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    return True


def _host_port() -> int:
    try:
        return int(PORT_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def open_from_outside() -> bool:
    """For a notification click (vigil_open.py) or GIL while the watcher hosts
    Vigil: bring the window forward, or ask the host to open it."""
    try:
        # A process the user just started by clicking may hand the foreground
        # on — this lets the host's window come to the front, not just flash.
        ctypes.windll.user32.AllowSetForegroundWindow(-1)        # ASFW_ANY
    except Exception:
        pass
    if _raise_window():
        return True
    port = _host_port()
    if not port:
        return False
    try:
        r = requests.post(f"http://127.0.0.1:{port}/api", timeout=8,
                          json={"method": "open", "args": []}, headers={"X-Vigil": "1"})
        return bool(r.ok and r.json().get("result"))
    except (requests.RequestException, ValueError):
        return False


def _name() -> str:
    return vigil.get_user() or _user


def _claim_host() -> bool:
    """Become the one process that runs Vigil, if no other does."""
    global _mutex
    if _mutex:
        return True
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    handle = k32.CreateMutexW(None, False, HOST_MUTEX)
    if not handle:
        return False
    if ctypes.get_last_error() == 183:                   # ERROR_ALREADY_EXISTS
        k32.CloseHandle(wintypes.HANDLE(handle))   # keep no hold, or the host could never leave
        return False
    _mutex = handle
    return True


def _register_protocol() -> None:
    """gil-vigil:open — what a notification click opens: this chat's window,
    brought forward, instead of a browser tab."""
    global _protocol_ok
    try:
        import winreg
        if getattr(sys, "frozen", False):
            cmd = f'"{sys.executable}" --open-vigil "%1"'
        else:
            pyw = Path(sys.executable).with_name("pythonw.exe")
            cmd = f'"{pyw if pyw.is_file() else sys.executable}" "{ROOT / "vigil_open.py"}" "%1"'
        base = rf"Software\Classes\{PROTOCOL}"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:G.I.L. Vigil")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\shell\open\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, cmd)
        _protocol_ok = True
    except Exception:
        log.warning("couldn't register gil-vigil: — notifications open the browser",
                    exc_info=True)


def _greet() -> None:
    hi = f"Hey {_name()} 👋" if _name() else "Hey 👋"
    add_message("system", "Vigil runs on this PC. What it watches and what we say here stay on it.")
    add_message("gil", hi)
    add_message("gil", "This is where I'll text you when something needs you. Quiet stuff "
                       "arrives without a ping, the rest with one — and if you're away from "
                       "the PC, I'll leave you voice messages.")
    add_message("gil", "Tell me what to keep an eye on. Like “let me know if the army emails "
                       "me”, or “watch this page for me” with the link. And “stop” or “go” "
                       "whenever you like.")


def start(username: str = "") -> str:
    """Host the Vigil chat here if no other process does: its server, its
    reply worker, presence, and Vigil's delivery into it. Returns "host", or
    "client" when another process hosts it — then this one only opens its
    window. The window opens only when asked."""
    global _user, _role
    if username:
        _user = username
        vigil.set_user(username)
    if _role == "host":
        return _role
    if not _claim_host():
        _role = "client"
        return _role
    _role = "host"
    _stop.clear()
    _start_server()
    if _port:
        try:
            PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
            PORT_FILE.write_text(str(_port), encoding="utf-8")
        except OSError:
            log.warning("couldn't write the vigil port file", exc_info=True)
    _register_protocol()
    threading.Thread(target=_worker, daemon=True, name="GIL-VigilChat").start()
    threading.Thread(target=_presence_loop, daemon=True, name="GIL-VigilPresence").start()
    vigil.set_deliver(deliver)
    vigil.set_status_callback(lambda text: set_activity("check", text))
    if not messages(limit=1):
        _greet()
    return _role


def stop_hosting() -> None:
    """Hand Vigil over (the watcher does this when GIL starts): stop the
    checks, the chat and its server, and let go of the host lock. Open windows
    reconnect to the new host on their own."""
    global _httpd, _port, _role, _mutex
    vigil.stop()
    _stop.set()
    if _httpd is not None:
        _httpd.shutdown()
        _httpd.server_close()
    _httpd, _port, _role = None, 0, ""
    if _mutex:
        ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(_mutex))
        _mutex = None
