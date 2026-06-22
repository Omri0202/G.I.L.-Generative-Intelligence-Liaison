"""
action_router.py — G.I.L.
Routes brain response actions to their handlers.

Extracted from ConversationEngine._do_process so that file stays readable.
The function dispatch() takes the engine instance and all relevant state,
routes to the correct handler, and returns True when an async thread
has taken ownership of set_state("listening") so the caller should return.
"""

import threading
from logger import get as _get_log
from constants import WEBGEN_WORDS

log = _get_log("router")


def dispatch(
    action: str,
    target: str,
    speech: str,
    text: str,
    lower: str,
    extra_actions: list,
    engine,
) -> bool:
    """
    Execute the action returned by the brain.
    Returns True  → an async handler owns the state; caller must return.
    Returns False → caller does window.set_state("listening") as normal.
    """
    speak        = engine._speak
    window       = engine.window
    run_action   = engine._execute_action
    last_spoke_at = engine._last_spoke_at
    last_said    = engine._last_said

    from action_handlers import (
        handle_save_credential, handle_list_credentials, handle_delete_credential,
        handle_create_project, handle_add_task, handle_complete_task,
        handle_create_3d,
    )

    # ── Credentials ───────────────────────────────────────────────────────────
    if action == "save_credential":
        handle_save_credential(target, speak, window)

    elif action == "list_credentials":
        handle_list_credentials(speak)

    elif action == "delete_credential":
        handle_delete_credential(target, speak)

    # ── Settings / UI ─────────────────────────────────────────────────────────
    elif action == "show_settings":
        window.after(0, window.open_settings)

    # ── Tasks ─────────────────────────────────────────────────────────────────
    elif action == "create_project":
        handle_create_project(target, window)

    elif action == "add_task":
        handle_add_task(target, window)

    elif action == "complete_task":
        handle_complete_task(target, window)

    elif action == "list_tasks":
        window.refresh_tasks()
        window.after(0, window.show_window)

    # ── System actions ────────────────────────────────────────────────────────
    elif action in ("system_vitals", "sign_in", "take_screenshot"):
        run_action(action, target)

    # ── 3-D ───────────────────────────────────────────────────────────────────
    elif action == "create_3d":
        threading.Thread(
            target=handle_create_3d,
            args=(target, engine._active_project[0] or ""),
            daemon=True, name="GIL-3DStudio",
        ).start()

    # ── Build / terminal ──────────────────────────────────────────────────────
    elif action in ("build", "open_terminal", "prompt_project"):
        threading.Thread(
            target=engine._dispatch_instant, args=(action, target),
            daemon=True, name=f"GIL-{action}",
        ).start()

    # ── Window management ─────────────────────────────────────────────────────
    elif action in ("focus_window", "arrange_windows", "close_window",
                    "minimize_all", "maximize_window", "open_file",
                    "read_file", "list_directory", "find_file",
                    "set_clipboard", "get_clipboard"):
        result = run_action(action, target)
        if result and action in ("read_file", "list_directory", "find_file", "get_clipboard"):
            log.info("result: %s", result[:200])

    # ── TV / mode / PC ────────────────────────────────────────────────────────
    elif action == "tv":
        threading.Thread(target=lambda: run_action("tv", target),
                         daemon=True, name="GIL-TV").start()

    elif action == "set_mode":
        run_action("set_mode", target)

    elif action in ("pc", "pc_sleep", "pc_lock", "pc_restart", "pc_shutdown"):
        threading.Thread(target=lambda a=action: run_action(a, target),
                         daemon=True, name="GIL-PC").start()

    elif action == "pc_volume":
        threading.Thread(target=lambda: run_action("pc_volume", target),
                         daemon=True, name="GIL-PCVol").start()

    # ── Async fetchers (return True — thread handles set_state) ───────────────
    elif action == "weather":
        def _weather():
            from ears import unmute
            result = run_action("weather", target)
            unmute()
            if result:
                window.set_state("speaking", said=result); speak(result)
                last_spoke_at[0] = __import__("time").time()
                last_said[0] = result
            window.set_state("listening")
        threading.Thread(target=_weather, daemon=True, name="GIL-Weather").start()
        return True

    elif action == "reminder":
        result = run_action("reminder", target)
        if result and result != speech:
            window.set_state("speaking", said=result); speak(result)
            last_spoke_at[0] = __import__("time").time()
            last_said[0] = result

    elif action == "list_reminders":
        def _reminders():
            from ears import unmute
            result = run_action("list_reminders", "")
            unmute()
            if result:
                window.set_state("speaking", said=result); speak(result)
                last_spoke_at[0] = __import__("time").time()
                last_said[0] = result
            window.set_state("listening")
        threading.Thread(target=_reminders, daemon=True, name="GIL-Reminders").start()
        return True

    elif action == "note":
        run_action("note", target)

    elif action == "list_notes":
        result = run_action("list_notes", "")
        if result:
            window.set_state("speaking", said=result); speak(result)
            last_spoke_at[0] = __import__("time").time()
            last_said[0] = result

    elif action == "clip_history":
        result = run_action("clip_history", "")
        if result:
            window.set_state("speaking", said=result); speak(result)
            last_spoke_at[0] = __import__("time").time()
            last_said[0] = result

    elif action == "spotify":
        def _spotify():
            result = run_action("spotify", target)
            if result and result != speech and any(
                w in result.lower() for w in
                ("couldn't", "failed", "not", "error", "isn't", "no ", "check")
            ):
                window.set_state("speaking", said=result); speak(result)
                last_spoke_at[0] = __import__("time").time()
                last_said[0] = result
                window.set_state("listening")
        threading.Thread(target=_spotify, daemon=True, name="GIL-Spotify").start()

    elif action == "briefing":
        def _briefing():
            from ears import unmute
            result = run_action("briefing", target)
            unmute()
            if result:
                window.set_state("speaking", said=result); speak(result)
                last_spoke_at[0] = __import__("time").time()
                last_said[0] = result
            window.set_state("listening")
        threading.Thread(target=_briefing, daemon=True, name="GIL-Briefing").start()
        return True

    elif action in ("calendar", "add_event", "news", "my_location"):
        def _fetch(act=action, tgt=target):
            from ears import unmute
            result = run_action(act, tgt)
            unmute()
            if result:
                window.set_state("speaking", said=result); speak(result)
                last_spoke_at[0] = __import__("time").time()
                last_said[0] = result
            window.set_state("listening")
        threading.Thread(target=_fetch, daemon=True, name=f"GIL-{action}").start()
        return True

    elif action in ("nearby", "directions", "food_delivery", "open_article"):
        threading.Thread(target=lambda: run_action(action, target),
                         daemon=True, name=f"GIL-{action}").start()

    # ── Camera (open) ─────────────────────────────────────────────────────────
    elif action == "open_camera":
        if engine._camera_win[0] and engine._camera_win[0].is_streaming():
            threading.Thread(target=engine._camera_win[0].bring_to_front,
                             daemon=True, name="GIL-CamFocus").start()
        else:
            _cam_ref = [None]
            def _cam_closed_cb():
                if engine._camera_win[0] is _cam_ref[0]:
                    engine._camera_win[0] = None
                engine._stop_gesture_watcher()
            def _open():
                from ears import unmute
                try:
                    from eyes import CameraWindow
                    c = CameraWindow(on_close=_cam_closed_cb)
                    _cam_ref[0] = c
                    engine._camera_win[0] = c
                    engine._start_gesture_watcher()
                    if not c.bring_to_front():
                        unmute()
                        fail = "Camera didn't open — make sure nothing else is using it."
                        window.set_state("speaking", said=fail); speak(fail)
                        last_spoke_at[0] = __import__("time").time() - 1.5
                        last_said[0] = fail
                        window.set_state("listening")
                except Exception:
                    unmute()
                    fail = "Camera failed to start. Make sure it's connected."
                    log.error("camera open failed", exc_info=True)
                    window.set_state("speaking", said=fail); speak(fail)
                    last_spoke_at[0] = __import__("time").time() - 1.5
                    last_said[0] = fail
                    window.set_state("listening")
            threading.Thread(target=_open, daemon=True, name="GIL-CamOpen").start()

    elif action == "close_camera":
        if engine._camera_win[0]:
            try:
                engine._camera_win[0].close()
            except Exception:
                pass
            engine._camera_win[0] = None
            engine._stop_gesture_watcher()

    # ── Website generation ────────────────────────────────────────────────────
    elif action == "build_website":
        def _webgen(desc=target or speech, utterance=text):
            from ears import unmute
            import time as _t
            ack = "On it — give me about 30 seconds."
            window.set_state("speaking", said=ack)
            last_spoke_at[0] = _t.time() + 120
            speak(ack)
            last_spoke_at[0] = _t.time()
            last_said[0] = ack
            window.show_webgen_progress()
            try:
                from webgen import generate as _wg, generate_for_project as _wgp, _find_web_project
                proj   = _find_web_project(utterance)
                result = _wgp(proj) if proj else _wg(desc)
            except Exception as exc:
                result = f"Website generation failed — {exc.__class__.__name__}."
                last_spoke_at[0] = _t.time()
            window.close_webgen_progress(); unmute()
            window.set_state("speaking", said=result); speak(result)
            last_spoke_at[0] = _t.time() - 1.5
            last_said[0] = result
            window.set_state("listening")
        threading.Thread(target=_webgen, daemon=True, name="GIL-WebGen").start()
        return True

    elif action == "look":
        def _look(q=target):
            from ears import unmute
            from eyes import look
            result = look(question=q)
            unmute()
            if result and result != speech:
                window.set_state("speaking", said=result); speak(result)
                last_spoke_at[0] = __import__("time").time() - 1.5
                last_said[0] = result
            window.set_state("listening")
        threading.Thread(target=_look, daemon=True, name="GIL-Look").start()
        return True

    # ── Extra actions (multi-task brain response) ─────────────────────────────
    for ea_item in extra_actions:
        ea_action = ea_item.get("action")
        ea_target = ea_item.get("target") or ""
        if not ea_action:
            continue
        if ea_action in ("build", "prompt_project") and (
            any(w in lower for w in WEBGEN_WORDS)
            or any(w in ea_target.lower() for w in WEBGEN_WORDS)
        ):
            ea_action = "build_website"
            ea_target = ea_target or text
        log.debug("extra action: %s -> %s", ea_action, ea_target[:60])
        threading.Thread(
            target=engine._dispatch_instant, args=(ea_action, ea_target),
            daemon=True, name=f"GIL-extra-{ea_action}",
        ).start()

    return False
