"""Background battery monitor.

Periodically checks Cozmo's battery voltage and, once it drops below the
configured thresholds, shows a battery icon on his face plus a red backpack
light — so there's a visible few-seconds warning before he actually dies,
rather than him just going dark with no notice. Backend-agnostic like the
rest of robot/base.py: SimulatedRobot.get_battery_voltage() is a constant
healthy value, so this simply never fires in simulated mode.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from cozmo_brain.config import Settings
from cozmo_brain.robot.base import RobotBackend
from cozmo_brain.robot.battery_face import render_battery_icon

logger = logging.getLogger(__name__)

# How long the icon stays up each time it's shown. display_custom_image()'s
# duration blocks this monitor's own thread (not the conversation thread),
# which is exactly what we want here.
_ICON_DISPLAY_S = 4.0


class BatteryMonitor:
    def __init__(
        self,
        robot: RobotBackend,
        settings: Settings,
        on_reading: Callable[[float], None] | None = None,
    ):
        """`on_reading` is called on this monitor's thread with every valid
        voltage reading (healthy ones too, so a listener can tell when a low
        streak ends) - charger_return.py's hook for deciding when to offer/
        start a return to the charger. It may block (a return drive does)."""
        self._robot = robot
        self._settings = settings
        self._on_reading = on_reading
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="battery-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._check_once()
            self._stop.wait(self._settings.battery_check_interval_s)

    def _check_once(self) -> None:
        # During a connection drop, pycozmo keeps returning the last voltage
        # it received - a frozen value that could falsely trigger (or mask)
        # a low-battery reaction. Skip until telemetry is flowing again.
        if not self._robot.is_healthy():
            return
        try:
            voltage = self._robot.get_battery_voltage()
        except Exception as e:  # noqa: BLE001 - a monitoring hiccup shouldn't crash the process
            logger.debug("Battery check failed: %s", e)
            return
        if voltage is None:
            return

        if voltage <= self._settings.battery_low_voltage:
            self._show_warning(voltage)

        if self._on_reading is not None:
            try:
                self._on_reading(voltage)
            except Exception as e:  # noqa: BLE001 - same as above
                logger.warning("Battery reading listener failed: %s", e)

    def _show_warning(self, voltage: float) -> None:
        low_v = self._settings.battery_low_voltage
        critical_v = self._settings.battery_critical_voltage

        critical = voltage <= critical_v
        logger.warning("Battery %s: %.2fV", "CRITICAL" if critical else "low", voltage)

        # Within the "low" band, fill shrinks from full (at low_v) to empty
        # (at critical_v); below critical_v the icon draws a warning mark
        # instead of a fill level (see battery_face.render_battery_icon).
        charge_ratio = (voltage - critical_v) / max(low_v - critical_v, 0.01)

        try:
            icon = render_battery_icon(charge_ratio, critical=critical)
            self._robot.set_backpack_light("red")
            self._robot.display_custom_image(icon, duration=_ICON_DISPLAY_S)
        except Exception as e:  # noqa: BLE001 - same as above
            logger.debug("Battery warning display failed: %s", e)
