"""
main.py — Project G.I.L.
Entry point. Starts the GUI, wires background services, launches ConversationEngine.

What used to live here → where it lives now:
  Fast-path resolvers  →  fast_paths.py
  Wake phrase helpers  →  wake_phrase.py
  Action handlers      →  action_handlers.py
  System tray          →  tray_manager.py
  Audio loop + logic   →  conversation_engine.py  (ConversationEngine class)
"""

import os
import sys
import time
import threading
import ctypes
from dotenv import load_dotenv

load_dotenv()

# ── A Vigil notification was clicked (gil-vigil:open, in the built app) ───────
# Bring the Vigil window forward through whichever process hosts it. If none
# does, carry on and start GIL, which then opens Vigil.
_OPEN_VIGIL = "--open-vigil" in sys.argv
if _OPEN_VIGIL:
    try:
        import vigil_chat as _vc
        if _vc.open_from_outside():
            sys.exit(0)
    except Exception:
        pass

# ── Single-instance guard ─────────────────────────────────────────────────────
import sys

_MUTEX = None
if sys.platform == "win32":
    import ctypes
    _MUTEX = ctypes.windll.kernel32.CreateMutexW(None, True, "ProjectGIL_SingleInstance")
    if ctypes.windll.kernel32.GetLastError() == 183:   # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.MessageBoxW(0, "G.I.L. is already running.", "G.I.L.", 0)
        sys.exit(0)

from logger import get as _get_log

log = _get_log("main")

