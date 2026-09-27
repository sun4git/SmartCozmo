"""Groq API wrappers: Whisper speech-to-text and Orpheus text-to-speech.

Voice character (pitch/tone) and gain are applied in tts_postprocess.py,
shared with every other provider client (e.g. openai_client.py).
"""

from __future__ import annotations

import requests

from cozmo_brain.config import Settings
from cozmo_brain.llm.tts_postprocess import apply_cozmo_voice_character

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
                data={"model": self._settings.groq_stt_model},
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
                "model": self._settings.groq_tts_model,
                "voice": voice or self._settings.groq_tts_voice,
                "input": text,
                "response_format": "wav",
                "sample_rate": self._settings.tts_sample_rate,
            },
            timeout=30,
        )
        resp.raise_for_status()

        audio_bytes = apply_cozmo_voice_character(resp.content, self._settings)
        with open(out_path, "wb") as f:
            f.write(audio_bytes)
        return out_path
