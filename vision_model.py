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

from pathlib import Path

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


#: Budget for images where nobody is trying to read small text — a photo the
#: user attached, "what is this?", a picture of a thing rather than of a screen.
#:
#: Measured on this account: a 788x443 capture costs 2,426 prompt tokens and a
#: 326x183 one costs 786, which fits cost ≈ 450 + 0.0057 per pixel. Against the
#: 200,000 tokens-per-DAY vision limit that is 82 reads a day at full detail
#: against roughly 150 here — and running out of them is the actual complaint.
#: Screen captures keep the larger budget, because there the entire job is
#: reading text that is already small.
SCENE_MAX_PIXELS = 160_000


def fit_for_send(img, detail: str = "text"):
    """Downscale a PIL image to the token-safe pixel budget, preserving aspect.
    Never upscales — a small window is sent at native resolution.

    detail="text"   full budget, for screen captures where small type has to
                    stay legible. The Visual Bug Inspector depends on this.
    detail="scene"  a smaller budget for ordinary photos and attachments, where
                    the answer does not hinge on reading four-pixel glyphs.
    """
    from PIL import Image
    import math
    budget = SCENE_MAX_PIXELS if detail == "scene" else SEND_MAX_PIXELS
    w, h = img.size
    area = w * h
    if area <= budget or area <= 0:
        return img
    s = math.sqrt(budget / area)
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


# ── second-provider fallback, for when Groq's day is spent ───────────────────
#
# Reached ONLY when Groq reports its daily cap, so it cannot spend anything
# while the free allowance still has room. Both providers are inert without a
# key, so this file behaves exactly as before on a machine that has neither.
#
# On the two candidates, measured rather than assumed:
#   Google AI Studio — genuinely free and does accept image input, but Google
#     no longer publishes free-tier numbers (the docs point you at the AI Studio
#     dashboard), and developer reports through 2026 describe a steep cut to
#     Flash's daily request allowance. It may well be SMALLER than the ~146
#     scene reads a day Groq already gives. Worth having as a last resort, not
#     as the capacity increase it would once have been.
#   Anthropic — the key in this project's .env is valid and lists every model,
#     but the account has no credit, so it returns 400 until credits are bought.
#
_GEMINI_MODEL = os.getenv("GEMINI_VISION_MODEL", "gemini-2.5-flash")
_CLAUDE_MODEL = os.getenv("CLAUDE_VISION_MODEL", "claude-haiku-4-5-20251001")

#: Paid providers are OFF unless explicitly allowed. A valid key sitting in .env
#: is not consent to spend money — the Anthropic key here is exactly that case,
#: and if credit ever lands on it for something else GIL must not quietly start
#: billing image reads to it. Set GIL_ALLOW_PAID_VISION=1 to opt in.
def _paid_allowed() -> bool:
    return os.getenv("GIL_ALLOW_PAID_VISION", "").strip() in ("1", "true", "yes")


#: Groq bills vision by TOKEN, so image area is what costs there and everything
#: is downscaled hard. Google's free tier is bounded by REQUESTS PER DAY, and a
#: request costs one unit whether the image is 300px or 1000px wide. So once we
#: are on the fallback, shrinking the picture buys nothing and only throws away
#: detail — send it large and get a better reading for the same quota.
GEMINI_MAX_PIXELS = 1_200_000


def _split_image_message(messages: list):
    """Pull (prompt text, base64 jpeg) back out of the Groq-shaped message."""
    for m in reversed(messages):
        content = m.get("content")
        if not isinstance(content, list):
            continue
        text, b64 = "", ""
        for part in content:
            if part.get("type") == "text":
                text = part.get("text", "")
            elif part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url", "")
                if "base64," in url:
                    b64 = url.split("base64,", 1)[1]
        if b64:
            return text, b64
    return "", ""


