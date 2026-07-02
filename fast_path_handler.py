"""
fast_path_handler.py — G.I.L.
Every voice-input fast-path lives here as its own named function.

Why: _do_process() used to be 500+ lines of chained if/elif blocks.
     Now each fast-path is a focused function you can find in seconds.
     If WhatsApp recap breaks → look at _whatsapp().
     If camera open breaks   → look at _camera_open().

Interface
---------
Every handler:
  - Takes (text, lower, eng)  where eng = ConversationEngine instance
  - Returns True  if it handled the request (caller should return)
  - Returns False to pass through to the next handler
  - Logs at DEBUG level when it fires so the log file shows which path ran

Entry point: process(text, lower, eng) — tries handlers in order.
"""

import threading
import time
from logger import get as _get_log

log = _get_log("fast_path")


# ── Pending recap yes/no ──────────────────────────────────────────────────────

def _recap_confirm(text: str, lower: str, eng) -> bool:
    """User said yes/no to a pending WhatsApp or email recap."""
    _active = eng._pending_recap[0] or eng._pending_recap_g[0]
    if not _active:
        return False

    _YES = {"yes", "yeah", "sure", "go ahead", "read them", "read it",
            "please", "ok", "okay", "yep", "do it", "read", "go"}
    _NO  = {"no", "nope", "nah", "dont", "don't", "cancel",
            "skip", "nevermind", "never mind", "stop"}

    words = lower.split()

    if any(w in words for w in _YES):
        log.debug("fast-path: recap confirmed")
        recap = _active
        eng._pending_recap[0]   = {}
        eng._pending_recap_g[0] = {}

        def _read():
            from ears import unmute
            if recap["type"] == "wa":
                parts = []
                for m in recap["items"]:
                    preview = f': "{m["preview"]}"' if m.get("preview") else ""
                    plural  = f'{m["count"]} messages' if m.get("count", 1) > 1 else "a message"
                    parts.append(f"{plural} from {m['name']}{preview}")
                speech = ". ".join(parts) + "."
            elif recap["type"] == "email":
                parts = []
                for e in recap["items"]:
                    snippet = f" — {e['snippet']}" if e.get("snippet") else ""
                    parts.append(f"From {e['sender']}: {e['subject']}{snippet}")
                speech = ". Next: ".join(parts) + "."
            else:
                return
            unmute()
            eng.window.set_state("speaking", said=speech)
            eng._speak(speech)
            eng._last_spoke_at[0] = time.time() - 1.5
            eng._last_said[0]     = speech
            eng.window.set_state("listening")

        threading.Thread(target=_read, daemon=True, name="GIL-ReadRecap").start()
        return True

    if any(w in words for w in _NO):
        log.debug("fast-path: recap dismissed")
        eng._pending_recap[0]   = {}
        eng._pending_recap_g[0] = {}
        eng.window.set_state("listening")
        return True

    return False


# ── WhatsApp unread recap ─────────────────────────────────────────────────────

_WA_TRIGGERS = {
    "whatsapp messages", "whatsapp message", "whatsapp",
    "unread whatsapp", "any whatsapp", "check whatsapp",
    "read my whatsapp", "what's on whatsapp", "whats on whatsapp",
    "missed messages", "missed whatsapp",
}

def _whatsapp(text: str, lower: str, eng) -> bool:
    if not any(t in lower for t in _WA_TRIGGERS):
        return False
    log.debug("fast-path: whatsapp recap")

    def _run():
        from ears import unmute
        unmute()
        eng.window.set_state("processing", heard=text)
        try:
            import whatsapp_recap
            msgs   = whatsapp_recap.get_unread_messages()
            speech = whatsapp_recap.build_recap_speech(msgs)
            if msgs:
                eng._pending_recap[0] = {"type": "wa", "items": msgs}
        except Exception:
            log.error("WhatsApp recap failed", exc_info=True)
            speech = "I couldn't reach WhatsApp right now. Want me to open it?"
        eng.window.set_state("speaking", said=speech)
        eng._speak(speech)
        eng._last_spoke_at[0] = time.time() - 1.5
        eng._last_said[0]     = speech
        eng.window.set_state("listening")

    threading.Thread(target=_run, daemon=True, name="GIL-WARecap").start()
    return True


# ── Gmail unread recap ────────────────────────────────────────────────────────

_GMAIL_TRIGGERS = {
    "unread emails", "unread mail", "any emails", "check my email",
    "check email", "new emails", "new mail", "what emails",
    "read my emails", "read my mail", "inbox recap", "email recap",
    "missed emails", "missed mail",
}

