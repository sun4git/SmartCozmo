"""Test: charger break (timed step-off), DockTimer, leave_charger, battery line."""
import dataclasses
import logging
import sys
import threading
import time

import _harness  # noqa: F401 - repo on sys.path, real .env ignored
from cozmo_brain.charger_break import ChargerBreak, DockTimer
from cozmo_brain.config import Settings
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.robot.base import MoveResult
from cozmo_brain.robot.simulated import SimulatedRobot
from cozmo_brain.tools.registry import leave_charger

logging.basicConfig(level=logging.WARNING)
failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


class R(SimulatedRobot):
    def __init__(self, docked=True, voltage=4.0, pose=True):
        super().__init__()
        self.docked, self.voltage, self.pose, self.picked = docked, voltage, pose, False
        self.blocked, self.low, self.drives, self.returns = False, False, [], 0

    def is_on_charger(self): return self.docked
    def is_movement_blocked(self): return self.docked and self.low
    def is_picked_up(self): return self.picked
    def get_battery_voltage(self): return self.voltage
    def has_charger_pose(self): return self.pose

    def drive(self, distance_mm, speed_mmps):
        self.drives.append((distance_mm, speed_mmps))
        if self.blocked:
            return MoveResult(moved=False)
        self.docked = False
        return MoveResult(moved=True)

    def return_to_charger(self):
        self.returns += 1
        self.docked = True
        return MoveResult(moved=True)


class E:
    last_interaction_monotonic = 0.0
    listening_window_open = False

    def __init__(self):
        self.turn_lock = threading.Lock()
        self.spoken, self.notes = [], []

    def speak(self, text, mood="neutral"):
        self.spoken.append(text)
        return True

    def add_note(self, text):
        self.notes.append(text)


def make(robot=None, **over):
    s = dataclasses.replace(
        Settings(), charger_break_enabled=True, charger_break_after_min=0.0, idle_fidget_after_s=0, **over
    )
    r, e = robot or R(), E()
    return r, e, s, ChargerBreak(r, e, s)


# --- DockTimer ---
r = R(docked=True)
t = DockTimer(r)
check("not docked-timed before first update", t.minutes() is None)
t.update()
check("docked: counting", t.minutes() is not None and t.minutes() < 0.1)
t._docked_since -= 600
check("docked 10 min", 9.9 < t.minutes() < 10.1)
r.docked = False
t.update()
check("leaving the dock for any reason resets it", t.minutes() is None)
r.docked, r.picked = True, True
t.update()
check("picked up counts as not docked", t.minutes() is None)

# --- enabled break fires: speaks, drives the configured exit, schedules a 5-10 min return ---
r, e, s, b = make()
b._check_once()
check("break: one spoken line", len(e.spoken) == 1)
check("break: drove the configured exit", r.drives == [(s.charger_exit_distance_mm, s.charger_exit_speed_mmps)])
delay_min = (b._return_at - time.monotonic()) / 60
check("break: return scheduled within 5-10 min", 4.9 <= delay_min <= 10.1)
check("break: note for the model", len(e.notes) == 1 and "stepped off" in e.notes[0])

# --- return after the stretch ---
b._return_at = time.monotonic() - 1
b._check_once()
check("return: spoke, drove back, docked", len(e.spoken) == 2 and r.returns == 1 and r.docked)
check("return: schedule cleared", b._return_at is None)

# --- not due yet / disabled ---
r, e, s, b = make()
b._settings = dataclasses.replace(s, charger_break_after_min=30.0)
b._check_once()
check("docked < threshold: nothing", e.spoken == [] and r.drives == [])
b._settings = dataclasses.replace(s, charger_break_enabled=False)
b.timer._docked_since = time.monotonic() - 99999
b._check_once()
check("disabled: nothing even when long docked", e.spoken == [] and r.drives == [])

# --- gates ---
r, e, s, b = make(R(voltage=3.6))
r.low = True  # docked at/below BATTERY_LOW_VOLTAGE - the normal movement block
b._check_once()
check("battery too low to leave: waits silently", e.spoken == [] and r.drives == [] and b.timer.minutes() is not None)
r.low, r.voltage = False, 3.75  # above the low line, however modest - no separate floor
b._check_once()
check("any voltage the normal rule allows: steps off", len(e.spoken) == 1 and len(r.drives) == 1)

r, e, s, b = make()
e.listening_window_open = True
b._check_once()
check("listening window open: nothing", e.spoken == [] and r.drives == [])

