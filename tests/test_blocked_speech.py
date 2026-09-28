"""Test: charger-blocked movement is announced up front and backstopped."""
import dataclasses
import logging
import sys
from types import SimpleNamespace as NS

import _harness  # noqa: F401 - repo on sys.path, real .env ignored
from cozmo_brain.config import Settings
from cozmo_brain.conversation import Conversation
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.robot.base import MoveResult
from cozmo_brain.robot.simulated import SimulatedRobot
from cozmo_brain.tools import build_tools

logging.basicConfig(level=logging.WARNING)
S = dataclasses.replace(Settings(), audio_output="cozmo")
failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


class R(SimulatedRobot):
    def __init__(self, blocked):
        super().__init__()
        self.blocked = blocked
    def is_movement_blocked(self): return self.blocked
    def drive(self, d, s): return MoveResult(moved=not self.blocked)
    def show_expression(self, name, duration=None): pass


class Speech:
    def __init__(self): self.spoken = []
    def synthesize(self, text, path):
        self.spoken.append(text)
        return path


def call(name, **args):
    return NS(id=name, name=name, arguments=args)


class Chat:
    """Replays a scripted list of responses; records what it was sent."""
    def __init__(self, script):
        self.script, self.seen = list(script), []
    def chat(self, messages, tools=None):
        self.seen.append([dict(m) for m in messages])
        return self.script.pop(0) if self.script else NS(content="", tool_calls=[])


def run(blocked, script):
    robot, speech, chat = R(blocked), Speech(), Chat(script)
    tools = build_tools(robot, speech, chat, S)
    engine = CozmoEngine(S, robot, chat, speech, tools, Conversation("sys"))
    engine.handle_turn("can you come out?")
    return speech.spoken, chat.seen[0][-1]["content"]


coming = NS(content="", tool_calls=[call("say", text="I'm coming!"), call("drive", distance_mm=100, speed_mmps=60)])

# 1. Blocked: status note reaches the model before it replies.
_, first_user = run(True, [])
check("blocked note appended when blocked", "too low to come off" in first_user)
_, first_user = run(False, [])
check("no blocked note when not blocked", "too low to come off" not in first_user)

# 2. Model ignores it, says "coming", drive refused, then stops -> engine fallback spoken.
spoken, _ = run(True, [coming, NS(content="", tool_calls=[])])
check(f"fallback spoken after unexplained block ({spoken})",
      spoken[-1].startswith("Sorry, I can't come out yet"))

# 3. Model corrects itself after seeing the refused drive -> no fallback.
fix = NS(content="", tool_calls=[call("say", text="Oops, my battery's too low - I can't come yet.")])
spoken, _ = run(True, [coming, fix, NS(content="", tool_calls=[])])
check(f"no fallback when model explains ({spoken})", not any(s.startswith("Sorry") for s in spoken))

# 4. Not blocked -> drive works, no fallback.
spoken, _ = run(False, [coming, NS(content="", tool_calls=[])])
check(f"unblocked: no fallback ({spoken})", spoken == ["I'm coming!"])

print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
