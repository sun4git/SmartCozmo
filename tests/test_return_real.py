"""Test: PyCozmoRobot's charger-pose + return_to_pose/return_to_charger
logic against a fake pycozmo client (tests/fake_pycozmo.py)."""
import logging
import sys
import threading
import time

import _harness  # noqa: F401 - repo on sys.path, real .env ignored
from fake_pycozmo import F, FakeCli, Pkt, Pose2D, make_robot

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


# 1. Leaving the charger records the docked pose; staging = +dock distance along heading.
r, cli = make_robot()
cli.set_pose(100.0, 50.0, 90.0)
r._latest_status = F.IS_ON_CHARGER
r.drive(150, 60)
cp = r._charger_pose; print(cp); check("charger pose recorded on exit", cp and abs(cp.x_mm-100)<1e-6 and abs(cp.y_mm-50)<1e-6 and abs(cp.heading_deg-90)<1e-6)
r._latest_status = 0
st = r._charger_staging_pose()
check("staging pose uses CHARGER_DOCK_DISTANCE_MM along heading",
      st is not None and abs(st.x_mm - 100.0) < 1e-6 and abs(st.y_mm - 200.0) < 1e-6 and abs(st.heading_deg - 90.0) < 1e-6)

r2, cli2 = make_robot(charger_dock_distance_mm=80.0)
cli2.set_pose(0.0, 0.0, 0.0)
r2._latest_status = F.IS_ON_CHARGER
r2.drive(150, 60)
r2._latest_status = 0
st2 = r2._charger_staging_pose()
check("staging distance follows the setting (80mm), not hardcoded 150", st2 and abs(st2.x_mm - 80.0) < 1e-6)

# 2. Pickup packet forgets it.
r._on_robot_state(None, Pkt(F.IS_PICKED_UP))
check("pickup forgets charger pose", not r.has_charger_pose())

# 3. Origin change forgets it.
r, cli = make_robot()
r._latest_status = F.IS_ON_CHARGER
r.drive(150, 60)
r._latest_status = 0
cli.set_pose(10, 10, 0, origin=2)
check("origin change forgets charger pose", not r.has_charger_pose())

# 4. disconnect()/reconnect path forgets it (even with the same origin_id).
r, cli = make_robot()
r._latest_status = F.IS_ON_CHARGER
r.drive(150, 60)
r._latest_status = 0
cli.disconnect = lambda: None
cli.stop = lambda: None
r.disconnect()
check("disconnect forgets charger pose", r._charger_pose is None and r._cli is None)

# 5. return_to_pose: go_to_pose skips the point turn by 25deg -> corrective turn fixes it.
r, cli = make_robot()
cli.set_pose(300.0, 200.0, 45.0)
res = r.return_to_pose(Pose2D(0.0, 0.0, 10.0))
final = r.get_pose()
check(f"return_to_pose completed ({res})", res.completed)
check(f"heading corrected to ~10deg (got {final.heading_deg:.2f})", abs(final.heading_deg - 10.0) < 0.5)

# 6. go_to_pose hang -> timeout abort, motors stopped, ClearPath sent.
r, cli = make_robot()
cli.go_to_pose_behavior = "hang"
cli.set_pose(300.0, 0.0, 0.0)  # away from the target, so it really navigates
t0 = time.monotonic()
res = r.return_to_pose(Pose2D(0.0, 0.0, 0.0))
check(f"hang -> timeout ({res}, {time.monotonic()-t0:.1f}s)", res.reason == "timeout")
check("timeout stops motors + sends ClearPath", ("stop",) in cli.calls and "ClearPath" in cli.conn.sent)

# 7. Pickup mid-navigation -> abort with picked_up.
r, cli = make_robot()
cli.go_to_pose_behavior = "hang"
cli.set_pose(300.0, 0.0, 0.0)
threading.Timer(0.3, lambda: setattr(r, "_latest_status", F.IS_PICKED_UP)).start()
res = r.return_to_pose(Pose2D(0.0, 0.0, 0.0))
check(f"pickup mid-nav -> picked_up ({res})", res.reason == "picked_up")

# 8. Full return_to_charger: navigates to staging, then dock() reverses; is_on_charger check after.
r, cli = make_robot()
cli.set_pose(0.0, 0.0, 180.0)
r._latest_status = F.IS_ON_CHARGER
r.drive(150, 60)          # leave charger (records (0,0,180))
r._latest_status = 0
cli.set_pose(-400.0, 300.0, 30.0)  # wandered off
cli.calls.clear()

orig_drive = r.drive
def drive_and_dock(distance, speed, suppress_cliff=False, **kw):
    res = orig_drive(distance, speed, suppress_cliff=suppress_cliff, **kw)
    if suppress_cliff and distance < 0:
        r._latest_status = F.IS_ON_CHARGER  # contacts read docked after reversing
    return res
r.drive = drive_and_dock
res = r.return_to_charger()
gtp = [c for c in cli.calls if c[0] == "go_to_pose"]
check(f"go_to_pose aimed at staging (-150,0), facing the travel direction -50.2deg, not the final 180 (got {gtp})", gtp and gtp[0][1:] == (-150.0, 0.0, -50.2))
check(f"return_to_charger completed ({res})", res.completed)

# 9. Same, but contacts never read docked -> not_on_charger reported honestly.
r, cli = make_robot(charger_contact_wait_s=1.0)  # no need to wait out the full 10s default
r._latest_status = F.IS_ON_CHARGER
r.drive(150, 60)
r._latest_status = 0
cli.set_pose(-400.0, 300.0, 30.0)
res = r.return_to_charger()
check(f"misaligned dock -> not_on_charger ({res})", res.reason == "not_on_charger")

# 10. No pose known.
r, cli = make_robot()
check("no pose -> no_charger_pose", r.return_to_charger().reason == "no_charger_pose")

print("\nFAILURES:", failures or "none")
sys.exit(1 if failures else 0)
