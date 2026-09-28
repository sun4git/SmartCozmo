"""Test: dock() stop-on-contact + overshoot, offset logging, charger status note."""
import dataclasses
import logging
import sys
import threading
import time
from types import SimpleNamespace as NS

import _harness  # noqa: F401 - repo on sys.path, real .env ignored

from pycozmo.robot import RobotStatusFlag as F

from cozmo_brain.config import Settings
from cozmo_brain.conversation import Conversation
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.robot.simulated import SimulatedRobot
from cozmo_brain.tools import build_tools
from fake_pycozmo import make_robot  # reuses the fake pycozmo client (runs its checks on import too)

records = []
logging.getLogger("cozmo_brain.robot.real").addHandler(type("H", (logging.Handler,), {"emit": lambda s, r: records.append(r.getMessage())})())
logging.getLogger("cozmo_brain.robot.real").setLevel(logging.INFO)  # the checks below read INFO lines
failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


print("\n--- dock contact tests ---")
# 1. Contacts engage partway -> stops early, doesn't run the full distance+overshoot.
r, cli = make_robot()
threading.Timer(0.6, lambda: setattr(r, "_latest_status", F.IS_ON_CHARGER)).start()
t0 = time.monotonic()
res = r.dock()
elapsed = time.monotonic() - t0
full = (r._settings.charger_dock_distance_mm + r._settings.charger_dock_overshoot_mm) / r._settings.charger_dock_speed_mmps
check(f"stops on contact ({elapsed:.1f}s vs full {full:.1f}s)", elapsed < 1.2 and res.completed)
check("reversed (negative wheel speed)", any(c[0] == "drive_wheels" and c[1] < 0 for c in cli.calls))
check("cliff protection restored after dock", cli.conn.sent.count("EnableStopOnCliff") >= 2)

# 2. Contacts never engage -> runs full distance+overshoot, waits CHARGER_CONTACT_WAIT_S, then gives up.
r, cli = make_robot(charger_contact_wait_s=1.0)
t0 = time.monotonic()
res = r.dock()
elapsed = time.monotonic() - t0
check(f"no contact: runs full dock distance + overshoot ({elapsed:.1f}s ~ {full:.1f}s)", abs(elapsed - (full + 1.0)) < 0.4)
check("logged contacts NOT engaged", any("contacts NOT engaged" in m for m in records))

# 3. Offset log math: charger at (0,0,0deg), stopped at (30,-8) -> 30 short, -8 sideways.
r, cli = make_robot()
records.clear()
from cozmo_brain.robot.base import Pose2D
cli.set_pose(30.0, -8.0, 2.0)
r._log_dock_offset(Pose2D(0.0, 0.0, 0.0))
check(f"offset math ({records})", records and "30.0mm short, -8.0mm sideways, 2.0deg" in records[-1])
records.clear()
cli.set_pose(0.0, 30.0, 92.0)  # charger heading 90: 30mm along +y = 30 short
r._log_dock_offset(Pose2D(0.0, 0.0, 90.0))
check(f"offset math rotated ({records})", records and "30.0mm short, 0.0mm sideways, 2.0deg" in records[-1])

# 4. Engine charger status note: first turn, only on change.
class R(SimulatedRobot):
    on, ch = True, True
    def is_on_charger(self): return self.on
    def is_charging(self): return self.ch
    def show_expression(self, name, duration=None): pass

class Chat:
    def __init__(self): self.users = []
    def chat(self, messages, tools=None):
        self.users.append(messages[-1]["content"] if messages[-1]["role"] == "user" else None)
        return NS(content="", tool_calls=[])

class Speech:
    def synthesize(self, t, p): return p

robot, chat = R(), Chat()
S = dataclasses.replace(Settings(), audio_output="cozmo")
engine = CozmoEngine(S, robot, chat, Speech(), build_tools(robot, Speech(), chat, S), Conversation("sys"))
engine.handle_turn("hi")
check("first turn: on-charger+charging note", "on my charger and charging" in chat.users[-1])
engine.handle_turn("hi again")
check("unchanged: note still sent every turn", "on my charger and charging" in chat.users[-1])
robot.on = False
engine.handle_turn("where are you")
check("left charger: off note", "off my charger" in chat.users[-1])
robot.on, robot.ch = True, False
engine.handle_turn("and now")
check("docked not charging: full note", "not currently charging" in chat.users[-1])

# 5. Flag lags behind physically seating (the real-hardware case): appears 1.5s after the reverse stops.
r, cli = make_robot(charger_contact_wait_s=5.0)
r._latest_status = F.IS_ON_CHARGER
r.drive(150, 60)                 # leave charger, records pose
r._latest_status = 0
cli.set_pose(-300.0, 200.0, 40.0)
orig_drive = r.drive
def drive_then_late_flag(d, s, **kw):
    res = orig_drive(d, s, **kw)
    if kw.get("stop_on_charger"):
        threading.Timer(1.5, lambda: setattr(r, "_latest_status", F.IS_ON_CHARGER)).start()
    return res
r.drive = drive_then_late_flag
records.clear()
res = r.return_to_charger()
check(f"late contact flag -> reported as docked ({res})", res.completed)
check(f"latency logged ({[m for m in records if 'engaged' in m][:1]})", any("Charger contacts engaged 1." in m for m in records))

print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
