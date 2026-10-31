"""
phone_push.py — G.I.L.'s notifications on your phone (Web Push).

The G.I.L. app on your phone subscribes once. After that GIL sends each
notification through the phone's own push service (Google's, on Android),
encrypted for that phone alone: the push service carries it but can't read
it. It arrives with the app closed and the phone asleep, and doesn't need
Tailscale to be connected — only opening the app does.

The VAPID key pair that signs GIL's pushes is made on first use and kept in
data/ — never committed, never bundled into a build.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from logger import get as _get_log

log = _get_log("phone.push")

ROOT = Path(__file__).parent
KEY_FILE = ROOT / "data" / "vigil_vapid.pem"
SUBS_FILE = ROOT / "data" / "vigil_push.json"
#: The contact push services ask senders for. py_vapid takes only a mailto:
#: or a bare https host; a no-reply address keeps the user's own out of it.
CONTACT = "mailto:gil-vigil@users.noreply.github.com"
#: Only real push services: a subscription is a URL GIL will POST to, so a
#: bad one must not be able to aim it at anything else.
PUSH_HOSTS = (".googleapis.com", ".mozilla.com", ".push.apple.com", ".notify.windows.com",
              ".mozaws.net")
MAX_SUBS = 5
BODY_CHARS = 280

_lock = threading.Lock()


def _vapid():
    from py_vapid import Vapid
    with _lock:
        if KEY_FILE.exists():
            return Vapid.from_file(str(KEY_FILE))
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        v = Vapid()
        v.generate_keys()
        v.save_key(str(KEY_FILE))
        log.info("made a new push key pair")
        return v


def public_key() -> str:
    """The key a phone subscribes with (applicationServerKey)."""
    from cryptography.hazmat.primitives import serialization
    raw = _vapid().public_key.public_bytes(serialization.Encoding.X962,
                                           serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _load() -> list[dict]:
    try:
        subs = json.loads(SUBS_FILE.read_text(encoding="utf-8"))
        return subs if isinstance(subs, list) else []
    except (OSError, ValueError):
        return []


def _save(subs: list[dict]) -> None:
    SUBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SUBS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(subs, indent=1), encoding="utf-8")
    tmp.replace(SUBS_FILE)


def valid(sub: dict) -> bool:
    try:
        url = urlparse(sub["endpoint"])
        keys = sub["keys"]
        return (url.scheme == "https" and any((url.hostname or "").endswith(h) for h in PUSH_HOSTS)
                and bool(keys.get("p256dh")) and bool(keys.get("auth")))
    except (KeyError, TypeError, AttributeError):
        return False


def subscribe(sub: dict, label: str = "") -> bool:
    if not valid(sub):
        return False
    entry = {"endpoint": sub["endpoint"],
             "keys": {"p256dh": sub["keys"]["p256dh"], "auth": sub["keys"]["auth"]},
             "label": str(label or "phone")[:60], "added": round(time.time())}
    with _lock:
        subs = [s for s in _load() if s["endpoint"] != entry["endpoint"]]
        _save((subs + [entry])[-MAX_SUBS:])
    return True


def unsubscribe(endpoint: str = "") -> None:
    """Forget one phone, or every phone when endpoint is empty."""
    with _lock:
        _save([s for s in _load() if endpoint and s["endpoint"] != endpoint])


def count() -> int:
    return len(_load())


def subscribed(endpoint: str) -> bool:
    return any(s["endpoint"] == endpoint for s in _load())


def send(payload: dict, urgency: str = "normal", ttl: int = 6 * 3600) -> int:
    """Push to every subscribed phone; returns how many took it. A phone that
    unsubscribed or uninstalled (404/410) is forgotten."""
    from pywebpush import WebPushException, webpush
    payload = dict(payload)
    payload["body"] = str(payload.get("body") or "")[:BODY_CHARS]
    data = json.dumps(payload, ensure_ascii=False)
    vapid = _vapid()
    delivered, gone = 0, []
    for sub in _load():
        try:
            webpush(subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]},
                    data=data, vapid_private_key=vapid, vapid_claims={"sub": CONTACT},
                    ttl=ttl, headers={"Urgency": urgency}, timeout=10)
            delivered += 1
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                gone.append(sub["endpoint"])
            else:
                log.warning("push to %s failed: %s", sub.get("label"), status or exc)
        except Exception:
            log.warning("push to %s failed", sub.get("label"), exc_info=True)
    if gone:
        with _lock:
            _save([s for s in _load() if s["endpoint"] not in gone])
    return delivered
