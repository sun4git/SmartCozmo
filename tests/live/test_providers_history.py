# LIVE TEST - makes real API calls with the keys in your .env (costs a few
# requests per provider; Groq's free tier can rate-limit with 429s). Not run
# by tests/run_all.py - run it yourself from the repo root, e.g.:
#   python tests/live/test_providers_history.py [groq|openai|ollama]
"""Scratch live test: does each chat provider accept a history that ends on a
tool result (END_TURN_AFTER_FINAL_SAY) when the next user turn arrives?
Runs the real CozmoEngine for two turns per provider. Never prints keys."""
import dataclasses
import logging
import os
import sys

from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import requests

from cozmo_brain.config import Settings
from cozmo_brain.conversation import Conversation
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.llm import create_chat_client
from cozmo_brain.personality import SYSTEM_PROMPT
from cozmo_brain.robot.simulated import SimulatedRobot
from cozmo_brain.tools import build_tools

logging.basicConfig(level=logging.WARNING, format="    %(levelname)s %(name)s: %(message)s")
logging.getLogger("cozmo_brain.engine").setLevel(logging.INFO)


class NoAudio:
    def synthesize(self, text, path):
        return path

    def transcribe(self, path):
        return ""


class QuietRobot(SimulatedRobot):
    def __init__(self):
        super().__init__()
        self._connected = True


def run(provider: str) -> None:
    print(f"\n=== {provider} ===")
    s = dataclasses.replace(
        Settings(), chat_provider=provider, vision_provider="", audio_output="cozmo",
        conversation_history_path="", end_turn_after_final_say=True,
    )
    try:
        chat = create_chat_client(s)
    except Exception as e:  # missing key etc.
        print(f"  SKIPPED - couldn't build client: {e}")
        return

    # Surface the provider's actual error body if a request is rejected.
    orig_chat = chat.chat
    def chat_logged(messages, tools=None):
        try:
            return orig_chat(messages, tools=tools)
        except requests.HTTPError as e:
            print(f"  HTTP {e.response.status_code} from provider: {e.response.text[:500]}")
            raise
    chat.chat = chat_logged

    robot = QuietRobot()
    tools = build_tools(robot, NoAudio(), chat, s)
    conv = Conversation(SYSTEM_PROMPT, history_path=None)
    engine = CozmoEngine(s, robot, chat, NoAudio(), tools, conv)

    t1 = engine.handle_turn("Hi Cozmo! Say hello to me.")
    print("  turn 1:", " | ".join(l for l in t1.splitlines() if l.startswith("[")) or t1)
    ended_on_tool = conv.messages[-1]["role"] == "tool"
    print(f"  history after turn 1 ends on: {conv.messages[-1]['role']!r}"
          + ("  <- the case being tested" if ended_on_tool else "  (turn didn't end early - model took another step)"))

    t2 = engine.handle_turn("What was the very first thing you said to me just now?")
    failed = "couldn't reach the LLM" in t2
    print("  turn 2:", " | ".join(l for l in t2.splitlines() if l.startswith("[")) or t2)
    if failed:
        print("  RESULT: FAILED - provider rejected or couldn't be reached (see above)")
    elif not ended_on_tool:
        print("  RESULT: INCONCLUSIVE - turn 1 didn't end on a tool result")
    else:
        print("  RESULT: OK - accepted history ending on a tool result, and replied")


for p in sys.argv[1:] or ["groq", "openai", "ollama"]:
    run(p)
