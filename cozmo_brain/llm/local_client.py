"""Fully local, offline STT/TTS: faster-whisper (CTranslate2) for
transcription, Piper for speech synthesis. No network call, no API key — see
LOCAL_STT_MODEL / LOCAL_STT_DEVICE / LOCAL_STT_COMPUTE_TYPE /
LOCAL_TTS_VOICE_PATH / LOCAL_TTS_SPEED in .env.example. Requires `pip install faster-whisper
piper-tts` (commented out in requirements.txt by default, like
webrtcvad/openwakeword) and a downloaded Piper voice
(`python3 -m piper.download_voices <name>`).

Note: the currently maintained `piper-tts` package (published from the
OHF-Voice/piper1-gpl fork, since the original rhasspy/piper repo was
archived) is GPL-3.0-or-later, not the original's MIT license.

Both engines load lazily, on first actual use rather than at construction —
so e.g. STT_PROVIDER=local + TTS_PROVIDER=groq only ever loads the Whisper
model into memory, never touches Piper, and vice versa. faster-whisper's
first run for a given LOCAL_STT_MODEL downloads it from Hugging Face into a
local cache — that one-time step needs internet; every run after that is
fully offline.
"""

from __future__ import annotations

import io
import wave

from cozmo_brain.config import Settings
from cozmo_brain.llm.stt_postprocess import filter_whisper_segments
from cozmo_brain.llm.tts_postprocess import apply_cozmo_voice_character


class LocalClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._whisper_model = None
        self._piper_voice = None

    def _whisper(self):
        if self._whisper_model is None:
            from faster_whisper import WhisperModel

            self._whisper_model = WhisperModel(
                self._settings.local_stt_model,
                device=self._settings.local_stt_device,
                compute_type=self._settings.local_stt_compute_type,
            )
        return self._whisper_model

    def transcribe(self, wav_path: str) -> str:
        # vad_filter: faster-whisper's built-in Silero voice-activity filter,
        # which skips non-speech stretches *before* decoding - noise inside
        # the clip never gets the chance to become hallucinated text. The
        # language hint and per-segment confidence filter are the same
        # defenses the hosted Whisper providers get (stt_postprocess.py).
        segments, _info = self._whisper().transcribe(
            wav_path, language=self._settings.stt_language or None, vad_filter=True
        )
        return filter_whisper_segments(
            [
                {
                    "text": s.text,
                    "no_speech_prob": s.no_speech_prob,
                    "avg_logprob": s.avg_logprob,
                    "compression_ratio": s.compression_ratio,
                }
                for s in segments
            ],
            self._settings,
        )

    def _piper(self):
        if self._piper_voice is None:
            from piper import PiperVoice

            self._piper_voice = PiperVoice.load(self._settings.local_tts_voice_path)
        return self._piper_voice

    def synthesize(self, text: str, out_path: str, voice: str | None = None) -> str:
        """Generate speech for `text`, apply voice effects + gain, and write a Cozmo-ready WAV to out_path."""
        # Only built (and only touches Piper's other synthesis defaults -
        # noise_scale etc.) when a non-default speed is actually configured,
        # so LOCAL_TTS_SPEED=1.0 (the default) leaves synthesize_wav() exactly
        # as it was before this setting existed.
        syn_config = None
        speed = self._settings.local_tts_speed
        if speed != 1.0:
            from piper import SynthesisConfig

            syn_config = SynthesisConfig(length_scale=1.0 / speed if speed > 0 else 1.0)

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            self._piper().synthesize_wav(text, wav_file, syn_config=syn_config)

        audio_bytes = apply_cozmo_voice_character(buffer.getvalue(), self._settings)
        with open(out_path, "wb") as f:
            f.write(audio_bytes)
        return out_path
