"""
notify.py — G.I.L. Windows notifications.

One job: put a toast on the Windows desktop. Used by vigil.py to tell the user
about something a standing order found.

Unlike the screenshot toast in gestures.py, the text here comes from emails and
web pages — untrusted. Pasting it into a PowerShell command string would let a
subject line like  '); Remove-Item ...  break out of the string and run. So the
PowerShell command below is a CONSTANT, and the toast arrives as XML in an
environment variable: data, never code. The XML itself is escaped so a stray
'<' or '&' can't break the document either.
"""

from __future__ import annotations

import os
import subprocess
import sys
from xml.sax.saxutils import escape

from logger import get as _get_log

log = _get_log("notify")

TITLE_CHARS = 64
BODY_CHARS = 300

#: Fixed command. The toast XML is read from $env:GIL_TOAST_XML.
_PS = (
    "[Windows.UI.Notifications.ToastNotificationManager,"
    "Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
    "[Windows.Data.Xml.Dom.XmlDocument,"
    "Windows.Data.Xml.Dom,ContentType=WindowsRuntime]|Out-Null;"
    "$x=New-Object Windows.Data.Xml.Dom.XmlDocument;"
    "$x.LoadXml($env:GIL_TOAST_XML);"
    "$t=New-Object Windows.UI.Notifications.ToastNotification $x;"
    "[Windows.UI.Notifications.ToastNotificationManager]"
    "::CreateToastNotifier('G.I.L.').Show($t)"
)


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def toast_xml(title: str, body: str, urgency: str = "normal") -> str:
    """The toast document. Urgent ones stay on screen longer; low ones are silent."""
    duration = ' duration="long"' if urgency == "urgent" else ""
    audio = '<audio silent="true"/>' if urgency == "low" else ""
    return (f"<toast{duration}><visual><binding template=\"ToastGeneric\">"
            f"<text>{escape(_clip(title, TITLE_CHARS))}</text>"
            f"<text>{escape(_clip(body, BODY_CHARS))}</text>"
            f"</binding></visual>{audio}</toast>")


def _launch(env: dict) -> None:
    subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
         "-Command", _PS],
        env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def toast(title: str, body: str, urgency: str = "normal") -> bool:
    """Show a Windows toast. Returns False where there's no Windows to show it on."""
    if sys.platform != "win32":
        return False
    try:
        env = dict(os.environ)
        env["GIL_TOAST_XML"] = toast_xml(title, body, urgency)
        _launch(env)
        return True
    except Exception:
        log.warning("toast failed", exc_info=True)
        return False
