"""
chat_web.py — G.I.L. 4.0 web-rendered chat.

Architecture (avoids the tkinter-vs-pywebview main-thread conflict):
  • A tiny stdlib HTTP server runs in a background thread.
      GET  /            → the chat UI (web_chat/index.html)
      GET  /events      → Server-Sent Events stream  (Python → browser push)
      POST /api         → JSON-RPC-ish calls          (browser → Python)
  • The UI opens in Microsoft Edge's chromeless --app mode: a real Chromium
    window (document typography, 60fps CSS motion, native spell-check) with
    no browser chrome — the same engine that renders Claude.
  • The Python engine is untouched; WebChat exposes the same surface the old
    tkinter ChatWindow did (add_message / show_typing / on_activity / …).
"""

import base64
import io
import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from logger import get as _get_log

log = _get_log("webchat")

_HTML_FILE = Path(__file__).parent / "web_chat" / "index.html"


# ── rich-content helpers ──────────────────────────────────────────────────────

def _thumb_b64(path: str, max_w: int = 520) -> str:
    try:
        from PIL import Image
        img = Image.open(path)
        img.thumbnail((max_w, 400))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=80)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def _rich_tag_to_card(content: str) -> dict | None:
    if content.startswith("[IMAGE:") and content.endswith("]"):
        p = content[7:-1]
        return {"kind": "image", "path": p, "thumb": _thumb_b64(p)}
    if content.startswith("[WEBSITE:") and content.endswith("]"):
        p = content[9:-1]
        title = Path(p).parent.name.replace("_", " ").replace("-", " ").title()
        return {"kind": "website", "path": p, "title": title}
    return None


