"""The core agentic loop: user text in, LLM decides tool calls, tools execute
against the robot, results feed back to the LLM, repeat until it stops
calling tools (or a safety cap is hit). Mode-agnostic — every mode (voice,
VAD, text, calibration) funnels user input through `CozmoEngine.handle_turn`.
"""

from __future__ import annotations

import logging
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
        self.last_interaction_monotonic = time.monotonic()
        self.conversation.add_user(user_text, images=images)
        schema = [t.schema() for t in self._tools]
        summary_lines: list[str] = []

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

        self.conversation.save()
        return "\n".join(summary_lines) if summary_lines else "(no response)"
