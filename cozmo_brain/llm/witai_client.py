"""Wit.ai (Meta) speech-to-text, as a free, no-billing STT_PROVIDER option.
STT ONLY: Wit.ai has no text-to-speech product, so this only ever
satisfies SpeechClient.transcribe(); synthesize() raises if actually
called (a TTS_PROVIDER=witai config mistake, not a real code path).

**Verified directly against a real account/token and a real WAV file**
(2026-09-27, a synthesized test utterance) - not just cross-checked
against docs/client source:
- POST https://api.wit.ai/dictation (pure transcription, no NLU/intent
  extraction - /speech is the NLU-flavored sibling endpoint, not needed here)
- Authorization: Bearer <access_token>
- Accept: application/vnd.wit.<version>+json (a dated version string;
  pywit itself defaults to "20200513", this uses a newer one)
- Content-Type: audio/wav for a plain WAV file - confirmed correct, a real
  utterance came back transcribed accurately.
- The response is genuinely multiple JSON objects concatenated in one
  body - confirmed real, not a defensive guess - but NOT one-object-per-line
  NDJSON: each object is itself pretty-printed across several lines
  (partial transcriptions building up word-by-word, ending in one with
  `"is_final": true`/`"type": "FINAL_TRANSCRIPTION"`). A naive
  line-by-line JSON parse fails on every single line, since no individual
  line is valid JSON on its own - see _parse_transcript(), which decodes
  each top-level object in sequence regardless of internal newlines.

An anomaly seen twice during verification, worth taking seriously rather
than dismissing as noise: 2 of ~9 test calls (different audio each time,
immediately surrounding calls all correct) returned the exact same
unrelated transcript, verbatim: "Hey Facebook." - not two different
garbage strings, the identical phrase both times. That specificity points
away from random network/audio corruption and toward something systematic
- e.g. a low-confidence fallback baked into a fresh, completely untrained
Wit.ai app (this app has zero custom utterances/training data), possibly
example/demo content from Wit.ai's own default app template. Observed
rate so far (~20%) is too high to treat as negligible. `stt_postprocess.py`
already has a phrase-matching hallucination backstop for this exact class
of problem, but it's scoped specifically to documented Whisper (Groq/
OpenAI) artifacts, not this - if "Hey Facebook." keeps recurring on real
testing, it likely deserves its own small check here rather than being
folded into that Whisper-specific list.
"""

from __future__ import annotations

import json

import requests

from cozmo_brain.config import Settings

_API_VERSION = "20240304"
_TRANSCRIBE_URL = f"https://api.wit.ai/dictation?v={_API_VERSION}"


def _parse_transcript(raw: str) -> str:
    """Decode every top-level JSON object in the (possibly multi-object,
    each itself multi-line) response body in sequence, and return the
    "is_final" one's text - falling back to the last object seen if none
    was marked final, which shouldn't happen in practice but is a cheap
    safety net."""
    decoder = json.JSONDecoder()
    last_text = ""
    final_text = None
    idx, length = 0, len(raw)
    while idx < length:
        while idx < length and raw[idx].isspace():
            idx += 1
        if idx >= length:
            break
        try:
            obj, end = decoder.raw_decode(raw, idx)
        except json.JSONDecodeError:
            break
        idx = end
        if "text" not in obj:
            continue
        last_text = obj["text"]
        if obj.get("is_final"):
            final_text = obj["text"]
    return (final_text if final_text is not None else last_text).strip()


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
