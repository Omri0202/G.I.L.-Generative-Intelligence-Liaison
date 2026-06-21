"""
conversation_engine.py — G.I.L.
ConversationEngine class — the core of GIL's voice/text processing loop.

Previously everything lived as nested closures inside _audio_loop() in main.py.
Moving it here makes each piece individually testable and easy to locate.

Layout:
  ConversationEngine.__init__   — create state
  ConversationEngine.start()    — wire dependencies, greet, begin listening (blocks)
  _start/stop_gesture_watcher   — camera gesture helpers
  _speak_wake_prompt            — beep + ask what user needs
  _run_trigger                  — fire a user-defined macro
  _process()                    — voice input gatekeeper (cooldown + lock)
  _do_process()                 — main dispatcher: fast-paths then brain
  _process_chat()               — typed chat input (bypasses mic cooldown)
  _dispatch_instant()           — fire-and-forget action runner
  on_utterance()                — STT callback, registered with ears.listen_forever
  _manual_activate()            — hotkey / button / clap handler
"""

import re as _re
import threading
import time
import winsound

# ── Imports from other GIL modules ───────────────────────────────────────────
from fast_paths import (
    fast_url_resolve, fast_youtube_resolve, fast_study_resolve,
    is_greeting_response, build_greeting,
)
from wake_phrase import (
    GIL_VARIANTS, contains_wake_phrase, strip_wake_phrase,
    is_addressed, edit_distance,
)
from logger import get as _get_log

log = _get_log("engine")

from action_handlers import (
    handle_save_credential, handle_list_credentials, handle_delete_credential,
    handle_create_project, handle_add_task, handle_complete_task,
    handle_create_3d, handle_build, handle_prompt_project,
)


# ── Minimal conversation state ─────────────────────────────────────────────────

class ConversationState:
    def __init__(self):
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def activate(self)   -> None: self._active = True
    def deactivate(self) -> None: self._active = False


# ── Engine ─────────────────────────────────────────────────────────────────────

