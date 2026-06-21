"""
voice.py — Project G.I.L.
Text-to-Speech output.
Priority: ElevenLabs (if key set) → edge-tts (en-GB-RyanNeural, free) → SAPI fallback.
"""

import asyncio
import os
import re
import subprocess
import tempfile
import threading
import time
import ctypes
from logger import get as _get_log

log = _get_log("voice")

ELEVENLABS_API_KEY  = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")

_HE_VOICE = "he-IL-AvriNeural"   # Hebrew male voice (edge-tts)
_HE_RE    = re.compile(r'[֐-׿]')

def _is_hebrew(text: str) -> bool:
    return bool(_HE_RE.search(text))


def _load_voice() -> str:
    if os.getenv("GIL_VOICE"):
        return os.getenv("GIL_VOICE")
    try:
        import json
        from pathlib import Path
        with open(Path(__file__).parent / "data" / "gil_config.json") as f:
            return json.load(f).get("tts_voice", "en-GB-RyanNeural")
    except Exception:
        return "en-GB-RyanNeural"


EDGE_TTS_VOICE = _load_voice()

_el_key_valid = bool(ELEVENLABS_API_KEY) and ELEVENLABS_API_KEY != "your_elevenlabs_key_here"

_speak_lock   = threading.Lock()
_winmm_active = [False]   # True while mciSendStringW("play ...") is blocking
_stop_flag    = [False]   # set by stop_speaking() to abort current TTS


# ── Text cleanup ──────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    text = re.sub(r'\*+|_+|`+|#{1,6}', '', text)
    text = re.sub(r'\s*[-–—]\s*', ', ', text)
    text = text.replace('...', ', ')
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


# ── Public API ────────────────────────────────────────────────────────────────

def speak(text: str) -> bool:
    """
    Deliver spoken output. Returns True if speech was delivered, False if skipped.
    Drops (never queues) if another voice is already active.
    Mic stays live during playback so the user can say "stop" to interrupt.
    """
    if not _speak_lock.acquire(blocking=False):
        log.debug("skipped (busy): %r", text[:40])
        try:
            from ears import unmute
            unmute()
        except Exception:
            pass
        return False
    try:
        _stop_flag[0] = False
        text = _clean(text)
        if not text:
            return False
        log.info("%s", text[:120])
        try:
            if _el_key_valid:
                try:
                    _speak_elevenlabs(text)
                except Exception as _el_exc:
                    log.warning("ElevenLabs failed (%s), falling back to edge-tts", _el_exc)
                    _speak_edge_tts(text)
            else:
                _speak_edge_tts(text)
        finally:
            if not _stop_flag[0]:
                time.sleep(0.8)   # brief post-speech gap before mic picks up again
    finally:
        _stop_flag[0] = False
        _speak_lock.release()

    return True


def stop_speaking() -> None:
    """Interrupt any ongoing TTS playback immediately."""
    _stop_flag[0] = True   # polling loop in _play_mp3_winmm picks this up within 50 ms
    try:
        from ears import unmute
        unmute()
    except Exception:
        pass


def is_speaking() -> bool:
    return _speak_lock.locked()


# ── edge-tts (primary free voice) ─────────────────────────────────────────────

async def _edge_generate(text: str, voice: str, output_path: str) -> None:
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


def _speak_edge_tts(text: str) -> None:
    tmp_path = None
    voice    = _HE_VOICE if _is_hebrew(text) else EDGE_TTS_VOICE
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tmp_path = f.name

        # asyncio.run() fails if a loop is already running in this thread.
        # Use a dedicated thread's event loop instead to guarantee a clean loop.
        result: list[Exception | None] = [None]

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(_edge_generate(text, voice, tmp_path))
            except Exception as exc:
                result[0] = exc
            finally:
                loop.close()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=30)

        if result[0]:
            raise result[0]

        if _stop_flag[0]:
            return

        _play_mp3_winmm(tmp_path)

    except Exception as exc:
        log.warning("edge-tts failed (%s), falling back to SAPI", exc)
        _speak_sapi(text)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


# ── ElevenLabs (optional premium) ────────────────────────────────────────────

def _speak_elevenlabs(text: str) -> None:
    try:
        from elevenlabs.client import ElevenLabs
        client    = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        audio_gen = client.text_to_speech.convert(
            voice_id=ELEVENLABS_VOICE_ID,
            text=text,
            model_id="eleven_monolingual_v1",
            voice_settings={
                "stability": 0.55, "similarity_boost": 0.80,
                "style": 0.10, "use_speaker_boost": True,
            },
        )
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            for chunk in audio_gen:
                tmp.write(chunk)
            tmp_path = tmp.name
        try:
            if not _stop_flag[0]:
                _play_mp3_winmm(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    except Exception as exc:
        log.warning("ElevenLabs error: %s", exc)
        _speak_edge_tts(text)


# ── WinMM MP3 playback ────────────────────────────────────────────────────────

def _play_mp3_winmm(filepath: str) -> None:
    """Play MP3 via WinMM MCI. Polls every 50 ms so stop_speaking() cuts it instantly."""
    if _stop_flag[0]:
        return
    winmm = ctypes.WinDLL("winmm")
    abs_path = os.path.abspath(filepath).replace("/", "\\")
    try:
        winmm.mciSendStringW(f'open "{abs_path}" type mpegvideo alias gil_audio', None, 0, None)
        winmm.mciSendStringW("play gil_audio", None, 0, None)   # non-blocking
        _winmm_active[0] = True
        buf = ctypes.create_unicode_buffer(512)
        while True:
            winmm.mciSendStringW("status gil_audio mode", buf, 512, None)
            if buf.value != "playing" or _stop_flag[0]:
                break
            time.sleep(0.05)
    except Exception as exc:
        log.error("WinMM playback failed: %s", exc, exc_info=True)
    finally:
        try:
            winmm.mciSendStringW("stop gil_audio", None, 0, None)
            winmm.mciSendStringW("close gil_audio", None, 0, None)
        except Exception:
            pass
        _winmm_active[0] = False


# ── SAPI (last resort) ────────────────────────────────────────────────────────

_SAPI_VOICE_NAME: str | None = None

_PREFERRED_VOICES = [
    "Microsoft David Desktop",
    "Microsoft Mark Desktop",
    "Microsoft David",
    "Microsoft Mark",
]


def _resolve_sapi_voice() -> str | None:
    global _SAPI_VOICE_NAME
    if _SAPI_VOICE_NAME is not None:
        return _SAPI_VOICE_NAME

    probe = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", probe],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        installed = [l.strip() for l in result.stdout.splitlines() if l.strip()]
        for name in _PREFERRED_VOICES:
            if name in installed:
                _SAPI_VOICE_NAME = name
                return _SAPI_VOICE_NAME
        _SAPI_VOICE_NAME = installed[0] if installed else ""
    except Exception:
        _SAPI_VOICE_NAME = ""
    return _SAPI_VOICE_NAME or None


def _speak_sapi(text: str) -> None:
    safe       = text.replace("'", "''")
    voice_name = _resolve_sapi_voice()
    select_cmd = f"$s.SelectVoice('{voice_name}'); " if voice_name else ""
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"{select_cmd}"
        "$s.Rate = 0; "
        f"$s.Speak('{safe}');"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            check=True, timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as exc:
        log.error("SAPI error: %s", exc, exc_info=True)