def _gmail(text: str, lower: str, eng) -> bool:
    if not any(t in lower for t in _GMAIL_TRIGGERS):
        return False
    log.debug("fast-path: gmail recap")

    def _run():
        from ears import unmute
        try:
            import gmail_recap
            emails = gmail_recap.get_unread_summary(max_results=5)
            speech = gmail_recap.build_recap_speech(emails) if emails else "No unread emails."
            if emails:
                eng._pending_recap[0] = {"type": "email", "items": emails}
        except Exception:
            log.error("Gmail recap failed", exc_info=True)
            speech = "I couldn't reach Gmail right now. Want me to open it instead?"
        eng.window.set_state("speaking", said=speech)
        unmute()
        eng._speak(speech)
        eng._last_spoke_at[0] = time.time() - 1.5
        eng._last_said[0]     = speech
        eng.window.set_state("listening")

    threading.Thread(target=_run, daemon=True, name="GIL-EmailRecap").start()
    return True


# ── Face enrollment ───────────────────────────────────────────────────────────

_FACE_ENROLL_TRIGGERS = {
    "remember my face", "enroll my face", "scan my face",
    "save my face", "learn my face", "memorize my face",
}

def _face_enroll(text: str, lower: str, eng) -> bool:
    if not any(t in lower for t in _FACE_ENROLL_TRIGGERS):
        return False
    log.debug("fast-path: face enrollment")

    def _run():
        from ears import unmute
        import tempfile, cv2 as _cv2
        from pathlib import Path as _P
        ack = "Scanning your face — hold still."
        eng.window.set_state("speaking", said=ack)
        unmute(); eng._speak(ack); time.sleep(1.8)
        frame_path = _P(tempfile.gettempdir()) / "gil_cam_frame.jpg"
        if not frame_path.exists():
            msg = "Open the camera first, then say remember my face."
        else:
            try:
                frame = _cv2.imread(str(frame_path))
                if frame is None:
                    raise ValueError("blank frame")
                from face_id import FaceID
                ok, detail = FaceID().enroll(frame, eng.username)
                msg = (f"Got it. I'll recognize you as {eng.username} from now on."
                       if ok else "Couldn't see your face clearly — try better lighting.")
                if not ok:
                    log.warning("face enroll failed: %s", detail)
            except Exception:
                log.error("face enroll error", exc_info=True)
                msg = "Face enrollment failed — make sure the camera is open."
        eng.window.set_state("speaking", said=msg)
        eng._speak(msg)
        eng._last_spoke_at[0] = time.time() - 1.5
        eng._last_said[0]     = msg
        eng.window.set_state("listening")

    threading.Thread(target=_run, daemon=True, name="GIL-FaceEnroll").start()
    return True


# ── Face query ────────────────────────────────────────────────────────────────

_FACE_QUERY_TRIGGERS = {
    "who do you see", "do you recognize me", "do you know me",
    "can you identify me", "who is there", "who's there",
    "is it me", "recognize my face", "do you see me", "who am i",
}

def _face_query(text: str, lower: str, eng) -> bool:
    if not any(t in lower for t in _FACE_QUERY_TRIGGERS):
        return False
    log.debug("fast-path: face query")

    def _run():
        from ears import unmute
        import tempfile, json as _j, cv2 as _cv2
        from pathlib import Path as _P
        face_file = _P(tempfile.gettempdir()) / "gil_face_state.json"
        if face_file.exists():
            try:
                state = _j.loads(face_file.read_bytes())
                nm, st = state.get("name"), state.get("status")
                if st == "match" and nm:   msg = f"Yes — I recognize you, {nm}."
                elif st == "unknown":       msg = "I see someone, but I don't recognize them."
                else:                       msg = "I can't see a face clearly right now."
                unmute(); eng.window.set_state("speaking", said=msg); eng._speak(msg)
                eng._last_spoke_at[0] = time.time() - 1.5
                eng._last_said[0]     = msg
                eng.window.set_state("listening")
                return
            except Exception:
                pass
        frame_path = _P(tempfile.gettempdir()) / "gil_cam_frame.jpg"
        if not frame_path.exists():
            msg = "Open the camera first so I can see you."
        else:
            try:
                from face_id import FaceID
                fid, frame = FaceID(), _cv2.imread(str(frame_path))
                if not fid.has_enrolled():
                    msg = "I haven't learned your face yet. Say 'remember my face' to enroll."
                else:
                    r = fid.identify(frame)
                    if r["status"] == "match":    msg = f"I see {r['name']}."
                    elif r["status"] == "unknown": msg = "I see someone, but I don't recognize them."
                    else:                          msg = "I can't detect a face — look straight at the camera."
            except Exception:
                log.error("face query error", exc_info=True)
                msg = "Face recognition isn't available right now."
        unmute(); eng.window.set_state("speaking", said=msg); eng._speak(msg)
        eng._last_spoke_at[0] = time.time() - 1.5
        eng._last_said[0]     = msg
        eng.window.set_state("listening")

    threading.Thread(target=_run, daemon=True, name="GIL-FaceQuery").start()
    return True


