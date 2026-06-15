"""
watcher.py — Project G.I.L.
Always-on background clap watcher. Runs even when GIL is fully closed.
On double clap: launches GIL if not running, or brings its window to front.

Register once with Windows startup:
    pythonw watcher.py --install
"""

import sys
import os
import time
import threading
import ctypes
import subprocess
import numpy as np
import sounddevice as sd

# ── Paths ─────────────────────────────────────────────────────────────────────

_DIR     = os.path.dirname(os.path.abspath(__file__))
_MAIN    = os.path.join(_DIR, "main.py")
_PYTHONW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
if not os.path.exists(_PYTHONW):
    _PYTHONW = sys.executable   # fallback to python.exe

# ── Startup registration ──────────────────────────────────────────────────────

_REG_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"
_REG_NAME = "GIL_Watcher"


def _install_startup() -> None:
    import winreg
    cmd = f'"{_PYTHONW}" "{os.path.abspath(__file__)}"'
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, _REG_NAME, 0, winreg.REG_SZ, cmd)
        winreg.CloseKey(key)
        print(f"[GIL-WATCHER] Registered for startup: {cmd}")
    except Exception as exc:
        print(f"[GIL-WATCHER] Startup registration failed: {exc}")


def _uninstall_startup() -> None:
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, _REG_NAME)
        winreg.CloseKey(key)
        print("[GIL-WATCHER] Removed from startup.")
    except FileNotFoundError:
        print("[GIL-WATCHER] Not in startup registry.")
    except Exception as exc:
        print(f"[GIL-WATCHER] Uninstall failed: {exc}")


# ── GIL process control ───────────────────────────────────────────────────────

_GIL_MUTEX = "ProjectGIL_SingleInstance"
_GIL_TITLE = "G.I.L. \u2014 Neural Core"   # em-dash


def _gil_is_running() -> bool:
    """Check if GIL's mutex is held (i.e. GIL is already running)."""
    h = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, _GIL_MUTEX)
    if h:
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    return False


def _show_gil_window() -> bool:
    """Find GIL's window by title and bring it to front. Returns True if found."""
    user32 = ctypes.windll.user32
    hwnd   = user32.FindWindowW(None, _GIL_TITLE)
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, 9)          # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    return True


def _launch_gil() -> None:
    """Start GIL as a detached background process."""
    try:
        subprocess.Popen(
            [_PYTHONW, _MAIN],
            cwd=_DIR,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
        )
        print("[GIL-WATCHER] GIL launched.")
    except Exception as exc:
        print(f"[GIL-WATCHER] Failed to launch GIL: {exc}")


def _wake_gil() -> None:
    """Called on double clap — show or launch GIL."""
    print("[GIL-WATCHER] Double clap — waking GIL.")
    if _gil_is_running():
        if not _show_gil_window():
            print("[GIL-WATCHER] GIL running but window not found.")
    else:
        _launch_gil()


# ── Clap detection ────────────────────────────────────────────────────────────

SAMPLERATE        = 16000
CHUNK_SECS        = 0.05          # 50 ms chunks — fast reaction time
CHUNK_SAMPLES     = int(SAMPLERATE * CHUNK_SECS)
CLAP_THRESH       = 0.025         # RMS threshold — duration (<=2 chunks) distinguishes clap from speech
CLAP_WINDOW_SECS  = 2.5           # two claps must land within this window
SPEECH_MIN_CHUNKS = 5             # chunks above thresh = probably speech, not clap
SILENCE_EARLY     = 2             # chunks of silence before declaring clap over (0.1s)
SILENCE_LONG      = 20            # chunks of silence before giving up (1.0s)


HELLO_VARIANTS = {
    "hello", "helo", "hullo", "hallow", "halo",
    "hey", "hi", "hei", "yo", "ok", "okay",
}
GIL_VARIANTS = {
    "gill", "gil", "g.i.l", "gail", "jill", "jil",
    "phil", "gio", "geo", "deal", "feel", "heal",
    "neil", "real", "guild",
}


