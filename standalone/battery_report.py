#!/usr/bin/env python3
"""
Summarise data/battery.jsonl (written by cozmo_brain/battery_log.py): how this
robot's battery actually behaves on and off the charger. It's evidence for
tuning the battery settings by hand - nothing in the app uses these numbers.

Shows:
  - charge sessions: voltage on docking, minutes until IS_CHARGING went off
    ("full", by the robot's own flag), voltage then, total time docked
  - stretches off the dock: how he left, voltage on the dock just before and
    first reading off it, minutes off, lowest voltage, minutes until the first
    low/critical reading, how it ended
  - a straight-line fit of "minutes until low" vs voltage on leaving, once at
    least 3 stretches actually reached low (otherwise it says so)
  - the voltage at every check during the last few stretches (minutes off
    the dock : volts), once there are any with per-reading data

Usage (from the repo root):
  python3 standalone/battery_report.py              # data/battery.jsonl
  python3 standalone/battery_report.py path/to/battery.jsonl
  python3 standalone/battery_report.py --demo       # made-up data, to see the format
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cozmo_brain.battery_log import (  # noqa: E402
    charge_sessions,
    minutes_to_low_fit,
    read_battery_log,
    stretch_readings,
    stretches,
)
from cozmo_brain.config import settings  # noqa: E402


def _fmt(value, spec: str = "", none: str = "-") -> str:
    return none if value is None else format(value, spec)


def _time(ts: str | None) -> str:
    return (ts or "?").replace("T", " ")[:16]


def report(records: list[dict], last_curves: int = 3) -> str:
    out: list[str] = []
    if not records:
        return "No battery records yet - run the app with Cozmo for a while (on and off the charger)."

    first, last = records[0].get("ts", "?"), records[-1].get("ts", "?")
    out.append(f"{len(records)} records, {_time(first)} .. {_time(last)}")
    out.append(f"Thresholds now: low {settings.battery_low_voltage:.2f}V, critical {settings.battery_critical_voltage:.2f}V")

    sessions = charge_sessions(records)
    out.append("")
    out.append(f"Charge sessions ({len(sessions)}):")
    out.append(f"  {'docked at':16}  {'V in':>5}  {'full after':>10}  {'V full':>6}  {'docked':>7}")
    for s in sessions:
        partial = " (run started docked)" if s.get("partial") else ""
        out.append(
            f"  {_time(s['docked_ts']):16}  {_fmt(s['v_docked'], '.2f'):>5}  "
            f"{_fmt(s['full_after_min'], '.0f') + ' min' if s['full_after_min'] is not None else 'never':>10}  "
            f"{_fmt(s['v_full'], '.2f'):>6}  {_fmt(s['docked_min'], '.0f') + ' min' if s['docked_min'] is not None else 'open':>7}"
            f"{partial}"
        )
    full_times = [s["full_after_min"] for s in sessions if s["full_after_min"] is not None and not s.get("partial")]
    if full_times:
        out.append(f"  IS_CHARGING went off after {min(full_times):.0f}-{max(full_times):.0f} min docked "
                   f"({len(full_times)} session(s))")

    rows = stretches(records)
    out.append("")
    out.append(f"Stretches off the dock ({len(rows)}):")
    out.append(f"  {'left at':16}  {'cause':9}  {'V dock':>6}  {'V off':>5}  {'mins':>5}  {'V min':>5}  "
               f"{'low at':>6}  {'crit at':>7}  ended")
    for s in rows:
        flag = " (picked up)" if s.get("picked_up") else ""
        out.append(
            f"  {_time(s.get('left_ts')):16}  {s.get('cause', '?'):9}  {_fmt(s.get('v_docked_last'), '.2f'):>6}  "
            f"{_fmt(s.get('v_off_first'), '.2f'):>5}  {_fmt(s.get('minutes'), '.1f'):>5}  {_fmt(s.get('v_min'), '.2f'):>5}  "
            f"{_fmt(s.get('first_low_min'), '.1f'):>6}  {_fmt(s.get('first_critical_min'), '.1f'):>7}  "
            f"{s.get('end_reason', '?')}{flag}"
        )
    never_low = [s for s in rows if s.get("first_low_min") is None and not s.get("picked_up")]
    if never_low:
        longest = max(never_low, key=lambda s: s.get("minutes") or 0)
        out.append(f"  {len(never_low)} stretch(es) never reached low - longest {_fmt(longest.get('minutes'), '.1f')} min "
                   f"(left at {_fmt(longest.get('v_off_first'), '.2f')}V). These only show 'at least this long'.")

    out.append("")
    fit = minutes_to_low_fit(records)
    if fit is None:
        reached = sum(1 for s in rows if s.get("first_low_min") is not None and not s.get("picked_up"))
        out.append(f"Minutes-to-low fit: not enough data ({reached} stretch(es) reached low; need 3, "
                   "with different voltages on leaving).")
    else:
        lo, hi = fit["v_range"]
        out.append(f"Minutes-to-low fit over {fit['points']} stretches (voltage off the dock {lo:.2f}-{hi:.2f}V):")
        out.append(f"  minutes ~= {fit['slope_min_per_v']:.1f} x V {fit['intercept_min']:+.1f}"
                   f"   (R^2 {_fmt(fit['r2'], '.2f')}; only meaningful inside that voltage range)")
        for v in (lo, (lo + hi) / 2, hi):
            out.append(f"  leaving at {v:.2f}V -> about {fit['slope_min_per_v'] * v + fit['intercept_min']:.0f} min before low")

    curves = [c for c in stretch_readings(records) if c["readings"]][-last_curves:]
    if curves:
        out.append("")
        out.append(f"Readings off the dock, last {len(curves)} stretch(es) (minutes off : volts):")
        for c in curves:
            s = c["stretch"]
            out.append(f"  left {_time(s.get('left_ts'))} ({s.get('cause', '?')}, ended {s.get('end_reason', '?')}, "
                       f"{_fmt(s.get('minutes'), '.1f')} min)")
            cells = [f"{_fmt(r.get('min'), '4.1f')}:{_fmt(r.get('v'), '.2f')}" + ("*" if r.get("picked_up") else "")
                     for r in c["readings"]]
            for i in range(0, len(cells), 8):
                out.append("    " + "  ".join(cells[i:i + 8]))
        if any(r.get("picked_up") for c in curves for r in c["readings"]):
            out.append("  * = picked up at that reading")
    return "\n".join(out)


def _demo_records() -> list[dict]:
    """ILLUSTRATIVE ONLY - made-up numbers to show the report format."""
    return [
        {"ts": "2026-10-03T09:00:00", "event": "run_start", "v": 3.62, "docked": True, "charging": True},
        {"ts": "2026-10-03T09:41:00", "event": "charging_stopped", "v": 4.12, "docked_min": 41.0},
        {"ts": "2026-10-03T09:45:00", "event": "undocked", "v": 4.02, "cause": "moved", "docked_min": 45.0},
        {"ts": "2026-10-03T09:56:00", "event": "docked", "v": 3.69},
        {"ts": "2026-10-03T09:56:00", "event": "stretch", "left_ts": "2026-10-03T09:45:00", "cause": "moved",
         "v_docked_last": 4.11, "v_off_first": 4.02, "v_min": 3.68, "v_end": 3.69, "first_low_min": 10.5,
         "first_critical_min": None, "picked_up": False, "minutes": 11.0, "end_reason": "dock_tool"},
        {"ts": "2026-10-03T10:26:00", "event": "undocked", "v": 3.95, "cause": "break", "docked_min": 30.0},
        {"ts": "2026-10-03T10:33:00", "event": "docked", "v": 3.78},
        {"ts": "2026-10-03T10:33:00", "event": "stretch", "left_ts": "2026-10-03T10:26:00", "cause": "break",
         "v_docked_last": 4.05, "v_off_first": 3.95, "v_min": 3.78, "v_end": 3.78, "first_low_min": None,
         "first_critical_min": None, "picked_up": False, "minutes": 7.0, "end_reason": "break_over"},
        {"ts": "2026-10-03T11:03:00", "event": "undocked", "v": 3.90, "cause": "asked", "docked_min": 30.0},
        *({"ts": f"2026-10-03T11:{3 + m:02d}:00", "event": "reading", "v": v, "min": float(m), "picked_up": False}
          for m, v in enumerate((3.90, 3.86, 3.83, 3.80, 3.77, 3.74, 3.71, 3.65, 3.57, 3.50))),
        {"ts": "2026-10-03T11:12:00", "event": "docked", "v": 3.55},
        {"ts": "2026-10-03T11:12:00", "event": "stretch", "left_ts": "2026-10-03T11:03:00", "cause": "asked",
         "v_docked_last": 4.0, "v_off_first": 3.90, "v_min": 3.5, "v_end": 3.55, "first_low_min": 7.5,
         "first_critical_min": 9.0, "picked_up": False, "minutes": 9.0, "end_reason": "critical"},
        {"ts": "2026-10-03T11:50:00", "event": "undocked", "v": 3.98, "cause": "moved", "docked_min": 38.0},
        {"ts": "2026-10-03T12:00:00", "event": "docked", "v": 3.66},
        {"ts": "2026-10-03T12:00:00", "event": "stretch", "left_ts": "2026-10-03T11:50:00", "cause": "moved",
         "v_docked_last": 4.08, "v_off_first": 3.98, "v_min": 3.66, "v_end": 3.66, "first_low_min": 9.5,
         "first_critical_min": None, "picked_up": False, "minutes": 10.0, "end_reason": "other"},
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", help="battery.jsonl (default: DATA_DIR/battery.jsonl)")
    parser.add_argument("--demo", action="store_true", help="show the format on made-up data")
    parser.add_argument("--curves", type=int, default=3, metavar="N",
                        help="list the readings of the last N stretches (default 3)")
    args = parser.parse_args(argv)

    if args.demo:
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write("".join(json.dumps(r) + "\n" for r in _demo_records()))
        print("=== DEMO - made-up numbers, not from your robot ===\n")
        print(report(read_battery_log(Path(f.name)), args.curves))
        Path(f.name).unlink(missing_ok=True)
        return 0

    path = Path(args.path) if args.path else Path(settings.data_dir) / "battery.jsonl"
    print(f"{path}\n")
    print(report(read_battery_log(path), args.curves))
    return 0


if __name__ == "__main__":
    sys.exit(main())