# ── Camera status ─────────────────────────────────────────────────────────────

_CAM_STATUS_PHRASES = {
    "is the camera open", "is the camera on", "is the camera showing",
    "is the camera visible", "is the camera running", "is your camera on",
    "is your camera active", "can you see me", "are you seeing me",
    "are you looking at me", "is camera open", "is camera on",
}

def _camera_status(text: str, lower: str, eng) -> bool:
    if not any(p in lower for p in _CAM_STATUS_PHRASES):
        return False
    log.debug("fast-path: camera status")
    import ctypes as _ct
    u32       = _ct.windll.user32
    hwnd      = u32.FindWindowW(None, "G.I.L. Vision")
    streaming = bool(eng._camera_win[0] and eng._camera_win[0].is_streaming())
    visible   = bool(hwnd and u32.IsWindowVisible(hwnd) and not u32.IsIconic(hwnd))
    if streaming and visible:
        speech = "Yes — the camera is open and visible on your screen."
    elif streaming:
        speech = "Camera is running but the window isn't visible — bringing it up now."
        if eng._camera_win[0]:
            threading.Thread(target=eng._camera_win[0].bring_to_front,
                             daemon=True, name="GIL-CamFocus").start()
    else:
        speech = "No — the camera is closed. Say 'open camera' to start it."
    eng.window.set_state("speaking", said=speech)
    eng._speak(speech)
    eng._last_spoke_at[0] = time.time() - 1.5
    eng._last_said[0]     = speech
    eng.window.set_state("listening")
    return True


# ── Camera open ───────────────────────────────────────────────────────────────

def _camera_open(text: str, lower: str, eng) -> bool:
    cam_kw = "camera" in lower or "webcam" in lower
    should_open = (
        cam_kw
        and any(w in lower for w in ("open", "show", "start", "enable",
                                     "activate", "turn on", "launch", "use"))
        and not any(w in lower for w in ("turn off", "close", "hide", "stop", "disable"))
        and not lower.startswith(("is ", "are ", "can ", "does ", "do ", "was "))
    ) or any(p in lower for p in (
        "open your eyes", "use your eyes", "turn on your camera",
        "turn on the camera", "your camera on", "start your camera",
    ))
    if not should_open:
        return False
    log.debug("fast-path: camera open")

    if eng._camera_win[0] and not eng._camera_win[0].is_alive():
        eng._camera_win[0] = None
    if eng._camera_win[0] and not eng._camera_win[0].is_streaming():
        eng._camera_win[0]._dead = True
        eng._camera_win[0] = None

    if eng._camera_win[0]:
        try:
            import ctypes as _ct
            hwnd = _ct.windll.user32.FindWindowW(None, "G.I.L. Vision")
            if hwnd:
                _ct.windll.user32.ShowWindow(hwnd, 9)
                _ct.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        speech = "Camera's already up. Say 'what do you see' to analyze."
    else:
        def _cam_closed():
            if eng._camera_win[0] is _cam:
                eng._camera_win[0] = None
            eng._stop_gesture_watcher()
        try:
            from eyes import CameraWindow
            _cam = CameraWindow(on_close=_cam_closed)
            eng._camera_win[0] = _cam
            eng._start_gesture_watcher()
            threading.Thread(target=_cam.bring_to_front,
                             daemon=True, name="GIL-CamFocus").start()
            speech = "Camera's up."
        except Exception:
            log.error("camera open failed", exc_info=True)
            speech = "Couldn't open the camera. Make sure it's connected."

    eng.window.set_state("speaking", said=speech)
    eng._speak(speech)
    eng._last_spoke_at[0] = time.time() - 1.5
    eng._last_said[0]     = speech
    eng.window.set_state("listening")
    return True


# ── Camera close ──────────────────────────────────────────────────────────────

