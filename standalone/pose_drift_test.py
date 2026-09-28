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
   on the floor works well), then print the starting pose. Position reads
   ~0,0 reliably (confirmed across every run so far), but heading is NOT
   guaranteed to be exactly 0deg (confirmed on real hardware: one run
   started at 3.7deg) - the actual reading is captured and used as the
   real target for steps 5-6 below, not assumed to be 0.
2. Drive the test path below (edit DRIVE_STEPS for your own test).
3. Print what `cli.pose` *believes* happened.
4. You physically measure where Cozmo actually is/facing and compare
   against step 3's numbers - tells you whether wheel-odometry pose
   tracking itself is accurate.
5. Command `go_to_pose()` back to the exact starting pose, then measure
   position accuracy.
6. Correct heading ourselves: confirmed directly on real hardware, across
   two runs, that go_to_pose()'s point-turn segment is *unreliable* -
   one run it never rotated at all (Cozmo just ended up facing whichever
   way the line segment drove), the next run it corrected the heading
   correctly on its own, likely a race condition rather than a
   deterministic failure. cli.pose's own heading readback tracked physical
   reality correctly both times though, so this reads it back, computes
   the delta to the desired heading, and issues our own already-calibrated
   turn() for it regardless - a negligible correction if go_to_pose()
   already got it right, or the real fix if it didn't - instead
   of trusting go_to_pose()'s built-in turn.
7. You physically measure how close he actually lands to the real start
   mark/heading after the correction - this is the number that decides
   whether dead-reckoning (+ this correction) is tight enough to build a
   "return to charger" behavior on top of, or whether it needs vision-loop
   correction too.

Usage (from the repo root): python3 standalone/pose_drift_test.py
(Same cozmo-env setup as standalone/orchestrator.py - this only talks to the robot
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
#
# Desk-scale, turn-heavy path (~800mm total, 4 tighter turns) - two
# room-scale runs already confirmed dead-reckoning is tight over a longer
# ~2.1m/2-turn path (see README's roadmap item 6), but Cozmo mostly stays
# on a desk in actual use, not crossing a room - a smaller area with more
# frequent, tighter maneuvering. More turns over a *shorter* total distance
# is actually a different (and in one way harder) stress test: each turn
# is its own source of calibration error, so this checks whether
# accumulated *heading* drift becomes the bigger problem here, even though
# total distance traveled is much smaller than the room-scale test.
DRIVE_STEPS: list[tuple[float, float] | tuple[str, float]] = [
    (200.0, 80.0),    # drive forward ~0.2m
    ("turn", 90.0),   # turn 90 degrees left
    (150.0, 80.0),    # drive forward ~0.15m
    ("turn", -120.0), # turn 120 degrees right
    (200.0, 80.0),    # drive forward ~0.2m
    ("turn", 45.0),   # turn 45 degrees left
    (150.0, 80.0),    # drive forward ~0.15m
    ("turn", -90.0),  # turn 90 degrees right
    (100.0, 80.0),    # drive forward ~0.1m
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
        # Position is reliably exactly (0,0) at a fresh pose origin (confirmed
        # across every run so far), but heading is NOT guaranteed to reset to
        # exactly 0deg (confirmed on real hardware: one run started at 3.7deg) -
        # a hardcoded (0,0,0deg) target here would silently return Cozmo to the
        # wrong heading rather than his real starting one. Read the actual
        # starting heading back instead of assuming it.
        start_heading_deg = cli.pose.rotation.angle_z.degrees

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
        start_pose = pycozmo.util.Pose(0.0, 0.0, 0.0, angle_z=pycozmo.util.Angle(degrees=start_heading_deg))
        cli.go_to_pose(start_pose)
        time.sleep(_SETTLE_S)
        print_pose(cli, "After go_to_pose() back to start")
        print(
            "\nNow physically measure how close Cozmo actually landed to the real "
            "starting mark - go_to_pose()'s position accuracy is what this checks.\n"
        )

        # Confirmed directly on real hardware, across two runs: go_to_pose()'s
        # AppendPathSegPointTurn segment is *unreliable*, not consistently broken -
        # one run left Cozmo facing exactly the line segment's own direction of
        # travel (the point-turn never happened at all: heading matched the
        # straight-line drive's bearing to within 0.5deg, and no rotation was
        # observed after stopping), the next run it corrected the heading
        # correctly on its own. Likely a race condition in how pycozmo's
        # multi-segment path completion event fires relative to when each
        # segment actually finishes, not a deterministic failure. Either way,
        # cli.pose's own heading *readback* tracks physical reality correctly in
        # both cases - so this reads the actual heading back, computes the delta
        # to the desired heading ourselves, and issues our own already-calibrated
        # turn_in_place() for it, regardless of whether go_to_pose() already got
        # it right (in which case this ends up a negligible near-zero correction)
        # or didn't (in which case this does the real work).
        current_heading_deg = cli.pose.rotation.angle_z.degrees
        heading_error_deg = (start_heading_deg - current_heading_deg + 180) % 360 - 180
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
