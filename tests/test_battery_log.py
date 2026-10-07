"""Test: battery.jsonl events, off-dock readings, stretch records, causes/end
reasons, retention pruning, analysis."""
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import _harness  # noqa: F401 - repo on sys.path, real .env ignored
from cozmo_brain.battery_log import (
    BatteryLog,
    charge_sessions,
    minutes_to_low_fit,
    prune_battery_log,
    read_battery_log,
    stretch_readings,
    stretches,
)
from cozmo_brain.config import Settings

logging.basicConfig(level=logging.WARNING)
failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


class R:
    def __init__(self):
        self.docked, self.charging, self.picked = True, True, False

    def is_on_charger(self): return self.docked
    def is_charging(self): return self.charging
    def is_picked_up(self): return self.picked


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 3, 9, 0).timestamp()

    def __call__(self): return self.t

    def advance(self, minutes): self.t += minutes * 60


def last_event(path, event):
    return [x for x in read_battery_log(path) if x["event"] == event][-1]


def fresh(name):
    path = Path(_harness.tmp(name))
    if path.exists():
        path.unlink()
    return path


S = Settings()  # low 3.7, critical 3.5
path = fresh("battery_log.jsonl")
r, clock = R(), Clock()
log = BatteryLog(path, r, S, clock=clock)

# docked + charging at start, charging stops after 40 min
log.on_reading(3.62)
clock.advance(40); r.charging = False; log.on_reading(4.12)
clock.advance(5); log.on_reading(4.11)  # no change: no record
events = [x["event"] for x in read_battery_log(path)]
check("run_start then charging_stopped, steady readings write nothing", events == ["run_start", "charging_stopped"])
check("charging_stopped carries docked minutes", read_battery_log(path)[-1]["docked_min"] == 40.0)

# every reading (even one that writes no event) refreshes battery_now.json
now_file = path.with_name("battery_now.json")
now = json.loads(now_file.read_text(encoding="utf-8"))
check("battery_now.json holds the latest reading", now["v"] == 4.11 and now["docked"] is True and now["charging"] is False)
check("battery_now.json carries the check interval", now["interval_s"] == S.battery_check_interval_s)
check("no temp file left behind", not now_file.with_name("battery_now.json.tmp").exists())
check("default check interval is 15s", Settings().battery_check_interval_s == 15.0)

# break exit: note -> undock, cause recorded; off-dock readings tracked
log.note_exit("break")
r.docked = False
log.on_reading(4.02)
undock = last_event(path, "undocked")
check("undocked with the noted cause", undock["event"] == "undocked" and undock["cause"] == "break")
clock.advance(4); log.on_reading(3.80)
clock.advance(4); log.on_reading(3.70)   # first low at 8 min
clock.advance(2); log.on_reading(3.65)
log.note_return("break_over")
clock.advance(1); r.docked = True; log.on_reading(3.68)
st = stretches(read_battery_log(path))
check("one stretch written on re-docking", len(st) == 1)
readings = [x for x in read_battery_log(path) if x["event"] == "reading"]
check("one reading record per off-dock check, none while docked",
      [(x["v"], x["min"]) for x in readings] == [(4.02, 0.0), (3.80, 4.0), (3.70, 8.0), (3.65, 10.0)])
check("reading carries picked_up", all(x["picked_up"] is False for x in readings))
s0 = st[0]
check("stretch fields", s0["cause"] == "break" and s0["v_docked_last"] == 4.11 and s0["v_off_first"] == 4.02
      and s0["v_min"] == 3.65 and s0["first_low_min"] == 8.0 and s0["first_critical_min"] is None
      and s0["minutes"] == 11.0 and s0["end_reason"] == "break_over" and not s0["picked_up"])

# an exit nobody announced (idle peek / gesture) -> "moved"; return unannounced -> "other"
clock.advance(30); r.docked = False; log.on_reading(3.98)
clock.advance(5); r.docked = True; log.on_reading(3.80)
st = stretches(read_battery_log(path))
check("unannounced exit = moved, unannounced return = other", st[-1]["cause"] == "moved" and st[-1]["end_reason"] == "other")

# a stale exit note doesn't stick to a much later undock
log.note_exit("asked")
clock.advance(30); r.docked = False; log.on_reading(3.97)
check("stale exit note ignored", last_event(path, "undocked")["cause"] == "moved")

# picked up off the dock: cause picked_up; critical reading tracked; critical return reason
clock.advance(1); r.docked = True; log.on_reading(3.9)  # back
clock.advance(30); r.picked, r.docked = True, False; log.on_reading(3.95)
r.picked = False
clock.advance(3); log.on_reading(3.49)
log.note_return("critical")
clock.advance(2); r.docked = True; log.on_reading(3.6)
last = stretches(read_battery_log(path))[-1]
check("picked up: cause + flag, critical minute, critical end",
      last["cause"] == "picked_up" and last["picked_up"] and last["first_critical_min"] == 3.0
      and last["first_low_min"] == 3.0 and last["end_reason"] == "critical")