def _camera_close(text: str, lower: str, eng) -> bool:
    cam_kw = "camera" in lower or "webcam" in lower
    should_close = (
        cam_kw and any(w in lower for w in ("close", "hide", "stop",
                                             "disable", "turn off", "shut"))
    ) or "close your eyes" in lower
    if not should_close:
        return False
    log.debug("fast-path: camera close")

    from eyes import _FRAME_FILE as _FF, _KILL_FILE as _KF
    running = (
        (eng._camera_win[0] and eng._camera_win[0].is_alive())
        or (_FF.exists() and time.time() - _FF.stat().st_mtime < 2.0)
    )
    if running:
        if eng._camera_win[0]:
            eng._camera_win[0].close()
        else:
            try:
                _KF.write_text("kill"); time.sleep(0.4)
                _FF.unlink(missing_ok=True); _KF.unlink(missing_ok=True)
            except Exception:
                pass
        eng._camera_win[0] = None
        eng._stop_gesture_watcher()
        speech = "Camera closed."
    else:
        speech = "Camera isn't open."
    eng.window.set_state("speaking", said=speech)
    eng._speak(speech)
    eng._last_spoke_at[0] = time.time() - 1.5
    eng._last_said[0]     = speech
    eng.window.set_state("listening")
    return True


# ── Vision / identify ─────────────────────────────────────────────────────────

_VISION_PHRASES = {
    "what is this", "what is that", "what's this", "what's that",
    "what are these", "what am i holding", "what do you see",
    "identify this", "identify that", "describe what you see",
    "what brand", "what model", "what color is this",
    "can you read this", "what does it say", "look at this",
    "look at that", "take a look", "what do i have", "recognize this",
    "what's in my hand", "what is in my hand", "look at my hand",
    "see what i", "look at me",
}

def _vision(text: str, lower: str, eng) -> bool:
    if not any(p in lower for p in _VISION_PHRASES):
        return False
    log.debug("fast-path: vision query")

    def _run(q=text):
        from ears import unmute
        eng.window.set_state("speaking", said="On it.")
        eng._speak("On it.")
        cam_open = eng._camera_win[0] and eng._camera_win[0].is_streaming()
        if not cam_open:
            def _cb():
                if eng._camera_win[0] is _tc:
                    eng._camera_win[0] = None
            try:
                from eyes import CameraWindow
                _tc = CameraWindow(on_close=_cb)
                eng._camera_win[0] = _tc
                threading.Thread(target=_tc.bring_to_front,
                                 daemon=True, name="GIL-CamFocus").start()
                cam_open = True
            except Exception:
                log.error("camera open for vision failed", exc_info=True)
        if cam_open and eng._camera_win[0]:
            frame = eng._camera_win[0].get_current_frame()
        else:
            from eyes import capture_frame
            frame = capture_frame()
        if not frame:
            result = "I can't see anything — open the camera first."
        else:
            try:
                from eyes import analyze_frame
                result = analyze_frame(frame, question=q)
            except Exception:
                log.error("vision analysis failed", exc_info=True)
                result = "Vision analysis failed."
        unmute()
        eng.window.set_state("speaking", said=result)
        eng._speak(result)
        eng._last_spoke_at[0] = time.time() - 1.5
        eng._last_said[0]     = result
        eng.window.set_state("listening")

    threading.Thread(target=_run, daemon=True, name="GIL-Identify").start()
    return True


# ── Website generation ────────────────────────────────────────────────────────

import re as _re

_WEB_NOUNS = {
    "website", "web site", "webpage", "web page", "landing page", "landing",
    "web app", "web application", "html page", "html site", "front page",
    "home page", "site", "homepage", "front end", "frontend",
}
_WEB_VERBS = {
    "build", "create", "make", "generate", "design", "write",
    "want", "need", "give me", "show me", "get me",
}
_WEB_DIRECT_RE = _re.compile(
    r"\b(build|create|make|generate|design|want|need)\b.{0,40}"
    r"\b(website|webpage|landing|web app|site|homepage|frontend)\b",
)

def _webgen(text: str, lower: str, eng) -> bool:
    if not (
        (any(n in lower for n in _WEB_NOUNS) and any(v in lower for v in _WEB_VERBS))
        or _WEB_DIRECT_RE.search(lower)
    ):
        return False
    log.debug("fast-path: website generation")

    def _run(utterance=text):
        from ears import unmute
        ack = "On it — give me about 30 seconds."
        eng.window.set_state("speaking", said=ack)
        eng._last_spoke_at[0] = time.time() + 120
        eng._speak(ack)
        eng._last_spoke_at[0] = time.time()
        eng._last_said[0]     = ack
        eng.window.show_webgen_progress()
        html_path = None
        try:
            from webgen import generate as _wg, generate_for_project as _wgp, _find_web_project
            proj   = _find_web_project(utterance)
            result, html_path = _wgp(proj) if proj else _wg(utterance)
        except Exception as exc:
            log.error("webgen failed", exc_info=True)
            result = f"Website generation failed — {exc.__class__.__name__}."
            eng._last_spoke_at[0] = time.time()
        eng.window.close_webgen_progress(); unmute()
        eng.window.set_state("speaking", said=result)
        eng._speak(result)
        eng._last_spoke_at[0] = time.time() - 1.5
        eng._last_said[0]     = result
        eng.window.set_state("listening")
        if html_path:
            eng.window.send_rich_to_chat("website", html_path)

    threading.Thread(target=_run, daemon=True, name="GIL-WebGen").start()
    return True


