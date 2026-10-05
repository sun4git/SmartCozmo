"""Conversation memory: what the model sees - the system prompt plus recent
chat history, trimmed to a message budget. Every message is also copied to
the conversation archive (history_archive.py), which keeps everything, and
the next run resumes from there (load())."""

from __future__ import annotations

import logging
from typing import Any

from cozmo_brain.history_archive import HistoryArchive

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
    enough while conversations stay short (see docs/roadmap.md for
    summarization as a future improvement).
    """

    def __init__(self, system_prompt: str, max_messages: int = 40, archive: HistoryArchive | None = None):
        self._system_prompt = system_prompt
        self._max_messages = max_messages
        self.archive = archive
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    def set_system_prompt(self, system_prompt: str) -> None:
        """Replace the system prompt (rebuilt every turn with the current
        memory and time - see engine.py). Not archived: it's not part of
        what was said."""
        self._system_prompt = system_prompt
        self.messages[0] = {"role": "system", "content": system_prompt}

    def mark(self, event: str, **fields: Any) -> None:
        """Write a marker to the archive (session_start, session_end), if any."""
        if self.archive is not None:
            self.archive.record_event(event, **fields)

    def _append(self, msg: dict[str, Any]) -> None:
        self.messages.append(msg)
        if self.archive is not None:
            self.archive.record_message(msg)
        self._trim()

    def add_user(self, text: str, images: list[str] | None = None) -> None:
        msg: dict[str, Any] = {"role": "user", "content": text}
        if images:
            msg["images"] = images
        self._append(msg)

    def add_assistant(self, content: str, tool_calls: list[dict[str, Any]] | None = None) -> None:
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self._append(msg)

    def add_tool_result(self, tool_name: str, result: str, tool_call_id: str = "") -> None:
        # "name" is Ollama's own convention; "tool_call_id" is what
        # OpenAI-compatible endpoints (Groq, OpenAI) require instead - see
        # openai_compatible_chat.py's _to_wire_messages(), which picks
        # whichever field its target API actually needs.
        self._append({"role": "tool", "name": tool_name, "tool_call_id": tool_call_id, "content": result})

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": self._system_prompt}]

    def _trim(self) -> None:
        if len(self.messages) <= self._max_messages:
            return
        system = self.messages[0]
        recent = self.messages[-(self._max_messages - 1):]
        self.messages = [system, *_from_first_user(recent)]
        logger.debug("Trimmed conversation history to %d messages", len(self.messages))

    def load(self) -> bool:
        """Resume from the archive: the most recent messages of earlier runs,
        behind the current system prompt (the saved one may be out of date).
        Returns whether anything was loaded."""
        if self.archive is None:
            return False
        recent = _from_first_user(self.archive.recent_messages(self._max_messages - 1))
        if not recent:
            return False
        self.messages = [self.messages[0], *recent]
        return True