class ConversationEngine:
    """Encapsulates the entire voice/text conversation loop."""

    ATTENTION_SECS  = 45.0
    _INSTANT_ACTIONS = {"open_url", "open_app", "web_search", "prompt_project"}
    _WEBGEN_WORDS   = {
        "website", "web site", "webpage", "web page", "landing page", "landing",
        "web app", "web application", "html", "frontend", "front-end", "site",
        "homepage", "home page", "front end",
    }

    def __init__(self, username: str, window):
        self.username = username
        self.window   = window

        from gil_brain import GILBrain
        self.brain = GILBrain(username=username)
        self.conv  = ConversationState()

        # ── Mutable shared state (single-element lists — same mutation pattern
        #    as the old closures, so threads updating them work identically) ──
        self._last_spoke_at    = [time.time()]
        self._last_said        = [""]
        self._active_project   = [None]
        self._pending_recap    = [{}]
        self._pending_recap_g  = [{}]   # global recap (set by proactive callbacks)
        self._paused           = [False]
        self._camera_win       = [None]
        self._gesture_watcher  = [None]
        self._last_addressed_at = [0.0]

        # Processing lock — one query in-flight at a time
        self.processing_lock = threading.Lock()

        # speak() function — set in start()
        self._speak = None

    # ── Public: expose state for main.py callbacks ────────────────────────────

    @property
    def pending_recap_global(self) -> list:
        return self._pending_recap_g

    # ── Start ─────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Wire everything up, greet the user, then block on listen_forever()."""
        from voice import speak
        from ears import listen_forever, set_passive, start_clap_detector
        from actions import execute_action, _build_app_index
        from credentials import initialize_credentials
        import context_engine, goal_tracker, proactive, session_manager, preferences

        self._speak          = speak
        self._execute_action = execute_action

        initialize_credentials()
        _build_app_index()

        # Activate immediately — no wake phrase needed on first launch
        self.conv.activate()
        set_passive(False)

        greeting = build_greeting(self.username)
        self.window.set_state("speaking", said=greeting)
        speak(greeting)
        self._last_spoke_at[0] = time.time() - 1.5
        self._last_said[0]     = greeting
        self.window.set_state("listening")

        self._last_addressed_at[0] = time.time()

        # ── Wire subsystems ───────────────────────────────────────────────────
        import modes as _modes
        _modes.set_window_ref(self.window)
        _modes.set_speak_ref(speak)
        _modes.set_paused_callback(lambda v: self._paused.__setitem__(0, v))

        import reminders as _reminders
        _reminders.set_speak_callback(speak)
        _reminders.set_window_ref(self.window)
        _reminders.restore_pending()

        # ── Expose chat callback + floating button ────────────────────────────
        self.window._chat_send_fn = self._process_chat
        self.window.after(0, self.window._create_floating_chat_button)
        self.window.register_activate_callback(self._manual_activate)

        # ── Global hotkeys ────────────────────────────────────────────────────
        try:
            import keyboard
            keyboard.add_hotkey("ctrl+shift+g", self._manual_activate, suppress=False)
            keyboard.add_hotkey("ctrl+shift+c",
                                lambda: self.window.after(0, self.window._open_chat_window),
                                suppress=False)
            log.info("hotkeys registered: Ctrl+Shift+G / Ctrl+Shift+C")
        except Exception:
            log.warning("keyboard library unavailable — hotkeys disabled")

        # ── Double-clap wake ──────────────────────────────────────────────────
        _clap_on = True
        try:
            import json as _json
            from pathlib import Path as _Path
            _cfg = _Path(__file__).parent / "data" / "gil_config.json"
            _clap_on = _json.loads(_cfg.read_text()).get("clap_detect_on", True)
        except Exception:
            pass
        if _clap_on:
            start_clap_detector(self._manual_activate)
            log.info("clap detector active")
        else:
            log.info("clap detector disabled")

        print("[G.I.L.] Listening. Say 'Hello G.I.L.' to begin.\n")
        listen_forever(self.on_utterance)

    # ── Gesture watcher helpers ───────────────────────────────────────────────

    def _start_gesture_watcher(self) -> None:
        try:
            from gestures import GestureWatcher
            if self._gesture_watcher[0] and self._gesture_watcher[0]._running:
                return
            gw = GestureWatcher(speak_fn=self._speak)
            gw.start()
            self._gesture_watcher[0] = gw
        except Exception as exc:
            log.warning("Gesture watcher failed to start: %s", exc)

    def _stop_gesture_watcher(self) -> None:
        if self._gesture_watcher[0]:
            self._gesture_watcher[0].stop()
            self._gesture_watcher[0] = None

    # ── Wake prompt ───────────────────────────────────────────────────────────

    def _speak_wake_prompt(self) -> None:
        def _go():
            winsound.Beep(1100, 180)
            time.sleep(0.15)
            phrase = "What are we working on today?"
            self._last_said[0]     = phrase
            self._last_spoke_at[0] = time.time()
            self.window.set_state("speaking", said=phrase)
            self._speak(phrase)
            self._last_spoke_at[0] = time.time() - 1.5
            self.window.set_state("listening")
        threading.Thread(target=_go, daemon=True, name="GIL-Wake").start()

    # ── Trigger runner ────────────────────────────────────────────────────────

    def _run_trigger(self, trig: dict) -> None:
        print(f"[G.I.L. TRIGGER] Firing: '{trig['phrase']}'")
        actions = trig.get("actions", [])

        labels = []
        for act in actions:
            t, target = act.get("type", ""), act.get("target", "")
            if t == "open_url":
                labels.append(target.split("//")[-1].split("/")[0].replace("www.", ""))
            elif t == "open_app":
                labels.append(target)
            elif t == "web_search":
                labels.append(f"searching {target[:20]}")
        confirm = f"Got it. Opening {', '.join(labels[:4])}." if labels else "On it."

        self.window.set_state("speaking", said=confirm)
        self._speak(confirm)
        self._last_spoke_at[0] = time.time()
        self._last_said[0]     = confirm

        threads = [
            threading.Thread(target=self._execute_action,
                             args=(a.get("type", ""), a.get("target", "")),
                             daemon=True, name=f"GIL-TrigAct-{i}")
            for i, a in enumerate(actions)
        ]
        for th in threads:
            th.start()

        followup = trig.get("followup", "").strip()
        if followup:
            time.sleep(0.6)
            self._last_said[0]     = followup
            self._last_spoke_at[0] = time.time()
            self.window.set_state("speaking", said=followup)
            self._speak(followup)
            self._last_spoke_at[0] = time.time() - 1.5

        self.window.set_state("listening")

    # ── Voice input gatekeeper ─────────────────────────────────────────────────

    def _process(self, text: str) -> None:
        """Voice input: apply cooldown + similarity filter, then dispatch."""
        from ears import mute, unmute

        if not self.processing_lock.acquire(blocking=False):
            log.debug("busy — ignored: %r", text[:40])
            return
        try:
            # Post-speech echo cooldown
            if time.time() - self._last_spoke_at[0] < 2.0:
                log.debug("echo suppressed (cooldown): %r", text[:40])
                return
            # Similarity filter (8-second window)
            if self._last_said[0] and time.time() - self._last_spoke_at[0] < 8.0:
                if self._word_overlap(text, self._last_said[0]) > 0.5:
                    log.debug("echo suppressed (similarity): %r", text[:40])
                    return

            self.window.add_chat_message(text, "user")
            if not self.conv.active:
                self.window.after(0, self.window.show_window)
            self.window.set_state("processing", heard=text)
            mute()
            self._do_process(text)
        finally:
            unmute()
            self.processing_lock.release()

    # ── Typed chat input ──────────────────────────────────────────────────────

    def _process_chat(self, text: str) -> None:
        """Typed chat message — skips mic echo filters."""
        if not self.processing_lock.acquire(blocking=False):
            log.debug("chat busy — dropped: %r", text[:40])
            self.window.chat_hide_typing()
            return
        try:
            self._last_addressed_at[0] = time.time()
            if not self.conv.active:
                self.conv.activate()
            from ears import mute, unmute
            mute()
            self._do_process(text)
        finally:
            from ears import unmute
            unmute()
            self.window.chat_hide_typing()
            self.processing_lock.release()

    # ── Helpers used by _process / _do_process ────────────────────────────────

    @staticmethod
    def _word_overlap(a: str, b: str) -> float:
        _strip = lambda s: set(_re.sub(r"[^\w\s]", "", s.lower()).split())
        wa, wb = _strip(a), _strip(b)
        if not wa or not wb:
            return 0.0
        return len(wa & wb) / max(len(wa), len(wb))

    # ── Main dispatcher ───────────────────────────────────────────────────────

    def _do_process(self, text: str) -> None:  # noqa: C901  (intentionally long)
        """
        Core processor. Tries fast-paths first, then sends to the brain.
        All references to closure variables replaced with self.xxx.
        """
        from ears import unmute
        import context_engine, goal_tracker, proactive, session_manager, preferences

        speak   = self._speak
        window  = self.window
        lower   = text.lower().replace(".", "").replace(",", "")

        # ── Pending recap yes/no ──────────────────────────────────────────────
        _YES = {"yes", "yeah", "sure", "go ahead", "read them", "read it",
                "please", "ok", "okay", "yep", "do it", "read", "go"}
        _active_recap = self._pending_recap[0] or self._pending_recap_g[0]
        if _active_recap and any(w in lower.split() for w in _YES):
            recap = _active_recap
            self._pending_recap[0]   = {}
            self._pending_recap_g[0] = {}
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
                self._last_spoke_at[0] = time.time() - 1.5
                self._last_said[0]     = speech
                window.set_state("listening")
            threading.Thread(target=_read_recap, daemon=True, name="GIL-ReadRecap").start()
            return

        _NO = {"no", "nope", "nah", "dont", "don't", "cancel", "skip", "nevermind", "never mind", "stop"}
        if _active_recap and any(w in lower.split() for w in _NO):
            self._pending_recap[0]   = {}
            self._pending_recap_g[0] = {}
            window.set_state("listening")
            return

        # ── WhatsApp recap ────────────────────────────────────────────────────
        _WA_RECAP_TRIGGERS = {
            "whatsapp messages", "whatsapp message", "whatsapp",
            "unread whatsapp", "any whatsapp", "check whatsapp",
            "read my whatsapp", "what's on whatsapp", "whats on whatsapp",
            "missed messages", "missed whatsapp",
        }
        if any(tr in lower for tr in _WA_RECAP_TRIGGERS):
            def _do_wa_recap():
                from ears import unmute
                unmute()
                window.set_state("processing", heard=text)
                try:
                    import whatsapp_recap
                    msgs   = whatsapp_recap.get_unread_messages()
                    speech = whatsapp_recap.build_recap_speech(msgs)
                    if msgs:
                        self._pending_recap[0] = {"type": "wa", "items": msgs}
                except Exception as exc:
                    log.error("WhatsApp recap: %s", exc, exc_info=True)
                    speech = "I couldn't reach WhatsApp right now. Want me to open it?"
                window.set_state("speaking", said=speech)
                speak(speech)
                self._last_spoke_at[0] = time.time() - 1.5
                self._last_said[0]     = speech
                window.set_state("listening")
            threading.Thread(target=_do_wa_recap, daemon=True, name="GIL-WARecap").start()
            return

        # ── Gmail recap ───────────────────────────────────────────────────────
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
                        self._pending_recap[0] = {"type": "email", "items": emails}
                except Exception:
                    speech = "I couldn't reach Gmail right now. Want me to open it instead?"
                window.set_state("speaking", said=speech)
                from ears import unmute
                unmute()
                speak(speech)
                self._last_spoke_at[0] = time.time() - 1.5
                self._last_said[0]     = speech
                window.set_state("listening")
            threading.Thread(target=_do_recap, daemon=True, name="GIL-EmailRecap").start()
            return

        # ── Face enrollment ───────────────────────────────────────────────────
        _FACE_ENROLL = {
            "remember my face", "enroll my face", "scan my face",
            "save my face", "learn my face", "memorize my face",
        }
        if any(tr in lower for tr in _FACE_ENROLL):
            def _do_enroll():
                from ears import unmute
                import tempfile, cv2 as _cv2
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
                        ok, detail = FaceID().enroll(frame, self.username)
                        msg = (f"Got it. I'll recognize you as {self.username} from now on." if ok
                               else "Couldn't see your face clearly — try better lighting.")
                        if not ok:
                            log.warning("Face enroll failed: %s", detail)
                    except Exception as exc:
                        msg = "Face enrollment failed — make sure the camera is open."
                        log.error("Face enroll error", exc_info=True)
                window.set_state("speaking", said=msg)
                speak(msg)
                self._last_spoke_at[0] = time.time() - 1.5
                self._last_said[0]     = msg
                window.set_state("listening")
            threading.Thread(target=_do_enroll, daemon=True, name="GIL-FaceEnroll").start()
            return

        # ── Face query ────────────────────────────────────────────────────────
        _FACE_QUERY = {
            "who do you see", "do you recognize me", "do you know me",
            "can you identify me", "who is there", "who's there",
            "is it me", "recognize my face", "do you see me", "who am i",
        }
        if any(tr in lower for tr in _FACE_QUERY):
            def _do_face_query():
                from ears import unmute
                import tempfile, json as _j, cv2 as _cv2
                from pathlib import Path as _Path
                face_file = _Path(tempfile.gettempdir()) / "gil_face_state.json"
                if face_file.exists():
                    try:
                        state = _j.loads(face_file.read_bytes())
                        nm, st = state.get("name"), state.get("status")
                        if st == "match" and nm:
                            msg = f"Yes — I recognize you, {nm}."
                        elif st == "unknown":
                            msg = "I see someone, but I don't recognize them."
                        else:
                            msg = "I can't see a face clearly right now."
                        unmute(); window.set_state("speaking", said=msg); speak(msg)
                        self._last_spoke_at[0] = time.time() - 1.5
                        self._last_said[0] = msg
                        window.set_state("listening")
                        return
                    except Exception:
                        pass
                frame_path = _Path(tempfile.gettempdir()) / "gil_cam_frame.jpg"
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
                            if r["status"] == "match":   msg = f"I see {r['name']}."
                            elif r["status"] == "unknown": msg = "I see someone, but I don't recognize them."
                            else:                          msg = "I can't detect a face — try looking straight at the camera."
                    except Exception as exc:
                        msg = "Face recognition isn't available right now."
                        log.error("Face query error", exc_info=True)
                unmute(); window.set_state("speaking", said=msg); speak(msg)
                self._last_spoke_at[0] = time.time() - 1.5
                self._last_said[0] = msg
                window.set_state("listening")
            threading.Thread(target=_do_face_query, daemon=True, name="GIL-FaceQuery").start()
            return

        # ── Camera status query ───────────────────────────────────────────────
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
            _streaming = bool(self._camera_win[0] and self._camera_win[0].is_streaming())
            _visible   = bool(_hwnd and _u32.IsWindowVisible(_hwnd) and not _u32.IsIconic(_hwnd))
            if _streaming and _visible:
                speech = "Yes — the camera is open and visible on your screen."
            elif _streaming:
                speech = "Camera is running but the window isn't visible — bringing it up now."
                if self._camera_win[0]:
                    threading.Thread(target=self._camera_win[0].bring_to_front,
                                     daemon=True, name="GIL-CamFocus").start()
            else:
                speech = "No — the camera is closed. Say 'open camera' to start it."
            window.set_state("speaking", said=speech); speak(speech)
            self._last_spoke_at[0] = time.time() - 1.5
            self._last_said[0] = speech
            window.set_state("listening")
            return

        # ── Open camera ───────────────────────────────────────────────────────
        _cam_kw   = "camera" in lower or "webcam" in lower
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
            if self._camera_win[0] and not self._camera_win[0].is_alive():
                self._camera_win[0] = None
            if self._camera_win[0] and not self._camera_win[0].is_streaming():
                self._camera_win[0]._dead = True
                self._camera_win[0] = None
            if self._camera_win[0]:
                try:
                    import ctypes as _ct
                    _hwnd2 = _ct.windll.user32.FindWindowW(None, "G.I.L. Vision")
                    if _hwnd2:
                        _ct.windll.user32.ShowWindow(_hwnd2, 9)
                        _ct.windll.user32.SetForegroundWindow(_hwnd2)
                except Exception:
                    pass
                speech = "Camera's already up. Say 'what do you see' to analyze."
            else:
                def _cam_closed_cb():
                    if self._camera_win[0] is _this_cam:
                        self._camera_win[0] = None
                    self._stop_gesture_watcher()
                try:
                    from eyes import CameraWindow
                    _this_cam = CameraWindow(on_close=_cam_closed_cb)
                    self._camera_win[0] = _this_cam
                    self._start_gesture_watcher()
                    threading.Thread(target=_this_cam.bring_to_front,
                                     daemon=True, name="GIL-CamFocus").start()
                    speech = "Camera's up."
                except Exception as exc:
                    log.error("Camera open failed", exc_info=True)
                    speech = "Couldn't open the camera. Make sure it's connected."
            window.set_state("speaking", said=speech); speak(speech)
            self._last_spoke_at[0] = time.time() - 1.5
            self._last_said[0] = speech
            window.set_state("listening")
            return

        # ── Close camera ──────────────────────────────────────────────────────
        _cam_close = (
            _cam_kw and any(w in lower for w in ("close", "hide", "stop", "disable", "turn off", "shut"))
        ) or "close your eyes" in lower
        if _cam_close:
            from eyes import _FRAME_FILE as _FF, _KILL_FILE as _KF
            _running = (
                (self._camera_win[0] and self._camera_win[0].is_alive())
                or (_FF.exists() and time.time() - _FF.stat().st_mtime < 2.0)
            )
            if _running:
                if self._camera_win[0]:
                    self._camera_win[0].close()
                else:
                    try:
                        _KF.write_text("kill"); time.sleep(0.4)
                        _FF.unlink(missing_ok=True); _KF.unlink(missing_ok=True)
                    except Exception:
                        pass
                self._camera_win[0] = None
                self._stop_gesture_watcher()
                speech = "Camera closed."
            else:
                speech = "Camera isn't open."
            window.set_state("speaking", said=speech); speak(speech)
            self._last_spoke_at[0] = time.time() - 1.5
            self._last_said[0] = speech
            window.set_state("listening")
            return

        # ── Vision / identify query ───────────────────────────────────────────
        _identify_kw = any(p in lower for p in (
            "what is this", "what is that", "what's this", "what's that",
            "what are these", "what am i holding", "what do you see",
            "identify this", "identify that", "describe what you see",
            "what brand", "what model", "what color is this",
            "can you read this", "what does it say", "look at this",
            "look at that", "take a look", "what do i have", "recognize this",
            "what's in my hand", "what is in my hand", "look at my hand",
            "see what i", "look at me",
        ))
        if _identify_kw:
            def _do_identify(q=text):
                from ears import unmute
                window.set_state("speaking", said="On it."); speak("On it.")
                cam_open = self._camera_win[0] and self._camera_win[0].is_streaming()
                if not cam_open:
                    def _cb():
                        if self._camera_win[0] is _tc:
                            self._camera_win[0] = None
                    try:
                        from eyes import CameraWindow
                        _tc = CameraWindow(on_close=_cb)
                        self._camera_win[0] = _tc
                        threading.Thread(target=_tc.bring_to_front,
                                         daemon=True, name="GIL-CamFocus").start()
                        cam_open = True
                    except Exception as exc:
                        log.error("Camera open failed", exc_info=True)
                if cam_open and self._camera_win[0]:
                    frame = self._camera_win[0].get_current_frame()
                else:
                    from eyes import capture_frame
                    frame = capture_frame()
                if not frame:
                    result = "I can't see anything — open the camera first."
                else:
                    try:
                        from eyes import analyze_frame
                        result = analyze_frame(frame, question=q)
                    except Exception as exc:
                        log.error("Vision analysis failed", exc_info=True)
                        result = "Vision analysis failed."
                unmute(); window.set_state("speaking", said=result); speak(result)
                self._last_spoke_at[0] = time.time() - 1.5
                self._last_said[0] = result
                window.set_state("listening")
            threading.Thread(target=_do_identify, daemon=True, name="GIL-Identify").start()
            return

        # ── Webgen fast-path ──────────────────────────────────────────────────
        _web_noun = any(p in lower for p in (
            "website", "web site", "webpage", "web page", "landing page", "landing",
            "web app", "web application", "html page", "html site", "front page",
            "home page", "site", "homepage", "front end", "frontend",
        ))
        _web_verb = any(w in lower for w in (
            "build", "create", "make", "generate", "design", "write",
            "want", "need", "give me", "show me", "get me",
        ))
        _web_direct = bool(_re.search(
            r"\b(build|create|make|generate|design|want|need)\b.{0,40}\b"
            r"(website|webpage|landing|web app|site|homepage|frontend)\b",
            lower,
        ))
        if (_web_noun and _web_verb) or _web_direct:
            def _do_webgen(utterance=text):
                from ears import unmute
                ack = "On it — give me about 30 seconds."
                window.set_state("speaking", said=ack)
                self._last_spoke_at[0] = time.time() + 120
                speak(ack)
                self._last_spoke_at[0] = time.time()
                self._last_said[0] = ack
                window.show_webgen_progress()
                try:
                    from webgen import generate as _wg, generate_for_project as _wgp, _find_web_project
                    proj = _find_web_project(utterance)
                    result = _wgp(proj) if proj else _wg(utterance)
                except Exception as exc:
                    result = f"Website generation failed — {exc.__class__.__name__}."
                    self._last_spoke_at[0] = time.time()
                window.close_webgen_progress(); unmute()
                window.set_state("speaking", said=result); speak(result)
                self._last_spoke_at[0] = time.time() - 1.5
                self._last_said[0] = result
                window.set_state("listening")
            threading.Thread(target=_do_webgen, daemon=True, name="GIL-WebGen").start()
            return

        # ── Fast URL ──────────────────────────────────────────────────────────
        fast = fast_url_resolve(text)
        if fast:
            url, label = fast
            from actions import open_url
            threading.Thread(target=open_url, args=(url,), daemon=True).start()
            speech = f"{label}."
            window.set_state("speaking", said=speech); speak(speech)
            self._last_spoke_at[0] = time.time()
            self._last_said[0] = speech
            window.set_state("listening")
            return

        # ── Mode commands ─────────────────────────────────────────────────────
        _MODE_MAP = {
            "do not disturb": "dnd", "dnd mode": "dnd", "dnd": "dnd",
            "study mode": "study", "studying mode": "study",
            "fun mode": "fun", "normal mode": "normal", "reset mode": "normal",
        }
        matched_mode = next((v for k, v in _MODE_MAP.items() if k in lower), None)
        if matched_mode:
            from modes import set_mode as _set_mode
            result = _set_mode(matched_mode)
            window.set_state("speaking", said=result); speak(result)
            self._last_spoke_at[0] = time.time() - 1.5
            self._last_said[0] = result
            window.set_state("listening")
            return

        # ── YouTube search ────────────────────────────────────────────────────
        yt_url = fast_youtube_resolve(text)
        if yt_url:
            from actions import open_url
            threading.Thread(target=open_url, args=(yt_url,), daemon=True).start()
            speech = "Here you go."
            window.set_state("speaking", said=speech); unmute(); speak(speech)
            self._last_spoke_at[0] = time.time()
            self._last_said[0] = speech
            window.set_state("listening")
            if self._active_project[0]:
                try:
                    from learning_projects import add_resource
                    add_resource(self._active_project[0], "video", yt_url, text[:80])
                except Exception:
                    pass
            return

        # ── Learning project auto-detect ──────────────────────────────────────
        _SUBJECT_KEYWORDS = {
            "math": "Math", "algebra": "Algebra", "calculus": "Calculus",
            "geometry": "Geometry", "physics": "Physics", "chemistry": "Chemistry",
            "biology": "Biology", "history": "History",
            "computer science": "Computer Science", "programming": "Programming",
            "python": "Python", "economics": "Economics",
            "statistics": "Statistics", "trigonometry": "Trigonometry",
        }
        for kw, proj_name in _SUBJECT_KEYWORDS.items():
            if kw in lower and any(t in lower for t in {
                "studying", "learning", "working on", "doing", "help me with", "explain", "teach"
            }):
                if self._active_project[0] != proj_name:
                    self._active_project[0] = proj_name
                    print(f"[G.I.L.] Active project: {proj_name}")
                break

        # ── Open learning project ─────────────────────────────────────────────
        _has_proj  = "project" in lower or "projects" in lower
        _has_open  = any(w in lower for w in {
            "open", "show", "enter", "access", "continue",
            "go to", "load", "see", "view", "bring up",
        })
        if _has_proj and _has_open:
            try:
                from learning_projects import list_all, get_context_summary, load
                projects = list_all()
                if not projects:
                    speech = "No learning projects saved yet."
                else:
                    _SKIP = {"my", "the", "a", "an", "i", "open", "show", "enter",
                             "access", "continue", "load", "see", "view", "project",
                             "projects", "bring", "up", "go", "to", "want", "please"}
                    lower_words = {w for w in lower.split() if w not in _SKIP and len(w) > 2}
                    matched, best = None, 0
                    for p in projects:
                        nw  = {w for w in p["name"].lower().split() if len(w) > 2}
                        sc  = len(nw & lower_words) * 2 + sum(
                            1 for uw in lower_words for nw2 in nw if uw in nw2 or nw2 in uw
                        )
                        if sc > best:
                            best, matched = sc, p["name"]
                    if matched:
                        self._active_project[0] = matched
                        data    = load(matched)
                        models  = [r for r in data.get("resources", []) if r.get("type") == "3d_model"]
                        studios = [r for r in data.get("resources", []) if r.get("type") == "3d_studio"]
                        last    = data["sessions"][-1] if data["sessions"] else None
                        window.after(0, lambda m=matched: window.open_project_view(m))
                        if models:
                            window.show_3d(models[0].get("url", "sphere"))
                        if studios:
                            def _reopen(s=studios):
                                from studio3d import reopen_studio
                                for r in s[:3]:
                                    reopen_studio(r.get("url", "")); time.sleep(1.5)
                            threading.Thread(target=_reopen, daemon=True,
                                             name="GIL-ReopenStudio").start()
                        if last:
                            convs   = last["conversations"]
                            last_q  = convs[-1]["user"][:60] if convs else "—"
                            has_3d  = bool(models or studios)
                            speech  = (f"Opening {matched} — {len(data['sessions'])} sessions saved. "
                                       + ("Your 3D creation is back up." if has_3d
                                          else f"Last topic: {last_q}."))
                        else:
                            speech = f"{matched} project opened. What do you want to work on?"
                    else:
                        names  = ", ".join(p["name"] for p in projects[:6])
                        speech = f"Your learning projects: {names}. Which one do you want to open?"
                window.set_state("speaking", said=speech)
                from ears import unmute
                unmute(); speak(speech)
                self._last_spoke_at[0] = time.time() - 1.5
                self._last_said[0] = speech
                window.set_state("listening")
                return
            except Exception as exc:
                print(f"[G.I.L.] Project open error: {exc}")

        # ── Study subject fast-path ───────────────────────────────────────────
        study = fast_study_resolve(text)
        if study:
            speech, url = study
            for kw, proj_name in _SUBJECT_KEYWORDS.items():
                if kw in lower and self._active_project[0] != proj_name:
                    self._active_project[0] = proj_name
                    break
            if url:
                from actions import open_url
                threading.Thread(target=open_url, args=(url,), daemon=True).start()
            window.set_state("speaking", said=speech)
            from ears import unmute
            speak(speech)
            self._last_spoke_at[0] = time.time()
            self._last_said[0] = speech
            window.set_state("listening")
            if self._active_project[0]:
                try:
                    from learning_projects import add_conversation
                    add_conversation(self._active_project[0], text, speech)
                except Exception:
                    pass
            return

        # ── Preferences / context tracking ────────────────────────────────────
        try:
            ctx = context_engine.get_active_context()
            preferences.learn_from_exchange(text)
            goal_tracker.update_from_reply(text, app=ctx.get("app", ""),
                                            file=ctx.get("file", ""))
            if goal_tracker.get_goal_text():
                session_manager.record_session_goal(goal_tracker.get_goal_text())
            proactive.record_interaction()
        except Exception:
            pass

        # ── Active project context for brain ──────────────────────────────────
        project_ctx = ""
        if self._active_project[0]:
            try:
                from learning_projects import get_context_summary
                project_ctx = get_context_summary(self._active_project[0])
            except Exception:
                pass

        # ── Brain query ───────────────────────────────────────────────────────
        _cam_state = (
            "streaming — G.I.L. Vision window is open and active on screen"
            if (self._camera_win[0] and self._camera_win[0].is_streaming())
            else "closed — no camera window is open"
        )
        try:
            response = self.brain.query(text, project_context=project_ctx,
                                         camera_state=_cam_state)
        except Exception as exc:
            log.error("Brain error", exc_info=True)
            unmute()
            window.set_state("listening" if self.conv.active else "standby")
            return

        if not response:
            unmute(); window.set_state("listening"); return

        speech        = response.get("speech", "")
        action        = response.get("action")
        target        = response.get("target") or ""
        extra_actions = response.get("extra_actions") or []

        if is_greeting_response(speech):
            print(f"[G.I.L.] Suppressed greeting loop: {speech!r}")
            speech = ""

        # Reroute build→build_website if web words detected
        if action in ("build", "prompt_project") and (
            any(w in lower for w in self._WEBGEN_WORDS)
            or any(w in target.lower() for w in self._WEBGEN_WORDS)
        ):
            log.debug("rerouting %r -> build_website", action)
            action = "build_website"
            target = target or text

        if action == "show_settings":
            speech = "Done."

        # Fire instant actions before speak() blocks
        if action in self._INSTANT_ACTIONS:
            threading.Thread(target=self._dispatch_instant, args=(action, target),
                             daemon=True, name=f"GIL-{action}").start()
            if self._active_project[0] and target and action in ("open_url", "web_search"):
                try:
                    from learning_projects import add_resource
                    kind = "video" if "youtube" in target.lower() else "url"
                    add_resource(self._active_project[0], kind, target, target[:80])
                except Exception:
                    pass

        # Speak the response
        if speech:
            self._last_said[0] = speech
            self._last_addressed_at[0] = time.time()
            window.set_state("speaking", said=speech)
            window.add_chat_message(speech, "gil")
            delivered = speak(speech)
            if delivered:
                self._last_spoke_at[0] = time.time()
            else:
                unmute()
            try:
                from memory import extract_memories_background
                extract_memories_background(text, speech)
                preferences.learn_from_exchange(text, speech)
            except Exception:
                pass
            if self._active_project[0]:
                try:
                    from learning_projects import add_conversation
                    add_conversation(self._active_project[0], text, speech)
                except Exception:
                    pass
        else:
            unmute()

        if response.get("report"):
            print(f"\n[G.I.L. REPORT]\n{response['report']}\n")

        # ── Action dispatch ───────────────────────────────────────────────────
        from action_router import dispatch as _route
        async_took_over = _route(action, target, speech, text, lower,
                                  extra_actions, self)
        if async_took_over:
            return

        window.set_state("listening")

    # ── Instant (fire-and-forget) action dispatcher ───────────────────────────

    def _dispatch_instant(self, action: str, target: str) -> None:
        if action == "build":
            _WEB_SIGNALS = {
                "website", "webpage", "landing", "web app", "web application",
                "html", "frontend", "front-end", "ui", "home page", "homepage",
            }
            if any(w in target.lower() for w in _WEB_SIGNALS):
                def _bw(t=target):
                    from ears import unmute
                    ack = "On it — give me about 30 seconds."
                    self.window.set_state("speaking", said=ack)
                    self._last_spoke_at[0] = time.time() + 120
                    self._speak(ack)
                    self._last_spoke_at[0] = time.time()
                    self._last_said[0] = ack
                    self.window.show_webgen_progress()
                    try:
                        from webgen import generate as _wg, generate_for_project as _wgp, _find_web_project
                        proj   = _find_web_project(t)
                        result = _wgp(proj) if proj else _wg(t)
                    except Exception as exc:
                        result = f"Website generation failed — {exc.__class__.__name__}."
                        self._last_spoke_at[0] = time.time()
                    self.window.close_webgen_progress(); unmute()
                    self.window.set_state("speaking", said=result); self._speak(result)
                    self._last_spoke_at[0] = time.time() - 1.5
                    self._last_said[0] = result
                    self.window.set_state("listening")
                threading.Thread(target=_bw, daemon=True, name="GIL-WebGen").start()
                return
            handle_build(target)
        elif action == "prompt_project":
            handle_prompt_project(target)
        elif action == "open_terminal":
            from actions import open_terminal
            open_terminal(target)
        else:
            self._execute_action(action, target)

    # ── STT callback ──────────────────────────────────────────────────────────

    def on_utterance(self, text: str) -> None:
        log.info("heard: %r", text[:100])
        lower = text.lower().replace(".", "").replace(",", "")

        # "Gil stop" — kill TTS immediately
        _STOP_NOW = {"gil stop", "stop gil", "stop talking", "stop speaking"}
        if any(p in lower for p in _STOP_NOW):
            try:
                from voice import stop_speaking
                stop_speaking()
            except Exception:
                pass
            self.window.set_state("listening")
            return

        # Single-word interrupt while speaking
        try:
            from voice import is_speaking, stop_speaking
            _INTERRUPT = {"stop", "cancel", "enough", "quiet", "silence"}
            if is_speaking() and (any(w in lower.split() for w in _INTERRUPT)
                                   or "shut up" in lower):
                stop_speaking()
                self.window.set_state("listening")
                return
        except Exception:
            pass

        # Pause mode
        _STOP_PHRASES = {"be quiet", "shut up", "go away"}
        if not self._paused[0] and any(p in lower for p in _STOP_PHRASES):
            self._paused[0] = True
            speech = "I'll be quiet. Just say my name when you need me."
            self.window.set_state("speaking", said=speech)
            self._speak(speech)
            self._last_said[0]     = speech
            self._last_spoke_at[0] = time.time()
            self.window.set_state("standby")
            return

        if self._paused[0]:
            if "gil" in lower.split() or lower.startswith("gil") or "hey gil" in lower:
                self._paused[0] = False
                speech = "I'm here."
                self.window.set_state("speaking", said=speech)
                self._speak(speech)
                self._last_said[0]     = speech
                self._last_spoke_at[0] = time.time() - 1.5
                self.window.set_state("listening")
            return

        # 3D shape visualizer
        try:
            from viewer3d import detect_shape
            shape = detect_shape(text)
            if shape and any(t in lower for t in {
                "show", "draw", "display", "visualize", "what does",
                "what is", "model", "3d", "explain",
            }):
                self.window.show_3d(shape)
                msg = "Here's the holographic model — drag it to rotate."
                self.window.set_state("speaking", said=msg)
                self._speak(msg)
                self._last_spoke_at[0] = time.time() - 1.5
                self.window.set_state("listening")
                if self._active_project[0]:
                    try:
                        from learning_projects import add_resource
                        add_resource(self._active_project[0], "3d_model", shape, f"3D: {shape}")
                    except Exception:
                        pass
                return
        except Exception as exc:
            log.warning("Visualizer error: %s", exc)

        # Song ID
        _SONG_TRIGGERS = {
            "what song", "what's this song", "identify this song",
            "what's playing", "shazam this", "name this song",
            "what is this song", "listen to this", "id this song",
        }
        if any(t in lower for t in _SONG_TRIGGERS):
            if not self.conv.active:
                self.conv.activate()
                from ears import set_passive
                set_passive(False)
                self.window.after(0, self.window.show_window)
            def _song_id():
                from ears import mute, unmute
                from actions import identify_song, get_spotify_now_playing
                self.window.set_state("processing", heard=text)
                track = get_spotify_now_playing()
                if track:
                    msg = f"Spotify is playing {track}."
                    self.window.set_state("speaking", said=msg)
                    self._speak(msg)
                    self._last_spoke_at[0] = time.time() - 1.5
                    self.window.set_state("listening")
                    return
                prompt = "Go ahead — playing near the mic."
                self.window.set_state("speaking", said=prompt)
                self._speak(prompt)
                time.sleep(0.5)
                mute(); time.sleep(0.2)
                def _on_result(msg):
                    unmute()
                    self.window.set_state("speaking", said=msg)
                    self._speak(msg)
                    self._last_spoke_at[0] = time.time() - 1.5
                    self.window.set_state("listening")
                self.window.set_state("listening")
                identify_song(_on_result)
            threading.Thread(target=_song_id, daemon=True, name="GIL-SongID").start()
            return

        # User-defined macro triggers
        try:
            from triggers import match_trigger, fuzzy_match_trigger
            trig = match_trigger(text) or fuzzy_match_trigger(text)
            if trig:
                if not self.conv.active:
                    self.conv.activate()
                    from ears import set_passive
                    set_passive(False)
                    self.window.after(0, self.window.show_window)
                threading.Thread(target=self._run_trigger, args=(trig,),
                                 daemon=True, name="GIL-Trigger").start()
                return
        except Exception as exc:
            log.warning("Trigger check failed: %s", exc)

        # Wake phrase
        if contains_wake_phrase(lower):
            log.info("wake phrase: %r", text[:80])
            if not self.conv.active:
                self.conv.activate()
                from ears import set_passive
                set_passive(False)
            self.window.after(0, self.window.show_window)
            self.window.set_state("listening")
            after = strip_wake_phrase(text)
            if after:
                threading.Thread(target=self._process, args=(after,),
                                 daemon=True, name="GIL-Process").start()
            else:
                self._speak_wake_prompt()
            return

        # Active conversation window
        if self.conv.active:
            addressed = is_addressed(lower)
            if addressed:
                self._last_addressed_at[0] = time.time()
            in_window = (time.time() - self._last_addressed_at[0]) < self.ATTENTION_SECS
            if addressed or in_window:
                threading.Thread(target=self._process, args=(text,),
                                 daemon=True, name="GIL-Process").start()
            else:
                log.debug("not addressed — ignored: %r", text[:50])

    # ── Manual activate ───────────────────────────────────────────────────────

    def _manual_activate(self) -> None:
        already_active = self.conv.active
        if not already_active:
            self.conv.activate()
            from ears import set_passive
            set_passive(False)
        self.window.after(0, self.window.show_window)
        self.window.set_state("listening")
        if not already_active or time.time() - self._last_spoke_at[0] > 30:
            self._speak_wake_prompt()
        log.info("manually activated")
