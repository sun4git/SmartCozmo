# LIVE TEST - makes real API calls with the keys in your .env (costs a few
# requests per provider; Groq's free tier can rate-limit with 429s). Not run
# by tests/run_all.py - run it yourself from the repo root, e.g.:
#   python tests/live/test_providers_batching.py [groq|openai|ollama]
"""Scratch live test: when asked to move, does each model batch say + action
in one step (safe with END_TURN_AFTER_FINAL_SAY), or say alone then act later?"""
import dataclasses, logging, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
from cozmo_brain.config import Settings
from cozmo_brain.conversation import Conversation
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.llm import create_chat_client
from cozmo_brain.personality import SYSTEM_PROMPT
from cozmo_brain.robot.base import MoveResult
from cozmo_brain.robot.simulated import SimulatedRobot
from cozmo_brain.tools import build_tools
logging.disable(logging.CRITICAL)

class NoAudio:
    def synthesize(self, t, p): return p
class Robot(SimulatedRobot):
    def __init__(self): super().__init__(); self.moves = []
    def turn(self, a): self.moves.append(f"turn {a:g}"); return MoveResult(moved=True)
    def drive(self, d, s): self.moves.append(f"drive {d:g}"); return MoveResult(moved=True)

provider = sys.argv[1]
s = dataclasses.replace(Settings(), chat_provider=provider, vision_provider="", audio_output="cozmo")
chat = create_chat_client(s)
steps = []
orig = chat.chat
def spy(messages, tools=None):
    r = orig(messages, tools=tools); steps.append([tc.name for tc in r.tool_calls]); return r
chat.chat = spy
for prompt in ["Please turn left 90 degrees.", "Drive forward 10 centimeters, then turn right a bit."]:
    steps.clear()
    robot = Robot()
    engine = CozmoEngine(s, robot, chat, NoAudio(), build_tools(robot, NoAudio(), chat, s), Conversation(SYSTEM_PROMPT))
    engine.handle_turn(prompt)
    ok = bool(robot.moves)
    print(f"  {prompt!r}: steps={steps} moves={robot.moves} -> {'OK' if ok else 'ACTION SKIPPED (the known risk)'}")
