"""
main.py — Project G.I.L.
Entry point, conversation state machine, and system tray.

Flow:
  PASSIVE  — always listening, waiting for the wake phrase "Hello G.I.L."
  ACTIVE   — full conversation mode, no wake phrase needed, no timeout
"""

import os
import sys
import time
import threading
import datetime
import winsound
import ctypes
from dotenv import load_dotenv

load_dotenv()

# ── Single-instance guard ─────────────────────────────────────────────────────
# Prevents the 4-voice bug caused by multiple GIL processes running at once.
_MUTEX = ctypes.windll.kernel32.CreateMutexW(None, True, "ProjectGIL_SingleInstance")
if ctypes.windll.kernel32.GetLastError() == 183:   # ERROR_ALREADY_EXISTS
    import ctypes as _ct
    _ct.windll.user32.MessageBoxW(0, "G.I.L. is already running.", "G.I.L.", 0)
    sys.exit(0)

from auth import run_login
import context_engine
import goal_tracker
import proactive
import session_manager
import preferences

# ── Module-level shared state ─────────────────────────────────────────────────
_pending_recap_global: list = [{}]          # shared between main and _audio_loop
_brain_ref:            list = []            # holds GILBrain instance; filled by _audio_loop
_processing_lock             = threading.Lock()   # one query at a time; shared with proactive
_last_addressed_at: list     = [0.0]        # filled on _audio_loop start; shared with proactive
_ATTENTION_SECS              = 45.0         # seconds of open attention window after last address

# ── Constants ─────────────────────────────────────────────────────────────────

HELLO_VARIANTS = {
    "hello", "helo", "hullo", "hallow", "halo",
    "hey", "hi", "hei", "yo", "ok", "okay",
}
GIL_VARIANTS = {
    "gill", "gil", "g.i.l", "gail",
    "jill", "jil", "phil", "gio", "geo",
    "deal", "feel", "heal", "neil", "real",   # common STT mishearings
    "guild",
    # "build" and "built" intentionally removed — they strip the action verb from
    # "build me a website" commands, breaking the fast-path detector.
}


# ── Conversation state machine ────────────────────────────────────────────────

class ConversationState:
    def __init__(self):
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def activate(self) -> None:
        self._active = True

    def deactivate(self) -> None:
        self._active = False


# ── System tray ───────────────────────────────────────────────────────────────

def _start_tray(window) -> None:
    """
    Launch a system tray icon in a daemon thread.
    Requires: pip install pystray Pillow
    Gracefully skips if packages are unavailable.
    """
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        print("[G.I.L.] pystray/Pillow not installed — tray icon disabled.")
        print("         Run: pip install pystray Pillow")
        return

    img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([2,  2,  62, 62], fill=(10,  10,  15))
    draw.ellipse([6,  6,  58, 58], outline=(0, 191, 255), width=3)
    draw.ellipse([24, 24, 40, 40], fill=(0, 191, 255))

    def _show(icon, _):
        window.after(0, window.show_window)

    def _settings(icon, _):
        window.after(0, window.open_settings)

    def _exit(icon, _):
        icon.stop()
        window.after(0, window._do_quit)

    menu = pystray.Menu(
        pystray.MenuItem("Show G.I.L.", _show, default=True),
        pystray.MenuItem("Settings",    _settings),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit",        _exit),
    )

    icon = pystray.Icon("GIL", img, "G.I.L. — Online", menu)
    threading.Thread(target=icon.run, daemon=True, name="GIL-Tray").start()

    # Closing the window hides to tray rather than quitting
    window.protocol("WM_DELETE_WINDOW", window.withdraw)
    print("[G.I.L.] Tray icon active. Close button now hides to tray.")


# ── Fast-path study subject detector (bypasses LLM entirely) ─────────────────

_STUDY_SUBJECTS: dict[str, tuple[str, str | None]] = {
    # keyword → (response, None)  — never auto-open, always ask first
    "computer science": ("Computer science — what topic are you on? Tell me and I'll explain it or find you a video.", None),
    "programming":      ("Programming — what language or concept? I can walk through it with you or find a video.", None),
    "python":           ("Python — what are you working on or stuck on? I can explain it or search a tutorial video.", None),
    "data structures":  ("Data structures — which one? I can explain it or find you a visual video on it.", None),
    "algorithms":       ("Algorithms — which one? Tell me and I'll break it down or find a good video.", None),
    "math":             ("Math — which topic exactly? I can explain it or find you a video — just say the word.", None),
    "algebra":          ("Algebra — what specifically? Equations, functions, factoring? Tell me and I'll explain or find a video.", None),
    "calculus":         ("Calculus — derivatives or integrals? I can explain it or find a 3Blue1Brown video — want that?", None),
    "trigonometry":     ("Trigonometry — unit circle, identities, or something else? Tell me what you need.", None),
    "physics":          ("Physics — which topic? Mechanics, waves, electricity? I can explain or find a video.", None),
    "chemistry":        ("Chemistry — what are you covering? I can explain it or search a video on it.", None),
    "biology":          ("Biology — which topic? I can explain or find a video — what are you studying?", None),
    "history":          ("History — which period or event? I can explain it or find a documentary or video.", None),
    "economics":        ("Economics — micro or macro? What's the specific topic?", None),
    "statistics":       ("Statistics — probability, distributions, hypothesis testing? Tell me the topic and I'll explain or find a video.", None),
    "cs50":             ("CS50 — which week or topic are you on? I can explain it or find the lecture video.", None),
}

_STUDY_TRIGGERS = {"studying", "doing", "learning", "working on", "stuck on", "need help with", "help me with"}


def _fast_study_resolve(text: str) -> tuple[str, str | None] | None:
    """Returns (speech, optional_url) if text mentions studying a known subject."""
    lower = text.lower()
    has_study = any(t in lower for t in _STUDY_TRIGGERS) or "i'm" in lower or "im" in lower
    if not has_study:
        return None
    for keyword, result in _STUDY_SUBJECTS.items():
        if keyword in lower:
            return result
    return None


# ── Fast-path URL resolver (bypasses LLM for obvious open commands) ───────────

_FAST_URLS: list[tuple[tuple[str, ...], str, str]] = [
    # (keywords_that_must_appear,  url,                           display_label)
    (("whatsapp",),                "https://web.whatsapp.com",    "WhatsApp"),
    (("youtube",),                 "https://youtube.com",         "YouTube"),
    (("gmail",),                   "https://mail.google.com",     "Gmail"),
    (("github",),                  "https://github.com",          "GitHub"),
    (("reddit",),                  "https://reddit.com",          "Reddit"),
    (("netflix",),                 "https://netflix.com",         "Netflix"),
    (("instagram",),               "https://instagram.com",       "Instagram"),
    (("twitter",), "https://twitter.com",                         "Twitter"),
    (("discord",),                 "https://discord.com/app",     "Discord"),
    (("linkedin",),                "https://linkedin.com",        "LinkedIn"),
    # Spotify intentionally excluded — always routed through spotify_control.py
]

_OPEN_TRIGGERS = {"open", "go to", "take me to", "navigate to", "show", "launch", "start"}


def _fast_url_resolve(text: str) -> tuple[str, str] | None:
    """
    Returns (url, label) if the command is a simple 'open X' for a known site.
    Returns None to let the LLM handle it.
    """
    lower = text.lower()
    has_trigger = any(t in lower for t in _OPEN_TRIGGERS)
    if not has_trigger:
        return None
    for keywords, url, label in _FAST_URLS:
        if all(k in lower for k in keywords):
            return url, label
    return None


# ── Fast-path YouTube search resolver ────────────────────────────────────────
import re as _re
import urllib.parse as _urlparse

_YT_PATS = [
    # "show me the Crimson Desert trailer", "find me a CS:GO gameplay video"
    _re.compile(
        r'(?:show|find|get|play|search(?:\s+youtube)?(?:\s+for)?)\s+(?:me\s+)?(?:the\s+|a\s+)?'
        r'(.+?)\s+(?:trailer|video|clip|gameplay)(?:\s+on\s+youtube)?$',
        _re.IGNORECASE,
    ),
    # "search YouTube for Crimson Desert", "look up CS:GO highlights"
    _re.compile(
        r'(?:look\s+up|search\s+(?:youtube\s+for|for)|youtube\s+search(?:\s+for)?)\s+'
        r'(?:the\s+)?(.+?)(?:\s+(?:trailer|video|clip|gameplay))?$',
        _re.IGNORECASE,
    ),
    # "Crimson Desert trailer on YouTube"
    _re.compile(
        r'^(.+?)\s+(?:trailer|video|clip)\s+(?:on\s+)?youtube$',
        _re.IGNORECASE,
    ),
]

_YT_INDICATORS = {
    "trailer", "youtube", "video on", "clip of", "gameplay",
    "find me a video", "show me a video", "search youtube", "look up",
}


def _fast_youtube_resolve(text: str) -> str | None:
    """Return a YouTube search URL if the user asks to find a specific video."""
    lower = text.lower()
    if not any(ind in lower for ind in _YT_INDICATORS):
        return None
    for pat in _YT_PATS:
        m = pat.search(text)
        if m:
            query = m.group(1).strip().strip(".,!?")
            query = _re.sub(r'^(the|a|an|some|me|us)\s+', '', query, flags=_re.IGNORECASE)
            if 2 < len(query) < 120:
                return "https://www.youtube.com/results?search_query=" + _urlparse.quote_plus(query)
    return None


# ── Greeting response filter (catches LLM greeting-loop output) ───────────────

_GREETING_PHRASES = (
    "how can i assist",
    "how may i assist",
    "how can i help",
    "i am g.i.l",
    "i'm g.i.l",
    "i am gil",
    "greetings",
    "hello, i am",
    "hi, i am",
    "at your service",
    "what can i do for you",
    "what would you like",
)


def _is_greeting_response(speech: str) -> bool:
    lower = speech.lower()
    return any(p in lower for p in _GREETING_PHRASES)


