"""The core agentic loop: user text in, LLM decides tool calls, tools execute
against the robot, results feed back to the LLM, repeat until it stops
calling tools (or a safety cap is hit). Mode-agnostic — every mode (voice,
VAD, text, calibration) funnels user input through `CozmoEngine.handle_turn`.
"""

from __future__ import annotations

import logging
import threading
import time

import requests

from cozmo_brain.config import Settings
from cozmo_brain.conversation import Conversation
from cozmo_brain.imaging import encode_image_b64
from cozmo_brain.llm.chat_client import ChatClient
from cozmo_brain.llm.speech_client import SpeechClient
from cozmo_brain.robot.base import RobotBackend
from cozmo_brain.tools.base import Tool, ToolResult

logger = logging.getLogger(__name__)

# Appended to the user's message when the robot can't move right now
# (RobotBackend.is_movement_blocked()). Confirmed on real hardware that
# telling the model only via the drive/turn tool result is too late: it
# usually calls `say` ("I'm coming!") *before* `drive` in the same turn, so
# the promise was already spoken before it ever learned the drive was
# refused. This gives it the fact before it decides what to say.
_CHARGER_BLOCKED_NOTE = (
    "[Status: I'm on my charger and my battery is still too low to come off - drive, turn, and "
    "the wheel parts of gestures won't work right now. If asked to move or come out, say I can't "
    "yet because I need to charge more; don't say I'm coming.]"
)

# Spoken by the engine itself if a drive/turn was refused for that reason
# and the model never said anything after finding out (e.g. it ran out of
# MAX_TOOL_ITERATIONS, or ignored the tool result) - the human must never be
# left with a spoken promise and a Cozmo that silently didn't move.
_CHARGER_BLOCKED_FALLBACK = "Sorry, I can't come out yet - my battery's too low. I need to charge a bit more first."