# ── Fast URL open ─────────────────────────────────────────────────────────────

def _fast_url(text: str, lower: str, eng) -> bool:
    from fast_paths import fast_url_resolve
    result = fast_url_resolve(text)
    if not result:
        return False
    url, label = result
    log.debug("fast-path: url open -> %s", url)
    from actions import open_url
    threading.Thread(target=open_url, args=(url,), daemon=True).start()
    speech = f"{label}."
    eng.window.set_state("speaking", said=speech)
    eng._speak(speech)
    eng._last_spoke_at[0] = time.time()
    eng._last_said[0]     = speech
    eng.window.set_state("listening")
    return True


# ── Mode command ──────────────────────────────────────────────────────────────

_MODE_MAP = {
    "do not disturb": "dnd", "dnd mode": "dnd", "dnd": "dnd",
    "study mode": "study", "studying mode": "study",
    "fun mode": "fun", "normal mode": "normal", "reset mode": "normal",
}

def _mode(text: str, lower: str, eng) -> bool:
    matched = next((v for k, v in _MODE_MAP.items() if k in lower), None)
    if not matched:
        return False
    log.debug("fast-path: mode -> %s", matched)
    from modes import set_mode
    result = set_mode(matched)
    eng.window.set_state("speaking", said=result)
    eng._speak(result)
    eng._last_spoke_at[0] = time.time() - 1.5
    eng._last_said[0]     = result
    eng.window.set_state("listening")
    return True


# ── YouTube search ────────────────────────────────────────────────────────────

def _youtube(text: str, lower: str, eng) -> bool:
    from fast_paths import fast_youtube_resolve
    url = fast_youtube_resolve(text)
    if not url:
        return False
    log.debug("fast-path: youtube -> %s", url[:60])
    from actions import open_url
    from ears import unmute
    threading.Thread(target=open_url, args=(url,), daemon=True).start()
    speech = "Here you go."
    eng.window.set_state("speaking", said=speech)
    unmute(); eng._speak(speech)
    eng._last_spoke_at[0] = time.time()
    eng._last_said[0]     = speech
    eng.window.set_state("listening")
    if eng._active_project[0]:
        try:
            from learning_projects import add_resource
            add_resource(eng._active_project[0], "video", url, text[:80])
        except Exception:
            pass
    return True


# ── Learning project open ─────────────────────────────────────────────────────

_PROJ_OPEN_WORDS = {"open", "show", "enter", "access", "continue",
                    "go to", "load", "see", "view", "bring up"}

def _project_open(text: str, lower: str, eng) -> bool:
    if not (("project" in lower or "projects" in lower)
            and any(w in lower for w in _PROJ_OPEN_WORDS)):
        return False
    log.debug("fast-path: project open")
    try:
        from learning_projects import list_all, load
        projects = list_all()
        if not projects:
            speech = "No learning projects saved yet."
        else:
            _SKIP = {"my", "the", "a", "an", "i", "open", "show", "enter",
                     "access", "continue", "load", "see", "view", "project",
                     "projects", "bring", "up", "go", "to", "want", "please"}
            words = {w for w in lower.split() if w not in _SKIP and len(w) > 2}
            matched, best = None, 0
            for p in projects:
                nw  = {w for w in p["name"].lower().split() if len(w) > 2}
                sc  = len(nw & words) * 2 + sum(
                    1 for uw in words for nw2 in nw if uw in nw2 or nw2 in uw
                )
                if sc > best:
                    best, matched = sc, p["name"]
            if matched:
                eng._active_project[0] = matched
                data    = load(matched)
                models  = [r for r in data.get("resources", []) if r.get("type") == "3d_model"]
                studios = [r for r in data.get("resources", []) if r.get("type") == "3d_studio"]
                last    = data["sessions"][-1] if data["sessions"] else None
                eng.window.after(0, lambda m=matched: eng.window.open_project_view(m))
                if models:
                    eng.window.show_3d(models[0].get("url", "sphere"))
                if studios:
                    def _reopen(s=studios):
                        from studio3d import reopen_studio
                        for r in s[:3]:
                            reopen_studio(r.get("url", "")); time.sleep(1.5)
                    threading.Thread(target=_reopen, daemon=True,
                                     name="GIL-ReopenStudio").start()
                last_q = (last["conversations"][-1]["user"][:60]
                          if last and last["conversations"] else "—")
                speech = (f"Opening {matched} — {len(data['sessions'])} sessions. "
                          + ("Your 3D creation is back up."
                             if (models or studios)
                             else f"Last topic: {last_q}."))
            else:
                names  = ", ".join(p["name"] for p in projects[:6])
                speech = f"Your learning projects: {names}. Which one?"
        from ears import unmute
        unmute()
        eng.window.set_state("speaking", said=speech)
        eng._speak(speech)
        eng._last_spoke_at[0] = time.time() - 1.5
        eng._last_said[0]     = speech
        eng.window.set_state("listening")
    except Exception:
        log.error("project open failed", exc_info=True)
        return False
    return True


