"""
eyes.py — Project G.I.L.
Camera vision: live preview (subprocess) + Groq vision LLM for identification.
Uses meta-llama/llama-4-scout-17b-16e-instruct (multimodal, free on Groq).

CameraWindow launches camera_viewer.py as a subprocess — pure OpenCV, no tkinter.
Frames are shared via a temp JPEG file written by the viewer every 0.4 s.
"""

import base64
import os
import sys
import subprocess
import tempfile
import threading
import time
import requests
from pathlib import Path

_VISION_MODELS = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.2-11b-vision-preview",
]
_GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
_VIEWER       = Path(__file__).parent / "camera_viewer.py"
_FRAME_FILE   = Path(tempfile.gettempdir()) / "gil_cam_frame.jpg"

_DEFAULT_PROMPT = (
    "Describe what you see in this image in 1-2 sentences. "
    "Identify objects, text, brands, or people clearly. "
    "Be specific and direct — no filler."
)
_QUESTION_PROMPT = (
    "Answer this about the image: {question}\n"
    "Be concise — 1-2 sentences max."
)


# ── One-shot capture (used when camera window is closed) ──────────────────────

def capture_frame(camera_index: int = 0) -> bytes | None:
    """Capture a single JPEG frame from the webcam. Returns bytes or None."""
    try:
        import cv2
    except ImportError:
        return None

    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        return None

    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        for _ in range(5):
            cap.read()
        ret, frame = cap.read()
        if not ret or frame is None:
            return None
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return bytes(buf)
    finally:
        cap.release()


# ── Vision LLM ────────────────────────────────────────────────────────────────

def analyze_frame(image_bytes: bytes, question: str = "", groq_keys: list = None) -> str:
    if groq_keys is None:
        groq_keys = [k for k in [
            os.getenv("GROQ_API_KEY",   ""),
            os.getenv("GROQ_API_KEY_2", ""),
        ] if k]

    if not groq_keys:
        return "No Groq API key configured for vision."

    b64    = base64.b64encode(image_bytes).decode("utf-8")
    prompt = (
        _QUESTION_PROMPT.format(question=question.strip())
        if question.strip() else _DEFAULT_PROMPT
    )

    payload = {
        "model": _VISION_MODELS[0],
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": prompt},
            ],
        }],
        "max_tokens": 250,
        "temperature": 0.2,
    }

    last_error = "unknown error"
    for model in _VISION_MODELS:
        payload["model"] = model
        for key in groq_keys:
            try:
                hdrs = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
                resp = requests.post(_GROQ_URL, json=payload, headers=hdrs, timeout=20)
                if resp.status_code == 429:
                    last_error = "rate limited"; continue
                if resp.status_code == 400:
                    last_error = f"model {model} rejected request"; break  # try next model
                resp.raise_for_status()
                result = resp.json()["choices"][0]["message"]["content"].strip()
                print(f"[G.I.L. EYES] Vision OK via {model}")
                return result
            except Exception as exc:
                last_error = str(exc); continue

    return f"Vision analysis failed — {last_error}."


def look(question: str = "", groq_keys: list = None) -> str:
    """One-shot capture + analyze. Used when camera window isn't open."""
    print("[G.I.L. EYES] Capturing frame...")
    frame = capture_frame()
    if frame is None:
        return "I can't access the camera — make sure it's connected and not in use."
    print(f"[G.I.L. EYES] Frame captured ({len(frame):,} bytes). Analyzing...")
    result = analyze_frame(frame, question, groq_keys)
    print(f"[G.I.L. EYES] Result: {result[:140]}")
    return result


# ── Live camera preview (subprocess via PowerShell Start-Process) ─────────────

_KILL_FILE  = Path(tempfile.gettempdir()) / "gil_cam_kill.txt"


