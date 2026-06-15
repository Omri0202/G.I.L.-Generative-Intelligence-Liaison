"""
gmail_recap.py — Project G.I.L.
Proactive Gmail unread recap — checks for unread emails and offers to read/open them.

Setup (one-time):
  pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client
  Run `python gmail_recap.py --setup` once to authorize GIL to read your Gmail.
  A credentials.json from Google Cloud Console must be present in data/.
"""

import json
import os
import threading
import time
from pathlib import Path

_DATA       = Path(__file__).parent / "data"
_TOKEN_FILE = _DATA / "gmail_token.json"
_CREDS_FILE = _DATA / "gmail_credentials.json"

_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

_last_check:   float = 0.0
_check_lock            = threading.Lock()
_show_callback         = None   # set by main.py


def set_show_callback(fn) -> None:
    global _show_callback
    _show_callback = fn


# ── Auth ──────────────────────────────────────────────────────────────────────

def _get_service():
    """Return an authorized Gmail API service object, or None if not set up."""
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
            except Exception:
                return None
        else:
            return None

    try:
        return build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception:
        return None


def authorize() -> bool:
    """
    Run OAuth flow to authorize GIL. Call once from a terminal:
        python gmail_recap.py --setup
    Returns True if successful.
    """
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("[G.I.L. GMAIL] Install: pip install google-auth-oauthlib google-api-python-client")
        return False

    if not _CREDS_FILE.exists():
        print(f"[G.I.L. GMAIL] Missing {_CREDS_FILE}")
        print("  Download OAuth credentials from Google Cloud Console → APIs & Services → Credentials.")
        return False

    _DATA.mkdir(parents=True, exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(_CREDS_FILE), _SCOPES)

    print("\n[G.I.L. GMAIL] Opening browser for Google authorization...")
    print("  If the browser shows an error, copy the full URL from the address bar and paste it here.\n")

    try:
        creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=120)
    except Exception:
        # Fallback: manual copy-paste flow
        print("[G.I.L. GMAIL] Local server failed — switching to manual flow.")
        auth_url, _ = flow.authorization_url(prompt="consent")
        print(f"\nOpen this URL in your browser:\n  {auth_url}\n")
        code = input("Paste the authorization code here: ").strip()
        flow.fetch_token(code=code)
        creds = flow.credentials

    _TOKEN_FILE.write_text(creds.to_json())
    print("\n[G.I.L. GMAIL] Authorization complete. Token saved — GIL can now read your inbox.")
    return True


# ── Unread fetch ──────────────────────────────────────────────────────────────

def get_unread_summary(max_results: int = 5) -> list[dict]:
    """
    Return a list of dicts with keys: id, sender, subject, snippet.
    Returns [] if not authorized or no unread messages.
    """
    service = _get_service()
    if not service:
        return []

    try:
        resp = service.users().messages().list(
            userId="me",
            labelIds=["INBOX", "UNREAD"],
            maxResults=max_results,
        ).execute()

        messages = resp.get("messages", [])
        results  = []
        for msg in messages:
            detail = service.users().messages().get(
                userId="me", id=msg["id"], format="metadata",
                metadataHeaders=["From", "Subject"],
            ).execute()

            headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
            sender  = headers.get("From", "Unknown")
            # Strip email address from "Name <email>" format
            if "<" in sender:
                sender = sender[:sender.index("<")].strip().strip('"')

            results.append({
                "id":      msg["id"],
                "sender":  sender[:30],
                "subject": headers.get("Subject", "(no subject)")[:60],
                "snippet": detail.get("snippet", "")[:100],
            })
        return results
    except Exception as exc:
        print(f"[G.I.L. GMAIL] Fetch error: {exc}")
        return []


def open_email(message_id: str) -> None:
    """Open the specific email in Gmail in the browser."""
    import webbrowser
    webbrowser.open(f"https://mail.google.com/mail/u/0/#inbox/{message_id}")


def open_gmail_inbox() -> None:
    import webbrowser
    webbrowser.open("https://mail.google.com")


# ── Proactive recap ──────────────────────────────────────────────────────────

def build_recap_speech(emails: list[dict]) -> str:
    """Build a short spoken summary GIL can read aloud."""
    if not emails:
        return ""
    count = len(emails)
    if count == 1:
        e = emails[0]
        return f"You have one unread email from {e['sender']} — {e['subject']}. Want me to read it to you?"
    senders = ", ".join(set(e["sender"] for e in emails[:3]))
    return (
        f"You have {count} unread emails — from {senders}"
        + ("..." if count > 3 else ".")
        + " Want me to read them to you?"
    )


def check_and_announce(force: bool = False) -> None:
    """
    Check for unread Gmail and push a toast if there are any.
    Called on startup and periodically. Respects a 30-minute cooldown.
    """
    global _last_check
    if not force and time.time() - _last_check < 1800:
        return

    with _check_lock:
        if not force and time.time() - _last_check < 1800:
            return
        _last_check = time.time()

    def _run():
        emails = get_unread_summary(max_results=5)
        if not emails:
            return
        speech = build_recap_speech(emails)
        if speech and _show_callback:
            try:
                _show_callback(speech, emails)
            except Exception as exc:
                print(f"[G.I.L. GMAIL] Callback error: {exc}")
        print(f"[G.I.L. GMAIL] {len(emails)} unread emails found.")

    threading.Thread(target=_run, daemon=True, name="GIL-GmailRecap").start()


def start_periodic_check(interval_secs: int = 1800) -> None:
    """Run check_and_announce every interval_secs in the background."""
    def _loop():
        time.sleep(30)   # brief startup delay
        while True:
            check_and_announce()
            time.sleep(interval_secs)
    threading.Thread(target=_loop, daemon=True, name="GIL-GmailLoop").start()
    print("[G.I.L. GMAIL] Periodic check active (every 30 min).")


# ── CLI setup ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if "--setup" in sys.argv:
        ok = authorize()
        if ok:
            emails = get_unread_summary()
            if emails:
                print(f"\nTest fetch — {len(emails)} unread emails:")
                for e in emails:
                    print(f"  From: {e['sender']}  |  {e['subject']}")
            else:
                print("Authorization OK — no unread emails found.")
    else:
        print("Usage: python gmail_recap.py --setup")