# ── Study subject fast-path ───────────────────────────────────────────────────

_SUBJECT_KEYWORDS = {
    "math": "Math", "algebra": "Algebra", "calculus": "Calculus",
    "geometry": "Geometry", "physics": "Physics", "chemistry": "Chemistry",
    "biology": "Biology", "history": "History",
    "computer science": "Computer Science", "programming": "Programming",
    "python": "Python", "economics": "Economics",
    "statistics": "Statistics", "trigonometry": "Trigonometry",
}

def _study(text: str, lower: str, eng) -> bool:
    from fast_paths import fast_study_resolve
    result = fast_study_resolve(text)
    if not result:
        return False
    log.debug("fast-path: study subject")
    speech, url = result
    for kw, proj_name in _SUBJECT_KEYWORDS.items():
        if kw in lower and eng._active_project[0] != proj_name:
            eng._active_project[0] = proj_name
            break
    if url:
        from actions import open_url
        threading.Thread(target=open_url, args=(url,), daemon=True).start()
    from ears import unmute
    eng.window.set_state("speaking", said=speech)
    eng._speak(speech)
    eng._last_spoke_at[0] = time.time()
    eng._last_said[0]     = speech
    eng.window.set_state("listening")
    if eng._active_project[0]:
        try:
            from learning_projects import add_conversation
            add_conversation(eng._active_project[0], text, speech)
        except Exception:
            pass
    return True


# ── Subject keyword auto-detect (side effect — no early return) ───────────────

def _subject_autodetect(text: str, lower: str, eng) -> bool:
    """Tag the active learning project from subject keywords. Never handles."""
    for kw, proj_name in _SUBJECT_KEYWORDS.items():
        if kw in lower and any(t in lower for t in {
            "studying", "learning", "working on", "doing",
            "help me with", "explain", "teach",
        }):
            if eng._active_project[0] != proj_name:
                eng._active_project[0] = proj_name
                log.info("active project set: %s", proj_name)
            break
    return False   # always pass through


# ── Image generation ──────────────────────────────────────────────────────────

_IMAGE_VERBS   = {"generate", "create", "make", "draw", "paint", "illustrate", "design"}
_IMAGE_NOUNS   = {"image", "photo", "picture", "illustration", "artwork",
                  "drawing", "painting", "poster", "wallpaper", "logo"}
_IMAGE_EXCLUDE = {"website", "webpage", "web", "site", "app"}

def _image_gen(text: str, lower: str, eng) -> bool:
    """Detect image generation requests and produce with Pollinations/FLUX."""
    words = set(lower.split())
    has_verb = bool(words & _IMAGE_VERBS)
    has_noun = bool(words & _IMAGE_NOUNS)
    if not (has_verb and has_noun):
        return False
    # Don't steal website-generation requests
    if any(w in lower for w in _IMAGE_EXCLUDE):
        return False
    log.debug("fast-path: image generation")

    # Strip the command prefix to get the actual description
    import re as _re
    desc = _re.sub(
        r"^(please\s+)?(generate|create|make|draw|paint|illustrate|design)\s+"
        r"(me\s+)?(an?\s+)?(image|photo|picture|illustration|artwork|drawing|painting|poster|wallpaper|logo)"
        r"(\s+of)?\s*",
        "", lower, flags=_re.IGNORECASE,
    ).strip() or text

    def _run(description=desc):
        from ears import unmute
        ack = "Generating your image — about 15 seconds."
        eng.window.set_state("speaking", said=ack)
        eng._speak(ack)

        img_path = None
        try:
            from image_gen import generate, open_image, infer_dimensions
            w, h = infer_dimensions(description)
            img_path = generate(description, width=w, height=h)
            open_image(img_path)
            result = f"Done. Saved as {img_path.name}."
        except Exception:
            log.error("image generation failed", exc_info=True)
            result = "Image generation failed — check your internet connection."

        unmute()
        eng.window.set_state("speaking", said=result)
        eng._speak(result)
        eng._last_spoke_at[0] = time.time() - 1.5
        eng._last_said[0]     = result
        eng.window.set_state("listening")
        if img_path:
            eng.window.send_rich_to_chat("image", img_path)

    threading.Thread(target=_run, daemon=True, name="GIL-ImageGen").start()
    return True