class CameraWindow:
    """
    Launches camera_viewer.py via PowerShell Start-Process — the only method
    confirmed to produce a visible cv2 window from inside GIL.
    Liveness tracked via frame-file freshness (viewer writes every 0.4 s).
    """

    def __init__(self, on_close=None):
        self._on_close = on_close
        self._dead     = False

        # Kill any stale camera_viewer.py processes (survive GIL restarts and hold camera device)
        try:
            import subprocess as _sp
            _sp.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "Get-WmiObject Win32_Process | Where-Object {$_.CommandLine -like '*camera_viewer*'}"
                 " | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
                creationflags=_sp.CREATE_NO_WINDOW,
                capture_output=True, timeout=3,
            )
        except Exception:
            pass

        # Send kill signal to any leftover instance tracked by frame file
        if _FRAME_FILE.exists():
            try:
                _KILL_FILE.write_text("kill")
                time.sleep(0.6)
            except Exception:
                pass
        for f in (_FRAME_FILE, _KILL_FILE):
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass

        self._born_at = time.time()

        # Grant any process permission to steal the foreground — required on Win 8+
        # for the camera subprocess to be able to call SetForegroundWindow on itself.
        # Must be called by the current foreground process; silently a no-op otherwise.
        try:
            import ctypes as _ct
            _ct.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        except Exception:
            pass

        # Write a .ps1 file to avoid all PowerShell quoting issues.
        # Start-Process via a script file is identical to running it from a PS terminal.
        _launcher = sys.executable.replace("python.exe", "pythonw.exe")
        if not Path(_launcher).exists():
            _launcher = sys.executable
        _ps1 = Path(tempfile.gettempdir()) / "gil_start_camera.ps1"
        _ps1.write_text(
            f"Start-Process -FilePath '{_launcher}' -ArgumentList '{_VIEWER}'\n"
        )
        subprocess.Popen(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(_ps1)],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        print("[G.I.L. EYES] Viewer launched via ps1 Start-Process.")

        threading.Thread(target=self._watch, daemon=True,
                         name="GIL-CamWatch").start()

    def _watch(self):
        for _ in range(40):          # wait up to 8 s for first frame
            if _FRAME_FILE.exists():
                break
            time.sleep(0.2)
        else:
            print("[G.I.L. EYES] Camera never started.")
            self._dead = True
            if self._on_close:
                try:
                    self._on_close()
                except Exception:
                    pass
            return

        while True:
            time.sleep(0.5)
            if not _FRAME_FILE.exists():
                break
            try:
                if time.time() - _FRAME_FILE.stat().st_mtime > 2.0:
                    break
            except Exception:
                break

        print("[G.I.L. EYES] Camera viewer stopped.")
        self._dead = True
        if self._on_close:
            try:
                self._on_close()
            except Exception:
                pass

    # ── Public API ────────────────────────────────────────────────────────────

    def get_current_frame(self) -> bytes | None:
        for _ in range(10):
            if _FRAME_FILE.exists():
                try:
                    data = _FRAME_FILE.read_bytes()
                    if len(data) > 1000:
                        return data
                except Exception:
                    pass
            time.sleep(0.2)
        return None

    def set_status(self, text: str):
        pass

    def bring_to_front(self) -> bool:
        """
        Poll until the cv2 window appears (up to 8 s), then force it to
        the foreground using AttachThreadInput — the only reliable way to
        steal focus from a background / detached process on Windows.
        Returns True if the window was found and raised.
        """
        import ctypes
        import ctypes.wintypes
        user32 = ctypes.windll.user32

        for _ in range(40):           # 40 × 0.2 s = up to 8 s
            hwnd = user32.FindWindowW(None, "G.I.L. Vision")
            if hwnd:
                fg_hwnd = user32.GetForegroundWindow()
                fg_tid  = user32.GetWindowThreadProcessId(fg_hwnd, None)
                tgt_tid = user32.GetWindowThreadProcessId(hwnd, None)
                if fg_tid != tgt_tid:
                    user32.AttachThreadInput(fg_tid, tgt_tid, True)
                user32.ShowWindow(hwnd, 9)        # SW_RESTORE
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
                if fg_tid != tgt_tid:
                    user32.AttachThreadInput(fg_tid, tgt_tid, False)
                print("[G.I.L. EYES] Camera window brought to front.")
                return True
            time.sleep(0.2)

        print("[G.I.L. EYES] Camera window not found after 8 s.")
        return False

    def is_streaming(self) -> bool:
        """True only if the viewer is actively writing frames right now."""
        if not _FRAME_FILE.exists():
            return False
        try:
            return time.time() - _FRAME_FILE.stat().st_mtime < 2.0
        except Exception:
            return False

    def is_alive(self) -> bool:
        if self._dead:
            return False
        if _FRAME_FILE.exists():
            try:
                return time.time() - _FRAME_FILE.stat().st_mtime < 2.0
            except Exception:
                pass
        # Grace period for startup — kept short so failures are detected quickly
        return time.time() - self._born_at < 3.0

    def close(self):
        try:
            _KILL_FILE.write_text("kill")
        except Exception:
            pass
        time.sleep(0.4)
        for f in (_FRAME_FILE, _KILL_FILE):
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass
