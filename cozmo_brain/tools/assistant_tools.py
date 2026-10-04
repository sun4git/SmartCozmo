"""The ask_assistant tool: hands a request Cozmo can't handle himself to an
external personal assistant agent (llm/assistant_client.py), under the name
set in ASSISTANT_NAME - so Cozmo calls it by that name too. With a relay
(ASSISTANT_BACKGROUND, assistant_relay.py) the request runs in the
background and the answer is spoken when it arrives; without one, the tool
waits for it."""

from __future__ import annotations

from cozmo_brain.assistant_relay import AssistantRelay
from cozmo_brain.llm.assistant_client import AssistantClient
from cozmo_brain.tools.base import Tool, ToolResult


def build_assistant_tools(assistant: AssistantClient, relay: AssistantRelay | None = None) -> list[Tool]:
    name = assistant.name

    def handle_ask(args: dict) -> ToolResult:
        request = str(args.get("request", ""))
        if relay is not None:
            started, message = relay.submit(request)
            return ToolResult(started, message)
        return ToolResult(True, f"{name} says: {assistant.ask(request)}")

    if relay is not None:
        waiting = (
            f"The answer comes back later on its own (you'll be told, and speak up then), so in "
            f"the same response `say` something like 'I've asked {name}, I'll let you know' - "
            f"never guess the answer."
        )
    else:
        waiting = (
            f"It can take a while, so say a short 'let me ask {name}' with `say` in the same "
            f"response, before this call. Then tell them the answer in your own words."
        )

    # Waiting for the answer: not safe_to_end_turn, the reply is what Cozmo
    # has to pass on. In the background the result only confirms it was
    # sent, so a turn may end after it.
    return [
        Tool(
            name="ask_assistant",
            description=(
                f"Ask {name}, your human's personal assistant, to do something you can't: set a "
                f"reminder (it arrives as a message on their phone, even when you're switched off), "
                f"look something up on the web (news, weather, facts you're not sure of), or run a "
                f"task for them. Write the request as one clear, complete sentence with every "
                f"detail they gave (times, dates, names). {waiting} Not for chatting, or for "
                f"things your own tools do (moving, looking, your memory)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "request": {
                        "type": "string",
                        "description": f"What to ask {name}, as one complete sentence.",
                    }
                },
                "required": ["request"],
            },
            handler=handle_ask,
            safe_to_end_turn=relay is not None,
        )
    ]


def assistant_prompt_section(name: str, background: bool = False) -> str:
    """System-prompt guidance, added only when the assistant is enabled."""
    later = (
        f"""
- {name}'s answers come back later, on their own: you'll get a note saying what {name} said, and then you tell them, even if they're talking about something else by now."""
        if background else ""
    )
    return f"""Your helper - {name}:
- {name} is your human's personal assistant (another AI, on their phone and computer), \
reached with `ask_assistant`. Call it {name}, like they do.
- Use it for reminders, anything you'd need the internet for, and tasks outside your \
little desk. Don't guess at news, weather, or facts you're unsure of - ask {name}.
- Your hearing isn't perfect. If the request is unclear, ask them to repeat it first.
- Before anything that reaches or affects someone else (messaging a person or a group, \
buying, deleting, changing settings), say back exactly what you'll ask {name} to do \
and only call it once they say yes. Reminders for them and plain questions need no \
check.
- If {name} can't be reached or fails, say so honestly - don't make up an answer.{later}"""
