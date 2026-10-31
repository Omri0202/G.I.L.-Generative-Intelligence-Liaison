"""
gmail_reader.py — G.I.L. reading your Gmail: new mail, mail from any time
back, and threads still waiting on your reply.

Read-only, always: GIL is connected with gmail.readonly, so nothing here can
send, delete, archive or even mark an email read. Vigil uses it three ways —
new mail for watches, searching years back when you ask about old emails
("what did the army send me last spring?", "sum up this week's mail"), and
finding emails from people that you haven't answered.

Unlike gmail_recap's announcer, every failure here is loud: a dead Google
login raises MailboxError instead of looking like an empty inbox. That
silence is what let a watch on the inbox see "nothing new" for months.

What comes back is other people's text, so callers treat it as untrusted:
it goes to a model that can only answer in words, never act.
"""

from __future__ import annotations

import base64
import re
import threading
import time
from email.utils import parseaddr

import gmail_recap
from logger import get as _get_log

log = _get_log("gmail.reader")

NOT_CONNECTED = "Gmail isn't connected"
BODY_CHARS = 1_500
BATCH = 25                      # Gmail allows 100 per batch; fewer avoids its burst limit
_NO_REPLY = re.compile(r"no-?reply|do-?not-?reply|notifications?@|mailer-daemon|postmaster|"
                       r"newsletter|updates?@|alerts?@|info@|news@", re.I)
_QUOTE_START = re.compile(
    r"^\s*(On .{4,200} wrote:|-{2,} ?Original Message ?-{2,}|-{5,} ?Forwarded message|"
    r"From: .+ Sent: |\W*בתאריך .{4,200}:\W*$|\W*ב-?.{4,120} מאת .{2,200}:\W*$)",
    re.M | re.I)
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤﻿­]")

_me: dict = {}
_reconnecting = threading.Lock()


class MailboxError(Exception):
    """Gmail can't be read right now, with a reason a person can act on."""


def _service():
    service = gmail_recap._get_service()
    if service is None:
        raise MailboxError(NOT_CONNECTED)
    return service


def connected() -> bool:
    return gmail_recap._get_service() is not None


def link(thread_id: str) -> str:
    """The thread in Gmail on the web — #all finds it archived or not."""
    return f"https://mail.google.com/mail/u/0/#all/{thread_id}"


# ── reading one message ───────────────────────────────────────────────────────

def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _html_text(html: str) -> str:
    html = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", html)
    try:
        import lxml.html
        doc = lxml.html.fromstring(html)
        for bad in doc.xpath("//script|//style|//head|//title"):
            bad.drop_tree()
        return doc.text_content()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)


def body_text(payload: dict) -> str:
    """The readable text of a message: its plain-text part, or its HTML made
    plain, without quoted replies and forwarded history below it."""
    plain, html = [], []

    def walk(part: dict) -> None:
        kind = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if data and kind == "text/plain":
            plain.append(_b64(data))
        elif data and kind == "text/html":
            html.append(_b64(data))
        for child in part.get("parts") or []:
            walk(child)
    walk(payload or {})
    text = "\n".join(plain) if plain else _html_text("\n".join(html))
    return clean(text)


def clean(text: str) -> str:
    text = _INVISIBLE.sub("", text or "").replace("\r", "")
    cut = _QUOTE_START.search(text)
    if cut and cut.start() > 0:
        text = text[:cut.start()]
    lines = [ln.strip() for ln in text.split("\n") if not ln.lstrip().startswith(">")]
    text = "\n".join(ln for ln in lines)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _headers(msg: dict) -> dict:
    return {h["name"].lower(): h["value"] for h in (msg.get("payload") or {}).get("headers", [])}


def _person(value: str) -> str:
    name, addr = parseaddr(value or "")
    return (name or addr or "someone").strip().strip('"')[:60]


def parse(msg: dict, body_chars: int = BODY_CHARS) -> dict:
    h = _headers(msg)
    body = body_text(msg.get("payload") or {})          # "" for metadata-only fetches
    if len(body) > body_chars:
        body = body[:body_chars].rstrip() + "…"
    return {"id": msg.get("id", ""), "thread": msg.get("threadId", ""),
            "at": int(msg.get("internalDate", 0)) / 1000,
            "from": _person(h.get("from", "")), "from_addr": parseaddr(h.get("from", ""))[1].lower(),
            "to": _person(h.get("to", "")), "subject": (h.get("subject") or "(no subject)")[:160],
            "snippet": _INVISIBLE.sub("", msg.get("snippet", ""))[:200],
            "body": body or _INVISIBLE.sub("", msg.get("snippet", "")),
            "link": link(msg.get("threadId", ""))}


