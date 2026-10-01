"""The memory tools: remember_fact, forget_fact, search_memory - backed by
memory.py (data/memory.md) and the conversation archive."""

from __future__ import annotations

from cozmo_brain.memory import Memory
from cozmo_brain.tools.base import Tool, ToolResult


def build_memory_tools(memory: Memory) -> list[Tool]:
    def handle_remember(args: dict) -> ToolResult:
        ok, message = memory.remember(str(args.get("fact", "")), str(args.get("person") or ""))
        return ToolResult(ok, message)

    def handle_forget(args: dict) -> ToolResult:
        ok, message = memory.forget(str(args.get("fact", "")))
        return ToolResult(ok, message)

    def handle_search(args: dict) -> ToolResult:
        return ToolResult(True, memory.search(str(args.get("query", ""))))

    # remember_fact is a pure action (its result only confirms the save), so
    # a turn may end right after it. forget_fact's result says which fact
    # went - or that the request was ambiguous - and search_memory's is the
    # answer itself, so the model must read both.
    return [
        Tool(
            name="remember_fact",
            description=(
                "Save a lasting fact about a person to your long-term memory (shown to you every "
                "turn under 'What you remember'). Use it when someone asks you to remember "
                "something, or tells you something clearly lasting and important about "
                "themselves (a preference, a birthday, who someone is to them) - and say that "
                "you'll remember it. One short fact per call, written in the third person "
                "('Likes cricket.'). Never save passwords, health, money, or address details, "
                "even if asked. For remembering a face, use remember_person instead."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "The fact, short and in the third person."},
                    "person": {
                        "type": "string",
                        "description": (
                            "Who the fact is about. Leave empty for the person you're talking to "
                            "(the primary user). Give a name only for someone else."
                        ),
                    },
                },
                "required": ["fact"],
            },
            handler=handle_remember,
            safe_to_end_turn=True,
        ),
        Tool(
            name="forget_fact",
            description=(
                "Remove a fact from your long-term memory when someone asks you to forget it or "
                "says it's wrong. Pass the fact's words as they appear under 'What you remember'. "
                "If several facts match, nothing is removed and the result lists them - ask which one."
            ),
            parameters={
                "type": "object",
                "properties": {"fact": {"type": "string", "description": "The fact to forget."}},
                "required": ["fact"],
            },
            handler=handle_forget,
        ),
        Tool(
            name="search_memory",
            description=(
                "Search your saved facts and all past conversations (every earlier day, with dates "
                "and times) by keywords. Use it when asked about something from before that isn't "
                "in the current conversation - 'what did we talk about yesterday?', 'when did I "
                "tell you about the trip?' - or when you need a detail you don't see. Use a few "
                "distinctive keywords, not a whole sentence."
            ),
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "A few keywords to search for."}},
                "required": ["query"],
            },
            handler=handle_search,
        ),
    ]
