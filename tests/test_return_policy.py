"""Test: ChargerReturner policy, BatteryMonitor gating, dock tool
branching, engine.speak()."""
import dataclasses
import logging
import sys
import threading
import time

import _harness  # noqa: F401 - repo on sys.path, real .env ignored
from cozmo_brain.charger_return import ChargerReturner
from cozmo_brain.config import Settings
from cozmo_brain.conversation import Conversation
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.robot.base import MoveResult
from cozmo_brain.robot.battery_monitor import BatteryMonitor
from cozmo_brain.robot.simulated import SimulatedRobot
from cozmo_brain.tools import build_tools

logging.basicConfig(level=logging.WARNING)
S = dataclasses.replace(Settings(), audio_output="cozmo")
failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


class FakeRobot(SimulatedRobot):
    def __init__(self):
        super().__init__()
        self.on_charger = False
        self.pose_known = True
        self.healthy = True
        self.voltage = 3.9
        self.return_result = MoveResult(moved=True)
        self.returns = 0
        self.docks = 0

    def is_on_charger(self): return self.on_charger
    def has_charger_pose(self): return self.pose_known
    def is_healthy(self): return self.healthy
    def get_battery_voltage(self): return self.voltage
    def show_expression(self, name, duration=None): pass
    def display_custom_image(self, image, duration=None): pass

    def return_to_charger(self):
        self.returns += 1
        return self.return_result

    def dock(self):
        self.docks += 1
        return MoveResult(moved=True)


class FakeEngine:
    def __init__(self):
        self.turn_lock = threading.Lock()
        self.said, self.notes = [], []
        self.listen_requests = 0

    def request_listen(self):
        self.listen_requests += 1

    def speak(self, text, mood="neutral"):
        self.said.append(text)
        return True

    def add_note(self, text):
        self.notes.append(text)


def feed(cr, robot, volts):
    for v in volts:
        robot.voltage = v
        cr.on_battery_reading(v)


# 1. Single low reading doesn't trigger; two do (offer, not return).
robot, eng = FakeRobot(), FakeEngine()
cr = ChargerReturner(robot, eng, S)
feed(cr, robot, [3.65])
check("one low reading: nothing yet", not eng.said)
feed(cr, robot, [3.65])
check("two low readings: offer spoken", len(eng.said) == 1 and "head back" in eng.said[0])
check("offer written to conversation", eng.notes and "call the dock tool" in eng.notes[0])
check("LOW does not drive", robot.returns == 0)
check("LOW offer asks to listen for the answer (no wake word)", eng.listen_requests == 1)
feed(cr, robot, [3.65, 3.65, 3.8, 3.65, 3.65])
check("offer not repeated in same episode (even after bounce)", len(eng.said) == 1)

# 2. Critical twice -> announce + autonomous return.
feed(cr, robot, [3.45])
check("one critical reading: no return yet", robot.returns == 0)
feed(cr, robot, [3.45])
check("two critical readings: returned", robot.returns == 1 and "heading back" in eng.said[-1])
check("critical 'heading back now' doesn't ask to listen (no question)", eng.listen_requests == 1)
check("return outcome noted", "Result: Drove back to the charger and docked." in eng.notes[-1])
feed(cr, robot, [3.45, 3.45])
check("no repeat critical return", robot.returns == 1)

# 3. On-charger reading ends the episode; next discharge can offer again.
robot.on_charger = True
feed(cr, robot, [3.45])
robot.on_charger = False
feed(cr, robot, [3.65, 3.65])
check("new episode after charger: offer again", sum("head back" in s for s in eng.said) == 2)

# 4. Load sag: alternating low/healthy never triggers.
robot, eng = FakeRobot(), FakeEngine()
cr = ChargerReturner(robot, eng, S)
feed(cr, robot, [3.65, 3.8, 3.65, 3.8, 3.45, 3.8])
check("non-consecutive low readings never trigger", not eng.said)

# 5. No pose known -> ask for help once, at LOW and not again at CRITICAL.
robot, eng = FakeRobot(), FakeEngine()
robot.pose_known = False
cr = ChargerReturner(robot, eng, S)
feed(cr, robot, [3.65, 3.65, 3.45, 3.45])
check("no pose: help asked exactly once, no return", eng.said == [eng.said[0]] and "put me on it" in eng.said[0]
      and robot.returns == 0)
check("help request (put me on it) doesn't ask to listen", eng.listen_requests == 0)

# 6. Return fails (picked up) -> ask for help.
robot, eng = FakeRobot(), FakeEngine()
robot.return_result = MoveResult(moved=True, reason="picked_up")
cr = ChargerReturner(robot, eng, S)
feed(cr, robot, [3.45, 3.45])
check("failed return -> help asked", robot.returns == 1 and "put me on it" in eng.said[-1])
check("failure noted honestly", "picked up" in eng.notes[0])

# 7. Straight to critical skips the LOW offer.
robot, eng = FakeRobot(), FakeEngine()
cr = ChargerReturner(robot, eng, S)
feed(cr, robot, [3.45, 3.45])
check("straight to critical: no offer, just go", robot.returns == 1 and not any("Want me" in s for s in eng.said))

# 8. Disabled -> nothing.
robot, eng = FakeRobot(), FakeEngine()
cr = ChargerReturner(robot, eng, dataclasses.replace(S, auto_return_to_charger_enabled=False))
feed(cr, robot, [3.45] * 4)
check("disabled: nothing", not eng.said and robot.returns == 0)

# 9. Turn in progress: waits for it, then acts.
robot, eng = FakeRobot(), FakeEngine()
cr = ChargerReturner(robot, eng, S)
eng.turn_lock.acquire()
threading.Timer(0.5, eng.turn_lock.release).start()
t0 = time.monotonic()
feed(cr, robot, [3.45, 3.45])
check(f"waited for in-progress turn ({time.monotonic()-t0:.1f}s) then returned", robot.returns == 1
      and time.monotonic() - t0 >= 0.45)

# 10. BatteryMonitor: stale connection -> no reading passed on; healthy -> passed on.
robot = FakeRobot()
got = []
bm = BatteryMonitor(robot, S, on_reading=got.append)
robot.healthy = False
bm._check_once()
check("stale connection: listener not called", not got)
robot.healthy = True
robot.voltage = 3.9
bm._check_once()
check("healthy reading passed to listener", got == [3.9])

# 11. dock tool branches + engine.speak via the real say tool.
class FakeSpeech:
    def synthesize(self, text, path):
        return path
    def transcribe(self, path):
        return ""

robot = FakeRobot()
tools = {t.name: t for t in build_tools(robot, FakeSpeech(), None, S)}
robot.pose_known = True
r = tools["dock"].handler({})
check(f"dock tool navigates when pose known ({r.message if hasattr(r,'message') else r})", robot.returns == 1 and robot.docks == 0)
robot.pose_known = False
tools["dock"].handler({})
check("dock tool falls back to blind dock without pose", robot.docks == 1)
robot.on_charger = True
r = tools["dock"].handler({})
check("dock tool: already on charger", robot.returns == 1 and robot.docks == 1)

conv = Conversation("sys")
engine = CozmoEngine(S, robot, None, FakeSpeech(), list(tools.values()), conv)
check("engine.speak goes through say tool", engine.speak("hello", mood="sleepy"))
engine.add_note("[note]")
check("engine.add_note appends assistant message", conv.messages[-1] == {"role": "assistant", "content": "[note]"})

print("\nFAILURES:", failures or "none")
sys.exit(1 if failures else 0)
