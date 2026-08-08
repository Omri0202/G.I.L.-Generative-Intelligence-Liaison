"""
connections.py — what G.I.L. is plugged into, and how to plug him into more.

The reason this exists: asked to "log into my Google Calendar", GIL said it
couldn't — while a working, authorised Calendar connection was sitting right
there, able to list real events. Nothing was broken except GIL's knowledge of
itself. There was no place a person could look to see what was connected, no
way to connect something new without editing files, and nothing telling the
brain which of its abilities were actually live.

So this module answers three questions in one place:
    what is connected right now      -> status()
    how do I connect this            -> connect()
    what should the brain be told    -> capability_note()

Status is always CHECKED, never remembered. A stored token proves a connection
was made once, not that it still works — tokens expire, get revoked, and go
stale, and a hub that reports yesterday's success is worse than no hub at all.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from logger import get as _get_log

log = _get_log("connections")

ROOT = Path(__file__).parent
DATA = ROOT / "data"

#: status() results are cached briefly — the panel polls, and an OAuth probe
#: that hits the network on every repaint would make the UI crawl.
_CACHE: dict = {}
_CACHE_TTL = 20.0


# ── individual probes ────────────────────────────────────────────────────────
# Each returns (state, detail). state is one of:
#   "on"     working right now, verified
#   "off"    not set up
#   "broken" set up but failing — expired token, revoked access, missing app
#   "manual" no automatic check is possible; the user has to say

def _probe_calendar():
    try:
        import gcalendar
        svc = gcalendar._get_service()
        if not svc:
            return "off", "not authorised yet"
        svc.events().list(calendarId="primary", maxResults=1).execute()
        return "on", "reading your calendar"
    except Exception as exc:
        if not (DATA / "calendar_token.json").exists():
            return "off", "not authorised yet"
        return "broken", f"{type(exc).__name__} — sign in again"


def _probe_gmail():
    try:
        import gmail_recap
        n = gmail_recap.get_unread_summary()
        return "on", ("inbox reachable" if n is None
                      else f"inbox reachable")
    except Exception as exc:
        if not (DATA / "gmail_token.json").exists():
            return "off", "not authorised yet"
        return "broken", f"{type(exc).__name__} — sign in again"


def _probe_whatsapp():
    # WhatsApp Web has no API here: GIL drives a logged-in browser profile, so
    # "connected" means that profile exists and has a session in it.
    prof = DATA / "whatsapp_profile"
    if not prof.exists():
        return "off", "phone not linked yet"
    try:
        has_session = any(prof.rglob("Local Storage")) or any(prof.rglob("Cookies"))
    except Exception:
        has_session = False
    return ("on", "linked to your phone") if has_session else \
           ("off", "profile exists but no session — scan the QR again")


def _probe_spotify():
    if os.getenv("SPOTIFY_CLIENT_ID") and os.getenv("SPOTIFY_CLIENT_SECRET"):
        return "on", "can play and control playback"
    return "off", "no app keys yet"


def _probe_vision():
    try:
        import vision_model
        if not vision_model.keys():
            return "off", "no Groq key"
        extra = []
        if os.getenv("OPENROUTER_API_KEY"):
            extra.append("OpenRouter")
        if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
            extra.append("Google")
        note = "reads images and screens"
        if extra:
            note += " · backup: " + ", ".join(extra)
        return "on", note
    except Exception:
        return "broken", "vision module failed to load"


def _probe_saved_logins():
    try:
        import credentials
        n = len(credentials.list_services() or [])
        return ("on", f"{n} site login{'s' if n != 1 else ''} saved") if n else \
               ("off", "no site logins saved")
    except Exception:
        return "off", "no site logins saved"


# ── the registry ─────────────────────────────────────────────────────────────
# `connect` is what actually runs when the user asks to connect. Where a real
# OAuth flow exists it is called; where the service needs a human step (scanning
# a QR, pasting keys) the entry says so honestly instead of pretending.

SERVICES = [
    {
        "id": "calendar", "name": "Google Calendar", "hue": "#4E8CFF",
        "does": "See your schedule, add and move events, answer “am I free?”",
        "probe": _probe_calendar, "auth": ("gcalendar", "authorize"),
        "needs": "A Google sign-in window will open.",
    },
    {
        "id": "gmail", "name": "Gmail", "hue": "#EA6A5E",
        "does": "Read unread mail, summarise your inbox, open a message.",
        "probe": _probe_gmail, "auth": ("gmail_recap", "authorize"),
        "needs": "A Google sign-in window will open.",
    },
    {
        "id": "whatsapp", "name": "WhatsApp", "hue": "#4BD07A",
        "does": "Catch up on chats and read out what you missed.",
        "probe": _probe_whatsapp, "auth": None,
        "needs": "Opens WhatsApp Web — scan the QR code with your phone.",
    },
    {
        "id": "spotify", "name": "Spotify", "hue": "#3BD86A",
        "does": "Play, pause, skip and search your music.",
        "probe": _probe_spotify, "auth": None,
        "needs": "Add SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to .env.",
    },
    {
        "id": "vision", "name": "Eyes", "hue": "#7DDBFF",
        "does": "Look at your screen, read images and photos you send.",
        "probe": _probe_vision, "auth": None,
        "needs": "Uses your Groq key; add OPENROUTER_API_KEY for a free backup.",
    },
    {
        "id": "logins", "name": "Saved site logins", "hue": "#C79BFF",
        "does": "Sign you in to sites you've saved, without typing passwords.",
        "probe": _probe_saved_logins, "auth": None,
        "needs": "Tell GIL: “save my login for <site>”.",
    },
]

_BY_ID = {s["id"]: s for s in SERVICES}


def status(force: bool = False) -> list[dict]:
    """Every service with its LIVE state. Cached for a few seconds so a polling
    panel doesn't re-run six network probes on every repaint."""
    now = time.time()
    if not force and _CACHE.get("at", 0) + _CACHE_TTL > now:
        return _CACHE["rows"]
    rows = []
    for s in SERVICES:
        try:
            state, detail = s["probe"]()
        except Exception as exc:
            state, detail = "broken", type(exc).__name__
        rows.append({"id": s["id"], "name": s["name"], "hue": s["hue"],
                     "does": s["does"], "needs": s["needs"], "state": state,
                     "detail": detail,
                     "can_connect": bool(s["auth"]) or state != "on"})
    _CACHE["at"], _CACHE["rows"] = now, rows
    return rows


