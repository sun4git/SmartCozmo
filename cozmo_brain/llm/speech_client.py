"""Common interface every STT/TTS provider client implements (GroqClient,
OpenAIClient, ...), so the rest of the app doesn't need to know which one
is actually active. Structural (Protocol), not inheritance — neither
concrete class needs to change to satisfy this."""

from __future__ import annotations

from typing import Protocol


class SpeechClient(Protocol):
    def transcribe(self, wav_path: str) -> str: ...

    def synthesize(self, text: str, out_path: str, voice: str | None = None) -> str: ...
