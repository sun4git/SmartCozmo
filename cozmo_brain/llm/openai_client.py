"""OpenAI API wrappers: Whisper speech-to-text and OpenAI TTS, as an
alternative to Groq (e.g. when Groq's free-tier rate limits are hit).
Same transcribe()/synthesize() shape as GroqClient — see SpeechClient in
speech_client.py — so AUDIO_PROVIDER=openai in .env is a drop-in swap.

Confirmed against OpenAI's own Python SDK source (not guessed): the create
speech (TTS) endpoint has **no `sample_rate` parameter at all**, unlike
Groq's Orpheus endpoint. Whatever rate it actually returns for
response_format="wav" isn't hardcoded here — tts_postprocess.py reads the
real rate from the response's own WAV header and resamples to
TTS_SAMPLE_RATE, so this doesn't depend on knowing that number in advance.

Voice/model names are OpenAI's own and differ from Groq/Orpheus's (see
OPENAI_TTS_VOICE / OPENAI_TTS_MODEL / OPENAI_STT_MODEL in .env.example).
"""

from __future__ import annotations

import requests

from cozmo_brain.config import Settings
from cozmo_brain.llm.tts_postprocess import apply_cozmo_voice_character

_STT_URL = "https://api.openai.com/v1/audio/transcriptions"
_TTS_URL = "https://api.openai.com/v1/audio/speech"


class OpenAIClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._session = requests.Session()
        self._headers = {"Authorization": f"Bearer {settings.require_openai_key()}"}

    def transcribe(self, wav_path: str) -> str:
        with open(wav_path, "rb") as f:
            resp = self._session.post(
                _STT_URL,
                headers=self._headers,
                files={"file": f},
                data={"model": self._settings.openai_stt_model},
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
                "model": self._settings.openai_tts_model,
                "voice": voice or self._settings.openai_tts_voice,
                "input": text,
                "response_format": "wav",
            },
            timeout=30,
        )
        resp.raise_for_status()

        audio_bytes = apply_cozmo_voice_character(resp.content, self._settings)
        with open(out_path, "wb") as f:
            f.write(audio_bytes)
        return out_path
