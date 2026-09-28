"""Test: end the turn after a clean final `say`, but not otherwise."""
import dataclasses, logging, sys
from types import SimpleNamespace as NS

import _harness  # noqa: F401 - repo on sys.path, real .env ignored
from cozmo_brain.config import Settings
from cozmo_brain.conversation import Conversation
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.robot.base import MoveResult
from cozmo_brain.robot.simulated import SimulatedRobot
from cozmo_brain.tools import build_tools

logging.disable(logging.CRITICAL)
failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: failures.append(name)

class R(SimulatedRobot):
    drive_result = MoveResult(moved=True)
    def drive(self, d, s): return self.drive_result
    def show_expression(self, name, duration=None): pass

class Speech:
    def synthesize(self, t, p): return p

def call(tool, **a): return NS(id=tool, name=tool, arguments=a)
DONE = NS(content="", tool_calls=[])

def llm_calls(script, robot=None, **settings):
    robot = robot or R()
    S = dataclasses.replace(Settings(), audio_output="cozmo", **{"final_llm_call": "skip", **settings})
    class Chat:
        n = 0
        def chat(self, messages, tools=None):
            Chat.n += 1
            return script.pop(0) if script else DONE
    chat = Chat()
    engine = CozmoEngine(S, robot, chat, Speech(), build_tools(robot, Speech(), chat, S), Conversation("sys"))
    engine.handle_turn("hi")
    return Chat.n, engine

say = lambda t="Hello!": call("say", text=t)

n, _ = llm_calls([NS(content="", tool_calls=[say()])])
check(f"plain reply: 1 LLM call, not 2 (got {n})", n == 1)

n, _ = llm_calls([NS(content="", tool_calls=[say()])], final_llm_call="sync")
check(f"setting off: old behavior, 2 calls (got {n})", n == 2)

n, _ = llm_calls([NS(content="", tool_calls=[call("drive", distance_mm=50, speed_mmps=60), say()])])
check(f"successful drive then say: 1 call (got {n})", n == 1)

r = R(); r.drive_result = MoveResult(moved=True, hazard="cliff")
n, _ = llm_calls([NS(content="", tool_calls=[call("drive", distance_mm=50, speed_mmps=60), say("Here I come!")])], robot=r)
check(f"drive hit a cliff, say written before knowing: goes back to model (got {n})", n == 2)

r = R(); r.drive_result = MoveResult(moved=False)
n, _ = llm_calls([NS(content="", tool_calls=[call("drive", distance_mm=50, speed_mmps=60), say("Coming!")])], robot=r)
check(f"drive refused (charger): goes back to model (got {n})", n == 2)

n, _ = llm_calls([NS(content="", tool_calls=[say("Let me look"), call("look")]), NS(content="", tool_calls=[say("I see a cat")])])
check(f"say + look, then say: 2 calls - multi-step still works (got {n})", n == 2)

n, _ = llm_calls([NS(content="", tool_calls=[call("say", text="x", mood="not_a_mood")])])
check(f"failed say (bad mood): goes back to model (got {n})", n >= 2)

n, _ = llm_calls([NS(content="", tool_calls=[say(), call("gesture", name="peek")])])
check(f"last call not say: goes back to model (got {n})", n == 2)

n, engine = llm_calls([NS(content="", tool_calls=[say()])])
roles = [m["role"] for m in engine.conversation.messages]
check(f"history ends on tool result, valid for next user turn ({roles})", roles == ["system", "user", "assistant", "tool"])


import time
# --- information tool before say: must go back to the model ---
n, _ = llm_calls([NS(content="", tool_calls=[call("list_animations"), say("Let me check my moves")])])
check(f"list_animations + say: goes back to model so it can read the list (got {n})", n == 2)

# --- natural order: say, then act (the pattern seen in real logs) ---
r = R()
moves = []
r.turn = lambda a: (moves.append(("turn", a)), MoveResult(moved=True))[1]
r.drive = lambda d, s: (moves.append(("drive", d)), MoveResult(moved=True))[1]
n, _ = llm_calls([NS(content="", tool_calls=[say("On it!"), call("turn", angle_degrees=90), call("drive", distance_mm=300, speed_mmps=80)])], robot=r)
check(f"say -> turn -> drive: actions happen, model sees results (calls={n}, moves={moves})", n == 2 and len(moves) == 2)

# --- gestures: a slow fake so timing is measurable ---
class SlowGestureRobot(R):
    def __init__(self):
        super().__init__(); self.events = []
    def set_head_angle_deg(self, a, duration=0.4):
        time.sleep(0.6); self.events.append(("head_done", time.monotonic()))
    def say_wav(self, path):
        self.events.append(("audio_start", time.monotonic())); time.sleep(0.2)

def gesture_turn(**settings):
    r = SlowGestureRobot()
    t0 = time.monotonic()
    n, _ = llm_calls([NS(content="", tool_calls=[call("say", text="Hi!", gesture="alert")])], robot=r, **settings)
    return n, r, time.monotonic() - t0

for label, st in [("async on (default)", {}),
                  ("async on + speech-sync", {"gesture_speech_sync_enabled": True}),
                  ("async off", {"gesture_async_enabled": False})]:
    n, r, took = gesture_turn(**st)
    heads = [t for e, t in r.events if e == "head_done"]
    audio = [t for e, t in r.events if e == "audio_start"][0]
    finished_before_return = bool(heads)
    check(f"{label}: 1 LLM call, turn returned only after the gesture finished (calls={n}, took {took:.1f}s)",
          n == 1 and finished_before_return and took >= 0.6)
    if label != "async off":
        check(f"{label}: speech still overlaps the gesture (audio started before head move finished)", audio < heads[-1])
    else:
        check("async off: gesture fully before speech, as before", heads[-1] <= audio)

# --- the known risk: model speaks alone, planning to act next step ---
r = R(); moves = []
r.turn = lambda a: (moves.append(("turn", a)), MoveResult(moved=True))[1]
n, _ = llm_calls([NS(content="", tool_calls=[say("Sure, turning now!")]), NS(content="", tool_calls=[call("turn", angle_degrees=90)])], robot=r)
check(f"KNOWN RISK (documents behavior): say alone then act next step -> turn is skipped (calls={n}, moves={moves})", n == 1 and moves == [])

print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
