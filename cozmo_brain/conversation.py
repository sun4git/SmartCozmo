"""Conversation memory: keeps chat history across turns with simple trimming."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _from_first_user(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop leading messages until a "user" one. A plain tail slice can cut
    between an assistant's tool_calls and their "tool" results, leaving
    history that starts with an orphaned tool result - Ollama shrugs that
    off, but OpenAI (and other strict OpenAI-compatible APIs) reject the
    whole request with a 400."""
    for i, msg in enumerate(messages):
        if msg.get("role") == "user":
            return messages[i:]
    return []


class Conversation:
    """A running list of chat messages, trimmed to stay under a message-count budget.

    Trimming keeps the system prompt plus the most recent messages. This is a
    simple, predictable strategy rather than token-aware summarization — good
    enough while conversations stay short (see README roadmap for
    summarization as a future improvement).
    """

    def __init__(self, system_prompt: str, max_messages: int = 40, history_path: str | None = None):
        self._system_prompt = system_prompt
        self._max_messages = max_messages
        self._history_path = Path(history_path) if history_path else None
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    def add_user(self, text: str, images: list[str] | None = None) -> None:
        msg: dict[str, Any] = {"role": "user", "content": text}
        if images:
            msg["images"] = images
        self.messages.append(msg)
        self._trim()

    def add_assistant(self, content: str, tool_calls: list[dict[str, Any]] | None = None) -> None:
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self.messages.append(msg)
        self._trim()

    def add_tool_result(self, tool_name: str, result: str, tool_call_id: str = "") -> None:
        # "name" is Ollama's own convention; "tool_call_id" is what
        # OpenAI-compatible endpoints (Groq, OpenAI) require instead - see
        # openai_compatible_chat.py's _to_wire_messages(), which picks
        # whichever field its target API actually needs.
        self.messages.append({"role": "tool", "name": tool_name, "tool_call_id": tool_call_id, "content": result})
        self._trim()

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": self._system_prompt}]

    def _trim(self) -> None:
        if len(self.messages) <= self._max_messages:
            return
        system = self.messages[0]
        recent = self.messages[-(self._max_messages - 1):]
        self.messages = [system, *_from_first_user(recent)]
        logger.debug("Trimmed conversation history to %d messages", len(self.messages))

    def save(self) -> None:
        if not self._history_path:
            return
        try:
            self._history_path.write_text(json.dumps(self.messages, indent=2), encoding="utf-8")
        except OSError as e:
            logger.warning("Could not save conversation history: %s", e)

    def load(self) -> bool:
        if not self._history_path or not self._history_path.exists():
            return False
        try:
            messages = json.loads(self._history_path.read_text(encoding="utf-8"))
            if not messages:
                return False
            # A file saved before _trim() learned to cut at a user message
            # may already start with an orphaned tool result.
            self.messages = [messages[0], *_from_first_user(messages[1:])]
            return True
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Could not load conversation history, starting fresh: %s", e)
            return False
