"""Background reactor: every CONNECTION_CHECK_INTERVAL_S, checks that Cozmo's
telemetry is still arriving (RobotBackend.is_healthy()) and, if it isn't,
tries reconnect() - so a dropped link gets noticed and fixed while he's
idle, not only when the next conversation turn happens to call a tool
(engine.py's own pre-tool check).

Same background-thread shape as idle_fidget.py, and lives beside it for the
same reason: it needs CozmoEngine's turn_lock, not just a RobotBackend.
"""

from __future__ import annotations

import logging
import threading
import time

from cozmo_brain.config import Settings
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.robot.base import RobotBackend

logger = logging.getLogger(__name__)

# While disconnected, retry at least this often rather than waiting a full
# CONNECTION_CHECK_INTERVAL_S between reconnect attempts.
_RETRY_WHILE_DOWN_S = 30.0


class ConnectionMonitor:
    def __init__(self, robot: RobotBackend, engine: CozmoEngine, settings: Settings):
        self._robot = robot
        self._engine = engine
        self._settings = settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._down_since: float | None = None

    def start(self) -> None:
        if self._settings.connection_check_interval_s <= 0:
            return
        self._thread = threading.Thread(target=self._run, name="connection-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._check_once()
            interval = self._settings.connection_check_interval_s
            if self._down_since is not None:
                interval = min(interval, _RETRY_WHILE_DOWN_S)
            self._stop.wait(interval)

    def _check_once(self) -> None:
        if self._robot.is_healthy():
            if self._down_since is not None:
                logger.info("Connection check: Cozmo's telemetry is back (was down %.0fs).", self._outage_s())
                self._down_since = None
            else:
                logger.debug("Connection check: Cozmo connected.")
            return

        # A conversation turn or autonomous charger return holds turn_lock
        # and has its own pre-tool reconnect (engine.py) - never reconnect
        # underneath one; just check again next time.
        if not self._engine.turn_lock.acquire(blocking=False):
            return
        try:
            if self._robot.is_healthy():  # recovered while we waited for the lock
                return
            if self._down_since is None:
                self._down_since = time.monotonic()
                logger.warning(
                    "Connection check: no telemetry from Cozmo for over %.0fs - looks disconnected, reconnecting.",
                    self._settings.robot_stale_after_s,
                )
            if self._robot.reconnect():
                logger.info("Connection check: reconnected after %.0fs.", self._outage_s())
                self._down_since = None
            else:
                logger.warning(
                    "Connection check: still disconnected (%.0fs) - retrying in %.0fs.",
                    self._outage_s(), min(self._settings.connection_check_interval_s, _RETRY_WHILE_DOWN_S),
                )
        except Exception as e:  # noqa: BLE001 - a monitoring hiccup shouldn't crash the process
            logger.warning("Connection check failed: %s", e)
        finally:
            self._engine.turn_lock.release()

    def _outage_s(self) -> float:
        return 0.0 if self._down_since is None else time.monotonic() - self._down_since
