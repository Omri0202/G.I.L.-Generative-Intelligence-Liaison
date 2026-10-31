"""
tailscale_link.py — reaching G.I.L. from your phone anywhere, privately.

Tailscale puts this PC and your phone on one private network of your own
(WireGuard, encrypted end to end, free for personal use). `tailscale serve`
then gives the Vigil chat a real HTTPS address that only your own devices
can open — https://<this-pc>.<your-tailnet>.ts.net — which is what a phone
needs to install G.I.L. as an app and receive push notifications. Nothing is
opened to the internet and no router needs touching.

This module only talks to the Tailscale client already on the PC: is it
installed, signed in, what is this PC's address, and serve (or stop serving)
the chat there. Signing in to Tailscale is always the user's own step.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time

from logger import get as _get_log

log = _get_log("tailscale")

DOWNLOAD_URL = "https://tailscale.com/download/windows"
PHONE_URL = "https://play.google.com/store/apps/details?id=com.tailscale.ipn"
_ENABLE_URL = re.compile(r"https://login\.tailscale\.com/\S+")
_cache: dict = {"at": 0.0, "status": None}


def _exe() -> str:
    for path in (shutil.which("tailscale"),
                 os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                              "Tailscale", "tailscale.exe"),
                 r"C:\Program Files (x86)\Tailscale\tailscale.exe"):
        if path and os.path.isfile(path):
            return path
    return ""


def _run(args: list[str], timeout: int = 15) -> tuple[int, str]:
    exe = _exe()
    if not exe:
        return 127, ""
    try:
        done = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return done.returncode, (done.stdout or "") + (done.stderr or "")
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        out = out.decode("utf-8", "replace") if isinstance(out, bytes) else out
        return 124, out
    except OSError:
        log.warning("couldn't run tailscale", exc_info=True)
        return 126, ""


def parse_status(raw: str) -> dict:
    """What `tailscale status --json` says, in the terms Vigil needs."""
    try:
        st = json.loads(raw or "{}")
    except ValueError:
        return {"installed": True, "running": False, "name": "", "https": False,
                "why": "Tailscale didn't answer"}
    state = st.get("BackendState", "")
    me = st.get("Self") or {}
    name = (me.get("DNSName") or "").rstrip(".").lower()
    certs = [c.rstrip(".").lower() for c in st.get("CertDomains") or []]
    why = ""
    if state == "NeedsLogin":
        why = "Tailscale isn't signed in on this PC"
    elif state in ("Stopped", "NoState", "Starting", ""):
        why = "Tailscale is turned off on this PC"
    elif state != "Running":
        why = f"Tailscale is {state.lower()}"
    elif not name:
        why = "turn on MagicDNS in the Tailscale admin page"
    return {"installed": True, "running": state == "Running", "name": name,
            "https": bool(name) and name in certs, "why": why}


def status(fresh: bool = False) -> dict:
    """{installed, running, name, https, why} — cached for a minute, since
    every request from a phone checks the address it came to."""
    if not fresh and _cache["status"] and time.time() - _cache["at"] < 60:
        return _cache["status"]
    if not _exe():
        st = {"installed": False, "running": False, "name": "", "https": False,
              "why": "Tailscale isn't installed on this PC"}
    else:
        code, out = _run(["status", "--json"])
        st = parse_status(out) if out.strip().startswith("{") else {
            "installed": True, "running": False, "name": "", "https": False,
            "why": "Tailscale is turned off on this PC"}
    _cache.update(at=time.time(), status=st)
    return st


def name() -> str:
    st = status()
    return st["name"] if st["running"] else ""


def serve(port: int) -> dict:
    """Serve the chat at https://<this-pc>.<tailnet>.ts.net — to this tailnet
    only (never Funnel, which would put it on the internet). Returns {ok, url,
    enable_url, why}; enable_url is the admin page where the user switches
    on HTTPS for their tailnet, the one step Tailscale can't do for them."""
    st = status(fresh=True)
    if not st["running"]:
        return {"ok": False, "url": "", "enable_url": "", "why": st["why"]}
    code, out = _run(["serve", "--bg", str(int(port))], timeout=20)
    enable = _ENABLE_URL.search(out or "")
    if code != 0 or enable:
        return {"ok": False, "url": "", "enable_url": enable.group(0) if enable else "",
                "why": "HTTPS isn't on for your tailnet yet" if enable
                       else (out.strip().splitlines() or ["tailscale serve failed"])[-1][:160]}
    _cache["at"] = 0.0
    return {"ok": True, "url": f"https://{st['name']}/", "enable_url": "", "why": ""}


def unserve() -> None:
    """Stop serving the chat on the tailnet (phone access turned off)."""
    if _exe():
        _run(["serve", "--https=443", "off"], timeout=15)
