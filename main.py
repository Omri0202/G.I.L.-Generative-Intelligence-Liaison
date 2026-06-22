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

# ── Single-instance guard ─────────────────────────────────────────────────────
_MUTEX = ctypes.windll.kernel32.CreateMutexW(None, True, "ProjectGIL_SingleInstance")
if ctypes.windll.kernel32.GetLastError() == 183:   # ERROR_ALREADY_EXISTS
    ctypes.windll.user32.MessageBoxW(0, "G.I.L. is already running.", "G.I.L.", 0)
    sys.exit(0)

from logger import get as _get_log

log = _get_log("main")

from auth import run_login
import context_engine
import goal_tracker
import proactive
import session_manager


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

    username = run_login()
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
    goal_tracker.start()
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

    goal_tracker.on_checkin(
        lambda msg: window.after(0, lambda m=msg: window.show_proactive_suggestion(m))
    )

    def _on_ctx_change(ctx: dict) -> None:
        app = ctx.get("app", "")
        proactive.set_active_app(app)
        session_manager.record_app_switch()
        # App-switch popups removed — too noisy

    context_engine.on_context_changed(_on_ctx_change)

    # ── Session summary (every 10 min + on shutdown) ──────────────────────────
    def _save_session_memory() -> None:
        try:
            from session_memory import save as _save_mem
            _save_mem(engine.brain.history)
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
                "model": "llama-3.1-8b-instant",
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

    session_manager.on_shutdown(_on_shutdown)

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

    window.after(500, window.show_window)
    window.mainloop()
    session_manager.trigger_shutdown(username)
    try:
        from chat_history import end_session
        end_session()
    except Exception:
        pass
    log.info("session terminated")


if __name__ == "__main__":
    main()
