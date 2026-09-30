"""Voice-activity-detected recording: starts capturing when you speak and stops
after a period of silence, so you don't need to press Enter each turn.

Requires the optional `webrtcvad` module - install `webrtcvad-wheels`, the
maintained fork (see requirements.txt) - only imported when this mode is
actually used, so it isn't a hard dependency for push-to-talk/text modes.
"""

from __future__ import annotations

import audioop
import contextlib
import logging
import subprocess
import threading
import wave

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_FRAME_MS = 30

# How much of the end-of-speech silence to keep on a captured clip before
# sending it to STT - enough not to clip a trailing soft consonant, short
# enough not to give Whisper a long silent stretch to hallucinate over.
_KEPT_SILENCE_TAIL_MS = 200
_FRAME_BYTES = int(_SAMPLE_RATE * (_FRAME_MS / 1000.0)) * 2  # 16-bit mono samples


def record_until_silence(
    path: str,
    device: str,
    aggressiveness: int,
    silence_ms: int,
    max_seconds: int,
    min_speech_ms: int = 60,
    min_rms: int = 150,
    max_utterance_s: int | None = None,
    stop_event: threading.Event | None = None,
) -> bool:
    """Record from `device` until `silence_ms` of silence follows detected speech.
    `max_seconds` bounds how long to wait for speech to *start*; once it has,
    the utterance itself may run up to `max_utterance_s` from its onset
    (defaults to `max_seconds`). Returns True if any speech was captured, and
    writes the captured audio to `path` in that case.

    The two limits are separate because one shared budget cut off speech
    that began late in the window - confirmed on real hardware: 0.4s
    captured, stopped by the 15s cap, sent to STT as an unusable fragment.

    `stop_event`, when set by another thread, stops recording within one
    30ms frame and returns False with nothing written - used when Cozmo
    continues his previous reply in the background (FINAL_LLM_CALL=async,
    see engine.py), so his motors/voice never end up in the recording.

    A frame only counts as "speech" if BOTH `webrtcvad` classifies it as
    speech AND its RMS loudness clears `min_rms` — webrtcvad only looks at
    spectral shape, not loudness, so it can't distinguish faint background
    noise that happens to look speech-shaped from someone actually talking
    into the mic; a loudness floor is an independent, complementary signal
    that quiet ambient noise essentially never clears. This exists because
    the onset debounce below (`min_speech_ms`) isn't always enough on its
    own in a genuinely noisy real room — confirmed on real hardware: it
    kept clearing a 60ms/2-frame debounce reliably enough to burn through a
    free-tier STT quota. `min_rms` is untested against real hardware for
    its exact value — tune it from what real background-noise vs. real
    speech RMS values actually look like in your environment (every
    accepted capture's length/peak RMS/stop reason is logged at INFO, and
    rejected-as-noise listens log their peak RMS too).

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
            "VAD mode requires the 'webrtcvad' module. Install it with: pip install webrtcvad-wheels"
        ) from e

    vad = webrtcvad.Vad(aggressiveness)
    silence_frames_needed = max(1, silence_ms // _FRAME_MS)
    wait_frames = max(1, int(max_seconds * 1000 / _FRAME_MS))
    utterance_s = max_seconds if max_utterance_s is None else max_utterance_s
    utterance_frames = max(1, int(utterance_s * 1000 / _FRAME_MS))
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
    peak_rms_seen = 0
    webrtcvad_only_hits = 0  # frames webrtcvad liked but the RMS floor rejected - tuning signal
    # Why the capture stopped - logged with every accepted capture, to tell
    # "you stopped talking" apart from "hit the time cap".
    end_reason = "unknown"
    interrupted = False

    try:
        while True:
            if not speech_started and frame_count >= wait_frames:
                end_reason = f"no speech started within {max_seconds}s"
                break
            if speech_started and len(voiced_frames) >= utterance_frames:
                end_reason = f"hit the {utterance_s}s utterance cap"
                break
            frame = proc.stdout.read(_FRAME_BYTES)
            if len(frame) < _FRAME_BYTES:
                end_reason = "mic stream ended"
                break
            frame_count += 1
            if stop_event is not None and stop_event.is_set():
                interrupted = True
                break
            frame_rms = audioop.rms(frame, 2)
            peak_rms_seen = max(peak_rms_seen, frame_rms)
            webrtcvad_says_speech = vad.is_speech(frame, _SAMPLE_RATE)
            if webrtcvad_says_speech and frame_rms < min_rms:
                webrtcvad_only_hits += 1
            is_speech = webrtcvad_says_speech and frame_rms >= min_rms

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
                        end_reason = f"{silence_ms}ms below speech threshold"
                        break
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=2)

    if interrupted:
        logger.info(
            "Stopped listening after %.1fs - Cozmo is continuing his reply%s.",
            frame_count * _FRAME_MS / 1000.0,
            " (discarded a capture already in progress)" if speech_started else "",
        )
        return False

    if frame_count == 0:
        logger.warning(
            "arecord on device '%s' produced no audio at all (exit code %s) - it likely "
            "couldn't open the device. Check for an arecord error printed just above this.",
            device,
            proc.poll(),
        )
        return False

    if not speech_started:
        if webrtcvad_only_hits > 0:
            # webrtcvad classified some frames as speech, but none cleared
            # the RMS floor - exactly the "quiet noise looks speech-shaped"
            # case min_rms exists for. Logged at INFO (not DEBUG) since this
            # is the number to look at when tuning VAD_MIN_RMS: if peak_rms
            # here is suspiciously close to what a real utterance measures,
            # min_rms is set too high and will start rejecting real speech.
            logger.info(
                "Listened for %.1fs: webrtcvad flagged %d frame(s) as speech but none reached "
                "min_rms=%d (peak RMS seen: %d) - discarded as noise, not real speech.",
                frame_count * _FRAME_MS / 1000.0,
                webrtcvad_only_hits,
                min_rms,
                peak_rms_seen,
            )
        else:
            logger.debug(
                "Listened for %.1fs, never got %d consecutive speech frames from webrtcvad "
                "(aggressiveness=%d, peak RMS seen: %d) - discarding as noise/nothing said.",
                frame_count * _FRAME_MS / 1000.0,
                onset_frames_needed,
                aggressiveness,
                peak_rms_seen,
            )
        return False

    # INFO, not DEBUG: raised directly on real hardware - many consecutive
    # captures in a row came back from STT as empty text with nothing at
    # all in the log, so there was no way to tell whether these clips were
    # truncated fragments (e.g. quieter words falling under min_rms and
    # counting as "silence" mid-sentence) or real speech the STT dropped.
    # Clip length + peak RMS + why it stopped answers that.
    #
    # When it stopped on silence, the clip's last `silence_ms` is that
    # silence itself (it's how the end of speech is detected). Only keep a
    # short tail of it: Whisper is prone to inventing an ending on long
    # silent stretches ("...thank you"), and there's nothing to transcribe
    # there anyway. The sent clip length is what's logged.
    if silence_run >= silence_frames_needed:
        keep = max(1, _KEPT_SILENCE_TAIL_MS // _FRAME_MS)
        extra = silence_run - keep
        if extra > 0:
            voiced_frames = voiced_frames[:-extra]
    logger.info(
        "Captured %.1fs of audio (listened %.1fs, peak RMS %d, min_rms=%d) - stopped: %s.",
        len(voiced_frames) * _FRAME_MS / 1000.0,
        frame_count * _FRAME_MS / 1000.0,
        peak_rms_seen,
        min_rms,
        end_reason,
    )
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(b"".join(voiced_frames))
    return True
