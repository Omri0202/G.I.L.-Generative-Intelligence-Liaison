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

_GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"


def _capture_b64(max_w: int = 1400) -> str | None:
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

    # Claude vision first when available (better OCR + reasoning)
    ant_key = os.getenv("ANTHROPIC_API_KEY", "")
    result = None
    if ant_key:
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                json={"model": "claude-haiku-4-5-20251001", "max_tokens": 500,
                      "messages": [{"role": "user", "content": [
                          {"type": "image", "source": {"type": "base64",
                           "media_type": "image/jpeg", "data": b64}},
                          {"type": "text", "text": ask}]}]},
                headers={"x-api-key": ant_key,
                         "anthropic-version": "2023-06-01"},
                timeout=30)
            r.raise_for_status()
            result = r.json()["content"][0]["text"].strip()
        except Exception as exc:
            log.warning("Claude vision failed: %s", exc)

    if result is None:
        keys = [k for k in (os.getenv("GROQ_API_KEY", ""),
                            os.getenv("GROQ_API_KEY_2", "")) if k]
        for key in keys:
            try:
                r = requests.post(
                    _GROQ_URL,
                    json={"model": _GROQ_MODEL, "max_tokens": 500,
                          "messages": [{"role": "user", "content": [
                              {"type": "text", "text": ask},
                              {"type": "image_url", "image_url": {"url":
                               f"data:image/jpeg;base64,{b64}"}}]}]},
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"},
                    timeout=30)
                if r.status_code == 429:
                    continue
                r.raise_for_status()
                result = r.json()["choices"][0]["message"]["content"].strip()
                break
            except Exception as exc:
                log.warning("Groq vision failed: %s", exc)

    if aid is not None:
        try:
            import activity
            (activity.done if result else activity.fail)(
                aid, (result or "no vision model reachable")[:100])
        except Exception:
            pass
    return result or ("I couldn't analyze the screen — no vision model is "
                      "reachable right now.")
