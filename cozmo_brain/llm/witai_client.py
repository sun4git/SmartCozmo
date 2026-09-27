"""Wit.ai (Meta) speech-to-text, as a free, no-billing STT_PROVIDER option.
STT ONLY: Wit.ai has no text-to-speech product, so this only ever
satisfies SpeechClient.transcribe(); synthesize() raises if actually
called (a TTS_PROVIDER=witai config mistake, not a real code path).

Confirmed against Wit.ai's own official Python client (wit-ai/pywit) source:
- POST https://api.wit.ai/dictation (pure transcription, no NLU/intent
  extraction - /speech is the NLU-flavored sibling endpoint, not needed here)
- Authorization: Bearer <access_token>
- Accept: application/vnd.wit.<version>+json (a dated version string;
  pywit itself defaults to "20200513", this uses a newer one)
- Audio sent as the raw request body (not multipart); Content-Type is left
  to the caller by Wit.ai's own client library

**Not independently verified against a real account/token yet** - Wit.ai's
docs site is a JS-rendered app that returned no usable content when
fetched in the session this was written in, so the request shape above was
cross-checked against their official client library source instead. Two
specific gaps to confirm once a real token is available:
1. The exact Content-Type for a plain WAV file - "audio/wav" is what
   community integrations commonly use, matching Cozmo's own recorder
   output (a real WAV file, not headerless raw PCM), but unconfirmed here.
2. Whether the response can be multiple newline-delimited JSON objects
   (partial results, then final) rather than always a single JSON blob -
   parses defensively for that case regardless. See _parse_transcript().
"""

from __future__ import annotations

import json

import requests

from cozmo_brain.config import Settings

_API_VERSION = "20240304"
_TRANSCRIBE_URL = f"https://api.wit.ai/dictation?v={_API_VERSION}"


def _parse_transcript(raw: str) -> str:
    """Take the last line that actually parses as JSON and has a "text"
    field, so both a single-JSON-object response and a multi-line
    (partial-then-final) one work the same way."""
    text = ""
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        text = data.get("text", text)
    return text.strip()


class WitAIClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._session = requests.Session()
        self._headers = {
            "Authorization": f"Bearer {settings.require_witai_key()}",
            "Accept": f"application/vnd.wit.{_API_VERSION}+json",
        }

    def transcribe(self, wav_path: str) -> str:
        with open(wav_path, "rb") as f:
            resp = self._session.post(
                _TRANSCRIBE_URL,
                headers={**self._headers, "Content-Type": "audio/wav"},
                data=f,
                timeout=30,
            )
        resp.raise_for_status()
        return _parse_transcript(resp.text)

    def synthesize(self, text: str, out_path: str, voice: str | None = None) -> str:
        raise NotImplementedError(
            "Wit.ai has no text-to-speech API - set TTS_PROVIDER to groq/openai/local instead of witai."
        )
