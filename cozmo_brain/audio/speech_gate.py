"""Neural speech check on a captured clip, before it's sent to ANY STT
provider: if Silero VAD finds (almost) no real speech in it, it isn't sent.

Why: every STT provider sees whatever passed the cheap local gate in
audio/vad.py (webrtcvad's spectral shape + a loudness floor), and loud,
speech-shaped noise - a bump, motor hum, clatter - can pass that. Whisper
then invents text for it ("Thank you.", "you", "."). Silero is a small
neural model trained to tell speech from noise, so it catches what the
loudness check can't. Measured 2026-09-29 with the real model (faster-whisper
1.2.1's bundled silero_vad_v6.onnx - the same file as on the Pi), over two
independent sets of clips: hiss and clatter 0.00s of speech, motor hum
0.03-0.06s, while one-word replies ("yes", "no", "okay", including a "yes"
at a quarter volume) all measured 0.42-0.67s, and a full sentence ~3.5s.
Took 2-20ms per clip on a desktop
CPU - negligible next to an STT round trip, which it saves entirely for
every noise clip it catches (and, with STT_PROVIDER=local, a whole CPU
Whisper decode).

No separate model download: it reuses the Silero model that ships inside
faster-whisper. If faster-whisper isn't installed, the check is skipped
(with a one-time warning) and every clip is sent as before. Disable with
SPEECH_GATE_ENABLED=false.
"""

from __future__ import annotations

import logging
import threading
import time
import wave

logger = logging.getLogger(__name__)

_import_lock = threading.Lock()
_vad = None  # (get_speech_timestamps, VadOptions) once imported; False if unavailable


def _load():
    global _vad
    with _import_lock:
        if _vad is None:
            try:
                from faster_whisper.vad import VadOptions, get_speech_timestamps

                _vad = (get_speech_timestamps, VadOptions)
            except ImportError:
                logger.warning(
                    "Speech gate (Silero VAD) unavailable - faster-whisper isn't installed, so every "
                    "captured clip is sent to STT unchecked. `pip install faster-whisper` to enable it, "
                    "or set SPEECH_GATE_ENABLED=false to silence this."
                )
                _vad = False
    return _vad


def speech_seconds(wav_path: str, threshold: float) -> float | None:
    """Seconds of speech Silero detects in a 16kHz mono 16-bit WAV, or None
    if the check isn't available (faster-whisper not installed)."""
    import numpy as np

    with wave.open(wav_path, "rb") as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    return _speech_seconds(audio, threshold)


def warm_up(settings) -> None:
    """Load the Silero model now, on a background thread, instead of on the
    first real clip - measured: the first check took ~1s on a desktop CPU
    (model load), later ones 2-15ms. Called when a listening mode starts."""
    if not settings.speech_gate_enabled:
        return

    def _run() -> None:
        try:
            import numpy as np

            _speech_seconds(np.zeros(16000, dtype=np.float32), settings.speech_gate_threshold)
        except Exception as e:  # noqa: BLE001 - warm-up is only an optimization
            logger.debug("Speech gate warm-up failed: %s", e)

    threading.Thread(target=_run, name="speech-gate-warmup", daemon=True).start()


def _speech_seconds(audio, threshold: float) -> float | None:
    vad = _load()
    if not vad:
        return None
    get_speech_timestamps, VadOptions = vad
    # speech_pad_ms=0: faster-whisper's default pads every detected segment
    # by 400ms, which is right for cutting audio but would inflate "how much
    # speech is in here". min_silence 100ms keeps short gaps inside a word
    # from splitting it; min_speech 0 so short words still count.
    options = VadOptions(threshold=threshold, min_speech_duration_ms=0, min_silence_duration_ms=100, speech_pad_ms=0)
    stamps = get_speech_timestamps(audio, options)
    return sum(s["end"] - s["start"] for s in stamps) / 16000.0


def clip_has_speech(wav_path: str, settings) -> bool:
    """Whether a captured clip should be sent to STT. True when the gate is
    disabled or unavailable, so it can only ever *remove* noise clips, never
    break transcription. Logs Silero's measurement for every clip, so
    SPEECH_GATE_MIN_SPEECH_MS can be tuned from real audio."""
    if not settings.speech_gate_enabled:
        return True
    started = time.monotonic()
    try:
        seconds = speech_seconds(wav_path, settings.speech_gate_threshold)
    except Exception as e:  # noqa: BLE001 - a gate failure must never block real speech
        logger.warning("Speech gate check failed, sending the clip anyway: %s", e)
        return True
    if seconds is None:
        return True
    took_ms = (time.monotonic() - started) * 1000
    send = seconds * 1000 >= settings.speech_gate_min_speech_ms
    logger.info(
        "Speech gate (Silero): %.2fs of speech detected (%.0fms check) - %s.",
        seconds, took_ms,
        "sending to STT" if send else f"not sent (under {settings.speech_gate_min_speech_ms}ms)",
    )
    return send