# ── Greeting builder ─────────────────────────────────────────────────────────

def _build_greeting(username: str) -> str:
    """Context-aware startup greeting powered by session_manager."""
    try:
        return session_manager.build_startup_greeting(username)
    except Exception:
        pass
    hour   = datetime.datetime.now().hour
    period = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
    return f"Good {period}, {username}. G.I.L. online."


# ── Audio loop ────────────────────────────────────────────────────────────────

def _audio_loop(username: str, window) -> None:
    from voice import speak
    from ears import listen_forever, set_passive
    from gil_brain import GILBrain
    from actions import execute_action, _build_app_index
    from credentials import initialize_credentials

    initialize_credentials()
    _build_app_index()

    _last_spoke_at: list[float] = [time.time()]
    _last_said:     list[str]   = [""]

    brain = GILBrain(username=username)
    conv  = ConversationState()

    # Start in active/listening mode immediately — no wake phrase needed on launch
    conv.activate()
    set_passive(False)

    greeting = _build_greeting(username)
    window.set_state("speaking", said=greeting)
    speak(greeting)
    _last_spoke_at[0] = time.time() - 1.5   # 1.5s cooldown so user can reply right away
    _last_said[0]     = greeting
    window.set_state("listening")

    _active_project:  list[str | None] = [None]   # currently active learning project
    _pending_recap:   list[dict]       = [{}]     # {"type": "wa"|"email", "items": [...]}
    _paused:          list[bool]       = [False]  # True = waiting for "Gil" to wake up
    _camera_win:      list             = [None]   # CameraWindow instance if open
    _gesture_watcher: list             = [None]   # GestureWatcher instance if camera is open

    # Seed the module-level attention timestamp so the first command works immediately
    _last_addressed_at[0] = time.time()

    def _start_gesture_watcher():
        try:
            from gestures import GestureWatcher
            if _gesture_watcher[0] and _gesture_watcher[0]._running:
                return
            gw = GestureWatcher(speak_fn=speak)
            gw.start()
            _gesture_watcher[0] = gw
        except Exception as _gw_exc:
            print(f"[G.I.L. GESTURE] Failed to start: {_gw_exc}")

    def _stop_gesture_watcher():
        if _gesture_watcher[0]:
            _gesture_watcher[0].stop()
            _gesture_watcher[0] = None

    _brain_ref.append(brain)   # expose for shutdown summary (declared in _gil_main scope)

    # Wire mode system — must be after _paused is defined
    import modes as _modes
    _modes.set_window_ref(window)
    _modes.set_speak_ref(speak)
    _modes.set_paused_callback(lambda v: _paused.__setitem__(0, v))

    # Wire reminders — speak callback + restore any that survived a restart
    import reminders as _reminders
    _reminders.set_speak_callback(speak)
    _reminders.set_window_ref(window)
    _reminders.restore_pending()

    _INSTANT_ACTIONS = {"open_url", "open_app", "web_search", "prompt_project"}

    def _word_overlap(a: str, b: str) -> float:
        import re as _re
        _strip = lambda s: set(_re.sub(r"[^\w\s]", "", s.lower()).split())
        wa, wb = _strip(a), _strip(b)
        if not wa or not wb:
            return 0.0
        return len(wa & wb) / max(len(wa), len(wb))

    def _is_addressed(text: str) -> bool:
        """Returns True if this utterance directly addresses GIL by name."""
        words = text.lower().replace(",", "").replace(".", "").split()
        for w in words:
            if w in GIL_VARIANTS or _edit_distance(w, "gil") <= 1:
                return True
        return False

    def _speak_wake_prompt() -> None:
        """Beep then ask 'What are we working on today?' — runs in a background thread."""
        def _go():
            winsound.Beep(1100, 180)
            time.sleep(0.15)
            phrase = "What are we working on today?"
            _last_said[0]     = phrase
            _last_spoke_at[0] = time.time()
            window.set_state("speaking", said=phrase)
            speak(phrase)
            _last_spoke_at[0] = time.time() - 1.5   # 1.5s cooldown so user can reply fast
            window.set_state("listening")
        threading.Thread(target=_go, daemon=True, name="GIL-Wake").start()

    def _run_trigger(trig: dict) -> None:
        """Execute a user-defined macro trigger — fires all actions in parallel."""
        print(f"[G.I.L. TRIGGER] Firing: '{trig['phrase']}'")

        actions = trig.get("actions", [])

        # Build a human label for the confirmation speech
        labels = []
        for act in actions:
            t      = act.get("type", "")
            target = act.get("target", "")
            if t == "open_url":
                host = target.split("//")[-1].split("/")[0].replace("www.", "")
                labels.append(host)
            elif t == "open_app":
                labels.append(target)
            elif t == "web_search":
                labels.append(f"searching {target[:20]}")
        if labels:
            label_str = ", ".join(labels[:4])
            confirm   = f"Got it. Opening {label_str}."
        else:
            confirm = "On it."

        window.set_state("speaking", said=confirm)
        speak(confirm)
        _last_spoke_at[0] = time.time()
        _last_said[0]     = confirm

        # Launch ALL actions simultaneously in separate threads
        threads = [
            threading.Thread(
                target=execute_action,
                args=(act.get("type", ""), act.get("target", "")),
                daemon=True,
                name=f"GIL-TrigAct-{i}",
            )
            for i, act in enumerate(actions)
        ]
        for th in threads:
            th.start()

        followup = trig.get("followup", "").strip()
        if followup:
            time.sleep(0.6)
            _last_said[0]     = followup
            _last_spoke_at[0] = time.time()
            window.set_state("speaking", said=followup)
            speak(followup)
            _last_spoke_at[0] = time.time() - 1.5

        window.set_state("listening")

    def _process(text: str) -> None:
        from ears import mute, unmute

        # DROP if another query is already in flight
        if not _processing_lock.acquire(blocking=False):
            print(f"[G.I.L.] Busy — ignored: '{text[:40]}'")
            return

        try:
            # POST-SPEECH COOLDOWN: reject utterances within 2s of GIL finishing speech
            if time.time() - _last_spoke_at[0] < 2.0:
                print(f"[G.I.L.] Echo suppressed (cooldown): '{text[:40]}'")
                return

            # SIMILARITY FILTER: only active within 8s — echoes can't last longer
            if _last_said[0] and time.time() - _last_spoke_at[0] < 8.0:
                if _word_overlap(text, _last_said[0]) > 0.5:
                    print(f"[G.I.L.] Echo suppressed (similarity): '{text[:40]}'")
                    return

            if not conv.active:
                window.after(0, window.show_window)
            window.set_state("processing", heard=text)
            mute()
            _do_process(text)
        finally:
            unmute()   # speak() no longer unmutes internally — always restore mic here
            _processing_lock.release()

    def _do_process(text: str) -> None:
        from ears import unmute
        lower = text.lower().replace(".", "").replace(",", "")

        # Fast-path: read pending recap aloud if user said yes
        _YES = {"yes", "yeah", "sure", "go ahead", "read them", "read it",
                "please", "ok", "okay", "yep", "do it", "read", "go"}
        _active_recap = _pending_recap[0] or _pending_recap_global[0]
        if _active_recap and any(w in lower.split() for w in _YES):
            recap = _active_recap
            _pending_recap[0] = {}
            _pending_recap_global[0] = {}
            def _read_recap():
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
                window.set_state("speaking", said=speech)
                speak(speech)
                _last_spoke_at[0] = time.time() - 1.5
                _last_said[0]     = speech
                window.set_state("listening")
            threading.Thread(target=_read_recap, daemon=True, name="GIL-ReadRecap").start()
            return

        _NO = {"no", "nope", "nah", "dont", "don't", "cancel", "skip", "nevermind", "never mind", "stop"}
        if _active_recap and any(w in lower.split() for w in _NO):
            _pending_recap[0] = {}
            _pending_recap_global[0] = {}
            window.set_state("listening")
            return

        # Fast-path: WhatsApp unread recap voice command
        _WA_RECAP_TRIGGERS = {
            "whatsapp messages", "whatsapp message", "whatsapp",
            "unread whatsapp", "any whatsapp", "check whatsapp",
            "read my whatsapp", "what's on whatsapp", "whats on whatsapp",
            "missed messages", "missed whatsapp",
        }
        if any(tr in lower for tr in _WA_RECAP_TRIGGERS):
            def _do_wa_recap():
                from ears import unmute
                unmute()   # free the mic NOW — scraping takes several seconds
                window.set_state("processing", heard=text)
                try:
                    import whatsapp_recap
                    msgs   = whatsapp_recap.get_unread_messages()
                    speech = whatsapp_recap.build_recap_speech(msgs)
                    if msgs:
                        _pending_recap[0] = {"type": "wa", "items": msgs}
                except Exception as exc:
                    print(f"[G.I.L. WHATSAPP] {exc}")
                    speech = "I couldn't reach WhatsApp right now. Want me to open it?"
                window.set_state("speaking", said=speech)
                speak(speech)
                _last_spoke_at[0] = time.time() - 1.5
                _last_said[0]     = speech
                window.set_state("listening")
            threading.Thread(target=_do_wa_recap, daemon=True, name="GIL-WARecap").start()
            return

        # Fast-path: Gmail unread recap voice command
        _GMAIL_RECAP_TRIGGERS = {
            "unread emails", "unread mail", "any emails", "check my email",
            "check email", "new emails", "new mail", "what emails",
            "read my emails", "read my mail", "inbox recap", "email recap",
            "missed emails", "missed mail",
        }
        if any(tr in lower for tr in _GMAIL_RECAP_TRIGGERS):
            def _do_recap():
                try:
                    import gmail_recap
                    emails = gmail_recap.get_unread_summary(max_results=5)
                    speech = gmail_recap.build_recap_speech(emails) if emails else "No unread emails."
                    if emails:
                        _pending_recap[0] = {"type": "email", "items": emails}
                except Exception:
                    speech = "I couldn't reach Gmail right now. Want me to open it instead?"
                window.set_state("speaking", said=speech)
                from ears import unmute
                unmute()
                speak(speech)
                _last_spoke_at[0] = time.time() - 1.5
                _last_said[0]     = speech
                window.set_state("listening")
            threading.Thread(target=_do_recap, daemon=True, name="GIL-EmailRecap").start()
            return

        # Fast-path: face enrollment — "remember my face", "scan my face"
        _FACE_ENROLL = {
            "remember my face", "enroll my face", "scan my face",
            "save my face", "learn my face", "memorize my face",
        }
        if any(tr in lower for tr in _FACE_ENROLL):
            def _do_enroll():
                from ears import unmute
                import tempfile
                import cv2 as _cv2
                from pathlib import Path as _Path

                ack = "Scanning your face — hold still."
                window.set_state("speaking", said=ack)
                unmute()
                speak(ack)
                time.sleep(1.8)

                frame_path = _Path(tempfile.gettempdir()) / "gil_cam_frame.jpg"
                if not frame_path.exists():
                    msg = "Open the camera first, then say remember my face."
                else:
                    try:
                        frame = _cv2.imread(str(frame_path))
                        if frame is None:
                            raise ValueError("blank frame")
                        from face_id import FaceID
                        ok, detail = FaceID().enroll(frame, username)
                        if ok:
                            msg = f"Got it. I'll recognize you as {username} from now on."
                        else:
                            msg = "Couldn't see your face clearly — try better lighting and look straight at the camera."
                            print(f"[G.I.L. FACE] Enroll failed: {detail}")
                    except Exception as exc:
                        msg = "Face enrollment failed — make sure the camera is open."
                        print(f"[G.I.L. FACE] Error: {exc}")
                window.set_state("speaking", said=msg)
                speak(msg)
                _last_spoke_at[0] = time.time() - 1.5
                _last_said[0]     = msg
                window.set_state("listening")
            threading.Thread(target=_do_enroll, daemon=True, name="GIL-FaceEnroll").start()
            return

        # Fast-path: face identity query — "who do you see", "do you recognize me"
        _FACE_QUERY = {
            "who do you see", "do you recognize me", "do you know me",
            "can you identify me", "who is there", "who's there",
            "is it me", "recognize my face", "do you see me", "who am i",
        }
        if any(tr in lower for tr in _FACE_QUERY):
            def _do_face_query():
                from ears import unmute
                import tempfile, json as _j
                import cv2 as _cv2
                from pathlib import Path as _Path

                face_file = _Path(tempfile.gettempdir()) / "gil_face_state.json"
                if face_file.exists():
                    try:
                        state = _j.loads(face_file.read_bytes())
                        nm  = state.get("name")
                        st  = state.get("status")
                        if st == "match" and nm:
                            msg = f"Yes — I recognize you, {nm}."
                        elif st == "unknown":
                            msg = "I see someone, but I don't recognize them."
                        else:
                            msg = "I can't see a face clearly right now."
                        unmute()
                        window.set_state("speaking", said=msg)
                        speak(msg)
                        _last_spoke_at[0] = time.time() - 1.5
                        _last_said[0]     = msg
                        window.set_state("listening")
                        return
                    except Exception:
                        pass

                frame_path = _Path(tempfile.gettempdir()) / "gil_cam_frame.jpg"
                if not frame_path.exists():
                    msg = "Open the camera first so I can see you."
                else:
                    try:
                        frame = _cv2.imread(str(frame_path))
                        from face_id import FaceID
                        fid = FaceID()
                        if not fid.has_enrolled():
                            msg = "I haven't learned your face yet. Say 'remember my face' to enroll."
                        else:
                            r = fid.identify(frame)
                            if r["status"] == "match":
                                msg = f"I see {r['name']}."
                            elif r["status"] == "unknown":
                                msg = "I see someone, but I don't recognize them."
                            else:
                                msg = "I can't detect a face — try looking directly at the camera."
                    except Exception as exc:
                        msg = "Face recognition isn't available right now."
                        print(f"[G.I.L. FACE] Query error: {exc}")
                unmute()
                window.set_state("speaking", said=msg)
                speak(msg)
                _last_spoke_at[0] = time.time() - 1.5
                _last_said[0]     = msg
                window.set_state("listening")
            threading.Thread(target=_do_face_query, daemon=True, name="GIL-FaceQuery").start()
            return

        # Fast-path: camera STATUS query — must be checked BEFORE _cam_open because
        # "is the camera open?" contains "open" and would wrongly fire the open path.
        _cam_status_kw = any(p in lower for p in (
            "is the camera open", "is the camera on", "is the camera showing",
            "is the camera visible", "is the camera running", "is your camera on",
            "is your camera active", "can you see me", "are you seeing me",
            "are you looking at me", "is camera open", "is camera on",
        ))
        if _cam_status_kw:
            import ctypes as _ct2
            _u32       = _ct2.windll.user32
            _hwnd      = _u32.FindWindowW(None, "G.I.L. Vision")
            _streaming = bool(_camera_win[0] and _camera_win[0].is_streaming())
            # IsWindowVisible + not IsIconic = actually on screen, not hidden or minimized
            _visible   = bool(
                _hwnd and _u32.IsWindowVisible(_hwnd) and not _u32.IsIconic(_hwnd)
            )
            if _streaming and _visible:
                speech = "Yes — the camera is open and visible on your screen."
            elif _streaming and not _visible:
                speech = "Camera is running but the window isn't visible — bringing it up now."
                if _camera_win[0]:
                    threading.Thread(target=_camera_win[0].bring_to_front,
                                     daemon=True, name="GIL-CamFocus").start()
            else:
                speech = "No — the camera is closed. Say 'open camera' to start it."
            window.set_state("speaking", said=speech)
            speak(speech)
            _last_spoke_at[0] = time.time() - 1.5
            _last_said[0]     = speech
            window.set_state("listening")
            return

        # Fast-path: open camera window
        # Keyword-based: "camera" + open verb, or exact phrases like "open your eyes"
        _cam_kw   = "camera" in lower or "webcam" in lower
        _words    = set(lower.split())
        _cam_open = (
            _cam_kw
            and any(w in lower for w in ("open", "show", "start", "enable", "activate", "turn on", "launch", "use"))
            and not any(w in lower for w in ("turn off", "close", "hide", "stop", "disable"))
            and not lower.startswith(("is ", "are ", "can ", "does ", "do ", "was "))
        ) or any(p in lower for p in (
            "open your eyes", "use your eyes", "turn on your camera",
            "turn on the camera", "your camera on", "start your camera",
        ))
        if _cam_open:
            # Clean up any stale reference (not alive, or alive but no frames after startup)
            if _camera_win[0] and not _camera_win[0].is_alive():
                _camera_win[0] = None
            # is_alive() has a 3s grace period; is_streaming() requires actual frames
            if _camera_win[0] and not _camera_win[0].is_streaming():
                _camera_win[0]._dead = True
                _camera_win[0] = None

            if _camera_win[0]:
                # Genuinely streaming — bring the window to front
                try:
                    import ctypes as _ct
                    for _title in ("G.I.L. Vision",):
                        _hwnd = _ct.windll.user32.FindWindowW(None, _title)
                        if _hwnd:
                            _ct.windll.user32.ShowWindow(_hwnd, 9)
                            _ct.windll.user32.SetForegroundWindow(_hwnd)
                            break
                except Exception:
                    pass
                speech = "Camera's already up. Say 'what do you see' or 'what am I holding' to analyze."
            else:
                def _cam_closed_cb():
                    if _camera_win[0] is _this_cam:
                        _camera_win[0] = None
                    _stop_gesture_watcher()
                try:
                    from eyes import CameraWindow
                    _this_cam = CameraWindow(on_close=_cam_closed_cb)
                    _camera_win[0] = _this_cam
                    _start_gesture_watcher()
                    threading.Thread(target=_this_cam.bring_to_front,
                                     daemon=True, name="GIL-CamFocus").start()
                    speech = "Camera's up."
                except Exception as exc:
                    print(f"[G.I.L. EYES] Camera failed: {exc}")
                    speech = "Couldn't open the camera. Make sure it's connected."

            window.set_state("speaking", said=speech)
            speak(speech)
            _last_spoke_at[0] = time.time() - 1.5
            _last_said[0]     = speech
            window.set_state("listening")
            return

        # Fast-path: close camera window
        _cam_close = (
            _cam_kw and any(w in lower for w in ("close", "hide", "stop", "disable", "turn off", "shut"))
        ) or "close your eyes" in lower
        if _cam_close:
            from eyes import CameraWindow as _CW, _FRAME_FILE as _FF, _KILL_FILE as _KF
            _cam_actually_running = (
                (_camera_win[0] and _camera_win[0].is_alive())
                or (_FF.exists() and time.time() - _FF.stat().st_mtime < 2.0)
            )
            if _cam_actually_running:
                if _camera_win[0]:
                    _camera_win[0].close()
                else:
                    # Orphaned camera_viewer.py — kill via kill file directly
                    try:
                        _KF.write_text("kill")
                        import time as _t; _t.sleep(0.4)
                        _FF.unlink(missing_ok=True)
                        _KF.unlink(missing_ok=True)
                    except Exception:
                        pass
                _camera_win[0] = None
                _stop_gesture_watcher()
                speech = "Camera closed."
            else:
                speech = "Camera isn't open."
            window.set_state("speaking", said=speech)
            speak(speech)
            _last_spoke_at[0] = time.time() - 1.5
            _last_said[0]     = speech
            window.set_state("listening")
            return

        # Fast-path: identify / vision query
        # Keyword-based: "what is/are/am", "identify", "what do you see", etc.
        _identify_kw = (
            any(p in lower for p in (
                "what is this", "what is that", "what's this", "what's that",
                "what are these", "what am i holding", "what do you see",
                "identify this", "identify that", "identify what",
                "describe what you see", "what brand", "what model", "what color is this",
                "can you read this", "what does it say", "read what",
                "look at this", "look at that", "take a look",
                "look at what i", "what do i have", "recognize this",
                "look at the items", "items i am holding", "items i'm holding",
                "what's in my hand", "what is in my hand", "look at my hand",
                "what are my hands", "see what i", "look at me",
            ))
        )
        if _identify_kw:
            _q = text
            def _do_identify(q=_q):
                from ears import unmute

                # Tell user we're working — API call takes a few seconds
                ack = "On it."
                window.set_state("speaking", said=ack)
                speak(ack)

                # Grab frame: from live window if open; open it first if not
                cam_open = _camera_win[0] and _camera_win[0].is_streaming()
                if not cam_open:
                    def _cam_closed_cb():
                        if _camera_win[0] is _this_cam:
                            _camera_win[0] = None
                    try:
                        from eyes import CameraWindow
                        _this_cam = CameraWindow(on_close=_cam_closed_cb)
                        _camera_win[0] = _this_cam
                        threading.Thread(target=_this_cam.bring_to_front,
                                         daemon=True, name="GIL-CamFocus").start()
                        cam_open = True
                    except Exception as _exc:
                        print(f"[G.I.L. EYES] Camera open failed: {_exc}")

                if cam_open and _camera_win[0]:
                    frame = _camera_win[0].get_current_frame()
                    print(f"[G.I.L. EYES] Got frame from viewer: {len(frame) if frame else 0} bytes")
                else:
                    from eyes import capture_frame
                    frame = capture_frame()
                    print(f"[G.I.L. EYES] One-shot capture: {len(frame) if frame else 0} bytes")

                if not frame:
                    result = "I can't see anything — open the camera first by saying 'open camera'."
                else:
                    try:
                        from eyes import analyze_frame
                        result = analyze_frame(frame, question=q)
                        print(f"[G.I.L. EYES] Analysis: {result[:100]}")
                    except Exception as exc:
                        print(f"[G.I.L. EYES] analyze_frame error: {exc}")
                        result = "Vision analysis failed — check the terminal for details."

                unmute()
                window.set_state("speaking", said=result)
                speak(result)
                _last_spoke_at[0] = time.time() - 1.5
                _last_said[0]     = result
                window.set_state("listening")
            threading.Thread(target=_do_identify, daemon=True, name="GIL-Identify").start()
            return

        # Fast-path: website generation — intercepts BEFORE brain sees it,
        # so the LLM never has a chance to misroute to the `build` (claude CLI) action.
        _web_noun = any(p in lower for p in (
            "website", "web site", "webpage", "web page",
            "landing page", "landing", "web app", "web application",
            "html page", "html site", "front page", "home page",
            "site", "homepage", "front end", "frontend",
        ))
        _web_verb = any(w in lower for w in (
            "build", "create", "make", "generate", "design", "write",
            "want", "need", "give me", "show me", "get me",
        ))
        # Extra safety: catch "build * website/page" even with words in between
        _web_direct = bool(_re.search(
            r"\b(build|create|make|generate|design|want|need)\b.{0,40}\b(website|webpage|landing|web app|site|homepage|frontend)\b",
            lower,
        ))
        if _web_noun and _web_verb or _web_direct:
            _web_text = text
            def _do_webgen(utterance=_web_text):
                from ears import unmute
                ack = "On it — give me about 30 seconds."
                window.set_state("speaking", said=ack)
                _last_spoke_at[0] = time.time() + 120  # block echo before & during speech
                speak(ack)
                _last_spoke_at[0] = time.time()
                _last_said[0]     = ack
                window.show_webgen_progress()
                try:
                    from webgen import generate as _wg, generate_for_project as _wgp, _find_web_project
                    proj = _find_web_project(utterance)
                    result = _wgp(proj) if proj else _wg(utterance)
                except Exception as exc:
                    result = f"Website generation failed — {exc.__class__.__name__}."
                    _last_spoke_at[0] = time.time()   # reset block so GIL doesn't go deaf
                window.close_webgen_progress()
                unmute()
                window.set_state("speaking", said=result)
                speak(result)
                _last_spoke_at[0] = time.time() - 1.5
                _last_said[0]     = result
                window.set_state("listening")
            threading.Thread(target=_do_webgen, daemon=True, name="GIL-WebGen").start()
            return

        # Fast-path: bypass the LLM for obvious open-URL commands
        fast = _fast_url_resolve(text)
        if fast:
            url, label = fast
            from actions import open_url
            threading.Thread(target=open_url, args=(url,), daemon=True).start()
            speech = f"{label}."
            window.set_state("speaking", said=speech)
            speak(speech)
            _last_spoke_at[0] = time.time()
            _last_said[0]     = speech
            window.set_state("listening")
            return

        # Fast-path: mode commands — bypass LLM to prevent misinterpretation
        _MODE_MAP = {
            "do not disturb": "dnd", "dnd mode": "dnd", "dnd": "dnd",
            "study mode": "study", "studying mode": "study",
            "fun mode": "fun",
            "normal mode": "normal", "reset mode": "normal",
        }
        _matched_mode = next((v for k, v in _MODE_MAP.items() if k in lower), None)
        if _matched_mode:
            from modes import set_mode as _set_mode
            result = _set_mode(_matched_mode)
            window.set_state("speaking", said=result)
            speak(result)
            _last_spoke_at[0] = time.time() - 1.5
            _last_said[0]     = result
            window.set_state("listening")
            return

        # Fast-path: YouTube search — "show me the X trailer / find a video about X"
        yt_url = _fast_youtube_resolve(text)
        if yt_url:
            from actions import open_url
            threading.Thread(target=open_url, args=(yt_url,), daemon=True).start()
            speech = "Here you go."
            window.set_state("speaking", said=speech)
            unmute()
            speak(speech)
            _last_spoke_at[0] = time.time()
            _last_said[0]     = speech
            window.set_state("listening")
            if _active_project[0]:
                try:
                    from learning_projects import add_resource
                    add_resource(_active_project[0], "video", yt_url, text[:80])
                except Exception:
                    pass
            return

        # Auto-detect active learning project from subject keywords
        _SUBJECT_KEYWORDS = {
            "math": "Math", "algebra": "Algebra", "calculus": "Calculus",
            "geometry": "Geometry", "physics": "Physics", "chemistry": "Chemistry",
            "biology": "Biology", "history": "History", "computer science": "Computer Science",
            "programming": "Programming", "python": "Python", "economics": "Economics",
            "statistics": "Statistics", "trigonometry": "Trigonometry",
        }
        for kw, proj_name in _SUBJECT_KEYWORDS.items():
            if kw in lower and any(t in lower for t in {
                "studying", "learning", "working on", "doing", "help me with", "explain", "teach"
            }):
                if _active_project[0] != proj_name:
                    _active_project[0] = proj_name
                    print(f"[G.I.L.] Active project: {proj_name}")
                    try:
                        from learning_projects import load, get_context_summary
                        ctx = get_context_summary(proj_name)
                        if ctx:
                            print(f"[G.I.L.] Loaded project context:\n{ctx}")
                    except Exception:
                        pass
                break

        # Fast-path: open / continue a learning project
        _has_project_word = "project" in lower or "projects" in lower
        _has_open_word    = any(w in lower for w in {
            "open", "show", "enter", "access", "continue",
            "go to", "load", "see", "view", "bring up",
        })
        if _has_project_word and _has_open_word:
            try:
                from learning_projects import list_all, get_context_summary, load
                projects = list_all()
                if not projects:
                    speech = "No learning projects saved yet. Start one by saying you're studying something."
                else:
                    # Fuzzy match: exact word overlap + substring fallback
                    _SKIP = {"my", "the", "a", "an", "i", "open", "show", "enter",
                             "access", "continue", "load", "see", "view", "project",
                             "projects", "bring", "up", "go", "to", "want", "please"}
                    matched = None
                    best_score = 0
                    lower_words = {w for w in lower.split() if w not in _SKIP and len(w) > 2}
                    for p in projects:
                        name_words = {w for w in p["name"].lower().split() if len(w) > 2}
                        # Exact word overlap
                        exact = len(name_words & lower_words)
                        # Substring: user word appears inside a project name word (or vice versa)
                        sub = sum(
                            1 for uw in lower_words
                            for nw in name_words
                            if uw in nw or nw in uw
                        )
                        score = exact * 2 + sub   # exact match worth more
                        if score > best_score:
                            best_score = score
                            matched = p["name"]
                    if best_score == 0:
                        matched = None
                    if matched:
                        _active_project[0] = matched
                        data = load(matched)
                        last      = data["sessions"][-1] if data["sessions"] else None
                        res_count = len(data.get("resources", []))
                        models    = [r for r in data.get("resources", [])
                                     if r.get("type") == "3d_model"]
                        studios   = [r for r in data.get("resources", [])
                                     if r.get("type") == "3d_studio"]
                        # Open the project view window
                        window.after(0, lambda m=matched: window.open_project_view(m))
                        # Re-show 3D hologram if one was created
                        if models:
                            shape = models[0].get("url", "sphere")
                            window.show_3d(shape)
                        # Reopen 3D studio files in browser
                        if studios:
                            def _reopen_studios(s=studios):
                                import time as _t
                                from studio3d import reopen_studio
                                for r in s[:3]:
                                    reopen_studio(r.get("url", ""))
                                    _t.sleep(1.5)
                            threading.Thread(target=_reopen_studios, daemon=True,
                                             name="GIL-ReopenStudio").start()
                        if last:
                            convs  = last["conversations"]
                            last_q = convs[-1]["user"][:60] if convs else "—"
                            has_3d = bool(models or studios)
                            speech = (f"Opening {matched} — {len(data['sessions'])} sessions saved. "
                                      + (f"Your 3D creation is back up." if has_3d else f"Last topic: {last_q}."))
                        else:
                            speech = f"{matched} project opened. What do you want to work on?"
                    else:
                        names = ", ".join(p["name"] for p in projects[:6])
                        speech = f"Your learning projects: {names}. Which one do you want to open?"
                window.set_state("speaking", said=speech)
                from ears import unmute
                unmute()
                speak(speech)
                _last_spoke_at[0] = time.time() - 1.5
                _last_said[0] = speech
                window.set_state("listening")
                return
            except Exception as exc:
                print(f"[G.I.L.] Project open error: {exc}")

        # Fast-path: study subject — instant tutor response, no LLM
        study = _fast_study_resolve(text)
        if study:
            speech, url = study
            # Auto-set active learning project from subject keyword
            for kw, proj_name in _SUBJECT_KEYWORDS.items():
                if kw in lower:
                    if _active_project[0] != proj_name:
                        _active_project[0] = proj_name
                        print(f"[G.I.L.] Active project set: {proj_name}")
                    break
            if url:
                from actions import open_url
                threading.Thread(target=open_url, args=(url,), daemon=True).start()
            window.set_state("speaking", said=speech)
            from ears import unmute
            speak(speech)
            _last_spoke_at[0] = time.time()
            _last_said[0]     = speech
            window.set_state("listening")
            # Save to learning project
            if _active_project[0]:
                try:
                    from learning_projects import add_conversation
                    add_conversation(_active_project[0], text, speech)
                except Exception:
                    pass
            return

        # Inject active project context into brain
        project_ctx = ""
        if _active_project[0]:
            try:
                from learning_projects import get_context_summary
                project_ctx = get_context_summary(_active_project[0])
            except Exception:
                pass

        # Learn preferences from what the user said
        try:
            ctx = context_engine.get_active_context()
            preferences.learn_from_exchange(text)
            goal_tracker.update_from_reply(
                text, app=ctx.get("app", ""), file=ctx.get("file", "")
            )
            if goal_tracker.get_goal_text():
                session_manager.record_session_goal(goal_tracker.get_goal_text())
            proactive.record_interaction()
        except Exception:
            pass

        # Tell the LLM the real camera state so it can't hallucinate
        _cam_state = (
            "streaming — G.I.L. Vision window is open and active on screen"
            if (_camera_win[0] and _camera_win[0].is_streaming())
            else "closed — no camera window is open"
        )

        try:
            response = brain.query(text, project_context=project_ctx, camera_state=_cam_state)
        except Exception as exc:
            print(f"[G.I.L. BRAIN ERROR] {exc}")
            unmute()
            window.set_state("listening" if conv.active else "standby")
            return

        if not response:
            unmute()
            window.set_state("listening")
            return
        speech = response.get("speech", "")

        if _is_greeting_response(speech):
            print(f"[G.I.L.] Suppressed greeting response: {speech!r}")
            speech = ""

        action = response.get("action")
        target = response.get("target") or ""
        extra_actions = response.get("extra_actions") or []

        # ── Final backstop: reroute "build"/"prompt_project" → "build_website"
        # if the user's original speech OR the LLM's target contains any web word.
        # The fast-path above catches most cases, but misses phrasings like
        # "build a site" or "build the water company project" where the LLM strips
        # "website" from the target. This check uses the ORIGINAL SPOKEN TEXT so it
        # is immune to the LLM dropping website-related words from the target.
        _WEBGEN_WORDS = {
            "website", "web site", "webpage", "web page", "landing page", "landing",
            "web app", "web application", "html", "frontend", "front-end", "site",
            "homepage", "home page", "front end",
        }
        if action in ("build", "prompt_project") and (
            any(w in lower for w in _WEBGEN_WORDS)
            or any(w in target.lower() for w in _WEBGEN_WORDS)
        ):
            print(f"[G.I.L.] Rerouting '{action}' -> build_website (web word detected in speech/target)")
            action = "build_website"
            target = target or text

        if action == "show_settings":
            speech = "Done."

        # Fire safe instant actions before speak() blocks
        if action in _INSTANT_ACTIONS:
            threading.Thread(
                target=_dispatch_instant, args=(action, target),
                daemon=True, name=f"GIL-{action}"
            ).start()
            # Save resource to active project
            if _active_project[0] and target and action in ("open_url", "web_search"):
                try:
                    from learning_projects import add_resource
                    kind = "video" if "youtube" in target.lower() else "url"
                    add_resource(_active_project[0], kind, target, target[:80])
                except Exception:
                    pass

        if speech:
            _last_said[0] = speech
            _last_addressed_at[0] = time.time()   # GIL responded → keep attention window open
            window.set_state("speaking", said=speech)
            delivered = speak(speech)
            if delivered:
                _last_spoke_at[0] = time.time()
            else:
                unmute()   # speak was skipped — make sure mic is live
            try:
                from memory import extract_memories_background
                extract_memories_background(text, speech)
                preferences.learn_from_exchange(text, speech)
            except Exception:
                pass
            # Auto-save to active learning project
            if _active_project[0]:
                try:
                    from learning_projects import add_conversation
                    add_conversation(_active_project[0], text, speech)
                except Exception:
                    pass
        else:
            unmute()

        if response.get("report"):
            print(f"\n[G.I.L. REPORT]\n{response['report']}\n")

        # Sequential actions run after speech
        if action == "save_credential":
            _handle_save_credential(target, speak, window)
        elif action == "list_credentials":
            _handle_list_credentials(speak)
        elif action == "delete_credential":
            _handle_delete_credential(target, speak)
        elif action == "show_settings":
            window.after(0, window.open_settings)
        elif action == "create_project":
            _handle_create_project(target, window)
        elif action == "add_task":
            _handle_add_task(target, window)
        elif action == "complete_task":
            _handle_complete_task(target, window)
        elif action == "list_tasks":
            window.refresh_tasks()
            window.after(0, window.show_window)
        elif action == "system_vitals":
            execute_action("system_vitals", target)
        elif action == "sign_in":
            execute_action("sign_in", target)
        elif action == "take_screenshot":
            execute_action("take_screenshot", target)
        elif action == "create_3d":
            _proj_for_3d = _active_project[0] or ""
            threading.Thread(target=_handle_create_3d, args=(target, _proj_for_3d),
                             daemon=True, name="GIL-3DStudio").start()
        elif action in ("build", "open_terminal", "prompt_project"):
            threading.Thread(target=_dispatch_instant, args=(action, target),
                             daemon=True, name=f"GIL-{action}").start()
        elif action in ("focus_window", "arrange_windows", "close_window",
                        "minimize_all", "maximize_window", "open_file",
                        "read_file", "list_directory", "find_file",
                        "set_clipboard", "get_clipboard"):
            result = execute_action(action, target)
            if result and action in ("read_file", "list_directory", "find_file", "get_clipboard"):
                print(f"[G.I.L. ACTIONS] Result: {result[:200]}")
        elif action == "tv":
            threading.Thread(target=lambda: execute_action("tv", target),
                             daemon=True, name="GIL-TV").start()

        elif action == "set_mode":
            execute_action("set_mode", target)

        elif action in ("pc", "pc_sleep", "pc_lock", "pc_restart", "pc_shutdown"):
            threading.Thread(
                target=lambda a=action: execute_action(a, target),
                daemon=True, name="GIL-PC",
            ).start()

        elif action == "pc_volume":
            threading.Thread(
                target=lambda: execute_action("pc_volume", target),
                daemon=True, name="GIL-PCVol",
            ).start()

        elif action == "weather":
            def _do_weather():
                from ears import unmute
                result = execute_action("weather", target)
                unmute()
                if result:
                    window.set_state("speaking", said=result)
                    speak(result)
                    _last_spoke_at[0] = time.time()
                    _last_said[0]     = result
                window.set_state("listening")
            threading.Thread(target=_do_weather, daemon=True, name="GIL-Weather").start()
            return   # thread handles set_state("listening")

        elif action == "reminder":
            result = execute_action("reminder", target)
            if result and result != speech:
                window.set_state("speaking", said=result)
                speak(result)
                _last_spoke_at[0] = time.time()
                _last_said[0]     = result

        elif action == "list_reminders":
            def _do_reminders():
                from ears import unmute
                result = execute_action("list_reminders", "")
                unmute()
                if result:
                    window.set_state("speaking", said=result)
                    speak(result)
                    _last_spoke_at[0] = time.time()
                    _last_said[0]     = result
                window.set_state("listening")
            threading.Thread(target=_do_reminders, daemon=True, name="GIL-Reminders").start()
            return

        elif action == "note":
            execute_action("note", target)

        elif action == "list_notes":
            result = execute_action("list_notes", "")
            if result:
                print(f"[G.I.L. NOTES]\n{result}")
                window.set_state("speaking", said=result)
                speak(result)
                _last_spoke_at[0] = time.time()
                _last_said[0]     = result

        elif action == "clip_history":
            result = execute_action("clip_history", "")
            if result:
                print(f"[G.I.L. CLIPBOARD]\n{result}")
                window.set_state("speaking", said=result)
                speak(result)
                _last_spoke_at[0] = time.time()
                _last_said[0]     = result

        elif action == "spotify":
            def _do_spotify():
                result = execute_action("spotify", target)
                print(f"[G.I.L. SPOTIFY] Result: {result}")
                # Speak the real result only if it differs from what brain already said
                # (errors, "couldn't find", etc. should always be reported back)
                if result and result != speech and any(w in result.lower() for w in (
                    "couldn't", "failed", "not", "error", "isn't", "no ", "check"
                )):
                    window.set_state("speaking", said=result)
                    speak(result)
                    _last_spoke_at[0] = time.time()
                    _last_said[0]     = result
                    window.set_state("listening")
            threading.Thread(target=_do_spotify, daemon=True, name="GIL-Spotify").start()

        elif action == "briefing":
            def _do_briefing():
                from ears import unmute
                result = execute_action("briefing", target)
                unmute()
                if result:
                    window.set_state("speaking", said=result)
                    speak(result)
                    _last_spoke_at[0] = time.time()
                    _last_said[0]     = result
                window.set_state("listening")
            threading.Thread(target=_do_briefing, daemon=True, name="GIL-Briefing").start()
            return   # thread handles set_state("listening")

        elif action in ("calendar", "add_event", "news", "my_location"):
            def _do_fetch(act=action, tgt=target):
                from ears import unmute
                result = execute_action(act, tgt)
                unmute()
                if result:
                    window.set_state("speaking", said=result)
                    speak(result)
                    _last_spoke_at[0] = time.time()
                    _last_said[0]     = result
                window.set_state("listening")
            threading.Thread(target=_do_fetch, daemon=True, name=f"GIL-{action}").start()
            return

        elif action in ("nearby", "directions", "food_delivery", "open_article"):
            threading.Thread(
                target=lambda: execute_action(action, target),
                daemon=True, name=f"GIL-{action}"
            ).start()

        elif action == "open_camera":
            if _camera_win[0] and _camera_win[0].is_streaming():
                # Already streaming — just bring it forward
                threading.Thread(target=_camera_win[0].bring_to_front,
                                 daemon=True, name="GIL-CamFocus").start()
            else:
                _cam_ref = [None]  # filled by thread so _cam_closed_cb sees the real instance
                def _cam_closed_cb():
                    if _camera_win[0] is _cam_ref[0]:
                        _camera_win[0] = None
                    _stop_gesture_watcher()
                def _open_and_confirm():
                    from ears import unmute
                    try:
                        from eyes import CameraWindow
                        _this_cam2 = CameraWindow(on_close=_cam_closed_cb)
                        _cam_ref[0] = _this_cam2
                        _camera_win[0] = _this_cam2
                        _start_gesture_watcher()
                        found = _this_cam2.bring_to_front()
                        if not found:
                            unmute()
                            _fail = "Camera didn't open — make sure nothing else is using it."
                            window.set_state("speaking", said=_fail)
                            speak(_fail)
                            _last_spoke_at[0] = time.time() - 1.5
                            _last_said[0]     = _fail
                            window.set_state("listening")
                    except Exception as exc:
                        unmute()
                        _fail = "Camera failed to start. Make sure it's connected."
                        print(f"[G.I.L. EYES] Camera failed: {exc}")
                        window.set_state("speaking", said=_fail)
                        speak(_fail)
                        _last_spoke_at[0] = time.time() - 1.5
                        _last_said[0]     = _fail
                        window.set_state("listening")
                try:
                    threading.Thread(target=_open_and_confirm,
                                     daemon=True, name="GIL-CamOpen").start()
                except Exception as exc:
                    print(f"[G.I.L. EYES] Thread failed: {exc}")

        elif action == "close_camera":
            if _camera_win[0]:
                try:
                    _camera_win[0].close()
                except Exception:
                    pass
                _camera_win[0] = None
                _stop_gesture_watcher()

        elif action == "build_website":
            _wg_desc = target or speech
            _wg_orig = text
            def _do_webgen_action(desc=_wg_desc, utterance=_wg_orig):
                from ears import unmute
                ack = "On it — give me about 30 seconds."
                window.set_state("speaking", said=ack)
                _last_spoke_at[0] = time.time() + 120
                speak(ack)
                _last_spoke_at[0] = time.time()
                _last_said[0]     = ack
                window.show_webgen_progress()
                try:
                    from webgen import generate as _wg, generate_for_project as _wgp, _find_web_project
                    proj = _find_web_project(utterance)
                    result = _wgp(proj) if proj else _wg(desc)
                except Exception as exc:
                    result = f"Website generation failed — {exc.__class__.__name__}."
                    _last_spoke_at[0] = time.time()   # reset block so GIL doesn't go deaf
                window.close_webgen_progress()
                unmute()
                window.set_state("speaking", said=result)
                speak(result)
                _last_spoke_at[0] = time.time() - 1.5
                _last_said[0]     = result
                window.set_state("listening")
            threading.Thread(target=_do_webgen_action, daemon=True, name="GIL-WebGen").start()
            return

        elif action == "look":
            def _do_look_action(q=target):
                from ears import unmute
                from eyes import look
                result = look(question=q)
                unmute()
                if result and result != speech:
                    window.set_state("speaking", said=result)
                    speak(result)
                    _last_spoke_at[0] = time.time() - 1.5
                    _last_said[0]     = result
                window.set_state("listening")
            threading.Thread(target=_do_look_action, daemon=True, name="GIL-Look").start()
            return

        # ── Execute any extra actions returned by multi-task brain response ──────
        _WEBGEN_WORDS_SET = {
            "website", "web site", "webpage", "web page", "landing page", "landing",
            "web app", "web application", "html", "frontend", "front-end", "site",
            "homepage", "home page", "front end",
        }
        for _ea in extra_actions:
            _ea_action = _ea.get("action")
            _ea_target = _ea.get("target") or ""
            if not _ea_action:
                continue
            # Apply same website reroute to extra actions
            if _ea_action in ("build", "prompt_project") and (
                any(w in lower for w in _WEBGEN_WORDS_SET)
                or any(w in _ea_target.lower() for w in _WEBGEN_WORDS_SET)
            ):
                _ea_action = "build_website"
                _ea_target = _ea_target or text
            print(f"[G.I.L.] Extra action: {_ea_action} → {_ea_target[:60]}")
            threading.Thread(
                target=_dispatch_instant, args=(_ea_action, _ea_target),
                daemon=True, name=f"GIL-extra-{_ea_action}",
            ).start()

        window.set_state("listening")

    def _dispatch_instant(action: str, target: str) -> None:
        """Execute instant (fire-and-forget) actions — runs in its own thread."""
        if action == "build":
            # If the description is about a website, redirect to webgen instead of
            # opening a terminal — no CMD window, no claude CLI dependency.
            _WEB_SIGNALS = {
                "website", "webpage", "landing", "web app", "web application",
                "html", "frontend", "front-end", "ui", "home page", "homepage",
            }
            if any(w in target.lower() for w in _WEB_SIGNALS):
                def _build_as_web(t=target):
                    from ears import unmute
                    ack = "On it — give me about 30 seconds."
                    window.set_state("speaking", said=ack)
                    _last_spoke_at[0] = time.time() + 120
                    speak(ack)
                    _last_spoke_at[0] = time.time()
                    _last_said[0]     = ack
                    window.show_webgen_progress()
                    try:
                        from webgen import generate as _wg, generate_for_project as _wgp, _find_web_project
                        proj = _find_web_project(t)
                        result = _wgp(proj) if proj else _wg(t)
                    except Exception as exc:
                        result = f"Website generation failed — {exc.__class__.__name__}."
                        _last_spoke_at[0] = time.time()   # reset block so GIL doesn't go deaf
                    window.close_webgen_progress()
                    unmute()
                    window.set_state("speaking", said=result)
                    speak(result)
                    _last_spoke_at[0] = time.time() - 1.5
                    _last_said[0]     = result
                    window.set_state("listening")
                threading.Thread(target=_build_as_web, daemon=True, name="GIL-WebGen").start()
                return
            _handle_build(target)
        elif action == "prompt_project":
            _handle_prompt_project(target)
        elif action == "open_terminal":
            from actions import open_terminal
            open_terminal(target)
        else:
            execute_action(action, target)

    def on_utterance(text: str) -> None:
        print(f"[G.I.L. EARS] Heard: '{text}'")
        lower = text.lower().replace(".", "").replace(",", "")

        # ── "Gil stop" — kill TTS immediately, no response, back to listening ───
        _STOP_NOW = {"gil stop", "stop gil", "stop talking", "stop speaking"}
        if any(p in lower for p in _STOP_NOW):
            try:
                from voice import stop_speaking as _stop_sp
                _stop_sp()
            except Exception:
                pass
            window.set_state("listening")
            return

        # ── Interrupt: single-word stop while GIL is mid-speech ───────────────
        try:
            from voice import is_speaking, stop_speaking as _stop_sp
            _INTERRUPT_WORDS = {"stop", "cancel", "enough", "quiet", "silence"}
            if is_speaking() and (
                any(w in lower.split() for w in _INTERRUPT_WORDS)
                or "shut up" in lower
            ):
                _stop_sp()
                window.set_state("listening")
                return
        except Exception:
            pass

        # ── Pause mode — ignore everything until "Gil" is heard ───────────────
        _STOP_PHRASES = {"be quiet", "shut up", "go away"}
        if not _paused[0] and any(p in lower for p in _STOP_PHRASES):
            _paused[0] = True
            speech = "I'll be quiet. Just say my name when you need me."
            window.set_state("speaking", said=speech)
            speak(speech)
            _last_said[0]     = speech
            _last_spoke_at[0] = time.time()
            window.set_state("standby")
            return

        if _paused[0]:
            if "gil" in lower.split() or lower.startswith("gil") or "hey gil" in lower:
                _paused[0] = False
                speech = "I'm here."
                window.set_state("speaking", said=speech)
                speak(speech)
                _last_said[0]     = speech
                _last_spoke_at[0] = time.time() - 1.5
                window.set_state("listening")
            return

        # Geometry / science 3D visualizer — instant, embedded in GIL
        try:
            from viewer3d import detect_shape
            shape = detect_shape(text)
            if shape and any(t in lower for t in {
                "show", "draw", "display", "visualize", "what does",
                "what is", "model", "3d", "explain",
            }):
                window.show_3d(shape)
                msg = f"Here's the holographic model — drag it to rotate."
                window.set_state("speaking", said=msg)
                speak(msg)
                _last_spoke_at[0] = time.time() - 1.5
                window.set_state("listening")
                if _active_project[0]:
                    try:
                        from learning_projects import add_resource
                        add_resource(_active_project[0], "3d_model", shape, f"3D: {shape}")
                    except Exception:
                        pass
                return
        except Exception as exc:
            print(f"[G.I.L.] Visualizer error: {exc}")

        # Song ID — instant fast-path, never hits LLM
        _SONG_TRIGGERS = {"what song", "what's this song", "identify this song",
                          "what's playing", "shazam this", "name this song",
                          "what is this song", "listen to this", "id this song"}
        if any(t in lower for t in _SONG_TRIGGERS):
            if not conv.active:
                conv.activate()
                set_passive(False)
                window.after(0, window.show_window)
            def _song_id():
                from ears import mute, unmute
                from actions import identify_song, get_spotify_now_playing

                window.set_state("processing", heard=text)

                # Check Spotify title first — instant, no audio needed
                spotify_track = get_spotify_now_playing()
                if spotify_track:
                    msg = f"Spotify is playing {spotify_track}."
                    window.set_state("speaking", said=msg)
                    speak(msg)
                    _last_spoke_at[0] = time.time() - 1.5
                    window.set_state("listening")
                    return

                # Tell the user, then go silent before recording
                prompt = "Go ahead — playing near the mic."
                window.set_state("speaking", said=prompt)
                speak(prompt)
                time.sleep(0.5)   # brief gap after TTS ends

                # Mute GIL's listener so mic is free for recording
                mute()
                time.sleep(0.2)

                def _on_result(msg):
                    unmute()
                    window.set_state("speaking", said=msg)
                    speak(msg)
                    _last_spoke_at[0] = time.time() - 1.5
                    window.set_state("listening")

                window.set_state("listening")
                identify_song(_on_result)

            threading.Thread(target=_song_id, daemon=True, name="GIL-SongID").start()
            return

        # Triggers fire from ANY state — standby, passive, or active
        try:
            from triggers import match_trigger, fuzzy_match_trigger
            trig = match_trigger(text) or fuzzy_match_trigger(text)
            if trig:
                if not conv.active:
                    conv.activate()
                    from ears import set_passive
                    set_passive(False)
                    window.after(0, window.show_window)
                threading.Thread(target=_run_trigger, args=(trig,),
                                 daemon=True, name="GIL-Trigger").start()
                return
        except Exception as exc:
            print(f"[G.I.L.] Trigger check failed: {exc}")

        # Wake phrase works from any state — always shows window, never hits LLM
        if _contains_wake_phrase(lower):
            print(f"[G.I.L.] Wake phrase detected in: '{text}'")
            if not conv.active:
                conv.activate()
                set_passive(False)
            window.after(0, window.show_window)
            window.set_state("listening")
            after = _strip_wake_phrase(text)
            if after:
                threading.Thread(target=_process, args=(after,),
                                 daemon=True, name="GIL-Process").start()
            else:
                _speak_wake_prompt()
            return

        if conv.active:
            addressed = _is_addressed(lower)
            if addressed:
                _last_addressed_at[0] = time.time()

            time_since_addressed = time.time() - _last_addressed_at[0]
            in_window = time_since_addressed < _ATTENTION_SECS

            if addressed or in_window:
                threading.Thread(target=_process, args=(text,),
                                 daemon=True, name="GIL-Process").start()
            else:
                print(f"[G.I.L.] Not addressed — ignored: '{text[:50]}'")

    def _manual_activate() -> None:
        """Triggered by the GUI ACTIVATE button, hotkey, or double clap."""
        already_active = conv.active
        if not already_active:
            conv.activate()
            from ears import set_passive
            set_passive(False)
        window.after(0, window.show_window)
        window.set_state("listening")
        # Only prompt if we were genuinely waking up — not mid-conversation
        if not already_active or time.time() - _last_spoke_at[0] > 30:
            _speak_wake_prompt()
        print("[G.I.L.] Manually activated.")

    window.register_activate_callback(_manual_activate)

    # Global hotkey: Ctrl+Shift+G activates GIL from anywhere
    try:
        import keyboard
        keyboard.add_hotkey("ctrl+shift+g", _manual_activate, suppress=False)
        print("[G.I.L.] Global hotkey active: Ctrl+Shift+G")
    except Exception:
        print("[G.I.L.] keyboard library not available — hotkey disabled.")

    # Double-clap wake: two sharp claps within 1.6 s activates G.I.L.
    _clap_on = True
    try:
        import json as _json
        from pathlib import Path as _Path
        _cfg = _Path(__file__).parent / "data" / "gil_config.json"
        with open(_cfg) as _f:
            _clap_on = _json.load(_f).get("clap_detect_on", True)
    except Exception:
        pass
    if _clap_on:
        from ears import start_clap_detector
        start_clap_detector(_manual_activate)
        print("[G.I.L.] Clap detector active. Two claps to wake.")
    else:
        print("[G.I.L.] Clap detector disabled via settings.")

    print("[G.I.L.] Passive listening active. Say 'Hello G.I.L.' to begin.\n")
    listen_forever(on_utterance)


