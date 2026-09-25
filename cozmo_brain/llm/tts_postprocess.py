"""Shared TTS post-processing, used by every STT/TTS provider client so
Cozmo's voice character and Cozmo-required output format stay consistent
regardless of which one is active (AUDIO_PROVIDER in .env).

Pipeline: resample to TTS_SAMPLE_RATE (only if the provider's raw output
isn't already at that rate) -> pitch/tempo shift -> ring modulation ->
peak-normalizing gain -> re-encoded as a Cozmo-ready WAV.

The resample step exists because not every provider lets you request a
specific rate the way Groq's Orpheus endpoint does (`sample_rate` in the
request) — OpenAI's TTS endpoint has no such parameter at all (confirmed
against its own Python SDK's create() signature). Rather than assume what
rate it actually returns, this reads whatever the response's WAV header
declares and resamples from that to Cozmo's required 22050/48000Hz — the
same audioop.ratecv() primitive already used for the pitch-shift effect,
just used here for a plain format conversion instead of a deliberate one.

See groq_client.py / openai_client.py for where this gets called; see the
README's "Making the voice sound less generic" section for what each
effect is actually for.
"""

from __future__ import annotations

import array
import audioop
import io
import logging
import math
import wave

from cozmo_brain.config import Settings

logger = logging.getLogger(__name__)


def apply_cozmo_voice_character(wav_bytes: bytes, settings: Settings) -> bytes:
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
            # Some providers (confirmed: Groq) stream this WAV with a
            # placeholder "unknown length" RIFF/data size (nframes reads
            # back as 2147483647, not the real frame count). Only copy the
            # actual format fields — never getparams()/setparams() wholesale
            # — and let the writer compute the real size from what's
            # actually written, or it'll try to write a header claiming
            # billions of frames and overflow struct's unsigned-long pack.
            nchannels = reader.getnchannels()
            sampwidth = reader.getsampwidth()
            framerate = reader.getframerate()
            frames = reader.readframes(reader.getnframes())

        target_rate = settings.tts_sample_rate
        if framerate != target_rate:
            frames = _resample(frames, sampwidth, nchannels, framerate, target_rate)
            framerate = target_rate

        if settings.tts_pitch_shift != 1.0:
            frames = _pitch_shift(frames, sampwidth, nchannels, framerate, settings.tts_pitch_shift)

        if settings.tts_robot_mod_depth > 0.0:
            frames = _ring_modulate(
                frames, sampwidth, nchannels, framerate, settings.tts_robot_mod_hz, settings.tts_robot_mod_depth
            )

        if settings.tts_gain > 1.0:
            frames = _normalize_gain(frames, sampwidth, settings.tts_gain)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as writer:
            writer.setnchannels(nchannels)
            writer.setsampwidth(sampwidth)
            writer.setframerate(framerate)
            writer.writeframes(frames)
        return buf.getvalue()
    except (wave.Error, audioop.error) as e:
        logger.warning("Could not post-process TTS output, using unmodified audio: %s", e)
        return wav_bytes


def _resample(frames: bytes, width: int, nchannels: int, from_rate: int, to_rate: int) -> bytes:
    converted, _ = audioop.ratecv(frames, width, nchannels, from_rate, to_rate, None)
    return converted


def _normalize_gain(frames: bytes, width: int, max_gain: float, target_peak_ratio: float = 0.95) -> bytes:
    """Boost volume up to `max_gain`, but automatically back off short of
    clipping: scales only as far as the loudest sample reaching
    `target_peak_ratio` of full scale, whichever is smaller.
    """
    peak = audioop.max(frames, width)
    if peak == 0:
        return frames
    full_scale = (1 << (8 * width - 1)) - 1
    safe_gain = (full_scale * target_peak_ratio) / peak
    gain = max(1.0, min(max_gain, safe_gain))
    return audioop.mul(frames, width, gain)


def _pitch_shift(frames: bytes, width: int, nchannels: int, framerate: int, shift: float) -> bytes:
    """Shift pitch and tempo together by resampling: `shift` > 1.0 raises
    pitch and speeds up playback (the classic "chipmunk" trick), < 1.0
    lowers/slows it. Output stays at the same declared `framerate` — Cozmo's
    play_audio() only accepts 22050/48000Hz, so the rate itself can't change."""
    converted, _ = audioop.ratecv(frames, width, nchannels, int(framerate * shift), framerate, None)
    return converted


def _ring_modulate(frames: bytes, width: int, nchannels: int, framerate: int, carrier_hz: float, depth: float) -> bytes:
    """Mix in a fixed-frequency carrier tone for a subtly metallic/robotic
    timbre (classic ring modulation). `depth` 0-1 controls the dry/wet mix;
    the result is always <= the original amplitude, so this can't clip."""
    if width != 2:
        logger.debug("Skipping ring modulation: only 16-bit audio is supported (got width=%d).", width)
        return frames

    samples = array.array("h")
    samples.frombytes(frames)
    angular_step = 2 * math.pi * carrier_hz / framerate
    dry = 1.0 - depth

    for i in range(len(samples)):
        # For multi-channel audio, every channel's frame shares one carrier phase.
        carrier = math.sin(angular_step * (i // nchannels))
        sample = samples[i]
        # dry+depth*carrier has magnitude <= 1, so this can't clip except the
        # one-past-int16-max edge case at sample == -32768; clamp defensively.
        samples[i] = max(-32768, min(32767, int(dry * sample + depth * sample * carrier)))

    return samples.tobytes()
