"""Thin HTTP client for an Ollama-compatible /api/chat endpoint with tool-calling."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import requests

from cozmo_brain.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    # Ollama's own tool_calls don't carry one; OpenAI-compatible endpoints
    # (Groq, OpenAI) require one on every tool_call, echoed back on the
    # matching "tool" role reply. Generated here if the backend didn't send
    # one, so callers (engine.py) can thread it through uniformly regardless
    # of which chat provider is active.
    id: str = ""


@dataclass
class ChatResponse:
    content: str
    tool_calls: list[ToolCall]
    raw: dict[str, Any]


class OllamaClient:
    """Talks to Ollama's chat API. Model must support `tools` to get tool_calls back."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._session = requests.Session()

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResponse:
        payload: dict[str, Any] = {
            "model": self._settings.ollama_model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        resp = self._session.post(
            f"{self._settings.ollama_base_url}/api/chat",
            json=payload,
            timeout=self._settings.ollama_timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()

        message = data.get("message", {}) or {}
        content = message.get("content", "") or ""

        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls", []) or []:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args = fn.get("arguments", {})
            if isinstance(args, str):
                # Some backends stringify arguments instead of returning a dict.
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    logger.warning("Could not parse tool arguments as JSON: %r", args)
                    args = {}
            tool_calls.append(ToolCall(name=name, arguments=args, id=tc.get("id") or f"call_{len(tool_calls)}"))

        return ChatResponse(content=content, tool_calls=tool_calls, raw=data)

    def is_reachable(self) -> bool:
        try:
            resp = self._session.get(f"{self._settings.ollama_base_url}/api/tags", timeout=5)
            return resp.ok
        except requests.RequestException:
            return False
