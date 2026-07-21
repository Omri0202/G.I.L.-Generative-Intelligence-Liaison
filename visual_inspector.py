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


def foreground_window() -> tuple:
    """(bbox, title) of the active window, or (None, "") if it can't be resolved.

    Uses DWM's extended frame bounds rather than GetWindowRect: the latter
    includes the invisible resize border Windows pads around a window, which
    would crop in slivers of whatever is behind it."""
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return None, ""
        rect = wintypes.RECT()
        got = False
        try:
            # DWMWA_EXTENDED_FRAME_BOUNDS = 9
            if ctypes.windll.dwmapi.DwmGetWindowAttribute(
                    wintypes.HWND(hwnd), ctypes.c_uint(9),
                    ctypes.byref(rect), ctypes.sizeof(rect)) == 0:
                got = True
        except Exception:
            pass
        if not got:
            u.GetWindowRect(hwnd, ctypes.byref(rect))
        n = u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        box = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
        if box[2] - box[0] < 80 or box[3] - box[1] < 80:
            return None, buf.value            # degenerate/minimised — use the screen
        return box, buf.value
    except Exception:
        log.debug("foreground window lookup failed", exc_info=True)
        return None, ""


def capture(max_w: int = 1600, region=None, whole_screen: bool = False) -> dict:
    """Grab the ACTIVE WINDOW by default (sharp text, fewer tokens), or an
    explicit `region` bbox, or the whole desktop when asked.

    Returns {ok, b64, preview_b64, path, w, h, title, scope}. `b64` is the
    token-budgeted payload for the model; `preview_b64`/`path` stay crisp for
    the chat thumbnail."""
    try:
        from PIL import ImageGrab, Image
    except Exception:
        return {"ok": False, "error": "Pillow isn't available, so I can't capture the screen."}
    try:
        import vision_model
        title, scope = "", "screen"
        bbox = None
        if region and len(region) == 4:
            bbox, scope = tuple(int(v) for v in region), "region"
        elif not whole_screen:
            bbox, title = foreground_window()
            if bbox:
                scope = "window"
        img = ImageGrab.grab(bbox=bbox) if bbox else ImageGrab.grab()
        if img.width < 40 or img.height < 40:     # bad bbox → fall back
            img, scope, bbox = ImageGrab.grab(), "screen", None
        full_w, full_h = img.size

        def _jpeg(im, width, quality):
            if im.width > width:
                im = im.resize((width, int(im.height * width / im.width)), Image.LANCZOS)
            buf = io.BytesIO()
            im.convert("RGB").save(buf, format="JPEG", quality=quality)
            return buf.getvalue()

        # Two renditions on purpose: a crisp one the user sees in the chat, and a
        # token-budgeted one for the model — the 8000 TPM ceiling counts image
        # tokens, so a full-resolution payload 429s every time.
        preview = _jpeg(img, max_w, 78)
        _SHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = _SHOT_DIR / f"shot_{int(time.time() * 1000)}.jpg"
        path.write_bytes(preview)
        _prune()
        sized = vision_model.fit_for_send(img)
        pbuf = io.BytesIO()
        sized.convert("RGB").save(pbuf, format="JPEG", quality=82)
        payload = pbuf.getvalue()
        return {"ok": True, "b64": base64.b64encode(payload).decode(),
                "preview_b64": base64.b64encode(preview).decode(),
                "path": str(path), "w": full_w, "h": full_h,
                "sent_w": sized.size[0], "sent_h": sized.size[1],
                "title": title, "scope": scope}
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


def inspect(user_text: str, b64: str = "", shot_path: str = "",
            title: str = "") -> dict:
    """Full read step: capture if needed, then read the screen.
    Returns {ok, reading, path, task} ready to hand to the coding agent."""
    if not b64:
        shot = capture()
        if not shot.get("ok"):
            return {"ok": False, "error": shot.get("error", "capture failed")}
        b64, shot_path, title = shot["b64"], shot["path"], shot.get("title", "")
    seen = read_screen(b64, user_text)
    if not seen.get("ok"):
        return {"ok": False, "error": seen.get("error", "vision failed"), "path": shot_path}
    reading = seen["reading"]
    if title:
        reading = f"(window: {title})\n{reading}"
    return {"ok": True, "reading": reading, "path": shot_path,
            "task": build_task(reading, user_text, shot_path)}
