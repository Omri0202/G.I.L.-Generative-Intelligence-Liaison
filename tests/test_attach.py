"""
File / photo attachment backend (drag-drop · paste · paperclip). The vision path
needs the network; here we cover saving, validation, routing and image detection.
Run: python -m pytest tests/test_attach.py -q
"""

import base64
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import chat_web


def _web():
    w = chat_web.WebChat.__new__(chat_web.WebChat)
    w._out = []
    w._pushes = []
    w._brain = []
    w.add_message = lambda t, who="gil": w._out.append((who, t))
    w.add_image_message = lambda p: w._out.append(("img", str(p)))
    w._push = lambda m, p=None: w._pushes.append(m)
    w._persist = lambda *a, **k: None
    w._gil_window = types.SimpleNamespace(
        _engine=None, _chat_send_fn=lambda t: w._brain.append(t))
    return w


def _b64(raw: bytes, mime="application/octet-stream"):
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def _wait(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


# ── validation ────────────────────────────────────────────────────────────────

def test_rejects_unreadable_data():
    assert _web()._rpc_attach("x.txt", "not-base64!!", "")["ok"] is False


def test_rejects_empty_file():
    assert _web()._rpc_attach("x.txt", _b64(b""), "")["ok"] is False


def test_rejects_oversized_file():
    big = _b64(b"x" * (12_000_001))
    assert _web()._rpc_attach("big.bin", big, "")["ok"] is False


# ── text file → brain context ─────────────────────────────────────────────────

def test_text_file_is_saved_and_handed_to_the_brain():
    w = _web()
    res = w._rpc_attach("snippet.py", _b64(b"def hello():\n    return 42\n"), "what does this do?")
    assert res["ok"] and res["image"] is False
    assert any(who == "user" and "snippet.py" in t for who, t in w._out)
    assert _wait(lambda: w._brain)
    ctx = w._brain[0]
    assert "snippet.py" in ctx and "return 42" in ctx and "what does this do?" in ctx


def test_empty_text_file_reports_cleanly_without_calling_the_brain():
    w = _web()
    w._rpc_attach("blank.txt", _b64(b"   \n  "), "")
    assert _wait(lambda: any("readable text" in t for _, t in w._out))
    assert not w._brain


# ── image detection (routing only; vision itself needs the network) ───────────

def test_image_extension_is_detected_and_previewed():
    w = _web()
    # a 1x1 PNG
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
    res = w._rpc_attach("photo.png", _b64(png, "image/png"), "")
    assert res["ok"] and res["image"] is True
    # the thumbnail is rendered; the vision answer arrives on a worker thread
    assert any(kind == "img" for kind, _ in w._out)


def test_saved_file_lands_in_the_uploads_folder():
    w = _web()
    w._rpc_attach("note.txt", _b64(b"hello world"), "")
    updir = Path(chat_web.__file__).parent / "data" / "uploads"
    assert any(p.name.endswith("_note.txt") for p in updir.glob("*_note.txt"))
