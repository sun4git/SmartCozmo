"""Low-battery return-to-charger - decides *when* Cozmo offers to go back to
his charger, or goes on his own, off BatteryMonitor's voltage readings.
The actual navigation lives in RobotBackend.return_to_charger() (see
robot/real.py); this is only the policy on top of it.

- BATTERY_LOW_VOLTAGE: says out loud that he's getting low and offers to
  head back. The offer is written into the conversation history, so a
  plain "yes" (via the mode's normal wake word/tap/push-to-talk/typing) is
  understood, and the model calls the `dock` tool, which navigates first
  whenever a charger location is known.
- BATTERY_CRITICAL_VOLTAGE: announces it, then goes back on his own, no
  confirmation - the backstop for an offer nobody answered.
- Either level with no valid charger location (never left the charger this
  session, or picked up/reconnected since - see real.py's
  _forget_charger_pose()), or a return that fails partway: asks to be put
  on the charger instead, once per low-battery episode.

A level only counts once it holds for _CONFIRM_READINGS consecutive checks
(BATTERY_CHECK_INTERVAL_S apart), so a momentary motor-load voltage sag
mid-drive can't trigger any of this. Each level fires at most once per
episode; an episode ends the next time Cozmo is seen on the charger.

Lives alongside engine.py rather than under robot/ for the same reason as
idle_fidget.py: it needs CozmoEngine (speech, conversation, turn_lock),
not just a RobotBackend. Runs on BatteryMonitor's own thread.
"""

from __future__ import annotations

import logging

from cozmo_brain.battery_log import BatteryLog
from cozmo_brain.config import Settings
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.robot.base import RobotBackend
from cozmo_brain.tools.registry import charger_return_message

logger = logging.getLogger(__name__)

_CONFIRM_READINGS = 2

# How long to wait for an in-progress conversation turn to finish before
# speaking/driving. Turns are bounded by MAX_TOOL_ITERATIONS, so this is
# rarely hit; if it is, the reading is simply retried on the next check.
_TURN_WAIT_S = 60.0

_OFFER_TEXT = "My battery's getting low. Want me to head back to my charger?"
_GOING_TEXT = "My battery's almost empty - I'm heading back to my charger now."
_HELP_TEXT = "My battery's really low, and I can't find my way back to my charger. Can you put me on it?"


class ChargerReturner:
    def __init__(
        self, robot: RobotBackend, engine: CozmoEngine, settings: Settings, battery_log: BatteryLog | None = None
    ):
        self._robot = robot
        self._engine = engine
        self._settings = settings
        self._battery_log = battery_log
        self._reset_episode()

    def _reset_episode(self) -> None:
        self._low_streak = 0
        self._critical_streak = 0
        self._low_handled = False
        self._critical_handled = False
        self._help_asked = False

    def on_battery_reading(self, voltage: float) -> None:
        """BatteryMonitor's on_reading hook - called with every valid reading."""
        if not self._settings.auto_return_to_charger_enabled:
            return
        if self._robot.is_on_charger():
            if self._low_handled or self._critical_handled:
                logger.info("Back on the charger - low-battery episode over.")
            self._reset_episode()
            return

        if voltage <= self._settings.battery_critical_voltage:
            self._critical_streak += 1
            self._low_streak += 1
        elif voltage <= self._settings.battery_low_voltage:
            self._critical_streak = 0
            self._low_streak += 1
        else:
            # Voltage can bounce back up a little once load drops - the
            # streaks restart, but the handled flags deliberately don't, so
            # a reading hovering around a threshold can't re-ask every
            # minute. Only reaching the charger ends the episode.
            self._low_streak = 0
            self._critical_streak = 0
            return

        if self._critical_streak >= _CONFIRM_READINGS and not self._critical_handled:
            self._handle_critical(voltage)
        elif self._low_streak >= _CONFIRM_READINGS and not self._low_handled and not self._critical_handled:
            self._handle_low(voltage)

    def _handle_low(self, voltage: float) -> None:
        if not self._engine.turn_lock.acquire(timeout=_TURN_WAIT_S):
            logger.info("Conversation turn still running - will retry the low-battery offer next check.")
            return
        try:
            self._low_handled = True
            if not self._robot.has_charger_pose():
                self._ask_for_help(voltage)
                return
            logger.info("Battery low (%.2fV) - offering to return to the charger.", voltage)
            with self._robot.keep_backpack_light():  # usually idle (off) - don't leave it lit
                self._engine.speak(_OFFER_TEXT, mood="sleepy")
            self._engine.add_note(
                f"[Battery low ({voltage:.2f}V). I said this out loud on my own, not in reply to "
                f"anything: \"{_OFFER_TEXT}\" If they agree, call the dock tool.]"
            )
        finally:
            self._engine.turn_lock.release()

    def _handle_critical(self, voltage: float) -> None:
        if not self._engine.turn_lock.acquire(timeout=_TURN_WAIT_S):
            logger.info("Conversation turn still running - will retry the critical-battery return next check.")
            return
        try:
            self._critical_handled = True
            self._low_handled = True
            if not self._robot.has_charger_pose():
                self._ask_for_help(voltage)
                return

            logger.warning("Battery critical (%.2fV) - returning to the charger on my own.", voltage)
            with self._robot.keep_backpack_light():
                self._engine.speak(_GOING_TEXT, mood="sleepy")
            if self._battery_log is not None:
                self._battery_log.note_return("critical")
            try:
                result = self._robot.return_to_charger()
                outcome = charger_return_message(result)
                succeeded = result.completed
            except Exception as e:  # noqa: BLE001 - a failed return must still end in asking for help
                logger.warning("Autonomous return to charger failed: %s", e)
                outcome = f"Didn't make it onto the charger - {e}."
                succeeded = False
            logger.info("Autonomous return to charger: %s", outcome)
            self._engine.add_note(
                f"[Battery critical ({voltage:.2f}V). I said \"{_GOING_TEXT}\" and drove back to my "
                f"charger on my own. Result: {outcome}]"
            )
            if not succeeded:
                self._ask_for_help(voltage)
        finally:
            self._engine.turn_lock.release()

    def _ask_for_help(self, voltage: float) -> None:
        """Caller holds turn_lock."""
        if self._help_asked:
            return
        self._help_asked = True
        logger.info("Battery low (%.2fV), no way back to the charger - asking for help.", voltage)
        with self._robot.keep_backpack_light():
            self._engine.speak(_HELP_TEXT, mood="sad")
        self._engine.add_note(
            f"[Battery low ({voltage:.2f}V). I said this out loud on my own: \"{_HELP_TEXT}\"]"
        )
