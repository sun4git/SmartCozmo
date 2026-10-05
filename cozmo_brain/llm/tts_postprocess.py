"""Shared TTS post-processing, used by every STT/TTS provider client so
Cozmo's voice character and Cozmo-required output format stay consistent
regardless of which one is active (TTS_PROVIDER in .env).

Pipeline: resample to TTS_SAMPLE_RATE (only if the provider's raw output
isn't already at that rate) -> pitch/tempo shift -> ring modulation ->
RMS-targeted gain with a soft limiter -> silent lead-in -> re-encoded as a
Cozmo-ready WAV.

The resample step exists because not every provider lets you request a
specific rate the way Groq's Orpheus endpoint does (`sample_rate` in the
request) — OpenAI's TTS endpoint has no such parameter at all (confirmed
against its own Python SDK's create() signature). Rather than assume what
rate it actually returns, this reads whatever the response's WAV header
declares and resamples from that to Cozmo's required 22050/48000Hz — the
same audioop.ratecv() primitive already used for the pitch-shift effect,
just used here for a plain format conversion instead of a deliberate one.

See groq_client.py / openai_client.py for where this gets called; see
docs/audio-output.md, "Making the voice sound less generic", for what each
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

        if settings.tts_leadin_ms > 0:
            frames = _prepend_silence(frames, sampwidth, nchannels, framerate, settings.tts_leadin_ms)

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


def _prepend_silence(frames: bytes, width: int, nchannels: int, framerate: int, lead_in_ms: float) -> bytes:
    """Prepend a short block of silence before the actual speech.

    Reported symptom: the first word of a spoken line is often not heard,
    while the rest of the sentence plays fine. `anim_controller.py`'s
    playback loop is a background thread polling a queue at a fixed frame
    rate and sending either real audio or `OutputSilence()` — nothing
    there structurally drops the first real packets, which points at
    Cozmo's own audio hardware needing a brief moment to actually start
    outputting sound after a run of silence (the same class of issue as
    the camera needing a stabilization wait after enable_camera() — a
    hardware warm-up, not a software queueing bug). This is the standard
    fix for that class of problem: throwaway silence eats the warm-up
    instead of the first real word. Untested against actual hardware —
    there's no Cozmo here to confirm how much lead-in is actually needed.
    """
    n_samples = int(framerate * (lead_in_ms / 1000.0)) * nchannels
    return (b"\x00" * (n_samples * width)) + frames


def _resample(frames: bytes, width: int, nchannels: int, from_rate: int, to_rate: int) -> bytes:
    converted, _ = audioop.ratecv(frames, width, nchannels, from_rate, to_rate, None)
    return converted


def _normalize_gain(frames: bytes, width: int, max_gain: float, target_rms_ratio: float = 0.2) -> bytes:
    """Boost volume up to `max_gain`, targeting a minimum RMS (average
    loudness) level, with a soft limiter protecting against overflow
    instead of a hard peak ceiling.

    This replaced a pure peak-based normalizer (scale until the loudest
    sample hits ~95% of full scale). That's not the same as "equally
    loud": two voices with different crest factors (peak-to-RMS ratio) can
    be peak-matched exactly and still sound very different in volume,
    since RMS — not the single loudest instant — is what's actually
    perceived as loudness. Measured directly: OpenAI's default TTS voice
    sits around an 8:1 crest factor (peaky - mostly quiet, occasional
    spikes), noticeably higher than Groq's Orpheus, so peak-matching them
    left OpenAI sounding quieter overall at an identical peak. A linear
    gain can't fix that by itself — it scales peak and RMS by the same
    factor — only compression/limiting can, which is what `_soft_limit`
    below does: it compresses samples as they approach full scale instead
    of hard-cutting them off, trading a little harmonic distortion on the
    loudest peaks for genuinely higher perceived loudness (the same
    tradeoff most commercial audio mastering makes).
    """
    rms = audioop.rms(frames, width)
    if rms == 0:
        return frames
    full_scale = (1 << (8 * width - 1)) - 1
    gain = max(1.0, min(max_gain, (full_scale * target_rms_ratio) / rms))
    return _soft_limit(audioop.mul(frames, width, gain), width)


def _soft_limit(frames: bytes, width: int) -> bytes:
    """tanh soft-clipper: compresses samples smoothly as they approach full
    scale instead of hard-cutting them off, so an RMS-driven gain that
    pushes some peaks past full scale doesn't produce harsh digital
    clipping. Samples well below full scale are left almost unchanged
    (tanh(x) ≈ x for small x)."""
    if width != 2:
        return frames
    full_scale = 32767.0
    samples = array.array("h")
    samples.frombytes(frames)
    for i, s in enumerate(samples):
        samples[i] = int(full_scale * math.tanh(s / full_scale))
    return samples.tobytes()


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