# ── Developer mode activation ────────────────────────────────────────────────

_DEV_MODE_ON  = {"activate developer mode", "developer mode on", "enable developer mode",
                 "dev mode", "activate dev mode", "turn on developer mode",
                 "start developer mode", "switch to developer mode"}
_DEV_MODE_OFF = {"deactivate developer mode", "developer mode off", "disable developer mode",
                 "turn off developer mode", "exit developer mode"}

def _dev_mode_toggle(text: str, lower: str, eng) -> bool:
    if any(t in lower for t in _DEV_MODE_OFF):
        log.debug("fast-path: deactivate developer mode")
        from dev_config import disable
        disable()
        speech = "Developer mode deactivated."
        eng.window.set_state("speaking", said=speech)
        eng._speak(speech)
        eng._last_spoke_at[0] = time.time() - 1.5
        eng._last_said[0]     = speech
        eng.window.set_state("listening")
        return True

    if not any(t in lower for t in _DEV_MODE_ON):
        return False
    log.debug("fast-path: activate developer mode")

    def _activate():
        from ears import unmute
        from dev_config import is_enabled
        if is_enabled():
            speech = "Developer mode is already active."
            unmute()
            eng.window.set_state("speaking", said=speech)
            eng._speak(speech)
            eng._last_spoke_at[0] = time.time() - 1.5
            eng._last_said[0]     = speech
            eng.window.set_state("listening")
            return
        # Show setup wizard
        speech = "Opening developer setup."
        eng.window.set_state("speaking", said=speech)
        eng._speak(speech)
        def _launch_wizard():
            from dev_setup_wizard import run_dev_wizard
            completed = run_dev_wizard(eng.window)
            unmute()
            if completed:
                result = "Developer mode is now active. Git, GitHub, Docker, code search — all ready."
            else:
                result = "Developer setup cancelled."
            eng.window.set_state("speaking", said=result)
            eng._speak(result)
            eng._last_spoke_at[0] = time.time() - 1.5
            eng._last_said[0]     = result
            eng.window.set_state("listening")
        eng.window.after(500, _launch_wizard)
    threading.Thread(target=_activate, daemon=True, name="GIL-DevMode").start()
    return True


# ── Developer fast-paths ──────────────────────────────────────────────────────

_GIT_STATUS_TRIGGERS  = {"git status","what changed","what's changed","any changes",
                          "uncommitted changes","working tree","repo status"}
_GIT_PUSH_TRIGGERS    = {"git push","push to origin","push it","push the code"}
_GIT_PULL_TRIGGERS    = {"git pull","pull latest","pull from origin","update the repo"}
_RUN_TEST_TRIGGERS    = {"run tests","run the tests","run test","test it","npm test",
                          "pytest","run unit tests","run specs"}
_DOCKER_PS_TRIGGERS   = {"docker ps","containers running","list containers",
                          "what containers","docker status"}

def _dev_git_status(text: str, lower: str, eng) -> bool:
    if not any(t in lower for t in _GIT_STATUS_TRIGGERS):
        return False
    log.debug("fast-path: git status")
    def _run():
        from ears import unmute
        from dev_git import status
        result = status()
        unmute()
        eng.window.set_state("speaking", said=result)
        eng._speak(result)
        eng._last_spoke_at[0] = time.time() - 1.5
        eng._last_said[0]     = result
        eng.window.set_state("listening")
    threading.Thread(target=_run, daemon=True, name="GIL-GitStatus").start()
    return True

def _dev_git_push(text: str, lower: str, eng) -> bool:
    if not any(t in lower for t in _GIT_PUSH_TRIGGERS):
        return False
    log.debug("fast-path: git push")
    def _run():
        from ears import unmute
        from dev_git import push
        result = push()
        unmute()
        eng.window.set_state("speaking", said=result)
        eng._speak(result)
        eng._last_spoke_at[0] = time.time() - 1.5
        eng._last_said[0]     = result
        eng.window.set_state("listening")
    threading.Thread(target=_run, daemon=True, name="GIL-GitPush").start()
    return True

