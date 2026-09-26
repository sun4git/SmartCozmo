"""Background reactor: plays a small idle-appropriate gesture every
IDLE_FIDGET_AFTER_S seconds while nothing real has happened for that long -
purely cosmetic personality (a bored/restless creature occasionally moving
on its own), not sensor-driven like battery_monitor.py/pickup_reactor.py, so
lives alongside engine.py rather than under robot/ (it needs CozmoEngine's
last_interaction_monotonic, not just a RobotBackend).

Same background-thread shape as battery_monitor.py/pickup_reactor.py.
"""

from __future__ import annotations

import logging
import random
import threading
import time

from cozmo_brain.config import Settings
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.robot.base import RobotBackend

logger = logging.getLogger(__name__)

_POLL_INTERVAL_S = 10.0

# Deliberately calm, no-drive gestures only - idle fidgeting shouldn't send
# Cozmo rolling off somewhere on its own. Both already end with the lift
# arm back down (see robot/gestures.py).
_IDLE_GESTURES = ("peek", "shrug")


class IdleFidgeter:
    def __init__(self, robot: RobotBackend, engine: CozmoEngine, settings: Settings):
        self._robot = robot
        self._engine = engine
        self._settings = settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_fidget_monotonic = time.monotonic()

    def start(self) -> None:
        if not self._settings.idle_fidget_enabled:
            return
        self._thread = threading.Thread(target=self._run, name="idle-fidget", daemon=True)
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
        now = time.monotonic()
        last_active = max(self._engine.last_interaction_monotonic, self._last_fidget_monotonic)
        if now - last_active < self._settings.idle_fidget_after_s:
            return

        self._last_fidget_monotonic = now
        gesture = random.choice(_IDLE_GESTURES)
        try:
            self._robot.run_gesture(gesture)
        except Exception as e:  # noqa: BLE001 - a cosmetic fidget hiccup shouldn't crash the process
            logger.debug("Idle fidget ('%s') failed: %s", gesture, e)
