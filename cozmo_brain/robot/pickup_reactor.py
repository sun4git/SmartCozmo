"""Background reactor: plays a startled face/light the moment Cozmo is
picked up, and settles back to neutral once he's set back down again -
a purely physical reaction, no LLM/conversation turn involved. Same shape
as battery_monitor.py: a small polling thread alongside the main
conversation loop. Backend-agnostic like the rest of robot/base.py:
SimulatedRobot.is_picked_up() always returns False, so this simply never
fires in simulated mode.

Cosmetic caveat: this can race with another thread also calling
apply_mood() around the same moment (e.g. --mode vad's own "curious"/
"neutral" listening indicator) - both are just face/light/pose calls with
no shared state to corrupt, so the worst case is a flicker between two
moods, not a crash.
"""

from __future__ import annotations

import logging
import threading

from cozmo_brain.robot.base import RobotBackend

logger = logging.getLogger(__name__)

_POLL_INTERVAL_S = 0.2


class PickupReactor:
    def __init__(self, robot: RobotBackend):
        self._robot = robot
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._was_picked_up = False
        self._light_before_pickup: str | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="pickup-reactor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._check_once()
            self._stop.wait(_POLL_INTERVAL_S)

    def _check_once(self) -> None:
        try:
            picked_up = self._robot.is_picked_up()
        except Exception as e:  # noqa: BLE001 - a monitoring hiccup shouldn't crash the process
            logger.debug("Pickup check failed: %s", e)
            return

        if picked_up and not self._was_picked_up:
            self._was_picked_up = True
            self._light_before_pickup = self._robot.current_backpack_light()
            self._react_safely("surprised")
        elif not picked_up and self._was_picked_up:
            self._was_picked_up = False
            self._react_safely("neutral")
            # Back to whatever the light was before the pickup - "off" if
            # he was idle (off means idle, see vad_mode.py), rather than
            # neutral's green.
            if self._light_before_pickup is not None:
                try:
                    self._robot.set_backpack_light(self._light_before_pickup)
                except Exception as e:  # noqa: BLE001 - same as above
                    logger.debug("Could not restore backpack light: %s", e)

    def _react_safely(self, mood: str) -> None:
        try:
            self._robot.apply_mood(mood)
        except Exception as e:  # noqa: BLE001 - same as above
            logger.debug("Pickup reaction ('%s') failed: %s", mood, e)
