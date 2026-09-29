"""OpenAI API wrappers: Whisper speech-to-text and OpenAI TTS, as an
alternative to Groq (e.g. when Groq's free-tier rate limits are hit), plus
(OpenAIChatClient) chat/tool-calling.
Same transcribe()/synthesize() shape as GroqClient — see SpeechClient in
speech_client.py — so STT_PROVIDER=openai / TTS_PROVIDER=openai in .env is a
drop-in swap, independently for STT and TTS.

Confirmed against OpenAI's own Python SDK source (not guessed): the create
speech (TTS) endpoint has **no `sample_rate` parameter at all**, unlike
Groq's Orpheus endpoint. Whatever rate it actually returns for
response_format="wav" isn't hardcoded here — tts_postprocess.py reads the
real rate from the response's own WAV header and resamples to
TTS_SAMPLE_RATE, so this doesn't depend on knowing that number in advance.

Voice/model names are OpenAI's own and differ from Groq/Orpheus's (see
OPENAI_TTS_VOICE / OPENAI_TTS_MODEL / OPENAI_STT_MODEL in .env.example).

Measured directly (same sentence, both providers, raw output before any of
our own post-processing): OpenAI's "alloy" voice speaks about 14% faster
than Groq's Orpheus "austin" voice for identical text — a real difference
between the two voices/engines, not a bug. OPENAI_TTS_SPEED uses the API's
own native `speed` parameter to compensate, rather than fighting it via our
own pitch-shift.
"""

from __future__ import annotations

import requests

from cozmo_brain.config import Settings
from cozmo_brain.llm.openai_compatible_chat import OpenAICompatibleChatClient
from cozmo_brain.llm.stt_postprocess import text_from_whisper_response, whisper_request_fields
from cozmo_brain.llm.tts_postprocess import apply_cozmo_voice_character

_STT_URL = "https://api.openai.com/v1/audio/transcriptions"
_TTS_URL = "https://api.openai.com/v1/audio/speech"
_CHAT_BASE_URL = "https://api.openai.com/v1"


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
                data={
                    "model": self._settings.openai_stt_model,
                    **whisper_request_fields(self._settings.openai_stt_model, self._settings),
                },
                timeout=30,
            )
        resp.raise_for_status()
        return text_from_whisper_response(resp.json(), self._settings)

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
                "speed": self._settings.openai_tts_speed,
            },
            timeout=30,
        )
        resp.raise_for_status()

        audio_bytes = apply_cozmo_voice_character(resp.content, self._settings)
        with open(out_path, "wb") as f:
            f.write(audio_bytes)
        return out_path


class OpenAIChatClient(OpenAICompatibleChatClient):
    """OpenAI's own chat-completions endpoint — the reference shape
    OpenAICompatibleChatClient is built against. See openai_compatible_chat.py."""

    def __init__(self, settings: Settings):
        super().__init__(
            base_url=_CHAT_BASE_URL,
            api_key=settings.require_openai_key(),
            model=settings.openai_chat_model,
            vision_model=settings.openai_vision_model or settings.openai_chat_model,
            timeout_s=settings.chat_timeout_s,
        )