r, e, s, b = make()
b._settings = dataclasses.replace(s, idle_fidget_after_s=10**6)
e.last_interaction_monotonic = time.monotonic()
b._check_once()
check("recent conversation: nothing", e.spoken == [] and r.drives == [])

r, e, s, b = make()
e.turn_lock.acquire()
b._check_once()
check("turn lock held: nothing", e.spoken == [] and r.drives == [])

r, e, s, b = make()
r.picked = True
b._check_once()
check("picked up: nothing", e.spoken == [] and r.drives == [])

# --- drive refused anyway (reading changed in between): no stretch scheduled, honest note ---
r, e, s, b = make()
r.blocked = True
b._check_once()
check("blocked: no return scheduled", b._return_at is None and len(e.notes) == 1 and "too low" in e.notes[0])

# --- exits that are not the break get no timed return (peek / asked to come out) ---
r, e, s, b = make()
b.timer.update()
r.docked = False  # an idle peek drove him off
b._check_once()
check("peek exit: timer reset, no timed return", b.timer.minutes() is None and b._return_at is None and r.returns == 0)
b._check_once()
check("peek exit: stays out (no spoken return)", e.spoken == [] and r.returns == 0)

# --- return gates ---
r, e, s, b = make(R(docked=False, pose=False))
b._return_at = time.monotonic() - 1
b._check_once()
check("no charger pose: no return, schedule dropped", r.returns == 0 and b._return_at is None and e.spoken == [])

r, e, s, b = make(R(docked=False))
b._return_at = time.monotonic() - 1
e.listening_window_open = True
b._check_once()
check("return waits while listening", r.returns == 0 and b._return_at is not None)

r, e, s, b = make(R(docked=False))
b._return_at = time.monotonic() + 600
b._check_once()
check("return not due yet: nothing", r.returns == 0 and e.spoken == [])

# --- leave_charger helper (the tool) ---
r = R(docked=False)
res = leave_charger(r, Settings())
check("already off: says so, no drive", res.ok and "Already off" in res.message and r.drives == [])
r = R(docked=True)
res = leave_charger(r, Settings())
check("docked: drives off", res.ok and res.message == "Drove off the charger." and len(r.drives) == 1)
r = R(docked=True, voltage=3.5)
r.blocked = True
res = leave_charger(r, Settings())
check("low battery: honest refusal with voltage + attention flag",
      "3.50V" in res.message and res.extra.get("needs_attention") and res.extra.get("blocked_by_charger"))

# --- per-turn battery line ---
eng = CozmoEngine(Settings(), R(voltage=3.92), None, None, [], None)
check("battery line, no dock timer", eng._battery_note() == "[Battery: 3.92V.]")
eng.dock_minutes_fn = lambda: 12.4
check("battery line with docked minutes", eng._battery_note() == "[Battery: 3.92V, docked for 12 min.]")
eng.dock_minutes_fn = lambda: None
check("battery line, not docked", eng._battery_note() == "[Battery: 3.92V.]")
eng = CozmoEngine(Settings(), R(voltage=None), None, None, [], None)
check("no voltage: no line", eng._battery_note() is None)

# --- battery_log notes: who moved him is recorded ---
class Notes:
    def __init__(self): self.exits, self.returns = [], []
    def note_exit(self, cause): self.exits.append(cause)
    def note_return(self, reason): self.returns.append(reason)


notes = Notes()
r, e, s, _ = make()
b = ChargerBreak(r, e, s, battery_log=notes)
b._check_once()
b._return_at = time.monotonic() - 1
b._check_once()
check("break notes its exit and its return", notes.exits == ["break"] and notes.returns == ["break_over"])

from cozmo_brain.tools.registry import build_tools  # noqa: E402

notes = Notes()
r = R(docked=True)
tools = {t.name: t for t in build_tools(r, None, None, Settings(), battery_log=notes)}
tools["leave_charger"].handler({})
check("leave_charger tool notes 'asked'", notes.exits == ["asked"] and not r.docked)
tools["leave_charger"].handler({})
check("leave_charger when already off: no note", notes.exits == ["asked"])
tools["dock"].handler({})
check("dock tool notes 'dock_tool'", notes.returns == ["dock_tool"])

from cozmo_brain.charger_return import ChargerReturner  # noqa: E402

notes = Notes()
r = R(docked=False)
cr = ChargerReturner(r, E(), dataclasses.replace(Settings(), auto_return_to_charger_enabled=True), battery_log=notes)
cr.on_battery_reading(3.4)
cr.on_battery_reading(3.4)
check("critical auto-return notes 'critical'", notes.returns == ["critical"] and r.returns == 1)

print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