# ── Action handlers ───────────────────────────────────────────────────────────

def _handle_save_credential(target: str, speak, window) -> None:
    from credentials import save_credential, initialize_credentials
    initialize_credentials()

    parts = [p.strip() for p in target.split("|")]

    if len(parts) == 3:
        service, email, password = parts
        save_credential(service, email, password)
        # Brain already announced this — no second speak
    else:
        from ears import listen_once
        speak("Which service is this for?")
        window.set_state("listening")
        service = listen_once(timeout_secs=8) or ""

        speak("Email or username?")
        window.set_state("listening")
        email = listen_once(timeout_secs=10) or ""

        speak("Password?")
        window.set_state("listening")
        password = listen_once(timeout_secs=10) or ""

        if service and email and password:
            save_credential(service.strip(), email.strip(), password.strip())
            speak(f"Saved.")
        else:
            speak("Didn't catch all of that. Try again or use the settings panel.")


def _handle_list_credentials(speak) -> None:
    from credentials import list_services, initialize_credentials
    initialize_credentials()
    services = list_services()
    # Brain has the credentials list in its context — it handles the speech.
    # We just ensure the data is printed for the terminal log.
    if services:
        print(f"[G.I.L. VAULT] Stored services: {', '.join(services)}")
    else:
        print("[G.I.L. VAULT] No credentials stored.")


