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

    # How long GIL stays active after the last addressed utterance.
    # 8s for statements (user must say "GIL" again after ~8s).
    # 15s after GIL asks a question (expects a follow-up answer).
    # Industry standard: Alexa=8s, Google=5-8s, Siri=~0s (single-turn).
    ATTENTION_SECS  = 8.0
    QUESTION_SECS   = 15.0

    # Minimum gap between Groq brain calls.
    # Prevents flooding the API when ambient speech keeps triggering GIL.
    GROQ_MIN_GAP    = 5.0

    _INSTANT_ACTIONS = {"open_url", "open_app", "web_search", "prompt_project"}
    _WEBGEN_WORDS   = __import__("constants").WEBGEN_WORDS

    def __init__(self, username: str, window):
        self.username = username
        self.window   = window

        from gil_brain import GILBrain
        self.brain = GILBrain(username=username)
        self.brain.last_tokens_used   = 0
        self.brain.last_prompt_tokens = 0
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

        # Attention-management state
        self._last_was_question = False   # True → use QUESTION_SECS window
        self._last_groq_call_at = 0.0    # rate-limit guard for Groq calls

        # speak() function — set in start()
        self._speak = None

    # ── Public: expose state for main.py callbacks ────────────────────────────

    @property
    def pending_recap_global(self) -> list:
        return self._pending_recap_g

    def trim_last_exchange(self) -> None:
        """
        Remove the last user + assistant pair from brain history.
        Called by ChatWindow when user clicks Regenerate so the brain
        doesn't have a duplicate or ghost user message.
        """
        h = self.brain.history
        if h and h[-1].get("role") == "assistant":
            h.pop()
        if h and h[-1].get("role") == "user":
            h.pop()
        log.debug("trimmed last exchange from brain history (%d messages remain)", len(h))

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

        # ── Expose chat callback, history trim, and floating button ─────────────
        self.window._chat_send_fn    = self._process_chat
        self.window._trim_history_fn = self.trim_last_exchange
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
    def _is_ambient_speech(lower: str) -> bool:
        """
        Heuristic: does this look like background conversation rather than a GIL command?
        Returns True → filter it out (don't process, don't hit Groq).

        Common ambient signals:
        - Third-person pronouns (user is talking ABOUT someone, not TO GIL)
        - Conversational fillers with no imperative structure
        - Very short utterances with no action word
        """
        words = lower.split()
        if not words:
            return True

        # Very short — likely a filler or background word
        if len(words) <= 2:
            return True

        # Third-person pronouns in the first 3 words = talking about someone else
        _THIRD = {"he", "she", "they", "her", "him", "them", "his", "hers", "their"}
        if any(w in _THIRD for w in words[:3]):
            return True

        # Pure conversational filler — clearly talking to a person
        _FILLERS = {
            "i know", "i see", "oh really", "no way", "for real", "you know",
            "like i said", "i mean", "oh yeah", "of course", "right right",
            "that makes sense", "i agree", "totally", "absolutely", "exactly",
            "good point", "fair enough", "makes sense", "true true", "yeah yeah",
            "oh wow", "no problem", "not really", "kind of", "sort of",
        }
        if any(phrase in lower for phrase in _FILLERS):
            return True

        return False

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

        # ── Fast-path handlers (see fast_path_handler.py) ──────────────────────
        from fast_path_handler import process as _fast
        if _fast(text, lower, self):
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

        # Update Groq call timestamp for rate-limit guard
        self._last_groq_call_at = time.time()

        # Speak the response
        if speech:
            self._last_said[0] = speech
            self._last_addressed_at[0] = time.time()

            # Track whether GIL ended with a question — controls follow-up window.
            # If it's a statement, collapse the attention window so GIL doesn't
            # keep listening to ambient conversation after responding.
            self._last_was_question = speech.rstrip().endswith("?")
            if not self._last_was_question:
                # Collapse window: user must re-address GIL after ~4s
                self._last_addressed_at[0] = time.time() - (self.ATTENTION_SECS - 4.0)

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

            now        = time.time()
            time_since = now - self._last_addressed_at[0]

            # Dynamic attention window:
            # - 15 s if GIL just asked a question (expects a follow-up answer)
            # - 8 s otherwise (tight window prevents ambient speech processing)
            window    = self.QUESTION_SECS if self._last_was_question else self.ATTENTION_SECS
            in_window = time_since < window

            if addressed or in_window:
                # Filter 1 — ambient speech detection
                # If the user didn't name GIL and it sounds like background conversation, drop it
                if in_window and not addressed and self._is_ambient_speech(lower):
                    log.debug("ambient speech filtered: %r", text[:50])
                    return

                # Filter 2 — Groq rate limit guard
                # If another Groq call was made very recently and this utterance
                # didn't name GIL directly, drop it to avoid flooding the API
                if not addressed and (now - self._last_groq_call_at) < self.GROQ_MIN_GAP:
                    log.debug("groq rate-limit guard: dropped %r", text[:40])
                    return

                threading.Thread(target=self._process, args=(text,),
                                 daemon=True, name="GIL-Process").start()
            else:
                log.debug("outside attention window — ignored: %r", text[:50])

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
