"""
ears.py — Project G.I.L.
Continuous microphone listener with energy-based VAD.
Mutes automatically while G.I.L. is speaking to prevent self-feedback.
"""

import os
import wave
import time
import tempfile
import threading
import numpy as np
import sounddevice as sd
import speech_recognition as sr

# ── Audio constants ───────────────────────────────────────────────────────────

SAMPLERATE        = 16000
CHUNK_SECS        = 0.05
CHUNK_SAMPLES     = int(SAMPLERATE * CHUNK_SECS)
ENERGY_THRESH     = 0.007  # lower = picks up quieter speech
PRE_ROLL_CHUNKS   = 16    # 0.8s pre-roll — catches start of words
SILENCE_CHUNKS    = 24    # 1.2s silence before ending utterance
MIN_SPEECH_CHUNKS = 3     # 0.15s minimum — catches short words
MAX_RECORD_SECS   = 15

_recognizer  = sr.Recognizer()
_muted       = False
_mute_lock   = threading.Lock()
_passive     = True   # English-only until wake phrase fires


# ── Mute control (called by voice.py during TTS) ──────────────────────────────

def mute() -> None:
    global _muted
    with _mute_lock:
        _muted = True


def unmute() -> None:
    global _muted
    with _mute_lock:
        _muted = False


def set_passive(passive: bool) -> None:
    global _passive
    _passive = passive


# ── Public interface ──────────────────────────────────────────────────────────

def listen_forever(on_utterance: callable) -> None:
    """
    Record utterances continuously and pass each transcription to on_utterance(text).
    Pauses automatically while _muted is True (during TTS playback).
    Runs forever — must be called from a daemon thread.
    """
    while True:
        try:
            with _mute_lock:
                currently_muted = _muted
            if currently_muted:
                time.sleep(0.05)
                continue

            audio = _record_utterance()
            if audio is None:
                continue

            text = _transcribe(audio)
            if text and text.strip():
                on_utterance(text.strip())
            else:
                print("[G.I.L. EARS] Audio captured but transcription was empty.")

        except Exception as exc:
            print(f"[G.I.L. EARS] Error: {exc}")
            time.sleep(0.5)


# ── Clap detection (integrated into main audio stream — no second device needed) ─

CLAP_THRESH        = 0.025  # RMS threshold — duration distinguishes clap from speech
CLAP_WINDOW_SECS   = 2.5    # two claps must land within this window
CLAP_SILENCE_EARLY = 3      # after a likely clap, only wait 3 chunks (0.3s) before returning

_clap_callback: callable | None = None
_clap_times:    list[float]     = []


def start_clap_detector(on_double_clap: callable) -> None:
    """Register the callback fired on two quick claps. Detection runs inside the main stream."""
    global _clap_callback
    _clap_callback = on_double_clap
    print("[G.I.L. EARS] Clap detector ready (integrated).")


def _register_clap() -> None:
    global _clap_times
    if _clap_callback is None:
        return
    now = time.time()
    _clap_times = [t for t in _clap_times if now - t < CLAP_WINDOW_SECS]
    _clap_times.append(now)
    print(f"[G.I.L. EARS] Clap #{len(_clap_times)}")
    if len(_clap_times) >= 2:
        _clap_times.clear()
        print("[G.I.L. EARS] Double clap — activating.")
        threading.Thread(target=_clap_callback, daemon=True, name="GIL-Clap").start()


def listen_once(timeout_secs: float = 8.0) -> str | None:
    """Record and transcribe a single utterance. Returns text or None on silence/timeout."""
    audio = _record_utterance(max_secs=timeout_secs)
    if audio is None:
        return None
    text = _transcribe(audio)
    return text.strip() if text and text.strip() else None


# ── Audio capture ─────────────────────────────────────────────────────────────

