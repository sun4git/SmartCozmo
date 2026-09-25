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
    min_speech_ms: int = 60,
) -> bool:
    """Record from `device` until `silence_ms` of silence follows detected speech,
    or `max_seconds` is reached. Returns True if any speech was captured, and
    writes the captured audio to `path` in that case.

    `min_speech_ms` is an onset debounce, not a minimum utterance length: a
    single false-positive frame from background noise used to be enough to
    flip straight into "recording" and get sent to Whisper, which
    hallucinates a plausible sentence rather than returning empty text on a
    noise-only clip. Requiring a couple of *consecutive* speech frames before
    committing filters that out. A first attempt at this instead required a
    minimum *total* accumulated speech-frame count across the whole capture —
    that's the wrong metric: it discarded genuinely short real utterances
    (confirmed on real hardware: a real short reply logged only 90ms of
    accumulated speech frames out of a 900ms capture) exactly as readily as
    the single-frame noise case it was meant to catch. Once the onset
    debounce is satisfied there's no further minimum on total length — real
    words can be legitimately brief."""
    try:
        import webrtcvad
    except ImportError as e:
        raise RuntimeError(
            "VAD mode requires the 'webrtcvad' package. Install it with: pip install webrtcvad"
        ) from e

    vad = webrtcvad.Vad(aggressiveness)
    silence_frames_needed = max(1, silence_ms // _FRAME_MS)
    max_frames = max(1, int(max_seconds * 1000 / _FRAME_MS))
    onset_frames_needed = max(1, min_speech_ms // _FRAME_MS)

    proc = subprocess.Popen(
        ["arecord", "-D", device, "-f", "S16_LE", "-r", str(_SAMPLE_RATE), "-c", "1", "-t", "raw"],
        stdout=subprocess.PIPE,
    )

    voiced_frames: list[bytes] = []
    pending_frames: list[bytes] = []
    consecutive_speech = 0
    silence_run = 0
    speech_started = False
    frame_count = 0

    try:
        while frame_count < max_frames:
            frame = proc.stdout.read(_FRAME_BYTES)
            if len(frame) < _FRAME_BYTES:
                break
            frame_count += 1
            is_speech = vad.is_speech(frame, _SAMPLE_RATE)

            if not speech_started:
                if is_speech:
                    consecutive_speech += 1
                    pending_frames.append(frame)
                    if consecutive_speech >= onset_frames_needed:
                        speech_started = True
                        voiced_frames.extend(pending_frames)
                        pending_frames = []
                else:
                    consecutive_speech = 0
                    pending_frames = []
            else:
                voiced_frames.append(frame)
                if is_speech:
                    silence_run = 0
                else:
                    silence_run += 1
                    if silence_run >= silence_frames_needed:
                        break
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=2)

    if frame_count == 0:
        logger.warning(
            "arecord on device '%s' produced no audio at all (exit code %s) - it likely "
            "couldn't open the device. Check for an arecord error printed just above this.",
            device,
            proc.poll(),
        )
        return False

    if not speech_started:
        logger.debug(
            "Listened for %.1fs, never got %d consecutive speech frames from webrtcvad "
            "(aggressiveness=%d) - discarding as noise/nothing said.",
            frame_count * _FRAME_MS / 1000.0,
            onset_frames_needed,
            aggressiveness,
        )
        return False

    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(b"".join(voiced_frames))
    return True
