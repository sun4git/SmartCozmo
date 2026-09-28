"""Tool plumbing: the schema the LLM sees, and the result contract handlers return."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ToolResult:
    ok: bool
    message: str
    extra: dict[str, Any] | None = None

    def to_tool_message(self) -> str:
        prefix = "OK" if self.ok else "ERROR"
        return f"{prefix}: {self.message}"


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], ToolResult]
    # Whether a turn may end right after this tool without showing its
    # result to the model (see engine.py's _turn_is_done()). True only for
    # fire-and-forget actions whose success message tells the model nothing
    # new (say, gesture, a completed drive...). Defaults to False on
    # purpose: a new tool that returns information the model must read
    # (a photo, a list, a status) is safe automatically - the engine
    # keeps asking the model, the old behavior - and only needs opting in
    # if it's a pure action.
    safe_to_end_turn: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
