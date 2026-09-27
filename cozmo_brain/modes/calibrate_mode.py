"""Interactive turn() calibration.

Two checks, offered as a choice:

1. Partial-spin baseline - spins the wheels for a fixed duration at the
   configured turn speed, asks how many degrees Cozmo actually turned, and
   derives TURN_SECONDS_PER_DEGREE from scratch. Deliberately drives the
   wheels directly (`spin_wheels_for`) rather than through `turn()`, since
   `turn()` is what's being calibrated. Good for an initial calibration
   with no prior value to build on, but derives a single linear rate from
   a short (2s) sample and extrapolates it to any commanded angle -
   confirmed on real hardware that this can be noticeably off for a much
   longer commanded turn (e.g. gestures.py's "spin", a full 360 degrees,
   ~4x longer than the 2s sample this rate was derived from) even when
   smaller turns feel fine, since a small estimation error at the 2s scale
   gets linearly amplified by however much longer the real commanded turn
   is.

2. Full-circle refinement - goes through the real `turn()` method (the
   actual code path gestures/tools use, calibration-in-progress or not)
   for a full 360 degrees using whatever TURN_SECONDS_PER_DEGREE is
   currently configured, then asks how far short of or past the starting
   mark it landed - comparing an end point to a visible start mark is a
   much easier and more precise thing for a person to judge than
   estimating an abstract angle from scratch, and it measures `turn()` at
   the same full-circle scale gestures.py's "spin" actually commands it
   at, rather than extrapolating from a much shorter sample. Corrects the
   existing rate proportionally rather than deriving one from zero, so it
   only makes sense to run this after some baseline value already exists
   in .env (either from check 1, or a previous full-circle pass).
"""

from __future__ import annotations

from cozmo_brain.config import Settings
from cozmo_brain.robot.base import RobotBackend

_TEST_DURATION_S = 2.0


def run(robot: RobotBackend, settings: Settings) -> None:
    print("Turn calibration.\n")
    print("Which check do you want to run?")
    print("  1) Partial-spin baseline - derive TURN_SECONDS_PER_DEGREE from scratch")
    print("  2) Full-circle refinement - correct the current value using a full 360-degree turn")
    choice = input("> ").strip()

    if choice == "2":
        _run_full_circle(robot, settings)
    else:
        _run_partial_spin(robot, settings)


def _run_partial_spin(robot: RobotBackend, settings: Settings) -> None:
    print(f"\nEach run spins the wheels for {_TEST_DURATION_S:.1f}s at {settings.turn_speed_mmps:.0f}mm/s.")
    print("Watch Cozmo and estimate how many degrees he actually turned.\n")

    samples: list[float] = []
    while True:
        cmd = input("Press Enter to run a test turn, 'done' to finish, or 'quit' to exit without saving > ").strip().lower()
        if cmd == "quit":
            return
        if cmd == "done":
            break

        result = robot.spin_wheels_for(_TEST_DURATION_S, settings.turn_speed_mmps)
        if not result.moved:
            print("Cozmo is still charging - wait until fully charged, or take him off, before calibrating.\n")
            continue
        if result.hazard:
            print(f"Detected a {result.hazard} mid-spin - discarding this sample, it won't be a clean reading.\n")
            continue
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


def _run_full_circle(robot: RobotBackend, settings: Settings) -> None:
    print(f"\nEach run turns a full 360 degrees using the current TURN_SECONDS_PER_DEGREE="
          f"{settings.turn_seconds_per_degree:.5f}.")
    print("Mark or note exactly where Cozmo starts facing before each run - you'll compare")
    print("where he stops against that same mark, not estimate an angle from scratch.\n")

    actual_degrees_samples: list[float] = []
    while True:
        cmd = input("Press Enter to run a full-circle turn, 'done' to finish, or 'quit' to exit without saving > ").strip().lower()
        if cmd == "quit":
            return
        if cmd == "done":
            break

        result = robot.turn(360)
        if not result.moved:
            print("Cozmo is still charging - wait until fully charged, or take him off, before calibrating.\n")
            continue
        if result.hazard:
            print(f"Detected a {result.hazard} mid-turn - discarding this sample, it won't be a clean reading.\n")
            continue

        landing = input("Did he stop short of the starting mark, past it, or right on it? [short/past/exact] > ").strip().lower()
        if landing == "exact":
            actual_degrees_samples.append(360.0)
            continue
        if landing not in ("short", "past"):
            print("Enter 'short', 'past', or 'exact'.\n")
            continue
        raw = input("About how many degrees short/past was he? > ").strip()
        try:
            degrees_off = float(raw)
        except ValueError:
            print("Not a number, skipping this sample.\n")
            continue
        if degrees_off < 0:
            print("Enter a positive number of degrees - use 'short'/'past' for the direction.\n")
            continue
        sign = -1 if landing == "short" else 1
        actual_degrees_samples.append(360.0 + sign * degrees_off)

    if not actual_degrees_samples:
        print("No samples collected, nothing to report.")
        return

    avg_actual_degrees = sum(actual_degrees_samples) / len(actual_degrees_samples)
    # The commanded duration (360 * current rate) produced avg_actual_degrees
    # of real rotation - scale the rate proportionally so that same duration
    # formula would produce exactly 360 degrees instead. Assumes turn()'s
    # duration-to-angle relationship is linear near the current rate (the
    # same assumption the partial-spin check above already makes), not a
    # fixed-offset overshoot independent of commanded duration.
    corrected = settings.turn_seconds_per_degree * (360.0 / avg_actual_degrees)
    print(f"\nAverage over {len(actual_degrees_samples)} run(s): {avg_actual_degrees:.1f} degrees actual "
          f"for a 360-degree command.")
    print("Add this to your .env to calibrate turn():\n")
    print(f"    TURN_SECONDS_PER_DEGREE={corrected:.5f}")