def _handle_delete_credential(target: str, speak) -> None:
    from credentials import delete_credential, initialize_credentials
    initialize_credentials()
    if target:
        delete_credential(target)
    # Brain already announced the result — no second speak


# ── Task / project handlers ───────────────────────────────────────────────────

def _handle_create_project(name: str, window) -> None:
    if not name:
        return
    from tasks import create_project
    create_project(name)
    window.refresh_tasks()
    window.after(0, window.show_window)


def _handle_add_task(target: str, window) -> None:
    if not target:
        return
    from tasks import add_task
    parts   = [p.strip() for p in target.split("|")]
    text    = parts[0]
    project = parts[1].lower().replace(" ", "_") if len(parts) > 1 else ""
    add_task(text, project)
    window.refresh_tasks()
    window.after(0, window.show_window)


def _handle_complete_task(target: str, window) -> None:
    if not target:
        return
    from tasks import complete_task
    complete_task(target)
    window.refresh_tasks()


# ── Build / project handlers ─────────────────────────────────────────────────

def _handle_create_3d(description: str, project_name: str = "") -> None:
    if not description:
        return
    try:
        from studio3d import open_studio
        open_studio(description, variant=1, project_name=project_name or description.title())
    except Exception as exc:
        print(f"[G.I.L. STUDIO] Error: {exc}")


