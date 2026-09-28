"""Test: every rotation of a return is our own shortest-direction turn; tracker; random spin."""
import sys, contextlib, io, logging, math
import _harness  # noqa: F401 - repo on sys.path, real .env ignored

with contextlib.redirect_stdout(io.StringIO()):
    from fake_pycozmo import make_robot
from cozmo_brain.robot.base import Pose2D
from cozmo_brain.robot.real import _RotationTracker, _shortest_turn
records = []
logging.getLogger("cozmo_brain.robot.real").addHandler(type("H", (logging.Handler,), {"emit": lambda s, r: records.append(r.getMessage())})())
logging.getLogger("cozmo_brain.robot.real").setLevel(logging.INFO)
failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: failures.append(name)

def turns_commanded(r):
    out = []
    orig = r.turn
    def spy(a):
        out.append(a); return orig(a)
    r.turn = spy
    return out

# Case where the long way would be counterclockwise: facing 10deg, target down-right (bearing -45), final -80
r, cli = make_robot()
cli.set_pose(0.0, 0.0, 10.0)
turns = turns_commanded(r)
res = r.return_to_pose(Pose2D(100.0, -100.0, -80.0))
gtp = [c for c in cli.calls if c[0] == "go_to_pose"]
final = r.get_pose()
check(f"face turn is the short clockwise one (-55), not +305 (turns={['%.1f' % t for t in turns]})", abs(turns[0] - (-55.0)) < 0.5)
check(f"go_to_pose asked to face the travel direction (-45), so its own point turn is ~0 (got {gtp})", gtp and gtp[0][3] == -45.0)
check("every turn we command is the short way (|angle| <= 180)", all(abs(t) <= 180 for t in turns))
check(f"still ends at the requested final heading (-80, got {final.heading_deg:.1f})", abs(final.heading_deg + 80) < 0.5 and res.completed)
check("firmware rotation logged", any("go_to_pose() firmware rotation:" in m for m in records))

# Target straight behind: shortest face turn is 180 either way - must not exceed 180
r, cli = make_robot(); cli.set_pose(0.0, 0.0, 0.0)
turns = turns_commanded(r)
r.return_to_pose(Pose2D(-200.0, 0.0, 0.0))
check(f"target directly behind: face turn is exactly 180, not more ({turns[0]:.1f})", abs(abs(turns[0]) - 180) < 0.01)

# Already there: no navigation, heading only
r, cli = make_robot(); cli.set_pose(3.0, 2.0, 30.0)
turns = turns_commanded(r)
cli.calls.clear()
r.return_to_pose(Pose2D(0.0, 0.0, 0.0))
check(f"within 10mm: no go_to_pose, just the heading correction ({turns})", not [c for c in cli.calls if c[0] == "go_to_pose"] and len(turns) == 1 and abs(turns[0] + 30) < 0.5)

# Tracker unwraps across the +/-180 seam
t = _RotationTracker(0.0)
for h in (90, 170, -170, -90):
    t.update(h)
check(f"tracker: 0 -> 90 -> 170 -> -170 -> -90 is +270 ccw, not -90 ({t.describe()})", abs(t.net - 270) < 0.01)
t = _RotationTracker(10.0); t.update(-20.0)
check(f"tracker: small clockwise turn ({t.describe()})", abs(t.net + 30) < 0.01 and "clockwise" in t.describe())

# spin: random direction; other gestures unchanged
from cozmo_brain.robot.simulated import SimulatedRobot
class S(SimulatedRobot):
    def __init__(self): super().__init__(); self.turns = []
    def turn(self, a): self.turns.append(a)
    def show_expression(self, n, duration=None): pass
s = S()
for _ in range(40):
    s.run_gesture("spin")
check(f"spin goes both ways over 40 runs (left={s.turns.count(360)}, right={s.turns.count(-360)})", 360 in s.turns and -360 in s.turns and set(s.turns) <= {360, -360})
s.turns.clear()
for _ in range(20):
    s.run_gesture("peek")
check(f"peek unchanged: always +15 then -15 ({set(s.turns)})", set(s.turns) == {15, -15} and s.turns[:2] == [15, -15])
print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
