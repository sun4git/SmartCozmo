"""Charger break: after CHARGER_BREAK_AFTER_MIN continuous minutes docked,
Cozmo says so, drives off the charger for a short stretch, then drives back
on his own - a rest for the charger/battery without waiting for the battery to
run low. There is no temperature sensor, so this is a time limit, not an
overheat detector.

Deliberately narrow, so everything else behaves as before:
  - the idle fidget is untouched: a peek still drives him off the dock once
    charging has stopped, at random. That (or a "come out" request) resets the
    dock timer and gets NO timed return - the low/critical battery rules
    (charger_return.py) bring him back as they always did.
  - only a break-initiated exit gets a timed return, after a random
    CHARGER_BREAK_STRETCH_MIN..MAX minutes (or earlier via those battery rules).
  - it never fires mid-conversation or listening window, while picked up, or
    while the battery is too low to leave the dock - the same rule every
    movement uses (real.py's _must_stay_on_charger(): docked at or below
    BATTERY_LOW_VOLTAGE); it just waits and tries again. No separate "high
    enough" voltage: the dock reads high and nobody knows this robot's curve
    yet - battery_log.py collects the data for a smarter call later.

DockTimer.minutes() (continuous time docked) also feeds the per-turn battery
line (engine.dock_minutes_fn). It always runs; the break itself only acts when
CHARGER_BREAK_ENABLED is true. Same thread/lock pattern as idle_fidget.py.
"""

from __future__ import annotations

import logging
import random
import threading
import time

from cozmo_brain.battery_log import BatteryLog
from cozmo_brain.config import Settings
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.robot.base import RobotBackend
from cozmo_brain.tools.registry import charger_return_message, leave_charger

logger = logging.getLogger(__name__)

_POLL_INTERVAL_S = 15.0

_LEAVE_TEXT = "I've been on the charger a while - I'm going to stretch my wheels for a bit."
_RETURN_TEXT = "Okay, stretch over - heading back to my charger."


class DockTimer:
    """Continuous time docked; resets whenever he leaves the dock (for any
    reason) or is picked up."""

    def __init__(self, robot: RobotBackend):
        self._robot = robot
        self._docked_since: float | None = None

    def update(self) -> None:
        try:
            docked = self._robot.is_on_charger() and not self._robot.is_picked_up()
        except Exception as e:  # noqa: BLE001 - a status read must not kill the thread
            logger.debug("Dock timer read failed: %s", e)
            return
        if not docked:
            self._docked_since = None
        elif self._docked_since is None:
            self._docked_since = time.monotonic()

    def minutes(self) -> float | None:
        since = self._docked_since
        return None if since is None else (time.monotonic() - since) / 60.0


class ChargerBreak:
    def __init__(
        self,
        robot: RobotBackend,
        engine: CozmoEngine,
        settings: Settings,
        timer: DockTimer | None = None,
        battery_log: BatteryLog | None = None,
    ):
        self._robot = robot
        self._engine = engine
        self._settings = settings
        self._battery_log = battery_log
        self.timer = timer or DockTimer(robot)
        self._return_at: float | None = None  # monotonic; set only by a break-initiated exit
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="charger-break", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._check_once()
            except Exception as e:  # noqa: BLE001 - a background policy hiccup must not crash the process
                logger.warning("Charger break check failed: %s", e)
            self._stop.wait(_POLL_INTERVAL_S)

    def _is_quiet(self) -> bool:
        """Same notion of idle as idle_fidget.py."""
        if self._engine.listening_window_open:
            return False
        return time.monotonic() - self._engine.last_interaction_monotonic >= self._settings.idle_fidget_after_s

    def _check_once(self) -> None:
        self.timer.update()
        if not self._settings.charger_break_enabled:
            return
        if self._robot.is_on_charger():
            self._return_at = None  # back (or still) on the dock - no stretch pending
            minutes = self.timer.minutes()
            if minutes is not None and minutes >= self._settings.charger_break_after_min:
                self._leave()
            return
        if self._return_at is not None and time.monotonic() >= self._return_at:
            self._return()

    def _leave(self) -> None:
        s = self._settings
        # Checked before speaking, so he never announces a stretch he can't take.
        if self._robot.is_movement_blocked():
            logger.debug("Charger break due, but the battery's too low to leave the dock - waiting.")
            return
        if self._robot.is_picked_up() or not self._is_quiet():
            return
        if not self._engine.turn_lock.acquire(blocking=False):
            return
        try:
            voltage = self._robot.get_battery_voltage()
            reading = f"{voltage:.2f}V" if voltage is not None else "unknown voltage"
            logger.info("Charger break: docked %.0f min at %s - stepping off.", self.timer.minutes() or 0.0, reading)
            with self._robot.keep_backpack_light():
                self._engine.speak(_LEAVE_TEXT, mood="happy")
            if self._battery_log is not None:
                self._battery_log.note_exit("break")
            result = leave_charger(self._robot, s)
            needs_attention = bool(result.extra and result.extra.get("needs_attention"))
            if result.ok and not needs_attention and not self._robot.is_on_charger():
                stretch = random.uniform(s.charger_break_stretch_min, max(s.charger_break_stretch_min, s.charger_break_stretch_max))
                self._return_at = time.monotonic() + stretch * 60.0
                logger.info("Charger break: off the dock, heading back in %.1f min.", stretch)
            self._engine.add_note(
                f"[Docked a long while (battery {reading}). I said \"{_LEAVE_TEXT}\" and stepped off my "
                f"charger on my own. Result: {result.message}]"
            )
        finally:
            self._engine.turn_lock.release()

    def _return(self) -> None:
        if self._robot.is_picked_up() or not self._robot.has_charger_pose():
            logger.info("Charger break: can't head back on my own (picked up / charger location lost) - leaving it to the battery rules.")
            self._return_at = None
            return
        # A return mid-conversation would be rude, but unlike the departure it
        # needn't wait for a full idle period - just not mid-window.
        if self._engine.listening_window_open:
            return
        if not self._engine.turn_lock.acquire(blocking=False):
            return
        try:
            self._return_at = None
            voltage = self._robot.get_battery_voltage()
            logger.info("Charger break: stretch over at %s V - returning to the charger.", f"{voltage:.2f}" if voltage is not None else "?")
            with self._robot.keep_backpack_light():
                self._engine.speak(_RETURN_TEXT, mood="neutral")
            if self._battery_log is not None:
                self._battery_log.note_return("break_over")
            result = self._robot.return_to_charger()
            outcome = charger_return_message(result)
            logger.info("Charger break return: %s", outcome)
            self._engine.add_note(
                f"[Stretch over. I said \"{_RETURN_TEXT}\" and drove back to my charger on my own. Result: {outcome}]"
            )
        finally:
            self._engine.turn_lock.release()