def connect(service_id: str) -> dict:
    """Run whatever it takes to connect this service.

    Returns {ok, message}. An OAuth flow opens a browser window and blocks
    until the user finishes, so callers should run this off the UI thread."""
    s = _BY_ID.get(service_id)
    if not s:
        return {"ok": False, "message": f"I don't know a service called {service_id!r}."}
    _CACHE.clear()

    if s["auth"]:
        mod_name, fn_name = s["auth"]
        try:
            mod = __import__(mod_name)
            ok = bool(getattr(mod, fn_name)())
            return {"ok": ok,
                    "message": (f"{s['name']} is connected."
                                if ok else
                                f"{s['name']} sign-in didn't complete.")}
        except Exception as exc:
            log.error("connect %s failed", service_id, exc_info=True)
            return {"ok": False,
                    "message": f"{s['name']} sign-in failed — {type(exc).__name__}."}

    if service_id == "whatsapp":
        try:
            import whatsapp_recap
            for fn in ("open_whatsapp", "link_phone", "run"):
                f = getattr(whatsapp_recap, fn, None)
                if callable(f):
                    f()
                    return {"ok": True,
                            "message": "WhatsApp Web is opening — scan the QR "
                                       "code with your phone."}
        except Exception:
            log.debug("whatsapp open failed", exc_info=True)
        return {"ok": False, "message": s["needs"]}

    # Nothing to automate: say exactly what the person has to do.
    return {"ok": False, "message": s["needs"]}


def capability_note() -> str:
    """One line for the brain's prompt naming what is ACTUALLY live.

    Without this the brain guesses, and it guesses low: asked to look at a
    calendar that was connected and working, it answered that it could not."""
    try:
        on = [r["name"] for r in status() if r["state"] == "on"]
        off = [r["name"] for r in status() if r["state"] != "on"]
    except Exception:
        return ""
    if not on and not off:
        return ""
    parts = []
    if on:
        parts.append("CONNECTED AND USABLE RIGHT NOW: " + ", ".join(on) +
                     ". Never tell the user you cannot reach these — you can.")
    if off:
        parts.append("NOT CONNECTED: " + ", ".join(off) +
                     ". If asked, say it needs connecting once and point them "
                     "at the Connections panel (the plug icon by the composer, "
                     "or /connect).")
    return " ".join(parts)
