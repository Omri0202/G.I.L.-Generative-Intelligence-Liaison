"""
gcalendar.py — Project G.I.L.
Google Calendar integration: read events and create new ones.

Reuses gmail_credentials.json — run setup once:
    python gcalendar.py --setup
"""

import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

_DATA       = Path(__file__).parent / "data"
_TOKEN_FILE = _DATA / "calendar_token.json"
_CREDS_FILE = _DATA / "gmail_credentials.json"   # same OAuth app as Gmail

_SCOPES = ["https://www.googleapis.com/auth/calendar"]


# ── Auth ──────────────────────────────────────────────────────────────────────

def _get_service():
    """Return authorized Calendar service, or None if not set up."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        return None

    if not _CREDS_FILE.exists():
        return None

    creds = None
    if _TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), _SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                _TOKEN_FILE.write_text(creds.to_json())
            except Exception:
                return None
        else:
            return None

    try:
        return build("calendar", "v3", credentials=creds, cache_discovery=False)
    except Exception:
        return None


def authorize() -> bool:
    """Run OAuth flow. Call once: python gcalendar.py --setup"""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("[G.I.L. CALENDAR] Install: pip install google-auth-oauthlib google-api-python-client")
        return False

    if not _CREDS_FILE.exists():
        print(f"[G.I.L. CALENDAR] Missing {_CREDS_FILE}")
        print("  Download OAuth credentials from Google Cloud Console.")
        return False

    _DATA.mkdir(parents=True, exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(_CREDS_FILE), _SCOPES)
    print("\n[G.I.L. CALENDAR] Opening browser for Google Calendar authorization...")
    try:
        creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=120)
    except Exception:
        auth_url, _ = flow.authorization_url(prompt="consent")
        print(f"\nOpen this URL:\n  {auth_url}\n")
        code = input("Paste the authorization code: ").strip()
        flow.fetch_token(code=code)
        creds = flow.credentials

    _TOKEN_FILE.write_text(creds.to_json())
    print("[G.I.L. CALENDAR] Authorization complete.")
    return True


# ── Read events ───────────────────────────────────────────────────────────────

def _fmt_event(event: dict) -> dict:
    """Normalize a Calendar API event dict."""
    start = event.get("start", {})
    end   = event.get("end", {})
    # All-day events use "date"; timed events use "dateTime"
    start_str = start.get("dateTime") or start.get("date", "")
    end_str   = end.get("dateTime")   or end.get("date", "")

    try:
        if "T" in start_str:
            dt = datetime.fromisoformat(start_str)
            time_str = dt.strftime("%I:%M %p").lstrip("0")
        else:
            time_str = "All day"
    except Exception:
        time_str = ""

    return {
        "id":       event.get("id", ""),
        "title":    event.get("summary", "(no title)"),
        "time":     time_str,
        "location": event.get("location", ""),
        "start":    start_str,
        "end":      end_str,
    }


def get_todays_events() -> list[dict]:
    """Return today's calendar events, sorted by start time."""
    service = _get_service()
    if not service:
        return []
    try:
        now   = datetime.now(timezone.utc)
        start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc).isoformat()
        end   = (datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + timedelta(days=1)).isoformat()
        result = service.events().list(
            calendarId="primary",
            timeMin=start,
            timeMax=end,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        return [_fmt_event(e) for e in result.get("items", [])]
    except Exception as exc:
        print(f"[G.I.L. CALENDAR] Fetch error: {exc}")
        return []


def get_upcoming_events(days: int = 7) -> list[dict]:
    """Return upcoming events for the next N days."""
    service = _get_service()
    if not service:
        return []
    try:
        now    = datetime.now(timezone.utc)
        end    = (now + timedelta(days=days)).isoformat()
        result = service.events().list(
            calendarId="primary",
            timeMin=now.isoformat(),
            timeMax=end,
            singleEvents=True,
            orderBy="startTime",
            maxResults=10,
        ).execute()
        return [_fmt_event(e) for e in result.get("items", [])]
    except Exception as exc:
        print(f"[G.I.L. CALENDAR] Upcoming fetch error: {exc}")
        return []


# ── Add event ─────────────────────────────────────────────────────────────────

def add_event(title: str, start_iso: str, duration_minutes: int = 60,
              description: str = "") -> str:
    """
    Add an event to the primary calendar.
    start_iso: ISO 8601 datetime string, e.g. "2026-05-01T15:00:00"
    """
    service = _get_service()
    if not service:
        return "Calendar not connected — run: python gcalendar.py --setup"
    try:
        start_dt = datetime.fromisoformat(start_iso)
        end_dt   = start_dt + timedelta(minutes=duration_minutes)
        event    = {
            "summary":     title,
            "description": description,
            "start":       {"dateTime": start_dt.isoformat(), "timeZone": _get_tz()},
            "end":         {"dateTime": end_dt.isoformat(),   "timeZone": _get_tz()},
        }
        created = service.events().insert(calendarId="primary", body=event).execute()
        return f"Done — '{title}' added at {start_dt.strftime('%I:%M %p')} on {start_dt.strftime('%b %d')}."
    except Exception as exc:
        err = str(exc)
        if "403" in err or "accessNotConfigured" in err or "has not been used" in err:
            return "Google Calendar API isn't enabled in your Google Cloud project. Enable it at console.developers.google.com, then retry."
        if "401" in err or "invalid_grant" in err or "Token" in err:
            return "Calendar auth expired. Run: python gcalendar.py --setup to reconnect."
        if "404" in err:
            return "Primary calendar not found. Make sure you're connected to the right Google account."
        return f"Couldn't add the event — {exc.__class__.__name__}. Check your calendar setup."


def _get_tz() -> str:
    try:
        from location import get_location
        return get_location().get("timezone", "UTC")
    except Exception:
        return "UTC"


# ── Speech builders ───────────────────────────────────────────────────────────

def build_today_speech(events: list[dict]) -> str:
    if not events:
        return "Your calendar is clear today."
    if len(events) == 1:
        e = events[0]
        t = f" at {e['time']}" if e["time"] != "All day" else " — all day"
        return f"You have one event today: {e['title']}{t}."
    lines = []
    for e in events[:4]:
        t = f" at {e['time']}" if e["time"] != "All day" else ""
        lines.append(f"{e['title']}{t}")
    return f"You have {len(events)} events today: {', '.join(lines)}."


def build_upcoming_speech(events: list[dict]) -> str:
    if not events:
        return "Nothing coming up in the next week."
    lines = []
    for e in events[:4]:
        try:
            dt   = datetime.fromisoformat(e["start"])
            day  = dt.strftime("%A")
            time = f" at {e['time']}" if e["time"] != "All day" else ""
            lines.append(f"{e['title']} on {day}{time}")
        except Exception:
            lines.append(e["title"])
    return f"Coming up: {', '.join(lines)}."


def build_calendar_context() -> str:
    """Short calendar context for the brain system prompt."""
    try:
        events = get_todays_events()
        if not events:
            return "CALENDAR: Clear today."
        titles = ", ".join(f"{e['title']} ({e['time']})" for e in events[:3])
        return f"CALENDAR TODAY: {titles}"
    except Exception:
        return ""


# ── Parse natural language event target ───────────────────────────────────────

def parse_event_target(target: str) -> tuple[str, str, int]:
    """
    Parse brain target like "Dentist | 2026-05-01 15:00 | 60"
    Returns (title, iso_datetime, duration_minutes).
    """
    parts = [p.strip() for p in target.split("|")]
    title    = parts[0] if len(parts) > 0 else "Event"
    dt_str   = parts[1] if len(parts) > 1 else ""
    duration = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 60

    # Try to parse the datetime
    if dt_str:
        try:
            # Accept "2026-05-01 15:00" or full ISO
            dt_str = dt_str.replace(" ", "T", 1) if "T" not in dt_str else dt_str
            datetime.fromisoformat(dt_str)   # validate
            return title, dt_str, duration
        except Exception:
            pass

    # Fallback: tomorrow at noon
    tomorrow = datetime.now() + timedelta(days=1)
    fallback = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 12, 0)
    return title, fallback.isoformat(), duration


# ── CLI setup ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if "--setup" in sys.argv:
        ok = authorize()
        if ok:
            events = get_todays_events()
            print(f"\nTest: {len(events)} event(s) today.")
            for e in events:
                print(f"  {e['time']:>10}  {e['title']}")
    else:
        print("Usage: python gcalendar.py --setup")
