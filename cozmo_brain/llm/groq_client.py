"""Groq API wrappers: Whisper speech-to-text and Orpheus text-to-speech.

Orpheus's voices are generic human voices, not Cozmo's actual (small,
squeaky, quasi-robotic) one — Anki's real voice processing is proprietary
and unavailable here. `_postprocess` gets closer with two cheap, well-known
DSP tricks applied to the raw PCM:

- Pitch/tempo shift ("chipmunk" resampling trick): makes the voice both
  higher-pitched and faster, which reads as smaller/more childlike/robotic.
- Ring modulation: mixes in a fixed-frequency carrier tone for a subtly
  metallic timbre — the classic cheap sci-fi "robot voice" effect.

Neither is a faithful recreation of Cozmo's real voice; both are blind,
untuned-by-ear defaults (this environment can't play audio to judge the
result) — expect to adjust TTS_PITCH_SHIFT / TTS_ROBOT_MOD_DEPTH in .env to
taste once you can actually listen on the robot.

Volume gain (`TTS_GAIN`, for Cozmo's quiet speaker) is peak-normalizing, not
a blind multiply — see `_normalize_gain`. The original fixed-multiply version
was clipping ~0.5% of samples at its default of 3.0x.
"""

from __future__ import annotations

import array
import audioop
import io
import logging
import math
import wave

import requests

from cozmo_brain.config import Settings

logger = logging.getLogger(__name__)

_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
_TTS_URL = "https://api.groq.com/openai/v1/audio/speech"


class GroqClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._session = requests.Session()
        self._headers = {"Authorization": f"Bearer {settings.require_groq_key()}"}

    def transcribe(self, wav_path: str) -> str:
        with open(wav_path, "rb") as f:
            resp = self._session.post(
                _STT_URL,
                headers=self._headers,
                files={"file": f},
                data={"model": self._settings.stt_model},
                timeout=30,
            )
        resp.raise_for_status()
        return resp.json()["text"].strip()

    def synthesize(self, text: str, out_path: str, voice: str | None = None) -> str:
        """Generate speech for `text`, apply voice effects + gain, and write a Cozmo-ready WAV to out_path."""
        resp = self._session.post(
            _TTS_URL,
            headers={**self._headers, "Content-Type": "application/json"},
            json={
                "model": self._settings.tts_model,
                "voice": voice or self._settings.tts_voice,
                "input": text,
                "response_format": "wav",
                "sample_rate": self._settings.tts_sample_rate,
            },
            timeout=30,
        )
        resp.raise_for_status()

        audio_bytes = _postprocess(resp.content, self._settings)
        with open(out_path, "wb") as f:
            f.write(audio_bytes)
        return out_path


def _postprocess(wav_bytes: bytes, settings: Settings) -> bytes:
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
            # Groq streams this WAV with a placeholder "unknown length" RIFF/data
            # size (nframes reads back as 2147483647, not the real frame count).
            # Only copy the actual format fields — never getparams()/setparams()
            # wholesale — and let the writer compute the real size from what's
            # actually written, or it'll try to write a header claiming billions
            # of frames and overflow struct's unsigned-long pack.
            nchannels = reader.getnchannels()
            sampwidth = reader.getsampwidth()
            framerate = reader.getframerate()
            frames = reader.readframes(reader.getnframes())

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


def _normalize_gain(frames: bytes, width: int, max_gain: float, target_peak_ratio: float = 0.95) -> bytes:
    """Boost volume up to `max_gain`, but automatically back off short of
    clipping: scales only as far as the loudest sample reaching
    `target_peak_ratio` of full scale, whichever is smaller.

    This replaced a blind fixed multiply (`audioop.mul(frames, width, gain)`)
    that clipped ~0.5% of samples at the previous default (TTS_GAIN=3.0 on
    Orpheus output that already peaks around 60% of full scale) — audible as
    harsh crackle, not the "small robot voice" effect it was meant to help
    with.
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
