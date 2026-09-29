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
        # Listening isn't idle - and a fidget's motor noise would land in
        # the recording (see CozmoEngine.listening_window_open).
        if self._engine.listening_window_open:
            return
        now = time.monotonic()
        last_active = max(self._engine.last_interaction_monotonic, self._last_fidget_monotonic)
        if now - last_active < self._settings.idle_fidget_after_s:
            return

        # Never overlap a conversation turn or an autonomous charger return
        # (both hold turn_lock) - skip this check instead of waiting.
        # Confirmed on real hardware: a fidget fired mid-return, its turn
        # step queued behind the return's wheel lock, and the instant Cozmo
        # docked it ran and drove him straight back off the charger at 3.2V.
        if not self._engine.turn_lock.acquire(blocking=False):
            return
        try:
            self._last_fidget_monotonic = now
            gesture = random.choice(_IDLE_GESTURES)
            # A fidget is the one thing that must not leave the charger while
            # it's still charging - there's no reason to come out. Face/head/
            # lift still play so he doesn't look dead on the dock. Once
            # charging finishes (docked but not charging = full), the full
            # gesture runs, and its wheel steps drive him off the dock
            # first like any other movement. Conversation-triggered
            # movement has no such restriction. is_movement_blocked() also
            # covers "just docked, IS_CHARGING not registered yet" (see
            # real.py's _must_stay_on_charger()).
            charging_on_dock = self._robot.is_on_charger() and self._robot.is_charging()
            wheels = not (charging_on_dock or self._robot.is_movement_blocked())
            # Its mood step would otherwise leave an idle (off) light lit.
            with self._robot.keep_backpack_light():
                self._robot.run_gesture(gesture, wheels=wheels)
        except Exception as e:  # noqa: BLE001 - a cosmetic fidget hiccup shouldn't crash the process
            logger.debug("Idle fidget ('%s') failed: %s", gesture, e)
        finally:
            self._engine.turn_lock.release()
