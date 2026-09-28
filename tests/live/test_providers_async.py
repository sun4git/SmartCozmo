# LIVE TEST - makes real API calls with the keys in your .env (costs a few
# requests per provider; Groq's free tier can rate-limit with 429s). Not run
# by tests/run_all.py - run it yourself from the repo root, e.g.:
#   python tests/live/test_providers_async.py [groq|openai|ollama]
"""Live: FINAL_LLM_CALL=async with real models - does the 'say alone, act later' model still act?"""
import dataclasses, logging, os, sys, time
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
logging.basicConfig(level=logging.WARNING, format="    %(name)s: %(message)s"); logging.getLogger("cozmo_brain.engine").setLevel(logging.INFO)
class NoAudio:
    def synthesize(self, t, p): return p
class Robot(SimulatedRobot):
    def __init__(self): super().__init__(); self.moves = []
    def turn(self, a): self.moves.append(f"turn {a:g}"); return MoveResult(moved=True)
    def drive(self, d, s): self.moves.append(f"drive {d:g}"); return MoveResult(moved=True)
s = dataclasses.replace(Settings(), chat_provider=sys.argv[1], vision_provider="", audio_output="cozmo", final_llm_call="async")
chat = create_chat_client(s)
for prompt in ["Please turn left 90 degrees.", "Hi Cozmo, how are you?"]:
    robot = Robot()
    engine = CozmoEngine(s, robot, chat, NoAudio(), build_tools(robot, NoAudio(), chat, s), Conversation(SYSTEM_PROMPT))
    t0 = time.monotonic(); summ = engine.handle_turn(prompt, allow_async_followup=True); back = time.monotonic() - t0; print("    summary:", summ.replace(chr(10), " | "))
    engine.turn_lock.acquire(timeout=60); engine.turn_lock.release(); total = time.monotonic() - t0
    print(f"  {prompt!r}: returned for listening after {back:.1f}s, turn fully done {total:.1f}s, moves={robot.moves}, continued={engine.followup_interrupt.is_set()}")