from auth import run_login
import context_engine
import memory
import profile_store
import proactive


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    # Tell Windows this is G.I.L., not python.exe
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("G.I.L.App.1")
    except Exception:
        pass

    # First-run setup wizard
    from setup_wizard import is_setup_complete, run_wizard
    if not is_setup_complete():
        completed = run_wizard()
        if not completed:
            sys.exit(0)
        load_dotenv(override=True)

    log.info("=" * 52)
    log.info("  G.I.L. starting up")
    log.info("=" * 52)

    # ── Biometric front door ──────────────────────────────────────────────────
    # Look at the camera first; a recognised face signs you straight in. Every
    # other outcome — no camera, nobody enrolled, an unfamiliar face, Esc — falls
    # through to the password window below, so this can never lock you out.
    username = None
    scanner_ran = False
    try:
        from auth import get_saved_session
        # Only a MINUTES-OLD session skips the front door now. It used to be
        # permanent, so after the very first sign-in GIL never looked at a face
        # or asked for a password again — the scanner and the password window
        # simply stopped appearing, which is what "it jumped straight in" was.
        if not get_saved_session():
            from face_login import scan_for_user
            scanner_ran = True
            username = scan_for_user()
            if username:
                from auth import save_session
                save_session(username)
                log.info("biometric sign-in: %s", username)
    except Exception:
        log.debug("biometric sign-in unavailable", exc_info=True)

    if not username:
        # The camera didn't know them, or they pressed "Use password instead".
        # Either way the next step must actually ASK: falling back to a saved
        # session here would sign them straight in and make that button a
        # no-op, which is exactly how it behaved.
        username = run_login(force=scanner_ran)
        # Enrol a face ONLY after a real sign-in, and only for someone who has
        # none. This used to run on every start, and with a saved session that
        # was the whole visible login experience: the login scanner above is
        # skipped, run_login returns instantly without asking anything, and
        # then the ENROLMENT scanner opened — a fullscreen face window that
        # looks exactly like the login one but is not. Pressing "Use password
        # instead" on it cancelled enrolment and let GIL carry on, because the
        # user had already been signed in three steps earlier. The button was
        # working; it was on the wrong window.
        if username and scanner_ran:
            try:
                from face_id import FaceID
                already = FaceID().has_enrolled()
            except Exception:
                already = True          # never re-enrol on a broken check
            if not already:
                try:
                    from face_login import enroll_user
                    if enroll_user(username):
                        log.info("enrolled a new face for %s", username)
                except Exception:
                    log.debug("face enrolment skipped", exc_info=True)
    if not username:
        log.warning("authentication aborted — exiting")
        sys.exit(0)

    log.info("identity confirmed: %s", username)

    # ── Chat history ──────────────────────────────────────────────────────────
    try:
        from chat_history import init_session, clear_old
        init_session()
        clear_old(days=30)   # prune messages older than 30 days on startup
    except Exception as exc:
        log.error("Chat history init failed", exc_info=True)

    # ── Intelligence engines ──────────────────────────────────────────────────
    context_engine.start()
    profile_store.start_goal_tracking()
    proactive.start()

    try:
        from location import get_location as _get_loc
        threading.Thread(target=_get_loc, daemon=True, name="GIL-LocWarm").start()
    except Exception:
        pass

    # ── GUI ───────────────────────────────────────────────────────────────────
    from gui import GILWindow
    window = GILWindow(username=username)

    # ── Conversation engine ───────────────────────────────────────────────────
    from conversation_engine import ConversationEngine
    engine = ConversationEngine(username=username, window=window)

    # ── Proactive recap callback ───────────────────────────────────────────────
    proactive.set_show_callback(
        lambda msg: window.after(0, lambda m=msg: window.show_proactive_suggestion(m))
    )

    def _proactive_recap_callback(msg: str, recap_type: str = "", items: list = None) -> None:
        try:
            from modes import is_proactive_blocked
            if is_proactive_blocked():
                return
        except Exception:
            pass
        window.after(0, lambda m=msg: window.show_proactive_suggestion(m))
        engine.pending_recap_global[0] = {"type": recap_type, "items": items or []}

        def _speak_it():
            from voice import speak as _speak, is_speaking
            for _ in range(180):
                busy = (
                    is_speaking()
                    or engine.processing_lock.locked()
                    or (time.time() - engine._last_addressed_at[0]) < ConversationEngine.ATTENTION_SECS
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

    # ── Background services ───────────────────────────────────────────────────
    try:
        import gmail_recap
        gmail_recap.set_show_callback(
            lambda msg, items=[]: _proactive_recap_callback(msg, "email", items)
        )
        gmail_recap.start_periodic_check(interval_secs=1800)
    except Exception as exc:
        log.warning("Gmail recap disabled: %s", exc)

    try:
        import whatsapp_recap
        whatsapp_recap.set_show_callback(
            lambda msg, items=[]: _proactive_recap_callback(msg, "wa", items)
        )
        whatsapp_recap.start_periodic_check(interval_secs=1800)
    except Exception as exc:
        log.warning("WhatsApp recap disabled: %s", exc)

    # ── Vigil: standing orders, checked in the background, and the separate
    #    chat where GIL texts you about them ("open vigil", /vigil, or the tray).
    #    While GIL was closed the watcher kept Vigil going; it hands over once
    #    it sees GIL, and until then GIL opens the watcher's chat.
    def _host_vigil():
        import vigil
        import vigil_chat
        role = vigil_chat.start(username)
        while role not in ("host", "link"):
            time.sleep(3)
            role = vigil_chat.start(username)
        vigil.set_busy_check(lambda: (
            engine.processing_lock.locked()
            or bool(getattr(engine, "_agent_running", False))
            or (time.time() - engine._last_addressed_at[0]) < ConversationEngine.ATTENTION_SECS))
        if role == "host":                   # with Vigil in the cloud, the checks run there
            vigil.start()
        # Jobs texted from the phone run here, and his replies go back to it.
        vigil_chat.connect_gil(
            send=lambda text: window._chat_send_fn(text),
            busy=lambda: (engine.processing_lock.locked()
                          or bool(getattr(engine, "_agent_running", False))),
            show=lambda text: window.add_chat_message(text, "user"))
        window.gil_event = vigil_chat.gil_event
        if _OPEN_VIGIL:
            vigil_chat.open_window()

    def _host_vigil_safely():
        try:
            _host_vigil()
        except Exception as exc:
            log.warning("Vigil disabled: %s", exc)
    threading.Thread(target=_host_vigil_safely, daemon=True, name="GIL-VigilHost").start()

    # ── Model health: Groq retires models without warning ─────────────────────
    # It retired five GIL depended on and nothing noticed for months. One free
    # request at startup, and the user hears about it the same day.
    def _model_check():
        try:
            import model_health
            note = model_health.report()
            if note:
                log.warning(note)
                window.add_chat_message(note, "gil")
        except Exception:
            log.debug("model health check failed", exc_info=True)
    threading.Thread(target=_model_check, daemon=True, name="GIL-ModelHealth").start()

    try:
        import notes as _notes_mod
        _notes_mod.start_clipboard_watcher()
    except Exception as exc:
        log.warning("Clipboard watcher disabled: %s", exc)

    try:
        import meeting_detector as _meet_det
        import modes as _meet_modes
        _meet_det.set_mode_callback(_meet_modes.set_mode)
        _meet_det.start_meeting_watcher()
    except Exception as exc:
        log.warning("Meeting detector disabled: %s", exc)

    profile_store.on_checkin(
        lambda msg: window.after(0, lambda m=msg: window.show_proactive_suggestion(m))
    )

    def _on_ctx_change(ctx: dict) -> None:
        app = ctx.get("app", "")
        proactive.set_active_app(app)
        memory.record_app_switch()
        # App-switch popups removed — too noisy

    context_engine.on_context_changed(_on_ctx_change)

    # ── Session summary (every 10 min + on shutdown) ──────────────────────────
    def _save_session_memory() -> None:
        try:
            memory.save_history(engine.brain.history)
        except Exception as exc:
            log.warning("session memory save failed: %s", exc)

    def _save_summary() -> None:
        try:
            brain     = engine.brain
            user_msgs = [h["content"] for h in brain.history if h.get("role") == "user"]
            if not user_msgs:
                return
            transcript = "\n".join(f"- {m[:200]}" for m in user_msgs)
            import requests as _req, json as _j
            groq_key = os.getenv("GROQ_API_KEY", "") or os.getenv("GROQ_API_KEY_2", "")
            if not groq_key:
                return
            payload = {
                "model": "qwen/qwen3.8-27b",
                "messages": [
                    {"role": "system", "content":
                        "Reply with ONLY a JSON array of 2-5 short topic strings (2-4 words each). "
                        'Example: ["gesture fix", "face recognition", "cursor accuracy"]'},
                    {"role": "user", "content":
                        f"User messages:\n{transcript}\n\nList main topics as JSON array."},
                ],
                "max_tokens": 80, "temperature": 0.2,
            }
            resp   = _req.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json=payload, timeout=8,
            )
            topics = _j.loads(resp.json()["choices"][0]["message"]["content"].strip())
            if isinstance(topics, list) and topics:
                from memory import record_task
                record_task(_j.dumps(topics))
                log.info("session topics saved: %s", topics)
        except Exception as exc:
            log.warning("Summary save failed: %s", exc)

    def _periodic_summary():
        while True:
            time.sleep(600)
            try:
                _save_summary()
                _save_session_memory()
            except Exception:
                pass

    threading.Thread(target=_periodic_summary, daemon=True, name="GIL-SummarySave").start()

    def _on_shutdown(msg: str) -> None:
        from voice import speak
        _save_summary()
        _save_session_memory()
        try:
            speak(msg)
        except Exception:
            pass

    memory.on_shutdown(_on_shutdown)

    # ── Startup registration ──────────────────────────────────────────────────
    from gui import _set_startup, _get_startup_enabled
    if not _get_startup_enabled():
        _set_startup(True)
        log.info("registered for Windows startup")

    # ── Tray + watcher process ────────────────────────────────────────────────
    from tray_manager import start_tray
    start_tray(window)

    threading.Thread(
        target=engine.start,
        daemon=True,
        name="GIL-Engine",
    ).start()

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
    except Exception as exc:
        log.warning("watcher start failed: %s", exc)

    # Check for updates 30 s after launch — silent, never blocks startup
    try:
        from updater import check_and_notify as _check_update
        window.after(30_000, lambda: _check_update(window))
    except Exception:
        pass

    # Developer screen watcher — starts 60s after launch so GIL is fully ready
    try:
        from dev_screen import start as _start_dev_screen
        from voice import speak as _speak_ref
        window.after(60_000, lambda: _start_dev_screen(window, _speak_ref))
        log.info("developer screen watcher scheduled")
    except Exception as exc:
        log.warning("dev screen watcher unavailable: %s", exc)

    window.after(500, window.show_window)
    window.mainloop()
    memory.trigger_shutdown(username)
    try:
        from chat_history import end_session
        end_session()
    except Exception:
        pass
    log.info("session terminated")


if __name__ == "__main__":
    main()