def _contains_wake_phrase(text: str) -> bool:
    words = text.lower().replace(".", "").replace(",", "").split()
    for i, w in enumerate(words[:-1]):
        if w in HELLO_VARIANTS:
            nxt = words[i + 1]
            if nxt in GIL_VARIANTS:
                return True
    return False


def _transcribe(audio_chunks: list) -> str:
    import wave, tempfile, speech_recognition as sr
    if not audio_chunks:
        return ""
    try:
        pcm_arr = np.concatenate(audio_chunks)
        pcm     = (np.clip(pcm_arr, -1.0, 1.0) * 32767).astype(np.int16)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = f.name
        with wave.open(tmp, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLERATE)
            wf.writeframes(pcm.tobytes())
        rec = sr.Recognizer()
        with sr.AudioFile(tmp) as src:
            recorded = rec.record(src)
        os.unlink(tmp)
        return rec.recognize_google(recorded, language="en-US")
    except Exception:
        return ""


def _listen_loop() -> None:
    """Single loop: detects claps AND voice wake phrase. Steps back when GIL is running."""
    clap_times: list[float] = []

    def _register_clap() -> None:
        nonlocal clap_times
        now        = time.time()
        clap_times = [t for t in clap_times if now - t < CLAP_WINDOW_SECS]
        clap_times.append(now)
        print(f"[GIL-WATCHER] Clap #{len(clap_times)}")
        if len(clap_times) >= 2:
            clap_times.clear()
            threading.Thread(target=_wake_gil, daemon=True).start()

    while True:
        # When GIL is running it owns the mic — back off
        if _gil_is_running():
            time.sleep(1)
            continue

        try:
            speech_count  = 0
            silence_count = 0
            peak_rms      = 0.0
            recording     = False
            audio_chunks: list = []

            with sd.InputStream(
                samplerate=SAMPLERATE,
                channels=1,
                dtype="float32",
                blocksize=CHUNK_SAMPLES,
            ) as stream:
                for _ in range(int(10 / CHUNK_SECS)):   # 10-second window per pass
                    if _gil_is_running():
                        break

                    data, _ = stream.read(CHUNK_SAMPLES)
                    chunk   = data.flatten()
                    rms     = float(np.sqrt(np.mean(chunk ** 2)))

                    if rms > peak_rms:
                        peak_rms = rms

                    if rms > CLAP_THRESH:
                        recording      = True
                        speech_count  += 1
                        silence_count  = 0
                        audio_chunks.append(chunk)
                    elif recording:
                        audio_chunks.append(chunk)
                        silence_count += 1
                        limit = SILENCE_EARLY if speech_count <= 2 else SILENCE_LONG
                        if silence_count >= limit:
                            break

            if not recording:
                continue

            if speech_count <= 2 and peak_rms > CLAP_THRESH:
                # Brief loud burst — clap
                _register_clap()
            elif speech_count >= 5:
                # Long enough to be speech — transcribe and check wake phrase
                threading.Thread(
                    target=_check_voice_wake,
                    args=(audio_chunks,),
                    daemon=True,
                ).start()

        except sd.PortAudioError as exc:
            print(f"[GIL-WATCHER] Audio error: {exc}")
            time.sleep(2)
        except Exception as exc:
            print(f"[GIL-WATCHER] Error: {exc}")
            time.sleep(1)


def _check_voice_wake(audio_chunks: list) -> None:
    text = _transcribe(audio_chunks)
    if not text:
        return
    print(f"[GIL-WATCHER] Heard: '{text}'")
    if _contains_wake_phrase(text):
        print("[GIL-WATCHER] Wake phrase detected — waking GIL.")
        _wake_gil()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--install" in sys.argv:
        _install_startup()
        sys.exit(0)
    if "--uninstall" in sys.argv:
        _uninstall_startup()
        sys.exit(0)

    # Single-instance guard — exit silently if already running
    _wm = ctypes.windll.kernel32.CreateMutexW(None, True, "ProjectGIL_Watcher")
    if ctypes.windll.kernel32.GetLastError() == 183:
        sys.exit(0)

    print("[GIL-WATCHER] Running. Listening for claps and 'Hello G.I.L.'...")
    _install_startup()   # ensure registered every run
    _listen_loop()       # blocks forever