class CozmoEngine:
    def __init__(
        self,
        settings: Settings,
        robot: RobotBackend,
        ollama: ChatClient,
        speech: SpeechClient,
        tools: list[Tool],
        conversation: Conversation,
    ):
        self._settings = settings
        self._robot = robot
        self._ollama = ollama
        self._speech = speech
        self._tools = tools
        self._tools_by_name = {t.name: t for t in tools}
        self.conversation = conversation
        # Tracks real conversation activity for idle_fidget.py - starts at
        # construction time (not 0/unset) so idle counting begins from
        # process start rather than looking infinitely idle before the
        # first turn ever happens.
        self.last_interaction_monotonic: float = time.monotonic()
        # Held for the whole of every handle_turn(). Background behavior that
        # speaks, drives, or writes to the conversation on its own
        # (charger_return.py) takes this too, so it never talks over a reply
        # in progress or mutates the message list mid-turn - and a turn that
        # starts meanwhile simply waits for it to finish.
        self.turn_lock = threading.Lock()
        # (is_on_charger, is_charging) as of the last status note sent to
        # the model - None until the first turn. See _charger_status_note().
        self._told_charger_state: tuple[bool, bool] | None = None

    def _charger_status_note(self) -> str | None:
        """A one-line status note whenever Cozmo's on/off-charger (or
        charging) state differs from what the model was last told - and on
        the first turn. Confirmed on real hardware: a gesture drove him off
        the charger mid-conversation (allowed) and the model, never told,
        insisted "I'm already here!" when asked to go back. Only sent on a
        change, so it doesn't pad every turn."""
        try:
            state = (self._robot.is_on_charger(), self._robot.is_charging())
        except Exception as e:  # noqa: BLE001 - a status check must never break a turn
            logger.debug("Charger status check failed: %s", e)
            return None
        if state == self._told_charger_state:
            return None
        self._told_charger_state = state
        on_charger, charging = state
        if not on_charger:
            return "[Status: I'm off my charger right now.]"
        if charging:
            return "[Status: I'm on my charger and charging.]"
        return "[Status: I'm on my charger, not currently charging (probably full).]"

    def _movement_blocked(self) -> bool:
        try:
            return self._robot.is_movement_blocked()
        except Exception as e:  # noqa: BLE001 - a status check must never break a turn
            logger.debug("Movement-blocked check failed: %s", e)
            return False

    def speak(self, text: str, mood: str = "neutral") -> bool:
        """Say something unprompted, through the same `say` tool the model
        uses (same TTS provider, AUDIO_OUTPUT routing, and lift handling).
        Caller must hold turn_lock. Returns whether it succeeded."""
        result = self._call_tool("say", {"text": text, "mood": mood})
        if not result.ok:
            logger.warning("Unprompted speech failed: %s", result.to_tool_message())
        return result.ok

    def add_note(self, text: str) -> None:
        """Record something Cozmo said/did on his own as an assistant message,
        so the model has context for the human's next reply (e.g. "yes" to a
        low-battery offer it never made itself). Caller must hold turn_lock."""
        self.conversation.add_assistant(text)
        self.conversation.save()

    def _call_tool(self, name: str, arguments: dict) -> ToolResult:
        tool = self._tools_by_name.get(name)
        if tool is None:
            known = ", ".join(sorted(self._tools_by_name))
            return ToolResult(False, f"Unknown tool '{name}'. Known tools: {known}")

        if not self._robot.is_healthy():
            logger.info("Robot connection looks stale before calling '%s' — reconnecting.", name)
            if not self._robot.reconnect():
                return ToolResult(False, "Cozmo seems to be disconnected, and reconnecting didn't work.")

        try:
            return tool.handler(arguments)
        except Exception as e:  # noqa: BLE001 - any tool failure becomes model-visible feedback
            logger.warning("Tool '%s' failed: %s", name, e)
            return ToolResult(False, str(e))

    def handle_turn(self, user_text: str, images: list[str] | None = None) -> str:
        """Runs one full user turn through the tool-calling loop. Returns a
        transcript-ish summary of what Cozmo said/did, for logging/display."""
        with self.turn_lock:
            return self._handle_turn(user_text, images)

    def _handle_turn(self, user_text: str, images: list[str] | None) -> str:
        self.last_interaction_monotonic = time.monotonic()
        charger_note = self._charger_status_note()
        if charger_note:
            user_text = f"{user_text}\n\n{charger_note}"
        if self._movement_blocked():
            user_text = f"{user_text}\n\n{_CHARGER_BLOCKED_NOTE}"
        self.conversation.add_user(user_text, images=images)
        schema = [t.schema() for t in self._tools]
        summary_lines: list[str] = []
        # True once a drive/turn got refused by the charger check, until a
        # successful `say` afterward - i.e. the model has told them why.
        unexplained_block = False

        for iteration in range(self._settings.max_tool_iterations):
            try:
                response = self._ollama.chat(self.conversation.messages, tools=schema)
            except requests.RequestException as e:
                logger.error("Ollama request failed: %s", e)
                summary_lines.append(f"(couldn't reach the LLM at {self._settings.ollama_base_url}: {e})")
                break
            tool_calls_raw = [
                {"id": tc.id, "function": {"name": tc.name, "arguments": tc.arguments}}
                for tc in response.tool_calls
            ]
            self.conversation.add_assistant(response.content, tool_calls=tool_calls_raw or None)

            if response.content:
                summary_lines.append(f"(thinking) {response.content}")

            if not response.tool_calls:
                if iteration == 0:
                    logger.info("Model returned no tool calls this turn.")
                break

            image_to_attach: str | None = None
            image_caption = "[Cozmo just looked around and captured a photo of what's in front of him.]"
            for tc in response.tool_calls:
                result = self._call_tool(tc.name, tc.arguments)
                summary_lines.append(f"[{tc.name}] {result.to_tool_message()}")
                self.conversation.add_tool_result(tc.name, result.to_tool_message(), tool_call_id=tc.id)
                if result.extra and result.extra.get("blocked_by_charger"):
                    unexplained_block = True
                elif tc.name == "say" and result.ok:
                    unexplained_block = False
                if result.ok and result.extra and result.extra.get("image_path"):
                    image_to_attach = result.extra["image_path"]
                    image_caption = result.extra.get("image_caption", image_caption)

            if image_to_attach and self._settings.vision_enabled:
                try:
                    b64 = encode_image_b64(image_to_attach)
                    self.conversation.add_user(image_caption, images=[b64])
                except OSError as e:
                    logger.warning("Could not attach captured photo: %s", e)
        else:
            logger.warning("Hit max_tool_iterations (%d) without a final reply.", self._settings.max_tool_iterations)

        if unexplained_block:
            logger.info("A move was refused (charging, low battery) and never explained - saying so directly.")
            if self.speak(_CHARGER_BLOCKED_FALLBACK, mood="sleepy"):
                self.conversation.add_assistant(f"[I said this out loud:] {_CHARGER_BLOCKED_FALLBACK}")
                summary_lines.append(f"[say] {_CHARGER_BLOCKED_FALLBACK}")

        self.conversation.save()
        return "\n".join(summary_lines) if summary_lines else "(no response)"
