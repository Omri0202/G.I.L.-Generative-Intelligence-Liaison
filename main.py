"""
main.py — Project G.I.L.
Entry point. Starts the GUI, wires background services, launches ConversationEngine.
"""

import os
import sys
import time
import threading
from dotenv import load_dotenv

load_dotenv()

# ── 1. Check if running on Cloud / Render (Headless environment) ──────────────
IS_RENDER = "RENDER" in os.environ or os.environ.get("HEADLESS") == "true"

# ── 2. Single-instance guard (Windows Desktop Only) ───────────────────────────
_MUTEX = None
if not IS_RENDER and sys.platform == "win32":
    import ctypes
    _MUTEX = ctypes.windll.kernel32.CreateMutexW(None, True, "ProjectGIL_SingleInstance")
    if ctypes.windll.kernel32.GetLastError() == 183:   # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.MessageBoxW(0, "G.I.L. is already running.", "G.I.L.", 0)
        sys.exit(0)

from logger import get as _get_log
log = _get_log("main")

# ── 3. Safe Imports: Bypass Desktop GUI on Render ─────────────────────────────
if not IS_RENDER:
    try:
        from auth import run_login
    except Exception as e:
        log.warning(f"GUI Auth bypass due to import failure: {e}")

import context_engine
import goal_tracker
import proactive
import session_manager

# ── 4. Main Execution Entrypoint ──────────────────────────────────────────────
def main():
    if IS_RENDER:
        log.info("Starting GIL in Cloud/Headless Web Mode for Render...")
        try:
            # Dynamically import web app to prevent loading tkinter
            from web_chat.app import app
            port = int(os.environ.get("PORT", 10000))
            app.run(host="0.0.0.0", port=port)
        except Exception as err:
            log.error(f"Failed to launch Web App on Render: {err}")
            while True:
                time.sleep(3600)
    else:
        log.info("Starting GIL in Desktop Mode...")
        try:
            run_login()
        except NameError:
            log.error("run_login is not available in headless mode.")

if __name__ == "__main__":
    main()