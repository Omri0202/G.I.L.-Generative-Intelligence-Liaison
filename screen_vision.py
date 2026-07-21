"""
screen_vision.py — G.I.L. actually SEES the screen.

Captures the display, sends it to a vision LLM (Groq Llama-4 Scout, with
Claude as the premium option when a key exists), and returns a real visual
understanding — errors, UI state, what the user is looking at.

Used by the "analyze_screen" action ("look at my screen") and to enrich
fix_error with pixels instead of just window titles.
"""

import base64
import io
import os

import requests

from logger import get as _get_log

log = _get_log("scrvis")

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# The model now lives in vision_model.py — see that module for why. Hardcoding it
# in three separate files is what let a single Groq decommission kill every
# vision feature at once, silently.


def _capture_b64(max_w: int = 0) -> str | None:
    # default follows vision_model.SEND_WIDTH — a full-size grab exceeds the
    # 8000 TPM ceiling on image tokens alone and every call 429s
    if not max_w:
        import vision_model
        max_w = vision_model.SEND_WIDTH
    try:
        from PIL import ImageGrab, Image
        img = ImageGrab.grab()
        if img.width > max_w:
            img = img.resize((max_w, int(img.height * max_w / img.width)),
                             Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=72)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        log.error("screen capture failed", exc_info=True)
        return None


def describe(question: str = "") -> str:
    """Look at the screen and answer. Returns a text description or an error."""
    b64 = _capture_b64()
    if not b64:
        return "I couldn't capture the screen."

    ask = (question.strip() or
           "Describe what is on this screen. If there is an error message, "
           "dialog, or anything notable, quote it exactly.")

    try:
        import activity
        aid = activity.start("vision", "Looking at your screen")
    except Exception:
        aid = None

    # One shared vision provider (vision_model.py) — it owns the model id, key
    # rotation and reasoning-block stripping. This module used to hardcode the
    # model, which is why it died silently when Groq decommissioned it.
    import vision_model
    ok, answer, err = vision_model.chat(
        vision_model.image_message(ask, b64), max_tokens=1600, timeout=45)
    result = answer if ok else None
    if not ok:
        log.warning("screen vision failed: %s", err)

    if aid is not None:
        try:
            import activity
            (activity.done if result else activity.fail)(
                aid, (result or "no vision model reachable")[:100])
        except Exception:
            pass
    return result or ("I couldn't analyze the screen — no vision model is "
                      "reachable right now.")