def _record_utterance(max_secs: float = MAX_RECORD_SECS) -> np.ndarray | None:
    with _mute_lock:
        if _muted:
            return None

    max_chunks    = int(max_secs / CHUNK_SECS)
    ring          = []
    audio         = []
    speech_count  = 0
    silence_count = 0
    recording     = False
    peak_rms      = 0.0   # track loudest chunk — used for clap detection

    try:
        with sd.InputStream(
            samplerate=SAMPLERATE,
            channels=1,
            dtype="float32",
            blocksize=CHUNK_SAMPLES,
        ) as stream:
            for _ in range(max_chunks + PRE_ROLL_CHUNKS):
                # Abort mid-recording if muted (e.g. GIL starts speaking)
                with _mute_lock:
                    if _muted:
                        return None

                data, _ = stream.read(CHUNK_SAMPLES)
                chunk   = data.flatten()
                rms     = float(np.sqrt(np.mean(chunk ** 2)))

                if rms > peak_rms:
                    peak_rms = rms

                if rms > ENERGY_THRESH:
                    if not recording:
                        recording = True
                        print(f"[G.I.L. EARS] Speech detected (rms={rms:.3f})")
                        audio.extend(ring)
                        ring.clear()
                    speech_count  += 1
                    silence_count  = 0
                    audio.append(chunk)
                else:
                    if recording:
                        audio.append(chunk)
                        silence_count += 1
                        # Short-circuit: if this looks like a clap (brief, loud) don't
                        # wait the full SILENCE_CHUNKS — return fast so clap 2 can be caught.
                        limit = CLAP_SILENCE_EARLY if (speech_count <= 2 and peak_rms > CLAP_THRESH) else SILENCE_CHUNKS
                        if silence_count >= limit:
                            break
                    else:
                        ring.append(chunk)
                        if len(ring) > PRE_ROLL_CHUNKS:
                            ring.pop(0)

    except sd.PortAudioError as exc:
        print(f"[G.I.L. EARS] PortAudio: {exc}")
        return None
    except Exception as exc:
        print(f"[G.I.L. EARS] Record error: {exc}")
        return None

    if speech_count < MIN_SPEECH_CHUNKS:
        # Brief loud burst that was too short for speech → could be a clap
        if 1 <= speech_count and peak_rms > CLAP_THRESH:
            _register_clap()
        return None

    return np.concatenate(audio)


# ── Transcription ─────────────────────────────────────────────────────────────

_GROQ_WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
_GROQ_WHISPER_MODEL = "whisper-large-v3-turbo"   # fast + accurate, multilingual


def _get_groq_key() -> str:
    keys = [k for k in [
        os.getenv("GROQ_API_KEY", ""),
        os.getenv("GROQ_API_KEY_2", ""),
    ] if k]
    return keys[0] if keys else ""


def _write_wav(audio: np.ndarray) -> str:
    """Normalize audio, write to a temp WAV file, return path."""
    peak = np.max(np.abs(audio))
    if peak > 0.001:
        audio = audio / peak * 0.95
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp = f.name
    with wave.open(tmp, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLERATE)
        wf.writeframes(pcm.tobytes())
    return tmp


def _transcribe_groq(tmp: str) -> str:
    import requests
    key = _get_groq_key()
    if not key:
        return ""
    with open(tmp, "rb") as f:
        resp = requests.post(
            _GROQ_WHISPER_URL,
            headers={"Authorization": f"Bearer {key}"},
            files={"file": ("audio.wav", f, "audio/wav")},
            data={"model": _GROQ_WHISPER_MODEL},
            timeout=10,
        )
    resp.raise_for_status()
    return resp.json().get("text", "").strip()


def _transcribe_google(tmp: str) -> str:
    langs = ["en-US"] if _passive else ["en-US", "he-IL"]
    with sr.AudioFile(tmp) as src:
        recorded = _recognizer.record(src)
    for lang in langs:
        try:
            result = _recognizer.recognize_google(recorded, language=lang)
            if result and result.strip():
                return result.strip()
        except sr.UnknownValueError:
            continue
        except Exception:
            break
    return ""


def _transcribe(audio: np.ndarray) -> str:
    tmp = None
    try:
        tmp = _write_wav(audio)

        # Try Groq Whisper first — far more accurate
        try:
            text = _transcribe_groq(tmp)
            if text:
                print(f"[G.I.L. EARS] Whisper: {text}")
                return text
        except Exception as exc:
            print(f"[G.I.L. EARS] Groq Whisper failed, falling back: {exc}")

        # Fallback: Google STT
        text = _transcribe_google(tmp)
        if text:
            print(f"[G.I.L. EARS] Google STT: {text}")
        return text

    except Exception as exc:
        print(f"[G.I.L. EARS] Transcription error: {exc}")
        return ""
    finally:
        if tmp:
            try: os.unlink(tmp)
            except Exception: pass
