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

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