# run ends while off the dock -> stretch closed as run_end
clock.advance(30); r.docked = False; log.on_reading(3.9)
log.close()
check("open stretch closed at run end", stretches(read_battery_log(path))[-1]["end_reason"] == "run_end")

# a run that starts off the dock still records the stretch (cause unknown)
p2 = fresh("battery_log_offstart.jsonl")
r2, c2 = R(), Clock()
r2.docked = False
l2 = BatteryLog(p2, r2, S, clock=c2)
l2.on_reading(3.9)
c2.advance(5); r2.docked = True; l2.on_reading(3.8)
check("run started off the dock: stretch with cause unknown", stretches(read_battery_log(p2))[0]["cause"] == "unknown")

# --- analysis ---
recs = read_battery_log(path)
sessions = charge_sessions(recs)
check("charge sessions: first one partial, full after 40 min", sessions[0]["partial"] and sessions[0]["full_after_min"] == 40.0)
check("no fit with fewer than 3 low-reaching stretches", minutes_to_low_fit(recs) is None)
curves = stretch_readings(recs)
check("stretch_readings: one entry per stretch", len(curves) == len(stretches(recs)))
check("stretch_readings: readings matched to their stretch",
      [x["v"] for x in curves[0]["readings"]] == [4.02, 3.80, 3.70, 3.65]
      and [x["v"] for x in curves[1]["readings"]] == [3.98])
old_format = [{"event": "run_start", "ts": "2026-10-01T09:00:00"},
              {"event": "stretch", "ts": "2026-10-01T09:05:00", "left_ts": "2026-10-01T09:01:00"}]
check("stretch_readings: older stretch without readings gets an empty list",
      stretch_readings(old_format)[0]["readings"] == [])
orphan = [{"event": "reading", "ts": "2026-10-01T09:02:00", "v": 3.9},  # run crashed, no stretch written
          {"event": "run_start", "ts": "2026-10-01T10:00:00"},
          {"event": "reading", "ts": "2026-10-01T10:01:00", "v": 3.8},
          {"event": "stretch", "ts": "2026-10-01T10:02:00", "left_ts": "2026-10-01T10:01:00"}]
check("stretch_readings: a crashed run's readings don't leak into the next stretch",
      [x["v"] for x in stretch_readings(orphan)[0]["readings"]] == [3.8])

# --- retention ---
check("default retention is 90 days", Settings().battery_log_retention_days == 90)
pp = fresh("battery_prune.jsonl")
pp.write_text("".join(json.dumps(x) + "\n" for x in [
    {"ts": "2026-06-01T09:00:00", "event": "run_start", "v": 4.0},
    {"ts": "2026-07-09T08:59:59", "event": "docked", "v": 4.0},
    {"ts": "2026-07-09T09:00:00", "event": "undocked", "v": 4.0},
    {"ts": "2026-10-07T09:00:00", "event": "reading", "v": 3.9},
]) + "not json\n", encoding="utf-8")
before = pp.read_text(encoding="utf-8")
check("retention 0 keeps everything", prune_battery_log(pp, 0) == 0 and pp.read_text(encoding="utf-8") == before)
dropped = prune_battery_log(pp, 90, now=datetime(2026, 10, 7, 9, 0))
kept = pp.read_text(encoding="utf-8").splitlines()
check("records older than 90 days dropped, cutoff itself kept", dropped == 2 and len(kept) == 3
      and json.loads(kept[0])["ts"] == "2026-07-09T09:00:00")
check("unreadable lines kept", kept[-1] == "not json")
check("no temp file left after prune", not pp.with_name(pp.name + ".tmp").exists())
check("prune again drops nothing", prune_battery_log(pp, 90, now=datetime(2026, 10, 7, 9, 0)) == 0)
check("missing file: nothing to prune", prune_battery_log(fresh("battery_prune_missing.jsonl"), 90) == 0)

synthetic = [
    {"event": "stretch", "v_off_first": 3.90, "first_low_min": 7.0, "picked_up": False},
    {"event": "stretch", "v_off_first": 4.00, "first_low_min": 9.0, "picked_up": False},
    {"event": "stretch", "v_off_first": 4.10, "first_low_min": 11.0, "picked_up": False},
    {"event": "stretch", "v_off_first": 4.20, "first_low_min": 99.0, "picked_up": True},   # excluded
    {"event": "stretch", "v_off_first": 4.20, "first_low_min": None, "picked_up": False},  # excluded
]
fit = minutes_to_low_fit(synthetic)
check("fit uses only clean low-reaching stretches", fit is not None and fit["points"] == 3)
check("fit slope/intercept", abs(fit["slope_min_per_v"] - 20.0) < 1e-6 and abs(fit["intercept_min"] + 71.0) < 1e-6)
check("fit refuses identical voltages", minutes_to_low_fit([dict(x, v_off_first=4.0) for x in synthetic[:3]]) is None)

# unwritable path doesn't raise
bad = BatteryLog(Path(os.devnull) / "nope" / "battery.jsonl", R(), S, clock=Clock())
bad.on_reading(4.0)
check("write failure is swallowed", True)

print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
