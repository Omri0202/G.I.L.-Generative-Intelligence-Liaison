"""
vision_model.py — the ONE place G.I.L.'s vision model is named.

Why this module exists: the model id used to be copy-pasted into eyes.py,
screen_vision.py and dev_screen.py. When Groq decommissioned
meta-llama/llama-4-scout-17b-16e-instruct, all three broke simultaneously and
silently (every call 404s), so GIL's whole "Vision" capability was dead while
still being advertised in the UI. Naming the model once means the next
decommission is a one-line change here.

Current model: qwen/qwen3.6-27b — at time of writing the only image-capable
model left on the account. Verified to identify colours it could not have
guessed and to OCR a traceback (file name + line number + exception) exactly.

IMPORTANT: qwen3.6 is a REASONING model. It emits a <think>…</think> block
before its answer, so callers must (a) allow a generous max_tokens or the
budget is spent thinking and the answer is truncated away, and (b) strip the
block before parsing. `chat()` handles both.
"""

from __future__ import annotations

import os
import re

import requests

from logger import get as _get_log

log = _get_log("vision")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

#: The vision model. Change here and every vision feature follows.
MODEL = "qwen/qwen3.6-27b"

#: Tried in order if MODEL is unavailable (e.g. decommissioned again).
FALLBACKS: list[str] = []

#: Max width to downscale a FULL-SCREEN capture to before sending. The account's
#: 8000 TPM ceiling counts image tokens: measured on a 1920x1080 desktop, 1000px
#: wide → 429, 760px wide → fine. Callers should keep any high-resolution copy
#: for display and send a shrunk one to the model.
SEND_WIDTH = 760

#: Image tokens scale with AREA, not width — so a budget in pixels is the honest
#: model. 760x428 (the measured-safe full-screen size) is ~325k px; 350k leaves a
#: little headroom. This is what makes region capture worth doing: a window
#: cropped out of the desktop spends the same budget on far less content, so the
#: text inside it lands several times larger.
SEND_MAX_PIXELS = 350_000


def fit_for_send(img):
    """Downscale a PIL image to the token-safe pixel budget, preserving aspect.
    Never upscales — a small window is sent at native resolution."""
    from PIL import Image
    import math
    w, h = img.size
    area = w * h
    if area <= SEND_MAX_PIXELS or area <= 0:
        return img
    s = math.sqrt(SEND_MAX_PIXELS / area)
    return img.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)


def keys() -> list[str]:
    return [k for k in (os.getenv("GROQ_API_KEY", ""),
                        os.getenv("GROQ_API_KEY_2", "")) if k]


def strip_thinking(text: str) -> str:
    """Remove a reasoning model's hidden chain-of-thought, keeping the answer."""
    if not text:
        return ""
    if "</think>" in text:
        text = text.split("</think>")[-1]
    text = re.sub(r"<think>.*?(?:</think>|$)", "", text, flags=re.S)
    return text.replace("<think>", "").replace("</think>", "").strip()


def available() -> bool:
    return bool(keys())


_STARVED = "model returned only reasoning, no answer"


def _humanise(raw: str, status: int) -> str:
    """Turn a provider error into something a person can act on.

    The raw text is a wall of quota jargon that also gets truncated mid-sentence
    ("Limit 200000,.7"), which tells the user nothing about what to do."""
    low = (raw or "").lower()
    if "tokens per day" in low or "tpd" in low:
        return ("I've used up today's image-reading allowance on the free tier. "
                "It resets tomorrow.")
    if "tokens per minute" in low or "tpm" in low or status == 429:
        return "I'm over my per-minute limit for images — try again in a minute."
    if status == 413:
        return "that image is too large for me to read."
    if raw:
        return raw[:160]
    return f"HTTP {status}"


def chat(messages: list, max_tokens: int = 2400, temperature: float = 0.2,
         timeout: int = 60) -> tuple[bool, str, str]:
    """One vision call. Returns (ok, answer, error). The answer already has the
    reasoning block stripped. Rotates API keys and model fallbacks on failure.

    A reasoning model can spend an ENTIRE token budget inside <think> and emit no
    answer at all — a dense full-screen capture does this readily. Rather than
    make every call site guess a big enough number, one budget escalation is
    retried automatically before reporting failure.
    """
    ks = keys()
    if not ks:
        return False, "", "no Groq API key configured"
    last = ""
    # Escalate only modestly: the reserved output counts toward the 8k TPM
    # ceiling, so a big retry budget turns a starved answer into a 429.
    for budget in (max_tokens, min(max(max_tokens * 2, 3000), 4000)):
        for model in [MODEL] + FALLBACKS:
            for key in ks:
                try:
                    r = requests.post(
                        GROQ_URL,
                        json={"model": model, "messages": messages,
                              "max_tokens": budget, "temperature": temperature},
                        headers={"Authorization": f"Bearer {key}",
                                 "Content-Type": "application/json"},
                        timeout=timeout)
                    if r.status_code != 200:
                        try:
                            raw_err = (r.json().get("error", {}).get("message") or "")
                        except Exception:
                            raw_err = ""
                        last = _humanise(raw_err, r.status_code)
                        if r.status_code in (400, 404):
                            break      # model itself is wrong — try the next model
                        if "for today" in last:
                            return False, "", last   # a daily cap won't clear on retry
                        continue       # rate limit / transient — try the next key
                    body = r.json()
                    try:                   # images are the biggest token spender
                        import token_meter
                        u = body.get("usage", {}) or {}
                        token_meter.record(body.get("model", model),
                                           u.get("total_tokens", 0),
                                           u.get("prompt_tokens", 0),
                                           u.get("completion_tokens", 0))
                    except Exception:
                        pass
                    answer = strip_thinking(
                        body["choices"][0]["message"].get("content") or "")
                    if answer:
                        return True, answer, ""
                    last = _STARVED
                except Exception as exc:
                    last = str(exc)[:160]
                    continue
        if last != _STARVED:
            break                      # not a budget problem — escalating won't help
        log.info("vision answer starved by reasoning at %d tokens — retrying larger",
                 budget)
    log.warning("vision call failed: %s", last)
    return False, "", last or "vision call failed"


def image_message(prompt: str, b64_jpeg: str) -> list:
    """Build the standard single-image chat payload."""
    return [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url",
         "image_url": {"url": f"data:image/jpeg;base64,{b64_jpeg}"}},
    ]}]
