"""
notes.py — Project G.I.L.
Voice notes (saved to JSON) and clipboard history watcher.
"""

import ctypes
import json
import threading
import time
from datetime import datetime
from pathlib import Path

_NOTES_FILE = Path(__file__).parent / "data" / "voice_notes.json"
_CLIP_FILE  = Path(__file__).parent / "data" / "clip_history.json"
_MAX_CLIP   = 50

_watcher_running = [False]


# ── Voice notes ───────────────────────────────────────────────────────────────

def save_note(text: str) -> str:
    notes = _load_notes()
    notes.append({"text": text, "timestamp": datetime.now().isoformat()})
    _save_notes(notes)
    return f"Note saved: {text[:60]}."


def list_notes(n: int = 5) -> str:
    notes = _load_notes()
    if not notes:
        return "No voice notes saved yet."
    parts = []
    for note in reversed(notes[-n:]):
        dt = note["timestamp"][:16].replace("T", " ")
        parts.append(f"[{dt}] {note['text'][:80]}")
    return "\n".join(parts)


def _load_notes() -> list:
    try:
        if _NOTES_FILE.exists():
            return json.loads(_NOTES_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _save_notes(notes: list) -> None:
    _NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _NOTES_FILE.write_text(json.dumps(notes, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Clipboard history ─────────────────────────────────────────────────────────

def get_clip_history(n: int = 10) -> str:
    hist = _load_clip_history()
    if not hist:
        return "Clipboard history is empty."
    parts = [f"{i + 1}. {h['text'][:80]}" for i, h in enumerate(reversed(hist[-n:]))]
    return "\n".join(parts)


def _load_clip_history() -> list:
    try:
        if _CLIP_FILE.exists():
            return json.loads(_CLIP_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _save_clip_history(hist: list) -> None:
    _CLIP_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CLIP_FILE.write_text(
        json.dumps(hist[-_MAX_CLIP:], indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _read_clipboard_text() -> str | None:
    CF_UNICODETEXT = 13
    u32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32
    if not u32.OpenClipboard(None):
        return None
    try:
        h = u32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        ptr = k32.GlobalLock(h)
        if not ptr:
            return None
        try:
            text = ctypes.wstring_at(ptr)
        finally:
            k32.GlobalUnlock(h)
        return text or None
    except Exception:
        return None
    finally:
        u32.CloseClipboard()


def start_clipboard_watcher() -> None:
    if _watcher_running[0]:
        return
    _watcher_running[0] = True

    def _watch():
        last = ""
        while _watcher_running[0]:
            try:
                text = _read_clipboard_text()
                if text and text != last and 1 < len(text) < 5000:
                    last = text
                    hist = _load_clip_history()
                    hist.append({"text": text[:500], "timestamp": datetime.now().isoformat()})
                    _save_clip_history(hist)
            except Exception:
                pass
            time.sleep(2)

    threading.Thread(target=_watch, daemon=True, name="GIL-ClipWatcher").start()
    print("[G.I.L. NOTES] Clipboard watcher active.")
