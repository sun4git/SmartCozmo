"""Conversation memory: keeps chat history across turns with simple trimming."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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

    def add_tool_result(self, tool_name: str, result: str) -> None:
        self.messages.append({"role": "tool", "name": tool_name, "content": result})
        self._trim()

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": self._system_prompt}]

    def _trim(self) -> None:
        if len(self.messages) <= self._max_messages:
            return
        system = self.messages[0]
        recent = self.messages[-(self._max_messages - 1):]
        self.messages = [system, *recent]
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
            self.messages = json.loads(self._history_path.read_text(encoding="utf-8"))
            return True
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Could not load conversation history, starting fresh: %s", e)
            return False
