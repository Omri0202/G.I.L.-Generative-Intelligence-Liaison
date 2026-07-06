"""
error_watch.py — G.I.L. proactively spots on-screen errors.

A lightweight background loop samples the active window's title/text for
crash/error signatures. When something new and error-shaped appears, GIL
uses screen vision to read it, then offers — once, unprompted — to fix it.
The user answers yes/no in the chat, which routes into the existing
fix_error → code-agent flow (approval before every change).

Cheap by design: only fires the vision model when a *new* error signature
shows up, and rate-limited so it never nags.
"""

import threading
import time

from logger import get as _get_log

log = _get_log("errwatch")

# Substrings that strongly suggest an error is visible on screen.
_ERROR_SIGNS = (
    "error", "exception", "traceback", "failed", "cannot find",
    "is not recognized", "syntaxerror", "modulenotfound", "segmentation fault",
    "unhandled", "fatal", "stack trace", "нет", "panic:", "err_",
    "not found", "permission denied", "crash", "no such file",
)
# Ignore chrome that merely contains the word "error" as UI (browsers, GIL itself)
_IGNORE_TITLES = ("g.i.l", "gil —", "google chrome", "- youtube",
                  "error_watch", "new tab")

_POLL_SECS      = 6.0
_COOLDOWN_SECS  = 90.0   # after offering, stay quiet at least this long


class ErrorWatcher:
    def __init__(self, engine):
        self.engine = engine
        self._last_offer_at = 0.0
        self._last_sig = ""
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, daemon=True,
                         name="GIL-ErrorWatch").start()
        log.info("proactive error watcher started")

    def stop(self):
        self._stop.set()

    def _enabled(self) -> bool:
        try:
            import json
            from pathlib import Path
            cfg = json.loads((Path(__file__).parent / "data" / "gil_config.json")
                             .read_text(encoding="utf-8"))
            return bool(cfg.get("proactive_fix", True))
        except Exception:
            return True

    def _screen_text(self) -> tuple[str, str]:
        """Return (active window title, any quick screen text) lowercased."""
        try:
            from context_engine import get_screen_context
            ctx = get_screen_context() or ""
        except Exception:
            ctx = ""
        title = ""
        try:
            import ctypes
            h = ctypes.windll.user32.GetForegroundWindow()
            buf = ctypes.create_unicode_buffer(512)
            ctypes.windll.user32.GetWindowTextW(h, buf, 512)
            title = buf.value or ""
        except Exception:
            pass
        return title.lower(), ctx.lower()

    def _loop(self):
        while not self._stop.is_set():
            time.sleep(_POLL_SECS)
            try:
                if not self._enabled():
                    continue
                # Don't interrupt while GIL is mid-task
                eng = self.engine
                if eng.processing_lock.locked():
                    continue
                if time.time() - self._last_offer_at < _COOLDOWN_SECS:
                    continue

                title, ctx = self._screen_text()
                if any(ig in title for ig in _IGNORE_TITLES):
                    continue
                haystack = title + " " + ctx
                if not any(sig in haystack for sig in _ERROR_SIGNS):
                    self._last_sig = ""
                    continue

                # Build a small signature so the same error isn't re-offered
                sig = "".join(c for c in haystack if c.isalnum())[:120]
                if sig and sig == self._last_sig:
                    continue
                self._last_sig = sig
                self._offer_fix()
            except Exception:
                log.debug("watch loop error", exc_info=True)

    def _offer_fix(self):
        """Look at the screen, confirm it's a real error, and offer to fix it."""
        eng = self.engine
        try:
            from screen_vision import describe
            seen = describe(
                "Is there a software error, crash, exception or failed command "
                "visible on this screen? If yes, quote the exact error text in "
                "one line. If there is NO error, reply exactly: NONE.")
        except Exception:
            return
        if not seen or "none" == seen.strip().lower()[:4]:
            return
        self._last_offer_at = time.time()

        # Arm the same pending-fix gate the fix_error flow uses, so a "yes"
        # launches the code agent with approval-per-change.
        eng._pending_fix = {
            "task": f"Fix this error seen on screen:\n{seen}",
            "commands": [],
        }
        msg = (f"I noticed an error on your screen:\n\n> {seen.strip()[:300]}\n\n"
               "Want me to fix it? I'll show you every change before I make it. "
               "Say **yes** or **no**.")
        try:
            eng.window.add_chat_message(msg, "gil")
            spoken = "I spotted an error on your screen — want me to fix it?"
            eng.window.set_state("speaking", said=spoken)
            eng._speak(spoken)
            eng._last_spoke_at[0] = time.time() - 1.5
            eng._last_said[0] = spoken
            eng.window.set_state("listening")
        except Exception:
            log.debug("offer failed", exc_info=True)
