"""
vigil_link.py — this PC's link to Vigil in the cloud.

When Vigil runs on its own server (vigil_cloud.py) the chat, the watches and
the checks all live there, so they carry on with the PC off. The PC is where
G.I.L. himself is, though, so while it's on this link keeps one stream open to
the server and does what only the PC can:

  - runs the jobs the user texted for G.I.L. (and sends his replies back),
  - opens the shop for "order it for me",
  - shows the desktop notification, and opens the chat window,
  - reconnects Gmail (Google's sign-in opens in a browser here),

and every few seconds tells the server how long since the user last touched
the keyboard — how Vigil knows to leave a voice message instead.

data/vigil_cloud.json says where the server is and holds the PC's key; it's
written by vigil_cloud_setup.py and never committed.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from logger import get as _get_log

log = _get_log("vigil.link")

ROOT = Path(__file__).parent
CONFIG = ROOT / "data" / "vigil_cloud.json"
_REAL_CONFIG = CONFIG
PRESENCE_SECS = 20
RETRY_SECS = (2, 5, 10, 30, 60)
READ_TIMEOUT = 45            # the server pings every 15 s: this much silence is a dead line

_stop = threading.Event()
_nudge = threading.Event()
_outbox: queue.Queue = queue.Queue()
_threads: list[threading.Thread] = []
_state = {"unread": 0, "connected": False, "resp": None}


def config() -> dict:
    if os.environ.get("PYTEST_CURRENT_TEST") and CONFIG == _REAL_CONFIG:
        return {}                # a test run must never link to (or act for) the real server
    try:
        c = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return c if isinstance(c, dict) and c.get("url") and c.get("key") else {}


def configured() -> bool:
    c = config()
    return bool(c) and c.get("enabled", True) is not False


def unread() -> int:
    return _state["unread"]


def connected() -> bool:
    return _state["connected"]


def _base() -> str:
    return config()["url"].rstrip("/")


def _headers() -> dict:
    return {"X-Vigil": "1", "X-GIL-Token": config()["key"]}


def call(method: str, **kwargs):
    """Ask the server something and wait for its answer; None when it can't
    be reached."""
    if not configured():
        return None
    try:
        r = requests.post(f"{_base()}/agent", json={"method": method, "kwargs": kwargs},
                          headers=_headers(), timeout=20)
        return r.json().get("result") if r.ok else None
    except (requests.RequestException, ValueError):
        return None


def tell(method: str, **kwargs) -> None:
    """Tell the server something without waiting. Everything told goes in the
    order it was said — so G.I.L.'s last reply always lands before "done"."""
    _outbox.put((method, kwargs))


def _sender() -> None:
    while not _stop.is_set():
        try:
            method, kwargs = _outbox.get(timeout=1)
        except queue.Empty:
            continue
        for wait in (0, 2, 5):                  # a blip on the line shouldn't lose a reply
            if _stop.wait(wait):
                return
            if call(method, **kwargs) is not None:
                break
        else:
            log.warning("couldn't tell Vigil's server %s", method)


def nudge() -> None:
    """Say how things are now rather than at the next beat (G.I.L. just opened)."""
    _nudge.set()


def _presence() -> None:
    import vigil_chat
    res = call("presence", idle=vigil_chat.idle_seconds(), gil=vigil_chat.gil_running())
    if isinstance(res, dict):
        _state["unread"] = int(res.get("unread") or 0)


def _presence_loop() -> None:
    while not _stop.is_set():
        try:
            _presence()
        except Exception:
            log.debug("presence failed", exc_info=True)
        _nudge.wait(PRESENCE_SECS)
        _nudge.clear()


# ── what the server asks of the PC ────────────────────────────────────────────

def _job(text: str = "", **_):
    import vigil_chat
    send = vigil_chat._gil["send"]
    if send is None:
        return None
    send(text)                              # returns once he's answered, or handed it on
    return True


def _busy(**_) -> bool:
    import vigil_chat
    busy = vigil_chat._gil["busy"]
    return bool(busy and busy())


def _checkout(offer: dict | None = None, details: dict | None = None, need: str = "", **_):
    import checkout
    return checkout.run(offer or {}, details or {}, need=need,
                        progress=lambda text: tell("activity", text=text))


def _reconnect_gmail(**_) -> dict:
    import gmail_reader
    import gmail_recap
    ok, why = gmail_reader.reconnect()
    if not ok:
        return {"ok": False, "why": why}
    try:
        return {"ok": True, "token": gmail_recap._TOKEN_FILE.read_text(encoding="utf-8")}
    except OSError:
        return {"ok": False, "why": "the new login wasn't saved"}


