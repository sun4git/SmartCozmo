"""Test: light off = idle. Every mood has a color; idle-time activity
(pickup, fidget, unprompted speech) puts the light back as it found it."""
import sys

import _harness  # noqa: F401 - repo on sys.path, real .env ignored

from fake_pycozmo import F, make_robot
from cozmo_brain.robot.moods import MOODS
from cozmo_brain.robot.pickup_reactor import PickupReactor
from cozmo_brain.modes import vad_mode

failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


check("every mood has a color (off is idle only)", all(m.light != "off" for m in MOODS.values()))
check("neutral is green", MOODS["neutral"].light == "green")

# keep_backpack_light: idle stays off after something lit it
r, cli = make_robot()
r.set_backpack_light("off")
with r.keep_backpack_light():
    r.apply_mood("confused")
    check("inside: mood color shows", r.current_backpack_light() == "green")
check("after: back to off", r.current_backpack_light() == "off")

# keep_backpack_light restores even if the body raises
try:
    with r.keep_backpack_light():
        r.set_backpack_light("red")
        raise RuntimeError("boom")
except RuntimeError:
    pass
check("restores after an exception", r.current_backpack_light() == "off")

# pickup while idle -> surprised (white) -> set down -> off again, not neutral green
r, cli = make_robot()
r.set_backpack_light("off")
pr = PickupReactor(r)
r._latest_status = F.IS_PICKED_UP
pr._check_once()
check("picked up: surprised white", r.current_backpack_light() == "white")
r._latest_status = 0
pr._check_once()
check("set down while idle: light back off", r.current_backpack_light() == "off")

# pickup mid-conversation (blue) -> back to blue
r.set_backpack_light("blue")
r._latest_status = F.IS_PICKED_UP; pr._check_once()
r._latest_status = 0; pr._check_once()
check("set down mid-conversation: previous color back", r.current_backpack_light() == "blue")

# vad idle: off after the window
r, cli = make_robot()
r.set_backpack_light("green")
vad_mode._go_idle(r)
check("vad idle: light off", r.current_backpack_light() == "off")

print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
