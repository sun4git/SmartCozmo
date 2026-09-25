"""Interactive turn() calibration.

Spins the wheels for a fixed duration at the configured turn speed, asks you
how many degrees Cozmo actually turned, and reports the seconds-per-degree
value to put in .env. Deliberately drives the wheels directly
(`spin_wheels_for`) rather than through `turn()`, since `turn()` is what's
being calibrated.
"""

from __future__ import annotations

from cozmo_brain.config import Settings
from cozmo_brain.robot.base import RobotBackend

_TEST_DURATION_S = 2.0


def run(robot: RobotBackend, settings: Settings) -> None:
    print("Turn calibration.")
    print(f"Each run spins the wheels for {_TEST_DURATION_S:.1f}s at {settings.turn_speed_mmps:.0f}mm/s.")
    print("Watch Cozmo and estimate how many degrees he actually turned.\n")

    samples: list[float] = []
    while True:
        cmd = input("Press Enter to run a test turn, 'done' to finish, or 'quit' to exit without saving > ").strip().lower()
        if cmd == "quit":
            return
        if cmd == "done":
            break

        robot.spin_wheels_for(_TEST_DURATION_S, settings.turn_speed_mmps)
        raw = input("How many degrees did he turn (best estimate)? > ").strip()
        try:
            degrees = float(raw)
        except ValueError:
            print("Not a number, skipping this sample.\n")
            continue
        if degrees <= 0:
            print("Enter a positive number of degrees.\n")
            continue
        samples.append(degrees)

    if not samples:
        print("No samples collected, nothing to report.")
        return

    avg_degrees = sum(samples) / len(samples)
    seconds_per_degree = _TEST_DURATION_S / avg_degrees
    print(f"\nAverage over {len(samples)} run(s): {avg_degrees:.1f} degrees in {_TEST_DURATION_S:.1f}s.")
    print("Add this to your .env to calibrate turn():\n")
    print(f"    TURN_SECONDS_PER_DEGREE={seconds_per_degree:.5f}")