_CALLS = {"send": _job, "busy": _busy, "checkout": _checkout, "reconnect_gmail": _reconnect_gmail}


def _answer(ev: dict) -> None:
    fn = _CALLS.get(ev.get("kind", ""))
    try:
        result = fn(**{k: v for k, v in ev.items() if k not in ("kind", "id")}) if fn else None
    except Exception:
        log.error("vigil link: %s failed", ev.get("kind"), exc_info=True)
        result = None
    tell("result", id=ev["id"], result=result)


def _handle(ev: dict) -> None:
    import vigil_chat
    kind = ev.get("kind", "")
    if ev.get("id"):                        # a question: answer it, however long it takes
        threading.Thread(target=_answer, args=(ev,), daemon=True,
                         name=f"GIL-Link-{kind}").start()
    elif kind == "unread":
        _state["unread"] = int(ev.get("n") or 0)
    elif kind == "toast":
        try:
            import notify
            notify.toast("G.I.L.", str(ev.get("body") or ""), ev.get("urgency") or "normal",
                         launch=vigil_chat._launch_url())
        except Exception:
            log.warning("vigil link toast failed", exc_info=True)
    elif kind == "open":
        open_window()
    elif kind == "open_url":
        address = str(ev.get("url") or "")
        if urlparse(address).scheme in ("http", "https"):    # a page, never a program
            import webbrowser
            webbrowser.open(address)
    elif kind == "show":
        show = vigil_chat._gil["show"]
        if show:
            show(str(ev.get("text") or ""))


def _listen() -> None:
    """One stream from the server, until it drops."""
    with requests.get(f"{_base()}/agent/events", headers=_headers(), stream=True,
                      timeout=(15, READ_TIMEOUT)) as r:
        if r.status_code == 403:
            raise PermissionError("Vigil's server refused this PC's key")
        r.raise_for_status()
        _state.update(connected=True, resp=r)
        log.info("linked to Vigil in the cloud")
        nudge()
        # A byte at a time: a bigger read would sit on a short event until
        # more arrived behind it.
        for line in r.iter_lines(chunk_size=1):
            if _stop.is_set():
                break
            if line.startswith(b"data: "):
                try:
                    _handle(json.loads(line[6:].decode("utf-8")))
                except Exception:
                    log.warning("vigil link: bad event", exc_info=True)


def _listen_loop() -> None:
    fails = 0
    while not _stop.is_set():
        began = time.time()
        try:
            _listen()
        except PermissionError as exc:
            log.error("%s — run vigil_cloud_setup.py again", exc)
            fails = len(RETRY_SECS) - 1
        except Exception as exc:
            log.info("Vigil's server unreachable (%s) — retrying", type(exc).__name__)
        finally:
            _state.update(connected=False, resp=None)
        fails = 0 if time.time() - began > 60 else min(fails + 1, len(RETRY_SECS) - 1)
        _stop.wait(RETRY_SECS[fails])


def open_window() -> bool:
    """The chat in its app window on this PC, signed in with the PC's key —
    or the open one brought forward."""
    import vigil_chat
    if not configured():
        return False
    if vigil_chat._raise_window():
        return True
    return vigil_chat.launch_app(f"{_base()}/?t={config()['key']}&pc=1")


def start() -> None:
    """Link this PC to the server (the process that holds Vigil's host lock)."""
    if any(t.is_alive() for t in _threads):
        return
    import vigil_chat
    _stop.clear()
    _threads.clear()
    vigil_chat._register_protocol()          # a toast's click opens the chat window
    for target, name in ((_listen_loop, "GIL-Link"), (_presence_loop, "GIL-LinkPresence"),
                         (_sender, "GIL-LinkSend")):
        t = threading.Thread(target=target, daemon=True, name=name)
        t.start()
        _threads.append(t)


def stop() -> None:
    """Let go (the watcher does this when G.I.L. starts and takes over)."""
    _stop.set()
    _nudge.set()
    if _state["connected"]:
        try:                                 # so the server knows now, not a minute from now
            requests.post(f"{_base()}/agent", json={"method": "bye", "kwargs": {}},
                          headers=_headers(), timeout=5)
        except (requests.RequestException, KeyError):
            pass
    resp = _state.get("resp")
    if resp is not None:
        try:
            resp.close()                     # unblocks the stream's read
        except Exception:
            pass
    for t in _threads:
        t.join(timeout=3)
