"""Test: the backpack blinks (in its current color, white when off) while
the mic records, and colors set meanwhile keep blinking until it stops."""
import sys

import _harness  # noqa: F401 - repo on sys.path, real .env ignored
import pycozmo

from fake_pycozmo import make_robot

failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


def last_light(cli):
    return [c for c in cli.calls if c[0] == "lights"][-1]


RED = pycozmo.lights.red.to_int16()
WHITE = pycozmo.lights.white.to_int16()
OFF = pycozmo.lights.off.to_int16()

r, cli = make_robot()
r.set_backpack_light("red")
check("solid red: on == off color, no blink", last_light(cli) == ("lights", RED, RED, 0, 0))

r.set_listening_indicator(True)
_, on, off, on_f, off_f = last_light(cli)
check(f"listening: blinks the current color (red on, off between, frames {on_f}/{off_f})",
      on == RED and off == OFF and on_f > 0 and off_f > 0)

r.set_backpack_light("off")
_, on, off, on_f, _ = last_light(cli)
check("color turned off while listening: blinks white, still visible", on == WHITE and off == OFF and on_f > 0)

r.set_backpack_light("red")  # e.g. battery warning mid-recording
_, on, _, on_f, _ = last_light(cli)
check("battery red mid-recording still blinks", on == RED and on_f > 0)

n = len([c for c in cli.calls if c[0] == "lights"])
r.set_listening_indicator(True)
check("repeat start: no extra packet", len([c for c in cli.calls if c[0] == "lights"]) == n)

r.set_listening_indicator(False)
check("recording stopped: back to solid current color", last_light(cli) == ("lights", RED, RED, 0, 0))

try:
    r.set_backpack_light("purple")
    check("unknown color rejected", False)
except ValueError:
    check("unknown color rejected", True)

print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
