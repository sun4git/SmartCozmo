"""Common interface every chat/LLM provider client implements (OllamaClient,
...), analogous to SpeechClient. Structural (Protocol), not inheritance — the
concrete class doesn't need to change to satisfy this."""

from __future__ import annotations

from typing import Any, Protocol

from cozmo_brain.llm.ollama_client import ChatResponse


class ChatClient(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResponse: ...

    def is_reachable(self) -> bool: ...