def _handle_build(target: str) -> None:
    """Parse 'description | project-name' and spawn claude -p."""
    parts       = [p.strip() for p in target.split("|", 1)]
    description = parts[0]
    name        = parts[1] if len(parts) > 1 else ""
    if not description:
        return
    from actions import build_project
    ok = build_project(description, name)
    if not ok:
        print("[G.I.L. BUILD] Failed to open terminal — is 'claude' CLI installed?")


def _handle_prompt_project(target: str) -> None:
    """Open an existing Desktop project in a Claude Code terminal.
    If the folder contains index.html (it's a website), opens browser instead."""
    parts       = [p.strip() for p in target.split("|", 1)]
    folder_name = parts[0]
    prompt      = parts[1] if len(parts) > 1 else "Summarize the project and what I should work on next."
    if not folder_name:
        return

    from pathlib import Path
    desktop = Path.home() / "Desktop"

    # Fuzzy match folder name
    project_dir = None
    for d in desktop.iterdir():
        if d.is_dir() and folder_name.lower() in d.name.lower():
            project_dir = d
            break

    if not project_dir:
        print(f"[G.I.L. PROJECT] Folder not found on Desktop: {folder_name}")
        return

    # If the folder has index.html it's a website — open in browser, never CMD
    index_html = project_dir / "index.html"
    if index_html.exists():
        print(f"[G.I.L. PROJECT] '{project_dir.name}' has index.html — opening in browser.")
        try:
            from webgen import _open_file
            _open_file(index_html)
        except Exception as exc:
            import webbrowser
            webbrowser.open(index_html.as_uri())
        return

    # Website words in folder name → redirect to webgen instead of CMD
    _WEB_WORDS = {
        "website", "webpage", "landing", "webapp", "frontend", "homepage",
    }
    if any(w in project_dir.name.lower() for w in _WEB_WORDS):
        print(f"[G.I.L. PROJECT] '{project_dir.name}' looks like a website — running webgen.")
        try:
            from webgen import generate_for_project
            generate_for_project(project_dir)
        except Exception as exc:
            print(f"[G.I.L. PROJECT] Webgen failed: {exc}")
        return

    dir_str  = str(project_dir)
    cmd_body = f'cd /d "{dir_str}" && echo {prompt} | claude -p --continue --dangerously-skip-permissions'

    import subprocess
    for launcher in [
        ["wt", "-d", dir_str, "cmd", "/k", cmd_body],
        ["cmd", "/c", "start", "cmd", "/k", cmd_body],
    ]:
        try:
            subprocess.Popen(launcher)
            print(f"[G.I.L. PROJECT] Opened '{project_dir.name}' with Claude Code.")
            return
        except FileNotFoundError:
            continue


