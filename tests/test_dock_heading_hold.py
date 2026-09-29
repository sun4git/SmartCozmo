"""Test: dock()'s reverse holds the charger heading, stops when a tread catches
(twist past CHARGER_DOCK_JAM_DEG), and return_to_charger() retries a miss."""
import logging
import sys
import threading
import time

import _harness  # noqa: F401 - repo on sys.path, real .env ignored
from fake_pycozmo import F, make_robot

records = []
logging.getLogger("cozmo_brain.robot.real").addHandler(type("H", (logging.Handler,), {"emit": lambda s, r: records.append(r.getMessage())})())
logging.getLogger("cozmo_brain.robot.real").setLevel(logging.INFO)
failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


def wheel_calls(cli):
    return [c for c in cli.calls if c[0] == "drive_wheels"]


# 1. Twisted clockwise (-5deg) mid-reverse -> steers counterclockwise: right
#    tread faster (less negative) than left, both still reversing.
r, cli = make_robot()
cli.set_pose(150.0, 0.0, 0.0)
threading.Timer(0.3, lambda: cli.set_pose(140.0, 0.0, -5.0)).start()
threading.Timer(0.8, lambda: setattr(r, "_latest_status", F.IS_ON_CHARGER)).start()
res = r.dock()
steered = [c for c in wheel_calls(cli) if c[1] != c[2]]
check(f"steers against a clockwise twist (right tread faster: {steered[-1:] if steered else 'none'})",
      steered and steered[-1][2] > steered[-1][1] and steered[-1][1] < 0 and steered[-1][2] < 0)
check(f"steering capped at 40% of speed ({steered[-1:]})", steered and abs(steered[-1][2] - steered[-1][1]) <= 2 * 0.4 * 60 + 1e-6)
check(f"docked on contact ({res})", res.completed)

# 2. Heading straight -> no steering at all.
r, cli = make_robot()
cli.set_pose(150.0, 0.0, 0.0)
threading.Timer(0.5, lambda: setattr(r, "_latest_status", F.IS_ON_CHARGER)).start()
r.dock()
check("straight reverse: both treads equal", all(c[1] == c[2] for c in wheel_calls(cli)))

# 3. Twist past CHARGER_DOCK_JAM_DEG -> stop early, reason jammed, no contact wait.
r, cli = make_robot(charger_contact_wait_s=5.0)
cli.set_pose(150.0, 0.0, 0.0)
threading.Timer(0.4, lambda: cli.set_pose(130.0, 0.0, -17.0)).start()
records.clear()
t0 = time.monotonic()
res = r.dock()
elapsed = time.monotonic() - t0
check(f"jam -> reason jammed, stopped early ({res}, {elapsed:.1f}s)", res.reason == "jammed" and elapsed < 1.5)
check("jam logged", any("a tread is probably caught" in m for m in records))
check("motors stopped", ("stop",) in cli.calls)

# 4. Gain 0 -> old blind reverse (no steering, no jam stop).
r, cli = make_robot(charger_dock_heading_gain=0.0)
cli.set_pose(150.0, 0.0, 0.0)
threading.Timer(0.3, lambda: cli.set_pose(140.0, 0.0, -17.0)).start()
threading.Timer(0.8, lambda: setattr(r, "_latest_status", F.IS_ON_CHARGER)).start()
res = r.dock()
check(f"gain 0: no steering, no jam ({res})", res.completed and all(c[1] == c[2] for c in wheel_calls(cli)))

# 5. return_to_charger: first reverse jams, retry lines up again and docks.
r, cli = make_robot()
cli.set_pose(0.0, 0.0, 0.0)
r._latest_status = F.IS_ON_CHARGER
r.drive(150, 60)          # leave charger (records (0,0,0))
r._latest_status = 0
cli.set_pose(300.0, 100.0, 90.0)
docks = []
orig_dock = r.dock
def fake_dock():
    docks.append(1)
    if len(docks) == 1:
        threading.Timer(0.3, lambda: cli.set_pose(120.0, 0.0, -17.0)).start()
    else:
        threading.Timer(0.3, lambda: setattr(r, "_latest_status", F.IS_ON_CHARGER)).start()
    return orig_dock()
r.dock = fake_dock
records.clear()
res = r.return_to_charger()
check(f"jam then retry docks ({res}, {len(docks)} dock attempts)", res.completed and len(docks) == 2)
check("retry logged", any("Dock attempt 2 of 2" in m for m in records))

# 6. Both attempts jam -> reported as jammed, no third attempt.
r, cli = make_robot()
cli.set_pose(0.0, 0.0, 0.0)
r._latest_status = F.IS_ON_CHARGER
r.drive(150, 60)
r._latest_status = 0
cli.set_pose(300.0, 100.0, 90.0)
docks = []
orig_dock = r.dock
def always_jam():
    docks.append(1)
    threading.Timer(0.3, lambda: cli.set_pose(120.0, 0.0, -17.0)).start()
    return orig_dock()
r.dock = always_jam
res = r.return_to_charger()
check(f"two jams -> jammed after 2 attempts ({res}, {len(docks)})", res.reason == "jammed" and len(docks) == 2)

from cozmo_brain.tools.registry import charger_return_message
from cozmo_brain.robot.base import MoveResult
check("jammed has a model-facing message", "caught on the edge" in charger_return_message(MoveResult(moved=True, reason="jammed")))

print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
