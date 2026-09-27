#!/usr/bin/env python3
"""
Pose / dead-reckoning drift test - standalone, NOT part of cozmo_brain or
--mode calibrate.

Why separate: this is one-off exploratory hardware testing (see the
"Switching speech providers"-adjacent PyCozmo localization research in
README/this project's history), not a regular calibration workflow. It
answers a single question: how much does `cli.pose`/`cli.go_to_pose()`
actually drift from physical reality over a real commanded path, on this
specific unit and floor. `Client.pose` and `Client.go_to_pose()` are real,
confirmed pycozmo primitives (`pycozmo/client.py`) - computed by Cozmo's
own firmware from wheel/tread monitoring, not something this project
builds itself - but `cozmo_brain`'s own `RobotBackend` doesn't read or use
either today.

Protocol:
1. Connect. Mark Cozmo's exact starting position/heading physically (tape
   on the floor works well), then print the starting pose (should read
   ~0,0,0/0deg - it's always zeroed at connect).
2. Drive the test path below (edit DRIVE_STEPS for your own test).
3. Print what `cli.pose` *believes* happened.
4. You physically measure where Cozmo actually is/facing and compare
   against step 3's numbers - tells you whether wheel-odometry pose
   tracking itself is accurate.
5. Command `go_to_pose()` back to the exact starting pose, then measure
   position accuracy.
6. Correct heading ourselves: confirmed directly on real hardware that
   go_to_pose()'s point-turn segment doesn't actually rotate Cozmo to the
   target heading at all - the line segment drives to position and just
   ends up facing whichever way it drove, then nothing further happens.
   cli.pose's own heading readback does track physical reality correctly
   though, so this reads it back, computes the delta to the desired
   heading, and issues our own already-calibrated turn() for it instead
   of trusting go_to_pose()'s built-in turn.
7. You physically measure how close he actually lands to the real start
   mark/heading after the correction - this is the number that decides
   whether dead-reckoning (+ this correction) is tight enough to build a
   "return to charger" behavior on top of, or whether it needs vision-loop
   correction too.

Usage: python3 pose_drift_test.py
(Same cozmo-env setup as orchestrator.py - this only talks to the robot
directly over PyCozmo, no LLM/STT/TTS/API keys involved at all.)
"""

from __future__ import annotations

import os
import time

import pycozmo
from dotenv import load_dotenv

load_dotenv()

# Edit this to whatever path you want to test. Each step is either
# (distance_mm, speed_mmps) for a straight drive, or ("turn", degrees) for
# a point turn (positive = left/counterclockwise, matching cozmo_brain's
# own turn() convention). Kept as a plain, hand-edited list rather than a
# CLI - this is meant to be tweaked per test run, not a polished tool.
DRIVE_STEPS: list[tuple[float, float] | tuple[str, float]] = [
    (300.0, 100.0),  # drive forward 300mm
    ("turn", 90.0),  # turn 90 degrees left
    (300.0, 100.0),  # drive forward another 300mm
]

# Matches cozmo_brain's own turn() calibration, read from the same .env so
# a drive/turn calibration issue doesn't get conflated with pose drift.
_TURN_SPEED_MMPS = float(os.environ.get("TURN_SPEED_MMPS", "40.0"))
_TURN_SECONDS_PER_DEGREE = float(os.environ.get("TURN_SECONDS_PER_DEGREE", "0.02222"))

# Extra pause after a move before reading pose - lets residual momentum
# settle so the reading reflects where Cozmo actually stopped, not
# mid-motion.
_SETTLE_S = 0.3


def print_pose(cli: pycozmo.Client, label: str) -> None:
    x, y, z = cli.pose.position.x_y_z
    heading_deg = cli.pose.rotation.angle_z.degrees
    print(f"{label}: x={x:.1f}mm  y={y:.1f}mm  heading={heading_deg:.1f}deg  origin_id={cli.pose.origin_id}")


def drive_straight(cli: pycozmo.Client, distance_mm: float, speed_mmps: float) -> None:
    duration = abs(distance_mm) / speed_mmps
    speed = speed_mmps if distance_mm >= 0 else -speed_mmps
    cli.drive_wheels(lwheel_speed=speed, rwheel_speed=speed)
    time.sleep(duration)
    cli.stop_all_motors()
    time.sleep(_SETTLE_S)


def turn_in_place(cli: pycozmo.Client, angle_degrees: float) -> None:
    duration = abs(angle_degrees) * _TURN_SECONDS_PER_DEGREE
    direction = 1 if angle_degrees > 0 else -1
    cli.drive_wheels(lwheel_speed=-_TURN_SPEED_MMPS * direction, rwheel_speed=_TURN_SPEED_MMPS * direction)
    time.sleep(duration)
    cli.stop_all_motors()
    time.sleep(_SETTLE_S)


def main() -> None:
    print("Pose/dead-reckoning drift test.\n")
    with pycozmo.connect() as cli:
        input("Mark Cozmo's exact starting position and heading (e.g. tape on the floor), then press Enter > ")
        print_pose(cli, "Start")

        print("\nDriving the test path...")
        for step in DRIVE_STEPS:
            kind, value = step
            if kind == "turn":
                turn_in_place(cli, value)
            else:
                drive_straight(cli, kind, value)
        print_pose(cli, "After driving the path")
        print(
            "\nNow physically measure/estimate where Cozmo actually is and which way "
            "he's actually facing, and compare against the numbers above - that tells "
            "you whether wheel-odometry pose tracking itself is accurate.\n"
        )

        input("Press Enter to command go_to_pose() back to the exact start pose > ")
        start_pose = pycozmo.util.Pose(0.0, 0.0, 0.0, angle_z=pycozmo.util.Angle(degrees=0.0))
        cli.go_to_pose(start_pose)
        time.sleep(_SETTLE_S)
        print_pose(cli, "After go_to_pose() back to start")
        print(
            "\nNow physically measure how close Cozmo actually landed to the real "
            "starting mark - go_to_pose()'s position accuracy is what this checks.\n"
        )

        # Confirmed directly on real hardware: go_to_pose()'s AppendPathSegPointTurn
        # segment doesn't actually rotate Cozmo to the target heading - the line
        # segment drives to position and naturally ends up facing whichever way it
        # just drove (that's inherent to driving a straight line, not a commanded
        # turn), and nothing further happens after it stops. cli.pose's own heading
        # *readback* does track physical reality correctly though (confirmed
        # separately by comparing it against the line segment's own direction of
        # travel) - so use it as a trustworthy feedback signal instead of trusting
        # go_to_pose()'s built-in point-turn: read the actual heading back, compute
        # the delta to the desired heading ourselves, and issue our own
        # already-calibrated turn_in_place() for it.
        target_heading_deg = 0.0
        current_heading_deg = cli.pose.rotation.angle_z.degrees
        heading_error_deg = (target_heading_deg - current_heading_deg + 180) % 360 - 180
        print(f"Heading error after go_to_pose(): {heading_error_deg:.1f}deg - correcting with our own turn()...")
        turn_in_place(cli, heading_error_deg)
        print_pose(cli, "After corrective turn")
        print(
            "\nNow physically measure how far off his heading still is - this checks "
            "whether the corrective turn actually worked, and whether dead-reckoning "
            "position + this correction is tight enough on its own.\n"
        )


if __name__ == "__main__":
    main()