# ── Wake phrase helpers ───────────────────────────────────────────────────────

def _load_wake_phrase() -> str:
    try:
        import json as _j
        from pathlib import Path as _P
        with open(_P(__file__).parent / "data" / "gil_config.json") as _f:
            return _j.load(_f).get("wake_phrase", "").lower().strip()
    except Exception:
        return ""

_CUSTOM_WAKE = _load_wake_phrase()


def _contains_wake_phrase(text: str) -> bool:
    cleaned = text.lower().replace(".", "").replace(",", "").replace("!", "").replace("?", "")

    # Check user's custom wake phrase first (exact substring match)
    if _CUSTOM_WAKE and _CUSTOM_WAKE in cleaned:
        return True

    words = cleaned.split()

    # "Hey/Hello/Hi Gil" — original pattern
    for i, w in enumerate(words[:-1]):
        if w in HELLO_VARIANTS or _edit_distance(w, "hello") <= 1:
            nxt = words[i + 1]
            if nxt in GIL_VARIANTS or _edit_distance(nxt, "gil") <= 2:
                return True

    # "Gil [command]" — Gil as first word is enough to wake from passive
    if words and (words[0] in GIL_VARIANTS or _edit_distance(words[0], "gil") <= 1):
        return True

    return False


def _edit_distance(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]


