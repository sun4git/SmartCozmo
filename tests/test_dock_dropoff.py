"""Test: the fidget-drives-off-after-docking bug and fixes 1-3."""
import contextlib, dataclasses, io, logging, sys, threading, time
from types import SimpleNamespace as NS

import _harness  # noqa: F401 - repo on sys.path, real .env ignored

logging.disable(logging.CRITICAL)
with contextlib.redirect_stdout(io.StringIO()):
    from fake_pycozmo import make_robot
from pycozmo.robot import RobotStatusFlag as F
from cozmo_brain.config import Settings
from cozmo_brain.conversation import Conversation
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.idle_fidget import IdleFidgeter
from cozmo_brain.tools import build_tools

failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: failures.append(name)

# --- Fix 2: stay-on-charger no longer depends on the lagging IS_CHARGING flag ---
r, cli = make_robot()
cases = [
    ("docked, IS_CHARGING not yet set, 3.2V (the real-hardware case)", F.IS_ON_CHARGER, 3.2, True),
    ("docked + charging, 3.2V", F.IS_ON_CHARGER | F.IS_CHARGING, 3.2, True),
    ("docked, full, 4.1V", F.IS_ON_CHARGER, 4.1, False),
    ("docked + charging, 3.9V (explicit come-out still allowed)", F.IS_ON_CHARGER | F.IS_CHARGING, 3.9, False),
    ("off the charger, 3.2V", 0, 3.2, False),
]
for label, status, volts, expect in cases:
    r._latest_status, cli.battery_voltage = status, volts
    check(f"fix 2: {label} -> stay={expect}", r._must_stay_on_charger() == expect)
r._latest_status, cli.battery_voltage = F.IS_ON_CHARGER | F.IS_CHARGING, 0.0
check("fix 2: unknown voltage + charging -> stay (unchanged)", r._must_stay_on_charger())
r._latest_status = F.IS_ON_CHARGER
check("fix 2: unknown voltage, not charging -> allowed (unchanged)", not r._must_stay_on_charger())

# --- Reproduce the real bug: return in progress, fidget fires, then docking ---
r, cli = make_robot(charger_contact_wait_s=2.0)
cli.battery_voltage = 3.9               # left the charger earlier, at a healthy voltage
r._latest_status = F.IS_ON_CHARGER
r.drive(150, 60)                      # -> charger pose recorded
r._latest_status = 0
cli.battery_voltage = 3.2               # battery since dropped to critical
cli.set_pose(-200.0, 150.0, 30.0)
orig_drive = r.drive
def drive_then_dock(d, s, **kw):
    res = orig_drive(d, s, **kw)
    if kw.get("stop_on_charger"):     # dock reverse done -> contacts on, IS_CHARGING lags
        r._latest_status = F.IS_ON_CHARGER
    return res
r.drive = drive_then_dock

class Chat:
    def chat(self, m, tools=None): return NS(content="", tool_calls=[])
class Speech:
    def synthesize(self, t, p): return p
S = dataclasses.replace(r._settings, idle_fidget_after_s=0)
engine = CozmoEngine(S, r, Chat(), Speech(), build_tools(r, Speech(), Chat(), S), Conversation("sys"))
fidgeter = IdleFidgeter(r, engine, S)
import cozmo_brain.idle_fidget as m
m.random.choice = lambda seq: "peek"   # the gesture with turn steps

def returner():
    with engine.turn_lock:            # what charger_return.py holds while returning
        r.return_to_charger()
t = threading.Thread(target=returner); t.start()
time.sleep(0.3)                       # mid-return
fidgeter._check_once()                # the fidget that used to queue behind the return
t.join()
time.sleep(0.2)
exits_after_dock = [c for c in cli.calls if c[0] == "drive_wheels" and c[1] > 0 and r.is_on_charger()]
check(f"repro: fidget during return was skipped, still on charger after docking (on_charger={r.is_on_charger()})",
      r.is_on_charger())
# and even if a drive is attempted right after docking (lagging IS_CHARGING), fix 2 refuses it
res = r.turn(15)
check(f"fix 2: a turn right after docking at 3.2V is refused, no drive-off ({res})", res.moved is False and r.is_on_charger())

# --- Fix 1: fidget never overlaps a turn / return ---
calls = []
class R2:
    def is_on_charger(self): return False
    def is_charging(self): return False
    def is_movement_blocked(self): return False
    def run_gesture(self, name, wheels=True): calls.append(wheels)
class E2:
    last_interaction_monotonic = 0.0
    listening_window_open = False
    turn_lock = threading.Lock()
f = IdleFidgeter(R2(), E2(), S)
E2.turn_lock.acquire()
f._check_once()
check("fix 1: no fidget while turn_lock is held", calls == [])
E2.turn_lock.release()
f._check_once()
check("fix 1: fidgets once free, and releases the lock after", calls == [True] and not E2.turn_lock.locked())

# --- Fix 3: status note on every turn ---
class R3:
    pass
from cozmo_brain.robot.simulated import SimulatedRobot
class Docked(SimulatedRobot):
    def is_on_charger(self): return True
    def is_charging(self): return True
    def show_expression(self, n, duration=None): pass
seen = []
class Chat3:
    def chat(self, messages, tools=None):
        seen.append(messages[-1]["content"]); return NS(content="", tool_calls=[])
robot = Docked()
eng = CozmoEngine(S, robot, Chat3(), Speech(), build_tools(robot, Speech(), Chat3(), S), Conversation("sys"))
eng.handle_turn("one"); eng.handle_turn("two"); eng.handle_turn("three")
check("fix 3: charger status sent on every turn, not just on change",
      all("[Status: I'm on my charger and charging.]" in u for u in seen) and len(seen) == 3)

# --- background speech counts as activity ---
eng.last_interaction_monotonic = 0.0
with eng.turn_lock:
    eng.speak("hi")
check("speak() counts as activity for the fidget timer", time.monotonic() - eng.last_interaction_monotonic < 1)

print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