def _dev_git_pull(text: str, lower: str, eng) -> bool:
    if not any(t in lower for t in _GIT_PULL_TRIGGERS):
        return False
    log.debug("fast-path: git pull")
    def _run():
        from ears import unmute
        from dev_git import pull
        result = pull()
        unmute()
        eng.window.set_state("speaking", said=result)
        eng._speak(result)
        eng._last_spoke_at[0] = time.time() - 1.5
        eng._last_said[0]     = result
        eng.window.set_state("listening")
    threading.Thread(target=_run, daemon=True, name="GIL-GitPull").start()
    return True

def _dev_run_tests(text: str, lower: str, eng) -> bool:
    if not any(t in lower for t in _RUN_TEST_TRIGGERS):
        return False
    log.debug("fast-path: run tests")
    def _run():
        from ears import unmute
        from dev_runner import run_tests
        ack = "Running tests..."
        eng.window.set_state("speaking", said=ack)
        eng._speak(ack)
        result = run_tests()
        unmute()
        eng.window.set_state("speaking", said=result)
        eng._speak(result)
        eng._last_spoke_at[0] = time.time() - 1.5
        eng._last_said[0]     = result
        eng.window.set_state("listening")
    threading.Thread(target=_run, daemon=True, name="GIL-RunTests").start()
    return True

def _dev_docker_ps(text: str, lower: str, eng) -> bool:
    if not any(t in lower for t in _DOCKER_PS_TRIGGERS):
        return False
    log.debug("fast-path: docker ps")
    def _run():
        from ears import unmute
        from dev_docker import containers
        result = containers()
        unmute()
        eng.window.set_state("speaking", said=result)
        eng._speak(result)
        eng._last_spoke_at[0] = time.time() - 1.5
        eng._last_said[0]     = result
        eng.window.set_state("listening")
    threading.Thread(target=_run, daemon=True, name="GIL-DockerPS").start()
    return True


# ── Master entry point ────────────────────────────────────────────────────────

def _fix_confirm(text: str, lower: str, eng) -> bool:
    """User said yes/no to a pending error-fix plan (see error_fixer.py)."""
    pending = getattr(eng, "_pending_fix", None)
    if not pending:
        return False

    _YES = {"yes", "yeah", "sure", "go ahead", "do it", "fix it", "run it",
            "ok", "okay", "yep", "please", "go"}
    _NO  = {"no", "nope", "nah", "dont", "don't", "cancel", "skip",
            "nevermind", "never mind", "stop", "not now"}
    words = set(lower.replace(".", "").replace(",", "").split())

    if words & _YES:
        eng._pending_fix = None
        log.debug("fast-path: fix confirmed (%d commands)", len(pending))

        def _run():
            from ears import unmute
            import error_fixer
            results = error_fixer.execute(pending)
            detail = "\n".join(
                f"{'✓' if ok else '✕'} `{cmd}`" + (f"\n```\n{out}\n```" if out else "")
                for cmd, ok, out in results)
            eng.window.add_chat_message("**Fix results:**\n" + detail, "gil")
            summary = error_fixer.summarize(results)
            unmute()
            eng.window.set_state("speaking", said=summary)
            eng._speak(summary)
            eng._last_spoke_at[0] = time.time() - 1.5
            eng._last_said[0]     = summary
            eng.window.set_state("listening")

        threading.Thread(target=_run, daemon=True, name="GIL-RunFix").start()
        return True

    if words & _NO:
        eng._pending_fix = None
        log.debug("fast-path: fix dismissed")
        msg = "Okay, skipped. The plan stays in the chat if you change your mind."
        eng.window.add_chat_message(msg, "gil")
        eng.window.set_state("listening")
        return True

    return False


_HANDLERS = [
    _fix_confirm,        # pending error-fix yes/no — must win when armed
    _dev_mode_toggle,    # check first — very specific phrases
    _recap_confirm,
    _whatsapp,
    _gmail,
    _face_enroll,
    _face_query,
    _camera_status,
    _camera_open,
    _camera_close,
    _vision,
    _image_gen,
    _webgen,
    # Developer fast-paths (checked before LLM for zero-latency dev commands)
    _dev_git_status,
    _dev_git_push,
    _dev_git_pull,
    _dev_run_tests,
    _dev_docker_ps,
    # General fast-paths
    _fast_url,
    _mode,
    _youtube,
    _project_open,
    _study,
    _subject_autodetect,
]


def process(text: str, lower: str, eng) -> bool:
    """
    Try every fast-path in order.
    Returns True if one handled the request (caller should return immediately).
    """
    for handler in _HANDLERS:
        if handler(text, lower, eng):
            return True
    return False
