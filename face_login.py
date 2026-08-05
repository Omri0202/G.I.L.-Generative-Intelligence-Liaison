"""
face_login.py — G.I.L.'s biometric front door.

Runs BEFORE the password screen: the scanner comes up full-screen, GIL looks at
you, and if it recognises an enrolled face you're straight in. Anything else —
no camera, nobody enrolled, an unfamiliar face, or you pressing Esc — falls
through to the normal username/password window, so this can never lock you out.

Deliberately self-contained: it stands up its own tiny HTTP server for the scan
page, because at this point in start-up the main web chat doesn't exist yet.
"""

from __future__ import annotations

import base64
import io
import json
import queue
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from logger import get as _get_log

log = _get_log("facelogin")

_SCAN_PAGE = Path(__file__).parent / "web_chat" / "scan.html"
_TIMEOUT = 25.0          # seconds to find a face before handing over to password


def _identify(face_id, data_url: str) -> dict:
    """Decode one browser frame and ask FaceID who it is."""
    try:
        import numpy as np
        from PIL import Image
        raw = base64.b64decode((data_url or "").split(",", 1)[-1])
        if not raw:
            return {"status": "no_face", "confidence": 0.0, "face_box": None}
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        bgr = np.array(img)[:, :, ::-1]          # PIL is RGB; FaceID wants BGR
        res = dict(face_id.identify(bgr))
    except Exception:
        log.debug("frame identify failed", exc_info=True)
        return {"status": "no_face", "confidence": 0.0, "face_box": None}
    try:
        import face_id as _fid
        res["threshold"] = float(getattr(_fid, "_SIM_THRESHOLD", 0.25))
        res["enrolled"] = len(getattr(face_id, "_embeddings", {}) or {})
    except Exception:
        pass
    return res


def enroll_user(name: str, timeout: float = _TIMEOUT) -> bool:
    """Register `name`'s face, using the same scanner UI in enrol mode.

    Called after a password sign-in by someone the camera didn't recognise, so
    the next time they open GIL their face alone gets them in."""
    return _run_scanner(mode="enroll", enroll_name=name, timeout=timeout) is not None


def _enroll_frame(face_id, data_url: str, name: str) -> bool:
    """Register the face in this frame under `name`."""
    try:
        import numpy as np
        from PIL import Image
        raw = base64.b64decode((data_url or "").split(",", 1)[-1])
        if not raw:
            return False
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        bgr = np.array(img)[:, :, ::-1]
        ok = face_id.enroll(bgr, name)
        # enroll() returns either a bool or (ok, detail) depending on the path
        if isinstance(ok, tuple):
            ok = bool(ok[0])
        return bool(ok)
    except Exception:
        log.debug("enrolment failed", exc_info=True)
        return False


def scan_for_user(timeout: float = _TIMEOUT) -> str | None:
    """Show the scanner and return the recognised username, or None."""
    return _run_scanner(mode="identify", timeout=timeout)


def _run_scanner(mode: str = "identify", enroll_name: str = "",
                 timeout: float = _TIMEOUT) -> str | None:
    if not _SCAN_PAGE.is_file():
        return None
    try:
        from face_id import FaceID
        face_id = FaceID()
        if mode == "identify" and not face_id.has_enrolled():
            log.info("no enrolled faces — skipping biometric login")
            return None
    except Exception:
        log.debug("FaceID unavailable", exc_info=True)
        return None

    html = _SCAN_PAGE.read_text(encoding="utf-8")
    result: queue.Queue = queue.Queue(maxsize=1)
    # the page polls until it has two consecutive matches, then calls face_confirmed
    saw_any_frame = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, ctype, body: bytes):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:
                pass

        def do_GET(self):
            route = self.path.split("?", 1)[0]
            if route in ("/", "/scan", "/scan.html"):
                self._send(200, "text/html; charset=utf-8", html.encode("utf-8"))
            else:
                self._send(404, "text/plain", b"no")

        def do_POST(self):
            if self.path.split("?", 1)[0] != "/api":
                return self._send(404, "text/plain", b"no")
            n = int(self.headers.get("Content-Length", 0) or 0)
            try:
                req = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                req = {}
            method = req.get("method", "")
            args = req.get("args") or []
            out = None
            if method == "face_scan":
                saw_any_frame.set()
                out = _identify(face_id, args[0] if args else "")
                if mode == "enroll":
                    # Same UI, different job: once a face is clearly in frame,
                    # register it rather than looking it up. Reported back as a
                    # "match" so the scanner runs its normal confirm sequence.
                    if out.get("face_box"):
                        if _enroll_frame(face_id, args[0] if args else "", enroll_name):
                            out = {**out, "status": "match", "name": enroll_name,
                                   "confidence": 1.0}
                        else:
                            out = {**out, "status": "no_face"}
                    else:
                        out = {**out, "status": "no_face"}
            elif method == "face_confirmed":
                name = (args[0] if args else "") or ""
                out = {"ok": True}
                if name and not result.full():
                    result.put(name)
            elif method == "scan_done":
                out = {"ok": True}
            self._send(200, "application/json",
                       json.dumps({"result": out}).encode("utf-8"))

    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    except Exception:
        log.debug("scan server failed to bind", exc_info=True)
        return None
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name="GIL-FaceLogin").start()

    proc = _open_fullscreen(f"http://127.0.0.1:{port}/scan")

    name = None
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                name = result.get(timeout=0.25)
                break
            except queue.Empty:
                # give the camera a fair chance to warm up before the clock counts
                if not saw_any_frame.is_set():
                    deadline = time.time() + timeout
    finally:
        # let the greeting land on screen before the window disappears
        if name:
            time.sleep(2.6)
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            pass
        _close(proc)

    if name:
        log.info("biometric login: %s", name)
    else:
        log.info("biometric login found no match — falling back to password")
    return name


def _open_fullscreen(url: str):
    """Open the scanner as a chromeless full-screen window."""
    data_dir = Path(__file__).parent / "data" / "_web_profile"
    for exe in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "msedge", "chrome",
    ):
        try:
            return subprocess.Popen(
                [exe, f"--app={url}", "--start-fullscreen",
                 f"--user-data-dir={data_dir}",
                 "--no-first-run", "--no-default-browser-check",
                 "--disable-extensions"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (FileNotFoundError, OSError):
            continue
    return None


def _close(proc):
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
