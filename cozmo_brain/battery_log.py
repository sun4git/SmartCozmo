"""Battery history: data/battery.jsonl, one JSON object per line, written from
BatteryMonitor's readings (every BATTERY_CHECK_INTERVAL_S). Events only -
not every reading - so it stays small:

  run_start         first reading of a run: v, docked, charging
  docked/undocked   the charger contacts changed (undocked also has `cause`)
  charging_started  IS_CHARGING turned on/off while docked, with minutes
  charging_stopped  docked so far - the on-charger charge curve
  stretch           one record per time off the dock, written when he's back
                    on it (or the run ends): how he left (cause), voltage on
                    the dock just before / first reading off it, minutes off,
                    lowest voltage, minutes until the first low / critical
                    reading, and how it ended (end_reason)

Causes: "break" (charger_break.py), "asked" (leave_charger tool), "picked_up",
"moved" (anything else - an idle peek, a gesture or drive in conversation).
End reasons: "break_over", "dock_tool" (asked to dock, incl. agreeing to the
low-battery offer), "critical" (autonomous critical return), "run_end",
"other" (put back by hand, or anything unannounced).

This is the raw data for a later "can I come out, and for how long?" call
based on this robot's own battery; standalone/battery_report.py summarises it.
Nothing personal is stored. Readings only arrive every 30s, so every time is
to that resolution. Kept until deleted for now - a retention setting of its
own comes with the "can I come out?" feature (README roadmap item 8).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from cozmo_brain.config import Settings
from cozmo_brain.robot.base import RobotBackend

logger = logging.getLogger(__name__)

# How long a note_exit()/note_return() stays attached to the next dock change.
# A return drive can take a while, so the return note lives longer.
_EXIT_NOTE_S = 120.0
_RETURN_NOTE_S = 600.0


class BatteryLog:
    def __init__(
        self,
        path: Path,
        robot: RobotBackend,
        settings: Settings,
        clock: Callable[[], float] = time.time,
    ):
        self._path = Path(path)
        self._robot = robot
        self._settings = settings
        self._clock = clock
        self._lock = threading.Lock()
        self._docked: bool | None = None  # None until the first reading
        self._charging = False
        self._docked_at: float | None = None
        self._last_docked_v: float | None = None
        self._stretch: dict | None = None
        self._exit_note: tuple[str, float] | None = None
        self._return_note: tuple[str, float] | None = None

    # --- notes from whoever is about to move him (other threads) ---

    def note_exit(self, cause: str) -> None:
        with self._lock:
            self._exit_note = (cause, self._clock())

    def note_return(self, reason: str) -> None:
        with self._lock:
            self._return_note = (reason, self._clock())

    # --- BatteryMonitor's on_reading hook ---

    def on_reading(self, voltage: float) -> None:
        try:
            docked = bool(self._robot.is_on_charger())
            charging = bool(self._robot.is_charging())
            picked_up = bool(self._robot.is_picked_up())
        except Exception as e:  # noqa: BLE001 - a status read must never break the monitor
            logger.debug("Battery log status read failed: %s", e)
            return
        now = self._clock()
        with self._lock:
            if self._docked is None:
                self._write("run_start", now, v=voltage, docked=docked, charging=charging)
                if docked:
                    self._docked_at = now
                else:
                    self._start_stretch(now, voltage, "unknown", None)
            elif docked and not self._docked:
                self._on_dock(now, voltage)
            elif not docked and self._docked:
                self._on_undock(now, voltage, picked_up)
            elif docked and charging != self._charging:
                self._write(
                    "charging_started" if charging else "charging_stopped",
                    now,
                    v=voltage,
                    docked_min=self._minutes_since(self._docked_at, now),
                )
                logger.info("Battery: charging %s at %.2fV.", "started" if charging else "stopped", voltage)

            if docked:
                self._last_docked_v = voltage
            elif self._stretch is not None:
                self._track_stretch(now, voltage, picked_up)
            self._docked, self._charging = docked, charging

    def close(self) -> None:
        """End of run: write any stretch still open."""
        with self._lock:
            if self._stretch is not None:
                self._end_stretch(self._clock(), "run_end")

    # --- internals (caller holds self._lock) ---

    def _on_dock(self, now: float, voltage: float) -> None:
        reason = self._take_note("_return_note", now, _RETURN_NOTE_S) or "other"
        self._write("docked", now, v=voltage)
        if self._stretch is not None:
            self._end_stretch(now, reason)
        self._docked_at = now

    def _on_undock(self, now: float, voltage: float, picked_up: bool) -> None:
        cause = "picked_up" if picked_up else (self._take_note("_exit_note", now, _EXIT_NOTE_S) or "moved")
        docked_min = self._minutes_since(self._docked_at, now)
        self._write("undocked", now, v=voltage, cause=cause, docked_min=docked_min)
        logger.info("Battery: left the dock (%s) at %.2fV after %s min docked.", cause, voltage, docked_min)
        self._start_stretch(now, voltage, cause, self._last_docked_v)
        self._docked_at = None

    def _start_stretch(self, now: float, voltage: float, cause: str, v_docked_last: float | None) -> None:
        self._stretch = {
            "left_ts": _iso(now),
            "_left": now,
            "cause": cause,
            "v_docked_last": v_docked_last,
            "v_off_first": voltage,
            "v_min": voltage,
            "v_end": voltage,
            "first_low_min": None,
            "first_critical_min": None,
            "picked_up": False,
        }

    def _track_stretch(self, now: float, voltage: float, picked_up: bool) -> None:
        st = self._stretch
        st["v_end"] = voltage
        st["v_min"] = min(st["v_min"], voltage)
        st["picked_up"] = st["picked_up"] or picked_up
        minutes = round((now - st["_left"]) / 60.0, 1)
        if st["first_low_min"] is None and voltage <= self._settings.battery_low_voltage:
            st["first_low_min"] = minutes
        if st["first_critical_min"] is None and voltage <= self._settings.battery_critical_voltage:
            st["first_critical_min"] = minutes

    def _end_stretch(self, now: float, reason: str) -> None:
        st = self._stretch
        self._stretch = None
        fields = {k: v for k, v in st.items() if not k.startswith("_")}
        fields["minutes"] = round((now - st["_left"]) / 60.0, 1)
        fields["end_reason"] = reason
        self._write("stretch", now, **fields)
        logger.info(
            "Battery: stretch over (%s, left via %s) - %.1f min off the dock, %.2fV -> %.2fV, first low at %s min.",
            reason, st["cause"], fields["minutes"], st["v_off_first"], st["v_end"], st["first_low_min"],
        )

    def _take_note(self, attr: str, now: float, max_age_s: float) -> str | None:
        note = getattr(self, attr)
        setattr(self, attr, None)
        if note is None or now - note[1] > max_age_s:
            return None
        return note[0]

    @staticmethod
    def _minutes_since(start: float | None, now: float) -> float | None:
        return None if start is None else round((now - start) / 60.0, 1)

    def _write(self, event: str, now: float, **fields) -> None:
        record = {"ts": _iso(now), "event": event, **fields}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except OSError as e:
            logger.warning("Could not write %s: %s", self._path, e)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def read_battery_log(path: Path) -> list[dict]:
    """Every record in battery.jsonl, skipping unreadable lines."""
    path = Path(path)
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


# --- analysis (standalone/battery_report.py now; the "can I come out?" call later) ---


def stretches(records: list[dict]) -> list[dict]:
    """Every finished time off the dock, oldest first."""
    return [r for r in records if r.get("event") == "stretch"]


def charge_sessions(records: list[dict]) -> list[dict]:
    """One entry per time on the dock: when it started, the voltage on
    arriving, minutes until IS_CHARGING went off (None if it never did), the
    voltage then, and how long he stayed docked in total (None if the run
    ended while docked)."""
    sessions: list[dict] = []
    current: dict | None = None
    for r in records:
        event = r.get("event")
        if event == "docked" or (event == "run_start" and r.get("docked")):
            current = {"docked_ts": r.get("ts"), "v_docked": r.get("v"), "full_after_min": None,
                       "v_full": None, "docked_min": None, "partial": event == "run_start"}
            sessions.append(current)
        elif event == "charging_stopped" and current is not None and current["full_after_min"] is None:
            current["full_after_min"] = r.get("docked_min")
            current["v_full"] = r.get("v")
        elif event == "undocked" and current is not None:
            current["docked_min"] = r.get("docked_min")
            current = None
    return sessions


def minutes_to_low_fit(records: list[dict], min_points: int = 3) -> dict | None:
    """Least-squares line: minutes until the first low reading, from the
    voltage of the first reading off the dock. Only stretches that actually
    reached low and weren't picked up count. None with fewer than
    `min_points` (any answer would be a guess). Stretches that never reached
    low are reported separately - they only say "at least this long"."""
    points = [
        (s["v_off_first"], s["first_low_min"])
        for s in stretches(records)
        if s.get("first_low_min") is not None and not s.get("picked_up") and s.get("v_off_first") is not None
    ]
    if len(points) < min_points:
        return None
    n = len(points)
    mean_v = sum(v for v, _ in points) / n
    mean_m = sum(m for _, m in points) / n
    var_v = sum((v - mean_v) ** 2 for v, _ in points)
    if var_v == 0:
        return None
    slope = sum((v - mean_v) * (m - mean_m) for v, m in points) / var_v
    intercept = mean_m - slope * mean_v
    ss_tot = sum((m - mean_m) ** 2 for _, m in points)
    ss_res = sum((m - (slope * v + intercept)) ** 2 for v, m in points)
    return {
        "points": n,
        "slope_min_per_v": slope,
        "intercept_min": intercept,
        "r2": 1 - ss_res / ss_tot if ss_tot else None,
        "v_range": (min(v for v, _ in points), max(v for v, _ in points)),
    }
