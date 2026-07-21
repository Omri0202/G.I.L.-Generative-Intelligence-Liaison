"""
visual_inspector.py — Visual Bug Inspector: the screen → chat → fix pipeline.

Ctrl+Shift+S freezes the screen, the user types what's wrong, and GIL:
  1. captures the screen natively (PIL ImageGrab),
  2. READS it with a vision model, then
  3. hands that reading plus the user's report to the coding agent, which finds
     the real source files and applies the fix.

Two deliberate architecture notes:

* Capture happens HERE, in Python — not in the browser. A web page can only
  screenshot via getDisplayMedia(), which raises a "share your screen?" picker
  on every single invocation and cannot capture the page's own window cleanly.
  That would wreck the "instant" feel. GIL is a desktop app and can already
  grab the framebuffer directly (this is what screen_vision.py does).

* The pipeline is two-stage because the coding agent's model (gpt-oss-120b) is
  TEXT-ONLY — it cannot receive an image at all. So the screenshot goes to the
  vision model (Llama-4 Scout) first and is converted into a dense textual
  reading; that text is what reaches the agent's tool-execution loop.
"""

from __future__ import annotations

import base64
import io
import os
import time
from pathlib import Path

import requests

from logger import get as _get_log

log = _get_log("visual")

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# The model itself lives in vision_model.py (single source of truth) — eyes.py,
# screen_vision.py and dev_screen.py now go through the same provider.
_SHOT_DIR = Path(__file__).parent / "data" / "screenshots"
_KEEP_SHOTS = 20


def _keys() -> list[str]:
    return [k for k in (os.getenv("GROQ_API_KEY", ""),
                        os.getenv("GROQ_API_KEY_2", "")) if k]


def _prune() -> None:
    """Keep the screenshot folder from growing without bound."""
    try:
        shots = sorted(_SHOT_DIR.glob("shot_*.jpg"), key=lambda p: p.stat().st_mtime)
        for old in shots[:-_KEEP_SHOTS]:
            old.unlink(missing_ok=True)
    except Exception:
        pass


def capture(max_w: int = 1600) -> dict:
    """Grab the screen. Returns {ok, b64, path, w, h} — b64 for the frozen
    overlay preview, path so the shot can be attached to the chat message."""
    try:
        from PIL import ImageGrab, Image
    except Exception:
        return {"ok": False, "error": "Pillow isn't available, so I can't capture the screen."}
    try:
        import vision_model
        img = ImageGrab.grab()
        full_w, full_h = img.size

        def _jpeg(im, width, quality):
            if im.width > width:
                im = im.resize((width, int(im.height * width / im.width)), Image.LANCZOS)
            buf = io.BytesIO()
            im.convert("RGB").save(buf, format="JPEG", quality=quality)
            return buf.getvalue()

        # Two renditions on purpose: a crisp one the user sees in the chat, and a
        # small one for the model — the 8000 TPM ceiling counts image tokens, so a
        # full-resolution payload 429s every time.
        preview = _jpeg(img, max_w, 78)
        _SHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = _SHOT_DIR / f"shot_{int(time.time() * 1000)}.jpg"
        path.write_bytes(preview)
        _prune()
        payload = _jpeg(img, vision_model.SEND_WIDTH, 70)
        return {"ok": True, "b64": base64.b64encode(payload).decode(),
                "preview_b64": base64.b64encode(preview).decode(),
                "path": str(path), "w": full_w, "h": full_h}
    except Exception as exc:
        log.error("screen capture failed", exc_info=True)
        return {"ok": False, "error": f"Screen capture failed: {exc}"}


_VISION_SYSTEM = (
    "You are the eyes of a coding agent. You are shown a screenshot of the user's "
    "screen and their report of what is wrong with it. Describe ONLY what you can "
    "actually see, in dense factual detail the agent can act on:\n"
    "- exact error text, exception names, tracebacks, failing test names, line numbers\n"
    "- the application/window, file names, and any visible code\n"
    "- for a UI/layout bug: which element, where it sits, and how it is visibly wrong\n"
    "Quote on-screen text verbatim — file paths and error strings are what the agent "
    "will search for. If the user's report does not match anything you can see, say so "
    "plainly. Never invent a filename, a line number, or an error you cannot read. "
    "No preamble, no advice, no fix suggestions — observation only."
)


def read_screen(b64: str, user_text: str, timeout: int = 45) -> dict:
    """Send the shot to the vision model. Returns {ok, reading} or {ok: False, error}."""
    if not b64:
        return {"ok": False, "error": "no image"}
    keys = _keys()
    if not keys:
        return {"ok": False, "error": "No Groq API key is configured, so I can't read the screen."}
    payload = {
        # qwen3.6 is a REASONING model — it spends tokens inside <think> before
        # answering, so a tight budget yields a truncated thought and no answer.
        "max_tokens": 2200,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": _VISION_SYSTEM},
            {"role": "user", "content": [
                {"type": "text",
                 "text": f"The user reports: {user_text or '(no description given)'}\n\n"
                         "Describe what you see that is relevant to this."},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]},
        ],
    }
    import vision_model
    ok, reading, err = vision_model.chat(
        payload["messages"], max_tokens=payload["max_tokens"],
        temperature=payload["temperature"], timeout=timeout)
    if ok:
        return {"ok": True, "reading": reading}
    log.warning("vision read failed: %s", err)
    return {"ok": False, "error": err or "vision read failed"}


def build_task(reading: str, user_text: str, shot_path: str = "") -> str:
    """Compose the coding-agent task from the screen reading + the user's report."""
    parts = [
        "A screenshot of the user's screen was captured and read by a vision model. "
        "You cannot see the image yourself — this reading is your only view of it.",
        "",
        "WHAT THE USER REPORTED:",
        (user_text or "(they didn't describe it — work it out from the screen reading)").strip(),
        "",
        "WHAT IS ACTUALLY ON SCREEN:",
        (reading or "(the screen could not be read)").strip(),
        "",
        "YOUR JOB: find the REAL cause in the source and fix it.",
        "- Search the project for the exact error text, file names and symbols quoted above; "
        "do not guess at a filename.",
        "- Read the relevant file before changing it, then make the smallest correct fix.",
        "- The reading is a description of pixels, not ground truth about the code: if it "
        "conflicts with what the files actually say, trust the files and say so.",
        "- If the screen shows a problem in software that is not this project, do not invent "
        "an edit — report what you found and stop.",
    ]
    if shot_path:
        parts.append(f"\n(The screenshot is saved at {shot_path} for reference.)")
    return "\n".join(parts)


def inspect(user_text: str, b64: str = "", shot_path: str = "") -> dict:
    """Full read step: capture if needed, then read the screen.
    Returns {ok, reading, path, task} ready to hand to the coding agent."""
    if not b64:
        shot = capture()
        if not shot.get("ok"):
            return {"ok": False, "error": shot.get("error", "capture failed")}
        b64, shot_path = shot["b64"], shot["path"]
    seen = read_screen(b64, user_text)
    if not seen.get("ok"):
        return {"ok": False, "error": seen.get("error", "vision failed"), "path": shot_path}
    return {"ok": True, "reading": seen["reading"], "path": shot_path,
            "task": build_task(seen["reading"], user_text, shot_path)}
