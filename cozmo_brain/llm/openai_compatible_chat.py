"""Shared implementation for any OpenAI-compatible /chat/completions
endpoint. Groq and OpenAI both expose one, with an identical request/
response shape for tool-calling - only the base URL, auth, and model name
differ. Used by GroqChatClient (groq_client.py) and OpenAIChatClient
(openai_client.py).

Translates cozmo_brain's internal message shape - Ollama's own /api/chat
conventions, since that's the format Conversation/engine.py build (see
conversation.py) - into what OpenAI-compatible endpoints strictly require:
"tool" messages keyed by tool_call_id (not name), tool_calls with a
JSON-*string*-encoded arguments field (not a dict), and image attachments
as a content-array "image_url" part (not Ollama's own top-level "images"
list of bare base64 strings). Getting the first two wrong doesn't degrade
gracefully - both providers reject the request outright with a 400 on the
very first tool-calling turn. See _to_wire_messages().
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import requests

from cozmo_brain.llm.ollama_client import ChatResponse, ToolCall

logger = logging.getLogger(__name__)


def _sniff_image_mime(raw: bytes) -> str:
    """Detect the real format from the image's own magic bytes rather than
    trusting a file extension - `who_is_this` compares a `look`-captured
    PNG (camera_snapshot_path) against `remember_person`-saved *.jpg
    reference photos in the same call, so a single hardcoded mime type
    would be wrong for one side of that comparison."""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    logger.warning("Unrecognized image format (first bytes: %r), assuming JPEG.", raw[:8])
    return "image/jpeg"


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

        images = msg.get("images")
        if images:
            content: list[dict[str, Any]] = []
            text = msg.get("content", "")
            if text:
                content.append({"type": "text", "text": text})
            for b64 in images:
                mime = _sniff_image_mime(base64.b64decode(b64))
                content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
            out: dict[str, Any] = {"role": msg["role"], "content": content}
        else:
            out = {"role": msg["role"], "content": msg.get("content", "")}

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

    def __init__(self, base_url: str, api_key: str, model: str, vision_model: str, timeout_s: int):
        self._base_url = base_url
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._model = model
        self._vision_model = vision_model
        self._timeout_s = timeout_s
        self._session = requests.Session()

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResponse:
        # Switch to the vision model for any turn carrying an image - checks
        # the whole history, not just the latest message, so it stays
        # selected until an old image-bearing turn ages out of the trimmed
        # history (see OllamaClient.chat()'s identical logic/rationale).
        model = self._vision_model if any(m.get("images") for m in messages) else self._model

        payload: dict[str, Any] = {
            "model": model,
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
