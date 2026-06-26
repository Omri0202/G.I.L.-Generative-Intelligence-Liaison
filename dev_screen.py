"""
dev_screen.py — G.I.L. developer tools
Smart screen monitor. Periodically captures the screen, analyses it with a
vision-capable Groq model, and proactively alerts the user when it detects
errors, failed tests, exceptions, or obvious bugs in visible code.

The user can then say "yes fix it" and GIL will analyse the problem and
provide a solution or offer to apply it directly.

IMPORTANT: This runs only in DEVELOPER MODE (toggled by the user).
It is NOT active by default — it would be too intrusive.
"""

import base64
import io
import json
import os
import re
import threading
import time
import requests
from logger import get as _get_log

log = _get_log("dev.screen")

# ── Config ────────────────────────────────────────────────────────────────────
_INTERVAL    = 30       # seconds between screen checks
_COOLDOWN    = 90       # don't re-alert for same issue within N seconds
_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
_GROQ_URL    = "https://api.groq.com/openai/v1/chat/completions"

# ── State ─────────────────────────────────────────────────────────────────────
_active      = False
_last_issue  = ""
_last_alert  = 0.0
_speak_fn    = None
_window_ref  = None


# ── Screen capture ────────────────────────────────────────────────────────────

def _capture() -> bytes | None:
    """Capture current screen as compressed JPEG bytes."""
    try:
        from PIL import ImageGrab, Image
        img = ImageGrab.grab()
        # Downscale so it fits in the vision model context (max 1280px wide)
        w, h = img.size
        if w > 1280:
            img = img.resize((1280, int(h * 1280 / w)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        return buf.getvalue()
    except Exception as exc:
        log.debug("screen capture failed: %s", exc)
        return None


# ── Vision analysis ───────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a developer assistant watching a programmer's screen. "
    "Analyse this screenshot and detect ONLY genuine, visible developer problems: "
    "error messages, exceptions, failed tests, compilation errors, runtime crashes, "
    "obvious syntax errors in visible code, or red CI indicators. "
    "Be conservative — only flag something if it is clearly visible and clearly a problem. "
    "Do NOT flag: normal terminal output, running code, empty editors, browser UIs. "
    "Respond ONLY with valid JSON:\n"
    '{"has_issue": true/false, '
    '"category": "error|test_failure|syntax|crash|ci|null", '
    '"description": "one concise sentence or null", '
    '"can_fix": true/false}'
)


def _analyse(image_bytes: bytes) -> dict | None:
    groq_key = os.getenv("GROQ_API_KEY", "") or os.getenv("GROQ_API_KEY_2", "")
    if not groq_key:
        return None

    b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "model": _VISION_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text",      "text": _SYSTEM_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
        "max_tokens": 150,
        "temperature": 0.1,
    }

    try:
        resp = requests.post(
            _GROQ_URL,
            headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
            json=payload, timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        m   = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as exc:
        log.debug("vision analysis failed: %s", exc)
    return None


def get_fix_suggestion(description: str) -> str:
    """Ask the brain for a fix suggestion given an error description."""
    groq_key = os.getenv("GROQ_API_KEY", "") or os.getenv("GROQ_API_KEY_2", "")
    if not groq_key:
        return "Could not reach AI for fix suggestion."
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system",
             "content": "You are a senior developer. Give a concise fix (2-3 sentences max) for the issue described. Be direct."},
            {"role": "user", "content": f"Issue: {description}\nHow do I fix this?"},
        ],
        "max_tokens": 200,
        "temperature": 0.2,
    }
    try:
        resp = requests.post(
            _GROQ_URL,
            headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
            json=payload, timeout=15,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        log.error("fix suggestion failed: %s", exc)
        return "Could not generate fix suggestion."


# ── Watcher loop ──────────────────────────────────────────────────────────────

def _loop():
    global _last_issue, _last_alert, _active
    log.info("developer screen watcher started (every %ds)", _INTERVAL)

    while _active:
        time.sleep(_INTERVAL)
        if not _active:
            break

        img = _capture()
        if not img:
            continue

        result = _analyse(img)
        if not result or not result.get("has_issue"):
            continue

        desc = result.get("description") or ""
        can_fix = result.get("can_fix", False)
        if not desc:
            continue

        now = time.time()
        # Suppress if same issue alerted recently
        if desc == _last_issue and (now - _last_alert) < _COOLDOWN:
            continue

        _last_issue = desc
        _last_alert = now

        category = result.get("category", "error")
        log.info("screen issue detected [%s]: %s", category, desc[:80])

        # Build the notification message
        if can_fix:
            msg = f"I see {desc}. Want me to help fix it?"
        else:
            msg = f"I notice {desc}."

        if _window_ref:
            try:
                _window_ref.after(0, lambda m=msg: _window_ref.show_proactive_suggestion(m))
                if _speak_fn:
                    threading.Thread(
                        target=lambda m=msg: _speak_fn(m),
                        daemon=True, name="GIL-ScreenAlert",
                    ).start()
            except Exception as exc:
                log.error("failed to show screen alert: %s", exc)


# ── Public API ────────────────────────────────────────────────────────────────

def start(window, speak_fn) -> None:
    """Start the developer screen watcher. Call once from main.py."""
    global _active, _speak_fn, _window_ref
    if _active:
        return
    _active     = True
    _speak_fn   = speak_fn
    _window_ref = window
    threading.Thread(target=_loop, daemon=True, name="GIL-DevScreen").start()
    log.info("developer screen watcher active")


def stop() -> None:
    global _active
    _active = False
    log.info("developer screen watcher stopped")


def is_active() -> bool:
    return _active
