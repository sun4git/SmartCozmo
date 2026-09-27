"""Shared implementation for any OpenAI-compatible /chat/completions
endpoint. Groq and OpenAI both expose one, with an identical request/
response shape for tool-calling - only the base URL, auth, and model name
differ. Used by GroqChatClient (groq_client.py) and OpenAIChatClient
(openai_client.py).

Translates cozmo_brain's internal message shape - Ollama's own /api/chat
conventions, since that's the format Conversation/engine.py build (see
conversation.py) - into what OpenAI-compatible endpoints strictly require:
"tool" messages keyed by tool_call_id (not name), and tool_calls with a
JSON-*string*-encoded arguments field (not a dict). Getting this wrong
doesn't degrade gracefully - both providers reject the request outright
with a 400 on the very first tool-calling turn. See _to_wire_messages().

Known gap: vision (the `look` tool's photo) isn't translated here - Ollama's
"images" message field is silently dropped rather than converted to
OpenAI's content-array image format, so vision-in-chat only works with
CHAT_PROVIDER=ollama today.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import requests

from cozmo_brain.llm.ollama_client import ChatResponse, ToolCall

logger = logging.getLogger(__name__)


def _to_wire_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wire: list[dict[str, Any]] = []
    for msg in messages:
        if msg["role"] == "tool":
            wire.append(
                {
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", ""),
                }
            )
            continue

        out: dict[str, Any] = {"role": msg["role"], "content": msg.get("content", "")}

        tool_calls = msg.get("tool_calls")
        if tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": json.dumps(tc["function"]["arguments"]),
                    },
                }
                for tc in tool_calls
            ]

        wire.append(out)
    return wire


class OpenAICompatibleChatClient:
    """Talks to an OpenAI-compatible /chat/completions endpoint with
    tool-calling. Model must support tools to get tool_calls back."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout_s: int):
        self._base_url = base_url
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._model = model
        self._timeout_s = timeout_s
        self._session = requests.Session()

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": _to_wire_messages(messages),
        }
        if tools:
            payload["tools"] = tools

        resp = self._session.post(
            f"{self._base_url}/chat/completions",
            headers=self._headers,
            json=payload,
            timeout=self._timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()

        message = data["choices"][0]["message"]
        content = message.get("content") or ""

        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls", []) or []:
            fn = tc.get("function", {})
            args = fn.get("arguments", "{}")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    logger.warning("Could not parse tool arguments as JSON: %r", args)
                    args = {}
            tool_calls.append(ToolCall(name=fn.get("name", ""), arguments=args, id=tc.get("id", "")))

        return ChatResponse(content=content, tool_calls=tool_calls, raw=data)

    def is_reachable(self) -> bool:
        try:
            resp = self._session.get(f"{self._base_url}/models", headers=self._headers, timeout=5)
            return resp.ok
        except requests.RequestException:
            return False
