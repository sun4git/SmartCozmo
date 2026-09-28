"""Test: idle fidget skips wheel steps only while docked + charging."""
import dataclasses
import logging
import sys

import _harness  # noqa: F401 - repo on sys.path, real .env ignored
from cozmo_brain.config import Settings
from cozmo_brain.idle_fidget import IdleFidgeter
from cozmo_brain.robot.simulated import SimulatedRobot

logging.basicConfig(level=logging.WARNING)
failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


class R(SimulatedRobot):
    def __init__(self, on, charging):
        super().__init__()
        self.on, self.ch, self.wheel_calls, self.head_calls = on, charging, 0, 0
    def is_on_charger(self): return self.on
    def is_charging(self): return self.ch
    def turn(self, a):
        self.wheel_calls += 1
        return super().turn(a)
    def set_head_angle_deg(self, a, duration=0.4): self.head_calls += 1


class E:
    last_interaction_monotonic = 0.0
    turn_lock = __import__('threading').Lock()
    listening_window_open = False


S = dataclasses.replace(Settings(), idle_fidget_after_s=0)
import cozmo_brain.idle_fidget as m
m.random.choice = lambda seq: "peek"  # the gesture with turn steps

for on, ch, expect_wheels in [(True, True, False), (True, False, True), (False, False, True), (False, True, True)]:
    r = R(on, ch)
    IdleFidgeter(r, E(), S)._check_once()
    check(f"on_charger={on} charging={ch}: wheels={'yes' if expect_wheels else 'no'}, head still moves",
          (r.wheel_calls > 0) == expect_wheels and r.head_calls == 2)
    print("   wheel_calls", r.wheel_calls, "head_calls", r.head_calls)

r = R(True, True)
r.run_gesture("peek")
check("conversation/gesture path (wheels default) still turns while charging on dock", r.wheel_calls == 2)
print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
