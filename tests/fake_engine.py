"""Fakes for driving the real CozmoEngine without a robot, mic, or LLM: a
scripted chat client (per-response delays, injected failures), a simulated
robot that records when it speaks/turns/drives, and make() to build an
engine for a given FINAL_LLM_CALL mode.
"""

import dataclasses
import time
from types import SimpleNamespace as NS  # noqa: F401 - re-exported for tests

import _harness  # noqa: F401 - repo on sys.path, real .env ignored

import requests

from cozmo_brain.config import Settings
from cozmo_brain.conversation import Conversation
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.robot.base import MoveResult
from cozmo_brain.robot.simulated import SimulatedRobot
from cozmo_brain.tools import build_tools


def call(tool, **a): return NS(id=f"{tool}-{time.monotonic_ns()}", name=tool, arguments=a)
DONE = NS(content="", tool_calls=[])
SAY = lambda t="Sure!": NS(content="", tool_calls=[call("say", text=t)])

class Robot(SimulatedRobot):
    def __init__(self):
        super().__init__(); self.events = []
    def turn(self, a): self.events.append(("turn", time.monotonic())); return MoveResult(moved=True)
    def drive(self, d, s): self.events.append(("drive", time.monotonic())); return MoveResult(moved=True)
    def show_expression(self, n, duration=None): pass
    def say_wav(self, p): self.events.append(("speak", time.monotonic()))

class Speech:
    def synthesize(self, t, p): return p

class Chat:
    """Scripted responses; `delays[i]` seconds before returning response i."""
    def __init__(self, script, delays=None, fail_at=None):
        self.script, self.delays, self.fail_at, self.n = list(script), delays or {}, fail_at, 0
    def chat(self, messages, tools=None):
        i = self.n; self.n += 1
        time.sleep(self.delays.get(i, 0))
        if i == self.fail_at: raise requests.ConnectionError("boom")
        return self.script.pop(0) if self.script else DONE

def make(mode, script, delays=None, fail_at=None):
    robot = Robot()
    s = dataclasses.replace(Settings(), audio_output="cozmo", final_llm_call=mode)
    chat = Chat(script, delays, fail_at)
    engine = CozmoEngine(s, robot, chat, Speech(), build_tools(robot, Speech(), chat, s), Conversation("sys"))
    return engine, robot, chat

def wait_idle(engine, timeout=5):
    ok = engine.turn_lock.acquire(timeout=timeout)
    if ok: engine.turn_lock.release()
    return ok