class WebChat:
    """Web chat window. Same interface GILWindow expects from a chat window."""

    def __init__(self, gil_window):
        self._gil_window = gil_window
        self._open = False
        self._clients: list[queue.Queue] = []   # one queue per SSE connection
        self._clients_lock = threading.Lock()
        self._httpd = None
        self._port = 0
        # GIL-for-mobile: off until the user pairs a phone (/mobile). While off,
        # non-localhost requests are refused outright; while on, they must carry
        # the pairing token. The desktop window (localhost) is always trusted.
        self._mobile_enabled = False
        self._mobile_token = ""
        self._start_server()
        self._hook_token_meter()      # live quota rings
        if self._port:
            self._launch_window()
            self._open = True

    # ── HTTP server ───────────────────────────────────────────────────────
    def _start_server(self) -> None:
        chat = self
        html = _HTML_FILE.read_text(encoding="utf-8")
        # GIL's app icon — Chromium's --app window uses the page's favicon for the
        # TASKBAR icon, but only if it can actually fetch one; a data: URI isn't
        # enough. Serve the real .ico/.png so the taskbar shows GIL, not Edge.
        _icon_dir = _HTML_FILE.parent
        try:
            _ico_bytes = (_icon_dir / "gil_icon.ico").read_bytes()
        except Exception:
            _ico_bytes = b""
        try:
            _png_bytes = (_icon_dir / "gil_icon.png").read_bytes()
        except Exception:
            _png_bytes = b""
        try:
            _manifest_bytes = (_icon_dir / "manifest.json").read_bytes()
        except Exception:
            _manifest_bytes = b""

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence console spam
                pass

            def _cors(self):
                self.send_header("Access-Control-Allow-Origin", "*")

            def _authorized(self) -> bool:
                """Localhost is always trusted (the desktop window). A remote
                phone is allowed only once mobile is paired AND it presents the
                token — as a header (RPC) or ?t= query (page load / SSE)."""
                import mobile
                tok = self.headers.get("X-GIL-Token", "")
                if not tok and "?" in self.path:
                    from urllib.parse import parse_qs, urlparse
                    tok = (parse_qs(urlparse(self.path).query).get("t") or [""])[0]
                return mobile.authorized(self.client_address[0], tok,
                                         chat._mobile_enabled, chat._mobile_token)

            def _deny(self):
                self.send_response(403)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                try:
                    self.wfile.write(b"G.I.L. mobile is not paired. Run /mobile on the desktop.")
                except Exception:
                    pass

            def do_GET(self):
                if not self._authorized():
                    return self._deny()
                # route on the path WITHOUT the query — a phone loads "/?t=token"
                # and connects SSE to "/events?t=token", which must still match.
                route = self.path.split("?", 1)[0]
                if route == "/" or route.startswith("/index"):
                    body = html.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif route.startswith("/favicon.ico") or route.startswith("/gil_icon.ico"):
                    if not _ico_bytes:
                        self.send_response(404); self.end_headers(); return
                    self.send_response(200)
                    self.send_header("Content-Type", "image/x-icon")
                    self.send_header("Content-Length", str(len(_ico_bytes)))
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(_ico_bytes)
                elif route.startswith("/gil_icon.png"):
                    if not _png_bytes:
                        self.send_response(404); self.end_headers(); return
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(_png_bytes)))
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(_png_bytes)
                elif route.startswith("/manifest.json"):
                    if not _manifest_bytes:
                        self.send_response(404); self.end_headers(); return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/manifest+json")
                    self.send_header("Content-Length", str(len(_manifest_bytes)))
                    self.end_headers()
                    self.wfile.write(_manifest_bytes)
                elif route == "/events":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self._cors()
                    self.end_headers()
                    q: queue.Queue = queue.Queue()
                    with chat._clients_lock:
                        chat._clients.append(q)
                    try:
                        while True:
                            try:
                                msg = q.get(timeout=15)
                            except queue.Empty:
                                self.wfile.write(b": ping\n\n")  # keepalive
                                self.wfile.flush()
                                continue
                            self.wfile.write(f"data: {msg}\n\n".encode("utf-8"))
                            self.wfile.flush()
                    except Exception:
                        pass
                    finally:
                        with chat._clients_lock:
                            if q in chat._clients:
                                chat._clients.remove(q)
                else:
                    # static assets from web_chat/ (PWA icons, sw.js, …). Only a
                    # whitelisted extension and a basename — no path traversal.
                    import os as _os
                    name = _os.path.basename(route)
                    if route in ("/scan", "/scan/"):
                        name = "scan.html"          # friendly alias
                    ext = _os.path.splitext(name)[1].lower()
                    CT = {".png": "image/png", ".ico": "image/x-icon",
                          ".js": "application/javascript",
                          ".json": "application/json", ".svg": "image/svg+xml",
                          ".css": "text/css", ".html": "text/html; charset=utf-8",
                          ".webmanifest": "application/manifest+json"}
                    fp = _icon_dir / name
                    if name and ext in CT and fp.is_file():
                        data = fp.read_bytes()
                        self.send_response(200)
                        self.send_header("Content-Type", CT[ext])
                        self.send_header("Content-Length", str(len(data)))
                        self.send_header("Cache-Control", "no-cache")
                        self.end_headers()
                        self.wfile.write(data)
                    else:
                        self.send_response(404); self.end_headers()

            def do_POST(self):
                if not self._authorized():
                    return self._deny()
                if self.path != "/api":
                    self.send_response(404); self.end_headers(); return
                n = int(self.headers.get("Content-Length", 0))
                try:
                    req = json.loads(self.rfile.read(n) or b"{}")
                    result = chat._dispatch(req.get("method", ""),
                                            req.get("args", []))
                except Exception as exc:
                    log.error("api error: %s", exc, exc_info=True)
                    result = None
                body = json.dumps({"result": result}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self._cors()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        # Keep the handler so the server can be restarted on a different network
        # interface when the user pairs a phone (localhost-only until then).
        self._Handler = Handler
        # A STABLE port matters now that GIL can be installed as a PWA: the
        # installed app hard-codes host:port at install time, so a random port
        # each launch would leave the icon pointing at a dead port. Prefer a
        # fixed port, falling back through a few, then to an OS-assigned one.
        self._httpd = None
        for cand in (8731, 8732, 8733, 8734, 0):
            try:
                self._httpd = ThreadingHTTPServer(("127.0.0.1", cand), Handler)
                break
            except OSError:
                continue
        if self._httpd is None:
            log.error("web chat server failed to start")
            self._port = 0
            return
        self._port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, daemon=True,
                         name="GIL-WebChatHTTP").start()
        log.info("web chat serving on 127.0.0.1:%d", self._port)

    def _rebind(self, host: str) -> None:
        """Restart the HTTP server on `host` (same port). '127.0.0.1' = local only;
        '0.0.0.0' = reachable from the LAN (for a paired phone). Runs off the
        request thread; existing SSE clients auto-reconnect after the swap."""
        try:
            old = self._httpd
            if old is not None:
                old.shutdown()
                old.server_close()
            srv = ThreadingHTTPServer((host, self._port), self._Handler)
            self._httpd = srv
            threading.Thread(target=srv.serve_forever, daemon=True,
                             name="GIL-WebChatHTTP").start()
            log.info("web chat rebound to %s:%d", host, self._port)
        except Exception:
            log.error("web chat rebind to %s failed", host, exc_info=True)

    def enable_lan(self) -> None:
        """Open GIL to the local network so a paired phone can connect. Deferred
        ~1s so the QR card and the command's own response flush first (the rebind
        drops in-flight connections). Non-localhost requests still need the token."""
        if getattr(self, "_lan_active", False):
            return
        self._lan_active = True

        def _go():
            time.sleep(1.2)
            self._rebind("0.0.0.0")
        threading.Thread(target=_go, daemon=True).start()

    def disable_lan(self) -> None:
        """Close network access — back to localhost only, and forget the token."""
        self._lan_active = False
        self._mobile_enabled = False
        self._mobile_token = ""
        threading.Thread(target=lambda: self._rebind("127.0.0.1"),
                         daemon=True).start()

    def open_scanner(self) -> bool:
        """Open the biometric scanner full-screen — GIL's sign-in sequence."""
        if not self._port:
            return False
        url = f"http://127.0.0.1:{self._port}/scan"
        import subprocess
        data_dir = Path(__file__).parent / "data" / "_web_profile"
        for exe in (
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "msedge", "chrome",
        ):
            try:
                subprocess.Popen(
                    [exe, f"--app={url}", "--start-fullscreen",
                     f"--user-data-dir={data_dir}",
                     "--no-first-run", "--no-default-browser-check",
                     "--disable-extensions"],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return True
            except (FileNotFoundError, OSError):
                continue
        import webbrowser
        webbrowser.open(url)
        return True

    def _launch_window(self) -> None:
        url = f"http://127.0.0.1:{self._port}/"
        import subprocess
        for exe in (
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "msedge", "chrome",
        ):
            try:
                data_dir = Path(__file__).parent / "data" / "_web_profile"
                # Chromium caches an --app window's TASKBAR icon in the profile's
                # "Web Applications" folder. This profile was first created before
                # GIL served any icon, so it cached the Edge fallback. Clear that
                # cache (icons only — safe, regenerated on demand) so Chromium
                # re-derives GIL's icon from the manifest on this launch.
                try:
                    import shutil
                    for wa in data_dir.rglob("Web Applications"):
                        shutil.rmtree(wa, ignore_errors=True)
                except Exception:
                    pass
                subprocess.Popen(
                    [exe, f"--app={url}", "--window-size=1180,860",
                     f"--user-data-dir={data_dir}",
                     "--no-first-run", "--no-default-browser-check",
                     "--disable-extensions"],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return
            except (FileNotFoundError, OSError):
                continue
        import webbrowser
        webbrowser.open(url)

    # ── JS → Python dispatch ──────────────────────────────────────────────
    def _dispatch(self, method: str, args: list):
        fn = getattr(self, f"_rpc_{method}", None)
        if fn is None:
            return None
        return fn(*args)

    def _rpc_ready(self):
        try:
            from voice import voice_enabled
            voice = voice_enabled()
        except Exception:
            voice = True
        try:
            from version import VERSION
        except Exception:
            VERSION = "4.0"
        return {"theme": self._theme(), "voice": voice, "version": VERSION,
                "sessions": self._rpc_sessions(),
                "current": self._session_payload()}

    def _rpc_sessions(self):
        try:
            from chat_history import list_sessions
            return [{"id": s["id"], "name": s.get("name") or "Conversation",
                     "preview": (s.get("preview") or "")[:60],
                     "msg_count": s.get("msg_count", 0)}
                    for s in list_sessions(40)]
        except Exception:
            return []

    # ── Visual Bug Inspector (Ctrl+Shift+S): screen → chat → fix ──────────────
    def _rpc_visual_capture(self, whole_screen=False):
        """Grab the ACTIVE WINDOW (sharp text, fewer image tokens) and hand the
        UI a preview to freeze behind the prompt bar. The model-sized payload
        stays here — it never needs to cross the wire twice."""
        try:
            import visual_inspector as vi
            shot = vi.capture(whole_screen=bool(whole_screen))
        except Exception:
            return {"ok": False, "error": "Screen capture failed."}
        if not shot.get("ok"):
            return {"ok": False, "error": shot.get("error", "capture failed")}
        self._pending_shot = shot
        return {"ok": True, "preview": shot["preview_b64"],
                "title": shot.get("title", ""), "scope": shot.get("scope", ""),
                "w": shot["w"], "h": shot["h"],
                "sent_w": shot.get("sent_w"), "sent_h": shot.get("sent_h")}

    def _rpc_visual_cancel(self):
        self._pending_shot = None
        return {"ok": True}

    def _rpc_visual_submit(self, text=""):
        """[Fix Screen] — read the capture, then hand it to the coding agent."""
        shot = getattr(self, "_pending_shot", None)
        if not shot:
            return {"ok": False, "error": "Nothing captured — press Ctrl+Shift+S first."}
        self._pending_shot = None
        threading.Thread(target=self._visual_pipeline, args=(shot, text or ""),
                         daemon=True, name="GIL-VisualInspect").start()
        return {"ok": True}

    def _visual_pipeline(self, shot: dict, text: str) -> None:
        """vision read → chat → coding agent. Runs off the request thread."""
        eng = getattr(self._gil_window, "_engine", None)
        try:
            self.add_image_message(shot["path"])       # thumbnail chip in chat
        except Exception:
            pass
        self._push("gil.showTyping")
        try:
            import visual_inspector as vi
            res = vi.inspect(text, shot["b64"], shot["path"], shot.get("title", ""))
        except Exception:
            res = {"ok": False, "error": "the vision pass crashed"}
        if not res.get("ok"):
            self._push("gil.hideTyping")
            self.add_message(
                f"I couldn't read that screen — {res.get('error', 'vision failed')}.", "gil")
            return
        self.add_message("**What I can see on that screen**\n\n" + res["reading"], "gil")
        # Hand the reading to the coding agent so it actually FIXES it. Writes are
        # still gated by the approval policy, so a screenshot can never silently
        # rewrite files.
        if eng is not None and not getattr(eng, "_agent_running", False):
            try:
                import code_agent
                code_agent.run_async(eng, res["task"])
                return
            except Exception:
                pass
        self._push("gil.hideTyping")

    # ── biometric sign-in (the face scanner at /scan.html) ────────────────────
    _face_id = None

    def _rpc_face_scan(self, frame=""):
        """One frame from the scanner's camera → who is this?

        The browser owns the camera (smooth preview, no extra dependency) and
        Python owns recognition, so only a small JPEG crosses per tick."""
        try:
            import base64 as _b64, io as _io
            import numpy as _np
            from PIL import Image as _Image
            raw = _b64.b64decode((frame or "").split(",", 1)[-1])
            if not raw:
                return {"status": "no_face", "confidence": 0.0, "face_box": None}
            img = _Image.open(_io.BytesIO(raw)).convert("RGB")
            bgr = _np.array(img)[:, :, ::-1]          # PIL is RGB, FaceID wants BGR
        except Exception:
            return {"status": "no_face", "confidence": 0.0, "face_box": None}

        try:
            if self._face_id is None:
                from face_id import FaceID
                self._face_id = FaceID()
            res = dict(self._face_id.identify(bgr))
        except Exception:
            log.debug("face scan failed", exc_info=True)
            return {"status": "no_face", "confidence": 0.0, "face_box": None}

        # a little context for the HUD panels
        try:
            import face_id as _fid
            res["threshold"] = float(getattr(_fid, "_SIM_THRESHOLD", 0.25))
            res["enrolled"] = len(getattr(self._face_id, "_embeddings", {}) or {})
        except Exception:
            pass
        return res

    def _rpc_face_confirmed(self, name=""):
        """Recognised — greet them out loud, the way a doorman would."""
        name = (name or "").strip() or "there"
        line = f"Identity confirmed. Hello {name}."
        try:
            eng = getattr(self._gil_window, "_engine", None)
            if eng is not None:
                eng._speak(line)
                eng._last_spoke_at[0] = time.time() - 1.5
                eng._last_said[0] = line
        except Exception:
            log.debug("greeting failed", exc_info=True)
        return {"ok": True, "said": line}

    def _rpc_scan_done(self, name=""):
        """Scanner finished — close it and hand over to the chat."""
        try:
            self.add_message(f"**Identity confirmed** — welcome back, {name}.", "gil")
        except Exception:
            pass
        return {"ok": True}

    # ── file / photo attachment (drag-drop, paste, or the paperclip) ──────────
    _IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")

    def _rpc_attach(self, name="", data="", text=""):
        """Receive a dropped/pasted/picked file. `data` is a data-URL or raw
        base64. Images go to the vision model; text files become context for the
        brain. Returns quickly; the answer streams in on a worker thread."""
        import re
        try:
            raw = base64.b64decode((data or "").split(",", 1)[-1])
        except Exception:
            return {"ok": False, "error": "I couldn't read that file."}
        if not raw:
            return {"ok": False, "error": "That file was empty."}
        if len(raw) > 12_000_000:
            return {"ok": False, "error": "That file is too big (max ~12 MB)."}
        try:
            updir = Path(__file__).parent / "data" / "uploads"
            updir.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^\w.\-]", "_", name or "file")[:80] or "file"
            path = updir / f"{int(time.time() * 1000)}_{safe}"
            path.write_bytes(raw)
        except Exception:
            return {"ok": False, "error": "I couldn't save that file."}
        is_image = path.suffix.lower() in self._IMG_EXT
        if is_image:
            self.add_image_message(str(path))       # thumbnail preview in chat
        else:
            self.add_message(f"📎 attached **{safe}**", "user")
        threading.Thread(target=self._attach_pipeline,
                         args=(str(path), text or "", is_image),
                         daemon=True, name="GIL-Attach").start()
        return {"ok": True, "image": is_image}

    def _attach_pipeline(self, path, text, is_image):
        """Off-thread: read the attachment and answer. An image is answered by the
        vision model directly (so 'what does this say?' works); a text file is
        handed to the brain as context."""
        self._push("gil.showTyping")
        try:
            if is_image:
                import vision_model
                from PIL import Image
                img = vision_model.fit_for_send(Image.open(path))
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=82)
                b64 = base64.b64encode(buf.getvalue()).decode()
                q = text.strip() or ("Describe this image. If it shows an error, "
                                     "code, a document or handwriting, quote the "
                                     "important text exactly.")
                ok, reading, err = vision_model.chat(
                    vision_model.image_message(q, b64), max_tokens=1400, timeout=45)
                self._push("gil.hideTyping")
                if ok:
                    self.add_message(reading, "gil")
                else:
                    self.add_message(f"I couldn't read that image — {err}.", "gil")
                return
            # text file → brain context (the brain is text-only, so this is ideal)
            try:
                content = Path(path).read_text(encoding="utf-8", errors="replace")
            except Exception:
                content = ""
            if not content.strip():
                self._push("gil.hideTyping")
                self.add_message("That file doesn't look like readable text.", "gil")
                return
            ext = Path(path).suffix.lstrip(".")
            msg = (f"[The user attached the file `{Path(path).name}`. Its contents:]\n"
                   f"```{ext}\n{content[:12000]}\n```\n\n---\n\n"
                   + (text.strip() or "Take a look at this file."))
            fn = getattr(self._gil_window, "_chat_send_fn", None)
            if fn:
                threading.Thread(target=fn, args=(msg,), daemon=True,
                                 name="GIL-AttachSend").start()
            else:
                self._push("gil.hideTyping")
        except Exception:
            self._push("gil.hideTyping")
            self.add_message("I couldn't process that attachment.", "gil")

    def _rpc_stop_subagent(self, aid=0):
        """Stop button on a background sub-agent row (Feature #10)."""
        try:
            import subagents
            ok = subagents.stop(int(aid))
            self._push("gil.subagents", subagents.status())
            return {"stopped": ok}
        except Exception:
            return {"stopped": False}

    def _rpc_list_files(self, query=""):
        """@file autocomplete (Feature #6) — project files matching `query`."""
        try:
            import code_agent
            return code_agent.list_project_files(query or "")
        except Exception:
            return []

    def _rpc_send(self, text="", pinned=None):
        text = (text or "").strip()
        if not text:
            return
        threading.Thread(target=self._persist, args=(text, "user"),
                         daemon=True).start()
        eng = getattr(self._gil_window, "_engine", None)
        # Feature #6: content of any pinned @files, prepended to the message ONLY
        # at the real dispatch points below — so it never bloats the persisted
        # message (kept clean above) nor the yes/no approval-gate detection.
        def _with_pins(t):
            if not pinned:
                return t
            try:
                import code_agent
                ctx = code_agent.read_pinned_context(pinned)
                return (ctx + "\n\n---\n\n" + t) if ctx else t
            except Exception:
                return t
        # In /codemode every message goes straight to the coding agent — GIL
        # works like Claude Code, editing files and running commands with
        # per-change approval, rendered in the terminal.
        if eng is not None and getattr(eng, "_code_mode", False):
            low = text.lower().strip(" .!?")
            # A change is waiting on approval → a typed yes/no resolves THAT gate,
            # it does not start a brand-new task (that was the "Task 'yes'" bug).
            if getattr(eng, "_agent_gate", None):
                if low in ("yes", "y", "yeah", "yep", "ok", "okay", "sure",
                           "approve", "approved", "go", "go ahead", "do it"):
                    self._rpc_approve("agent", True);  return
                if low in ("no", "n", "nope", "skip", "reject", "rejected",
                           "stop", "cancel", "dont", "don't"):
                    self._rpc_approve("agent", False); return
                self.add_message("There's a change waiting on your approval above — "
                                 "**Approve** or **Reject** it first.", "gil")
                return
            # An agent is mid-task → queue nothing, just tell the user (no rival run).
            if getattr(eng, "_agent_running", False):
                self.add_message("I'm still working on the previous task — "
                                 "one moment, then send the next one.", "gil")
                return
            # Not every message in codemode is a coding task. "do research on
            # whales" is a question for the brain, not the code agent — which
            # would otherwise grep the project for "whale" for 15 minutes. Route
            # a clearly non-coding request through the normal chat path instead.
            try:
                import code_agent
                if not code_agent.is_coding_task(text):
                    self.add_message(
                        "That's not a coding task, so I'm answering it normally "
                        "(still in the terminal — `/chat` to leave it).", "gil")
                    fn = getattr(self._gil_window, "_chat_send_fn", None)
                    if fn:
                        threading.Thread(target=fn, args=(_with_pins(text),),
                                         daemon=True, name="GIL-WebSend").start()
                        return
                code_agent.run_async(eng, _with_pins(text))
                return
            except Exception:
                pass
        fn = getattr(self._gil_window, "_chat_send_fn", None)
        if fn:
            threading.Thread(target=fn, args=(_with_pins(text),), daemon=True,
                             name="GIL-WebSend").start()

    def _rpc_new_chat(self):
        try:
            from chat_history import new_chat_session
            new_chat_session()
        except Exception:
            pass
        self._push("gil.loadMessages", self._session_payload())
        self._push("gil.setSessions", self._rpc_sessions())

    def _rpc_open_session(self, sid=""):
        try:
            from chat_history import set_current_session
            set_current_session(sid)
        except Exception:
            pass
        self._push("gil.loadMessages", self._session_payload(sid))
        self._push("gil.setSessions", self._rpc_sessions())

    def _rpc_delete_session(self, sid=""):
        try:
            from chat_history import delete_session, get_current_session
            cur = get_current_session()
            delete_session(sid)
        except Exception:
            cur = None
        if sid == cur:
            self._rpc_new_chat()
        else:
            self._push("gil.setSessions", self._rpc_sessions())

    def _rpc_command(self, name="", arg=""):
        """Slash commands from the web UI mapped to real GIL capabilities."""
        name = (name or "").lower().strip()
        arg = (arg or "").strip()
        eng = getattr(self._gil_window, "_engine", None)

        if name == "help":
            self.add_message(
                "**Commands**\n"
                "- `/goal <task>` — I work on it autonomously until it's done\n"
                "- `/research <question>` — live web answer with sources\n"
                "- `/study <exam>` — full prep: study guide, practice test, exercises\n"
                "- `/website <description>` — build a multi-page site\n"
                "- `/image <description>` — generate an image\n"
                "- `/code <task>` — write or fix code (I ask before each change)\n"
                "- `/remember <fact>` — save something for me to recall (no search)\n"
                "- `/approve ask|auto|readonly` — set the code-agent permission policy\n"
                "- `/compact` — summarize the chat so far & free up context\n"
                "- `/checkpoint [name]` — save a restore point · `/undo` — roll back to it\n"
                "- `/spawn <task>` — work it in the background · `/tasks` — background status\n"
                "- `/stop` — stop what I'm doing · `/voice` — toggle voice · "
                "`/clear` — new chat", "gil")
            return

        if name in ("codemode", "code_mode", "dev"):
            on = getattr(eng, "_code_mode", False) if eng else False
            new = not on if not arg else (arg.lower() not in ("off", "exit", "stop"))
            if eng is not None:
                eng._code_mode = new
            self._push("gil.setCodeMode", new)
            if new:
                self.add_message(
                    "**Code mode on** — I'm now a coding agent. Everything you "
                    "type is a dev task: I read files, edit code and run "
                    "commands, asking approval before each change. Type "
                    "`/chat` or `/codemode off` to leave.", "gil")
                if arg and arg.lower() not in ("on",):
                    self._rpc_send(arg)   # a task was passed inline
            else:
                self.add_message("Back to normal chat.", "gil")
            return
        if name in ("chat", "normal"):
            if eng is not None:
                eng._code_mode = False
            self._push("gil.setCodeMode", False)
            self.add_message("Back to normal chat.", "gil")
            return

        if name == "stop":
            if eng and getattr(eng, "_agent_running", False):
                self._rpc_stop_task()          # cancel a running coding agent too
            try:
                import autonomous
                if eng and autonomous.is_running(eng):
                    autonomous.stop(eng)
            except Exception:
                pass
            try:                            # background sub-agents too (Feature #10)
                import subagents
                for r in subagents.status():
                    if r["status"] == "running":
                        subagents.stop(r["id"])
            except Exception:
                pass
            try:
                from voice import stop_speaking
                stop_speaking()
            except Exception:
                pass
            self.add_message("Stopped.", "gil")
            return

        if name in ("approve", "approvals", "permissions"):
            # Feature #5: configure the destructive-op permission policy.
            valid = {"ask": "ask before writes, edits & shell commands (default)",
                     "auto": "auto-approve writes, edits & safe commands (power mode)",
                     "readonly": "read-only — refuse all writes, edits & commands"}
            mode = arg.lower().strip().replace("read-only", "readonly").replace("plan", "readonly")
            try:
                import code_agent
                cur = code_agent._approval_policy()
            except Exception:
                cur = "ask"
            if mode not in valid:
                self.add_message(
                    f"**Approval mode** is `{cur}`. Reads (read/list/search) are always "
                    "free. Change it with `/approve ask`, `/approve auto`, or "
                    "`/approve readonly`:\n"
                    + "\n".join(f"- `{k}` — {v}" for k, v in valid.items()), "gil")
                return
            self._save_cfg("agent_approval_mode", mode)
            self.add_message(f"**Approval mode → `{mode}`** — {valid[mode]}.", "gil")
            return

        if name in ("spatial", "hands", "hand"):
            import spatial
            a = arg.lower().strip()
            if a in ("off", "stop", "disable"):
                _, msg = spatial.stop()
            elif a in ("tutorial", "again", "help"):
                spatial.reset_tutorial()
                spatial.stop()
                _, msg = spatial.start()
            elif a in ("on", "start", "enable"):
                _, msg = spatial.start()
            else:
                _, msg = spatial.toggle()
            self.add_message(msg, "gil")
            return

        if name in ("scan", "faceid", "signin"):
            if self.open_scanner():
                self.add_message("**Biometric scan** — look at the camera.", "gil")
            else:
                self.add_message("I couldn't open the scanner.", "gil")
            return

        if name in ("mobile", "phone", "pair"):
            import mobile as _mob
            if arg.lower().strip() in ("off", "stop", "disable"):
                self.disable_lan()
                self.add_message("**Phone access off.** GIL is back to this PC only, "
                                 "and the old pairing link no longer works.", "gil")
                return
            # Pair a phone: mint a token, open a token-gated LAN listener (opt-in),
            # and show the URL + QR to scan.
            if not self._mobile_token:
                self._mobile_token = _mob.make_token()
            self._mobile_enabled = True
            ip = _mob.lan_ip()
            url = _mob.pairing_url(ip, self._port, self._mobile_token)
            qr = _mob.qr_svg(url)
            self.enable_lan()          # opens the LAN listener ~1s later
            self._push("gil.mobilePairing", {"url": url, "qr": qr, "ip": ip,
                                             "port": self._port, "lan": True})
            self.add_message(
                "**GIL on your phone** 📱\n\nScan this with your phone's camera — the "
                "phone must be on the **same Wi-Fi**. It opens GIL's chat in your "
                "mobile browser; tap **Share → Add to Home Screen** to keep it like "
                "an app.\n\n"
                f"Or open: `{url}`\n\n"
                "_The link holds a private pairing key — anyone with it can talk to "
                "your GIL, so keep it to yourself. Run `/mobile off` to close phone "
                "access._", "gil")
            return

        if name in ("compact", "compress"):
            # Feature #2: summarize the conversation so far into a condensed memory
            # and drop the verbose frames — frees context and resets the gauge.
            brain = getattr(eng, "brain", None)
            if brain is None or not hasattr(brain, "compact"):
                self.add_message("Nothing to compact yet.", "gil")
                return
            self._push("gil.showTyping")
            try:
                res = brain.compact()
            except Exception:
                res = {"ok": False, "reason": "Compaction hit an error — nothing changed."}
            self._push("gil.hideTyping")
            if not res.get("ok"):
                self.add_message(res.get("reason", "Nothing to compact yet."), "gil")
                return
            before, after = res.get("before", 0), res.get("after", 0)
            saved = max(0, before - after)
            removed = res.get("removed", 0)
            msg = (f"**Compacted.** Summarized {removed} message"
                   f"{'s' if removed != 1 else ''} — context ~{before:,} → ~{after:,} "
                   f"tokens (freed ~{saved:,}).")
            if res.get("reason"):
                msg += f"\n\n_{res['reason']}_"
            self.add_message(msg, "gil")
            # refresh the live gauge to the new (smaller) context size
            try:
                self.token_usage({"tokens": after, "budget": 128000})
            except Exception:
                pass
            return

        if name in ("checkpoint", "ckpt", "save"):
            # Feature #8: seal current edits as a named restore point.
            try:
                import code_agent
                res = code_agent.create_checkpoint(arg)
            except Exception:
                self.add_message("Couldn't create a checkpoint right now.", "gil")
                return
            n = res["files"]
            if n:
                self.add_message(
                    f"**Checkpoint saved** → “{res['label']}” · {n} file"
                    f"{'s' if n != 1 else ''} locked in. `/undo` rolls back to here. "
                    f"({res['total']} checkpoint{'s' if res['total'] != 1 else ''} total)", "gil")
            else:
                self.add_message(
                    f"**Checkpoint “{res['label']}” set** — no file changes since the "
                    "last one, so there's nothing new to roll back to yet.", "gil")
            return

        if name in ("spawn", "background", "bg"):
            # Feature #10: run a task in a detached sub-agent, read-only unless
            # the user explicitly opts into a writing one.
            mode, body = "readonly", arg
            first = arg.split(None, 1)
            if first and first[0].lower() in ("auto", "write", "--auto"):
                mode = "auto"
                body = first[1] if len(first) > 1 else ""
            if not body.strip():
                self.add_message(
                    "**/spawn `<task>`** runs a task in the background while we keep "
                    "talking — I'll report back when it's done.\n\n"
                    "It runs **read-only** (it investigates and reports; it won't "
                    "change files, since nobody's there to approve). Use "
                    "`/spawn auto <task>` to let it make changes. `/tasks` shows "
                    "what's running.", "gil")
                return
            try:
                import subagents
                res = subagents.spawn(eng, body, mode)
            except Exception:
                self.add_message("Couldn't start a background task right now.", "gil")
                return
            if not res.get("ok"):
                self.add_message(res.get("error", "Couldn't start that."), "gil")
                return
            self.add_message(
                f"**Background task #{res['id']} started** ({res['mode']}) — _{body}_\n\n"
                "I'll report back here when it's done. `/tasks` for status.", "gil")
            try:
                self.subagents(subagents.status())
            except Exception:
                pass
            return

        if name in ("tasks", "jobs"):
            try:
                import subagents
                rows = subagents.status()
            except Exception:
                rows = []
            if not rows:
                self.add_message(
                    "No background tasks yet — start one with `/spawn <task>`.", "gil")
                return
            icon = {"running": "◐", "done": "✓", "failed": "✕", "cancelled": "⊘"}
            lines = [f"- {icon.get(r['status'], '·')} **#{r['id']}** "
                     f"`{r['status']}` · {r['elapsed']}s · {r['mode']} — {r['task']}"
                     for r in rows]
            self.add_message("**Background tasks**\n" + "\n".join(lines), "gil")
            return

        if name in ("undo", "rollback"):
            # Feature #8: instant LIFO revert to the last checkpoint.
            try:
                import code_agent
                result = code_agent.undo_last_changes()
            except Exception:
                self.add_message("Undo hit an error — check data/agent_backups.", "gil")
                return
            self.add_message(result, "gil")
            return

        if name == "remember":
            if not arg:
                return
            try:
                from memory import remember
                remember(arg, mem_type="fact", source="user", importance=7)
            except Exception:
                pass
            self.add_message(f"Got it — I'll remember that: {arg}", "gil")
            return

        # goal / research / website / image / code → expand to a natural
        # request and route through the normal send path (brain + fast-paths),
        # which already knows how to dispatch each one.
        prefix = {
            "goal":     "",   # handled specially below
            "research": "Look this up and answer with sources: ",
            "website":  "Build a website for ",
            "image":    "Generate an image of ",
            "code":     "",
        }
        if name == "goal":
            if eng and arg:
                try:
                    import autonomous
                    autonomous.run(eng, arg)
                    return
                except Exception:
                    pass
            self._rpc_send(f"Complete this goal step by step: {arg}")
            return
        if name == "code":
            self._rpc_send(f"Write or fix code for this: {arg}")
            return
        if name == "study":
            eng = getattr(self._gil_window, "_engine", None)
            if eng:
                try:
                    import action_router
                    threading.Thread(
                        target=action_router.dispatch,
                        args=("study_prep", arg, "", arg, arg.lower(), [], eng),
                        daemon=True, name="GIL-StudyCmd").start()
                    return
                except Exception:
                    pass
            self._rpc_send(f"Prepare me for this exam: {arg}")
            return
        if name in prefix:
            self._rpc_send(prefix[name] + arg)
            return
        # unknown → treat as plain message
        self._rpc_send(f"/{name} {arg}".strip())

    def _rpc_approve(self, kind="", approved=False):
        """User clicked Approve/Reject on an approval card."""
        eng = getattr(self._gil_window, "_engine", None)
        approved = bool(approved)
        if kind == "fix":
            pending = getattr(eng, "_pending_fix", None) if eng else None
            eng and setattr(eng, "_pending_fix", None)
            if approved and pending:
                try:
                    import code_agent
                    if isinstance(pending, dict):
                        code_agent.run_async(eng, pending.get("task", ""),
                                             pending.get("commands") or None)
                    else:
                        code_agent.run_async(eng, "Apply this fix and verify it works.",
                                             list(pending))
                    self.add_message("On it — I'll ask before each change.", "gil")
                except Exception:
                    pass
            elif not approved:
                self.add_message("Skipped.", "gil")
            return
        # code-agent per-change gate
        gate = getattr(eng, "_agent_gate", None) if eng else None
        if gate:
            gate["approved"] = approved
            try:
                gate["event"].set()
            except Exception:
                pass
        if not approved:
            self.add_message("Skipped that change.", "gil")

    def _rpc_stop_task(self):
        """Esc / Stop button — cancel the running coding agent (Feature #1)."""
        eng = getattr(self._gil_window, "_engine", None)
        if not eng or not getattr(eng, "_agent_running", False):
            # No coding agent running — Stop/Esc must still do something useful
            # (silence speech, halt an autonomous run) instead of nothing at all.
            stopped = False
            try:
                from voice import stop_speaking
                stop_speaking()
                stopped = True
            except Exception:
                pass
            try:
                import autonomous
                if eng and autonomous.is_running(eng):
                    autonomous.stop(eng)
                    stopped = True
            except Exception:
                pass
            return {"stopped": stopped}
        eng._agent_cancel = True
        gate = getattr(eng, "_agent_gate", None)
        if gate:                       # unblock a pending approval wait
            try:
                gate["approved"] = False
                gate["event"].set()
            except Exception:
                pass
        hgate = getattr(eng, "_hunk_gate", None)
        if hgate:                      # unblock a pending hunk review too
            try:
                hgate["cancelled"] = True
                hgate["event"].set()
            except Exception:
                pass
        return {"stopped": True}

    def request_approval(self, text: str, kind: str = "agent",
                         diff: dict | None = None, file: str | None = None) -> None:
        """Push an approval request with clickable buttons + optional unified
        diff to the web UI."""
        self._push("gil.approval",
                   {"text": text, "kind": kind, "diff": diff, "file": file})

    def request_hunk_review(self, payload: dict) -> None:
        """Push a hunk-by-hunk diff review to the web UI (Feature #3). Each hunk
        gets its own accept / reject / edit control; the user's decisions come
        back through _rpc_hunk_review."""
        self._push("gil.hunkReview", payload)

    def _rpc_hunk_review(self, decisions=None, cancelled=False):
        """The user finished (or cancelled) a hunk-by-hunk review — resolve the
        code agent's review gate with their per-hunk decisions."""
        eng = getattr(self._gil_window, "_engine", None)
        gate = getattr(eng, "_hunk_gate", None) if eng else None
        if gate:
            gate["cancelled"] = bool(cancelled)
            gate["decisions"] = decisions if isinstance(decisions, dict) else {}
            try:
                gate["event"].set()
            except Exception:
                pass
        return {"ok": True}

    def tool_block(self, block: dict) -> None:
        """Push a Claude-Code-style tool-call block to the terminal UI."""
        self._push("gil.toolBlock", block)

    def set_working(self, on: bool) -> None:
        """Pin the working spinner + timer for a whole coding-agent run."""
        self._push("gil.setWorking", bool(on))

    def _rpc_open_path(self, p=""):
        try:
            import os
            if str(p).startswith("http"):
                import webbrowser; webbrowser.open(p)
            else:
                os.startfile(str(p).strip())
        except Exception:
            pass

    def _rpc_set_theme(self, t="dark"):
        self._save_cfg("chat_theme", t)

    def _rpc_toggle_voice(self):
        try:
            from voice import voice_enabled, set_voice_enabled, stop_speaking
            new = not voice_enabled()
            set_voice_enabled(new)
            if not new:
                stop_speaking()   # cut off any sentence mid-air immediately
            log.info("voice toggled %s via web chat", "on" if new else "off")
            self._push("gil.setVoice", new)
        except Exception:
            log.error("voice toggle failed", exc_info=True)

    # ── helpers ───────────────────────────────────────────────────────────
    @staticmethod
    def _persist(text, sender):
        try:
            from chat_history import save_message
            save_message(sender, text)
        except Exception:
            pass

    @staticmethod
    def _save_cfg(key, val):
        try:
            p = Path(__file__).parent / "data" / "gil_config.json"
            cfg = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
            cfg[key] = val
            p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _theme(self) -> str:
        try:
            cfg = json.loads((Path(__file__).parent / "data" / "gil_config.json")
                             .read_text(encoding="utf-8"))
            return cfg.get("chat_theme", "dark")
        except Exception:
            return "dark"

    def _session_payload(self, sid=None) -> dict:
        try:
            from chat_history import (load_session, get_current_session,
                                      list_sessions)
            sid = sid or get_current_session()
            msgs = load_session(sid) if sid else []
            name = "New conversation"
            for s in list_sessions(40):
                if s["id"] == sid:
                    name = s.get("name") or "Conversation"; break
            out = []
            for m in msgs:
                rich = _rich_tag_to_card(m["content"])
                out.append({"rich": rich} if rich else
                           {"sender": m["sender"], "text": m["content"],
                            "ts": m["ts"]})
            return {"id": sid, "name": name, "messages": out}
        except Exception:
            return {"id": "", "name": "New conversation", "messages": []}

    # ── Python → browser (SSE push) ───────────────────────────────────────
    def _push(self, fn: str, payload=None) -> None:
        msg = json.dumps({"fn": fn, "arg": payload})
        with self._clients_lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(msg)
            except Exception:
                pass

    # ── chat-window interface (engine/GILWindow call these) ──────────────
    def winfo_exists(self) -> bool:
        return self._open and bool(self._clients)

    def is_alive(self) -> bool:
        return self._open

    def add_message(self, text: str, sender: str) -> None:
        if not (text or "").strip():
            return
        threading.Thread(target=self._persist, args=(text, sender),
                         daemon=True).start()
        self._push("gil.addMessage",
                   {"sender": sender, "text": text, "ts": time.time()})

    def show_typing(self) -> None: self._push("gil.showTyping")
    def hide_typing(self) -> None: self._push("gil.hideTyping")
    def on_activity(self, entry: dict) -> None: self._push("gil.activity", entry)

    # live token stream — GIL's reasoning typewriters into the terminal as it
    # thinks (transient; not persisted to history).
    def stream_thought(self, delta: str) -> None: self._push("gil.streamThought", delta)
    def stream_thought_end(self) -> None: self._push("gil.streamThoughtEnd")

    # self-healing badge — a failed tool → GIL auto-corrects (Feature #9)
    def self_heal(self, info: dict) -> None: self._push("gil.selfHeal", info)

    # live token-budget gauge — context fullness per step (Feature #2)
    def token_usage(self, info: dict) -> None: self._push("gil.tokenUsage", info)

    # real API quota rings — how much of the minute/day allowance is spent
    def token_meter(self, rows: list, warning: str = "") -> None:
        self._push("gil.tokenMeter", {"models": rows, "warning": warning})

    def _rpc_token_meter(self):
        """The UI asks for this on load so the rings start populated."""
        try:
            import token_meter
            return {"models": token_meter.all_status(), "warning": ""}
        except Exception:
            return {"models": [], "warning": ""}

    def _hook_token_meter(self) -> None:
        """Push a fresh reading (and any threshold warning) after every call."""
        try:
            import token_meter

            def _on_usage(status, warning):
                try:
                    self.token_meter(token_meter.all_status(), warning or "")
                    if warning:
                        self.add_message("⚡ " + warning, "gil")
                except Exception:
                    pass

            token_meter.subscribe(_on_usage)
        except Exception:
            log.debug("token meter unavailable", exc_info=True)

    # background sub-agent roster — status chips + drawer rows (Feature #10)
    def subagents(self, rows: list) -> None: self._push("gil.subagents", rows)

    # Visual Bug Inspector — open the frozen overlay with a capture already taken
    def visual_overlay(self, shot: dict) -> None: self._push("gil.visualOverlay", shot)

    def open_visual_inspector(self) -> bool:
        """Global Ctrl+Shift+S: capture whatever the user is looking at RIGHT NOW
        (before GIL's own window takes focus), then raise the prompt overlay."""
        try:
            import visual_inspector as vi
            shot = vi.capture()
            if not shot.get("ok"):
                return False
            self._pending_shot = shot
            self.visual_overlay({"preview": shot["preview_b64"],
                                 "title": shot.get("title", ""),
                                 "scope": shot.get("scope", "")})
            return True
        except Exception:
            log.debug("visual inspector hotkey failed", exc_info=True)
            return False

    def add_image_message(self, path) -> None:
        p = str(path)
        threading.Thread(target=self._persist, args=(f"[IMAGE:{p}]", "gil"),
                         daemon=True).start()
        self._push("gil.richCard",
                   {"kind": "image", "path": p, "thumb": _thumb_b64(p)})

    def add_link_message(self, path) -> None:
        p = str(path)
        threading.Thread(target=self._persist, args=(f"[WEBSITE:{p}]", "gil"),
                         daemon=True).start()
        title = Path(p).parent.name.replace("_", " ").replace("-", " ").title()
        self._push("gil.richCard", {"kind": "website", "path": p, "title": title})

    def session_named(self, session_id: str, title: str) -> None:
        self._push("gil.sessionNamed", session_id)
        self._push("gil.setSessions", self._rpc_sessions())

    def show(self) -> None:
        """Reopen the Edge window (server keeps running the whole session)."""
        if self._port:
            self._launch_window()

    # tk-compat no-ops
    def lift(self):  pass
    def focus(self): pass
    def state(self, *_): pass
    def destroy(self):
        self._open = False
        try:
            if self._httpd:
                self._httpd.shutdown()
        except Exception:
            pass