def _read_with_gemini(text: str, b64: str, max_tokens: int, timeout: int):
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
    if not key:
        return False, "", ""
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{_GEMINI_MODEL}:generateContent")
    try:
        r = requests.post(url, params={"key": key}, timeout=timeout,
                          json={"contents": [{"parts": [
                              {"text": text or "Describe this image."},
                              {"inline_data": {"mime_type": "image/jpeg",
                                               "data": b64}}]}],
                                "generationConfig": {"maxOutputTokens": max_tokens}})
        if r.status_code != 200:
            return False, "", f"Gemini HTTP {r.status_code}"
        cands = r.json().get("candidates") or []
        parts = (cands[0].get("content", {}).get("parts") or []) if cands else []
        out = "".join(p.get("text", "") for p in parts).strip()
        return (True, out, "") if out else (False, "", "Gemini returned nothing")
    except Exception as exc:
        return False, "", f"Gemini {exc.__class__.__name__}"


def _read_with_claude(text: str, b64: str, max_tokens: int, timeout: int):
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        return False, "", ""
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=timeout,
                          headers={"x-api-key": key,
                                   "anthropic-version": "2023-06-01",
                                   "content-type": "application/json"},
                          json={"model": _CLAUDE_MODEL, "max_tokens": max_tokens,
                                "messages": [{"role": "user", "content": [
                                    {"type": "image", "source": {
                                        "type": "base64",
                                        "media_type": "image/jpeg", "data": b64}},
                                    {"type": "text",
                                     "text": text or "Describe this image."}]}]})
        if r.status_code != 200:
            msg = ""
            try:
                msg = r.json().get("error", {}).get("message", "")
            except Exception:
                pass
            if "credit balance" in msg.lower():
                return False, "", "Anthropic account has no credit"
            return False, "", f"Anthropic HTTP {r.status_code}"
        out = "".join(c.get("text", "") for c in r.json().get("content", [])).strip()
        return (True, out, "") if out else (False, "", "Anthropic returned nothing")
    except Exception as exc:
        return False, "", f"Anthropic {exc.__class__.__name__}"


def _encode_for_gemini(source_image):
    """Re-encode the ORIGINAL at Gemini's budget, since a request there costs
    one unit regardless of size. Returns base64, or "" if unavailable."""
    if source_image is None:
        return ""
    try:
        import base64 as _b64
        import io as _io
        import math
        from PIL import Image
        img = Image.open(source_image) if isinstance(source_image, (str, Path)) \
            else source_image
        w, h = img.size
        area = w * h
        if area > GEMINI_MAX_PIXELS:
            s = math.sqrt(GEMINI_MAX_PIXELS / area)
            img = img.resize((max(1, int(w * s)), max(1, int(h * s))),
                             Image.LANCZOS)
        buf = _io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=88)
        return _b64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def _fallback_read(messages, max_tokens, temperature, timeout,
                   source_image=None):
    """Try the other providers, freest first."""
    text, b64 = _split_image_message(messages)
    if not b64:
        return False, "", ""          # not an image call — nothing to fall back to

    errs = []
    # Google first, and at full detail: it is free, and its free tier is
    # bounded by requests per day rather than tokens, so a bigger picture
    # costs the same quota and reads far better.
    ok, out, err = _read_with_gemini(text, _encode_for_gemini(source_image) or b64,
                                     max_tokens, timeout)
    if ok:
        return True, out, ""
    if err:
        errs.append(err)

    if _paid_allowed():
        ok, out, err = _read_with_claude(text, b64, max_tokens, timeout)
        if ok:
            return True, out, ""
        if err:
            errs.append(err)
    return False, "", "; ".join(errs)


def chat(messages: list, max_tokens: int = 2400, temperature: float = 0.2,
         timeout: int = 60, source_image=None) -> tuple[bool, str, str]:
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
                            # Groq is dry until midnight. Before giving up, try a
                            # second provider — this is the ONLY point at which
                            # one is used, so a fallback can never cost anything
                            # while the free allowance still has room.
                            ok, ans, ferr = _fallback_read(
                                messages, max_tokens, temperature, timeout,
                                source_image)
                            if ok:
                                return True, ans, ""
                            return False, "", (last + (f" ({ferr})" if ferr else ""))
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
