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
        self._start_server()
        if self._port:
            self._launch_window()
            self._open = True

    # ── HTTP server ───────────────────────────────────────────────────────
    def _start_server(self) -> None:
        chat = self
        html = _HTML_FILE.read_text(encoding="utf-8")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence console spam
                pass

            def _cors(self):
                self.send_header("Access-Control-Allow-Origin", "*")

            def do_GET(self):
                if self.path == "/" or self.path.startswith("/index"):
                    body = html.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/events":
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
                    self.send_response(404); self.end_headers()

            def do_POST(self):
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

        try:
            self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            self._port = self._httpd.server_address[1]
            threading.Thread(target=self._httpd.serve_forever, daemon=True,
                             name="GIL-WebChatHTTP").start()
            log.info("web chat serving on 127.0.0.1:%d", self._port)
        except Exception:
            log.error("web chat server failed to start", exc_info=True)
            self._port = 0

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

    def _rpc_send(self, text=""):
        text = (text or "").strip()
        if not text:
            return
        threading.Thread(target=self._persist, args=(text, "user"),
                         daemon=True).start()
        fn = getattr(self._gil_window, "_chat_send_fn", None)
        if fn:
            threading.Thread(target=fn, args=(text,), daemon=True,
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
                "- `/website <description>` — build a multi-page site\n"
                "- `/image <description>` — generate an image\n"
                "- `/code <task>` — write or fix code (I ask before each change)\n"
                "- `/remember <fact>` — save something for me to recall (no search)\n"
                "- `/stop` — stop what I'm doing · `/voice` — toggle voice · "
                "`/clear` — new chat", "gil")
            return

        if name == "stop":
            try:
                import autonomous
                if eng and autonomous.is_running(eng):
                    autonomous.stop(eng)
            except Exception:
                pass
            try:
                from voice import stop_speaking
                stop_speaking()
            except Exception:
                pass
            self.add_message("Stopped.", "gil")
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

    def request_approval(self, text: str, kind: str = "agent") -> None:
        """Push an approval request with clickable buttons to the web UI."""
        self._push("gil.approval", {"text": text, "kind": kind})

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