def _strip_wake_phrase(text: str) -> str:
    cleaned = text.lower().replace(".", "").replace(",", "").replace("!", "").replace("?", "")
    words   = cleaned.split()
    orig    = text.split()

    # "Hello/Hey Gil [command]" — skip hello + gil words
    for i, w in enumerate(words[:-1]):
        if w in HELLO_VARIANTS:
            nxt = words[i + 1]
            if nxt in GIL_VARIANTS or _edit_distance(nxt, "gil") <= 1:
                after = " ".join(orig[i + 2:]).strip().lstrip(",").lstrip(".").strip()
                return after if len(after) > 2 else ""

    # "Gil [command]" — first word is Gil, strip just that one word
    if words and (words[0] in GIL_VARIANTS or _edit_distance(words[0], "gil") <= 1):
        after = " ".join(orig[1:]).strip().lstrip(",").lstrip(".").strip()
        return after if len(after) > 2 else ""

    return ""


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 52)
    print("  PROJECT G.I.L. — GENERATIVE INTELLIGENCE LIAISON")
    print("=" * 52)

    username = run_login()
    if not username:
        print("[G.I.L.] Authentication aborted. System locked.")
        sys.exit(0)

    print(f"[G.I.L.] Identity confirmed: {username}")

    # ── Start intelligence engines ─────────────────────────────────────────────
    context_engine.start()
    goal_tracker.start()
    proactive.start()

    # Warm location cache in background so first query is instant
    try:
        import threading as _t
        from location import get_location as _get_loc
        _t.Thread(target=_get_loc, daemon=True, name="GIL-LocWarm").start()
    except Exception:
        pass

    from gui import GILWindow, _set_startup, _get_startup_enabled
    window = GILWindow(username=username)

    # Wire proactive suggestions into GUI toast
    proactive.set_show_callback(lambda msg: window.after(0, lambda m=msg: window.show_proactive_suggestion(m)))

    def _proactive_recap_callback(msg: str, recap_type: str = "", items: list = None) -> None:
        """Show toast immediately; speak only when conversation is idle."""
        try:
            from modes import is_proactive_blocked
            if is_proactive_blocked():
                return
        except Exception:
            pass
        window.after(0, lambda m=msg: window.show_proactive_suggestion(m))
        _pending_recap_global[0] = {"type": recap_type, "items": items or []}

        def _speak_it():
            from voice import speak as _speak, is_speaking
            # Wait until GIL is idle — not speaking, not processing, and user hasn't
            # interacted in the last _ATTENTION_SECS seconds (conversation is over).
            for _ in range(180):   # max 3 minutes
                busy = (
                    is_speaking()
                    or _processing_lock.locked()
                    or (time.time() - _last_addressed_at[0]) < _ATTENTION_SECS
                )
                if not busy:
                    break
                time.sleep(1)
            else:
                print("[G.I.L.] Proactive recap deferred — conversation still active.")
                return

            window.set_state("speaking", said=msg)
            _speak(msg)
            window.set_state("listening")

        threading.Thread(target=_speak_it, daemon=True, name="GIL-RecapSpeak").start()

    # Gmail unread recap — check on startup + every 30 min
    try:
        import gmail_recap
        gmail_recap.set_show_callback(
            lambda msg, items=[]: _proactive_recap_callback(msg, "email", items)
        )
        gmail_recap.start_periodic_check(interval_secs=1800)
    except Exception as _ge:
        print(f"[G.I.L.] Gmail recap disabled: {_ge}")

    # WhatsApp unread recap — periodic background check
    try:
        import whatsapp_recap
        whatsapp_recap.set_show_callback(
            lambda msg, items=[]: _proactive_recap_callback(msg, "wa", items)
        )
        whatsapp_recap.start_periodic_check(interval_secs=1800)
    except Exception as _we:
        print(f"[G.I.L.] WhatsApp recap disabled: {_we}")

    # Clipboard history watcher
    try:
        import notes as _notes_mod
        _notes_mod.start_clipboard_watcher()
    except Exception as _ne:
        print(f"[G.I.L.] Clipboard watcher disabled: {_ne}")

    # Meeting detector — auto-switches to Presentation mode on Zoom/Teams
    try:
        import meeting_detector as _meet_det
        import modes as _meet_modes

        def _on_meeting_mode_change(mode_name: str) -> None:
            _meet_modes.set_mode(mode_name)

        _meet_det.set_mode_callback(_on_meeting_mode_change)
        _meet_det.start_meeting_watcher()
    except Exception as _mde:
        print(f"[G.I.L.] Meeting detector disabled: {_mde}")

    # Wire goal check-in into GIL speech
    def _on_checkin(msg: str):
        from voice import speak
        window.after(0, lambda m=msg: window.show_proactive_suggestion(m))
    goal_tracker.on_checkin(_on_checkin)

    # Wire context changes to goal-asking (fires when app switches)
    def _on_ctx_change(ctx: dict):
        app = ctx.get("app", "")
        proactive.set_active_app(app)
        session_manager.record_app_switch()
        # Ask goal question via proactive toast (not voice — non-intrusive)
        if goal_tracker.should_ask_about_context(app) and app:
            goal_tracker.mark_asked(app)
            file_ = ctx.get("file", "")
            q = (f"You opened {app}"
                 + (f" — {file_}" if file_ else "")
                 + ". What are we working on?")
            window.after(0, lambda m=q: window.show_proactive_suggestion(m))
    context_engine.on_context_changed(_on_ctx_change)

    def _save_conversation_summary() -> None:
        """Ask Groq for a JSON topic list covering the full session and save it."""
        try:
            if not _brain_ref:
                return
            b = _brain_ref[0]
            # Collect ALL user messages for an accurate full-session picture
            user_msgs = [h["content"] for h in b.history if h.get("role") == "user"]
            if not user_msgs:
                return
            transcript = "\n".join(f"- {m[:200]}" for m in user_msgs)

            import requests as _req
            groq_key = os.getenv("GROQ_API_KEY", "") or os.getenv("GROQ_API_KEY_2", "")
            if not groq_key:
                return
            payload = {
                "model": "llama-3.1-8b-instant",
                "messages": [
                    {"role": "system", "content":
                        "You extract topic labels from a chat log. "
                        "Reply with ONLY a JSON array of 2-5 short topic strings (2-4 words each). "
                        "No explanation, no markdown, just the raw JSON array. "
                        'Example: ["gesture drag fix", "facial recognition", "cursor accuracy"]'},
                    {"role": "user", "content":
                        f"Here are the user messages from this session:\n{transcript}\n\n"
                        "List the main topics as a JSON array."},
                ],
                "max_tokens": 80,
                "temperature": 0.2,
            }
            resp = _req.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json=payload, timeout=8,
            )
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            # Validate it's a proper JSON array before saving
            import json as _json
            topics = _json.loads(raw)
            if isinstance(topics, list) and topics:
                from memory import record_task
                record_task(_json.dumps(topics))
                print(f"[G.I.L.] Session topics saved: {topics}")
        except Exception as exc:
            print(f"[G.I.L.] Summary save failed: {exc}")

    # Periodic session summary save — survives ungraceful shutdown
    def _periodic_summary():
        while True:
            time.sleep(600)   # every 10 minutes
            try:
                _save_conversation_summary()
            except Exception:
                pass
    threading.Thread(target=_periodic_summary, daemon=True, name="GIL-SummarySave").start()

    # Wire session shutdown
    def _on_shutdown(msg: str):
        from voice import speak
        _save_conversation_summary()
        try:
            speak(msg)
        except Exception:
            pass
    session_manager.on_shutdown(_on_shutdown)

    # Ensure GIL auto-starts with Windows (Bixby-style always-on)
    if not _get_startup_enabled():
        _set_startup(True)
        print("[G.I.L.] Registered for Windows startup.")

    # System tray (hides window on close instead of quitting)
    _start_tray(window)

    t = threading.Thread(
        target=_audio_loop,
        args=(username, window),
        daemon=True,
        name="GIL-AudioLoop",
    )
    t.start()

    # Auto-start watcher (clap/wake listener) if not already running
    try:
        import subprocess
        from pathlib import Path as _Path
        _watcher = str(_Path(__file__).parent / "watcher.py")
        _pw = sys.executable.replace("python.exe", "pythonw.exe")
        if not os.path.exists(_pw):
            _pw = sys.executable
        subprocess.Popen(
            [_pw, _watcher],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
        )
        print("[G.I.L.] Watcher started.")
    except Exception as _we:
        print(f"[G.I.L.] Watcher start failed: {_we}")

    window.after(500, window.show_window)   # ensure window is visible on startup
    window.mainloop()
    session_manager.trigger_shutdown(username)
    print("[G.I.L.] Session terminated.")


if __name__ == "__main__":
    main()
