"""Voice-activity-detected recording: starts capturing when you speak and stops
after a period of silence, so you don't need to press Enter each turn.

Requires the optional `webrtcvad` package (only imported when this mode is
actually used, so it isn't a hard dependency for push-to-talk/text modes).
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
import wave

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_FRAME_MS = 30
_FRAME_BYTES = int(_SAMPLE_RATE * (_FRAME_MS / 1000.0)) * 2  # 16-bit mono samples


def record_until_silence(
    path: str,
    device: str,
    aggressiveness: int,
    silence_ms: int,
    max_seconds: int,
) -> bool:
    """Record from `device` until `silence_ms` of silence follows detected speech,
    or `max_seconds` is reached. Returns True if any speech was captured, and
    writes the captured audio to `path` in that case."""
    try:
        import webrtcvad
    except ImportError as e:
        raise RuntimeError(
            "VAD mode requires the 'webrtcvad' package. Install it with: pip install webrtcvad"
        ) from e

    vad = webrtcvad.Vad(aggressiveness)
    silence_frames_needed = max(1, silence_ms // _FRAME_MS)
    max_frames = max(1, int(max_seconds * 1000 / _FRAME_MS))

    proc = subprocess.Popen(
        ["arecord", "-D", device, "-f", "S16_LE", "-r", str(_SAMPLE_RATE), "-c", "1", "-t", "raw"],
        stdout=subprocess.PIPE,
    )

    voiced_frames: list[bytes] = []
    silence_run = 0
    speech_started = False
    frame_count = 0

    try:
        while frame_count < max_frames:
            frame = proc.stdout.read(_FRAME_BYTES)
            if len(frame) < _FRAME_BYTES:
                break
            frame_count += 1

            if vad.is_speech(frame, _SAMPLE_RATE):
                speech_started = True
                silence_run = 0
                voiced_frames.append(frame)
            elif speech_started:
                silence_run += 1
                voiced_frames.append(frame)
                if silence_run >= silence_frames_needed:
                    break
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=2)

    if not voiced_frames:
        return False

    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(b"".join(voiced_frames))
    return True