def _get_many(service, ids: list[str], fmt: str = "full", resource: str = "messages") -> list[dict]:
    """Fetch many messages (or threads) in a few batched requests, in order."""
    got: dict[str, dict] = {}

    def keep(request_id, response, exception):
        if exception is None and response:
            got[request_id] = response
        else:
            log.debug("gmail get %s failed: %s", request_id, exception)
    for i in range(0, len(ids), BATCH):
        batch = service.new_batch_http_request(callback=keep)
        for item_id in ids[i:i + BATCH]:
            api = service.users().messages() if resource == "messages" else service.users().threads()
            kwargs = {"userId": "me", "id": item_id, "format": fmt}
            if fmt == "metadata":
                kwargs["metadataHeaders"] = ["From", "To", "Subject", "Date"]
            batch.add(api.get(**kwargs), request_id=item_id)
        _run(batch.execute)
    return [got[i] for i in ids if i in got]


def _run(call):
    """A Gmail API call, with its failures turned into MailboxError."""
    try:
        return call()
    except MailboxError:
        raise
    except Exception as exc:
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status in (401, 403):
            raise MailboxError(NOT_CONNECTED) from exc
        log.warning("gmail call failed", exc_info=True)
        raise MailboxError(f"Gmail didn't answer ({status or type(exc).__name__})") from exc


# ── what callers use ──────────────────────────────────────────────────────────

def unread(max_results: int = 8) -> list[dict]:
    """The newest unread emails in the inbox (no bodies — a watch needs the gist)."""
    service = _service()
    resp = _run(service.users().messages().list(
        userId="me", labelIds=["INBOX", "UNREAD"], maxResults=max_results).execute)
    ids = [m["id"] for m in resp.get("messages", [])]
    return [parse(m) for m in _get_many(service, ids, "metadata")]


def search(query: str, limit: int = 40, body_chars: int = BODY_CHARS) -> tuple[list[dict], int]:
    """Emails matching a Gmail search, newest first — any date, read or not,
    archived or not. Returns (emails, about how many matched in all)."""
    service = _service()
    ids, page, estimate = [], None, 0
    while len(ids) < limit:
        resp = _run(service.users().messages().list(
            userId="me", q=query, maxResults=min(100, limit - len(ids)),
            pageToken=page).execute)
        ids += [m["id"] for m in resp.get("messages", [])]
        estimate = max(estimate, int(resp.get("resultSizeEstimate") or 0))
        page = resp.get("nextPageToken")
        if not page:
            break
    mails = [parse(m, body_chars) for m in _get_many(service, ids[:limit], "full")]
    total = max(len(ids), estimate) if page else len(ids)
    return mails, total


def my_address() -> str:
    if not _me.get("addr"):
        _me["addr"] = (_run(_service().users().getProfile(userId="me").execute)
                       .get("emailAddress") or "").lower()
    return _me["addr"]


def unanswered(days: int = 14, min_hours: int = 24, limit: int = 30) -> list[dict]:
    """Emails from people that are still waiting on the user: inbox threads
    from the last `days` whose newest message isn't theirs, left for at least
    `min_hours`. Newsletters, notifications and no-reply senders don't count."""
    service = _service()
    me = my_address()
    q = (f"in:inbox newer_than:{days}d -category:promotions -category:social "
         f"-category:updates -category:forums")
    resp = _run(service.users().threads().list(userId="me", q=q, maxResults=limit).execute)
    ids = [t["id"] for t in resp.get("threads", [])]
    waiting, now = [], time.time()
    for thread in _get_many(service, ids, "metadata", resource="threads"):
        msgs = thread.get("messages") or []
        if not msgs:
            continue
        last = parse(msgs[-1])
        if "SENT" in (msgs[-1].get("labelIds") or []) or last["from_addr"] == me:
            continue                                      # they answered last
        if not last["from_addr"] or _NO_REPLY.search(last["from_addr"]):
            continue
        age = now - last["at"]
        if age < min_hours * 3600:
            continue
        waiting.append({**last, "days": int(age // 86400), "replies": len(msgs)})
    return waiting


def reconnect(timeout: int = 180) -> tuple[bool, str]:
    """Open Google's sign-in in the browser so the user can let GIL read their
    Gmail again. The user signs in and agrees there; GIL never sees a password."""
    if not _reconnecting.acquire(blocking=False):
        return False, "I'm already waiting for you to sign in — check your browser"
    try:
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError:
            return False, "the Google sign-in library isn't installed"
        if not gmail_recap._CREDS_FILE.exists():
            return False, "gmail_credentials.json is missing from the data folder"
        flow = InstalledAppFlow.from_client_secrets_file(str(gmail_recap._CREDS_FILE),
                                                         gmail_recap._SCOPES)
        try:
            creds = flow.run_local_server(
                port=0, open_browser=True, timeout_seconds=timeout,
                authorization_prompt_message="",
                success_message="G.I.L. can read your Gmail again. You can close this tab.")
        except Exception:
            log.warning("gmail reconnect didn't finish", exc_info=True)
            return False, "the sign-in didn't finish"
        if not creds or not getattr(creds, "valid", False):
            return False, "the sign-in didn't finish"
        gmail_recap._TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        _me.clear()
        return True, ""
    finally:
        _reconnecting.release()
