"""A fake pycozmo.Client for testing PyCozmoRobot (robot/real.py) without a
robot: records motor/packet calls, keeps a settable pose, and simulates
go_to_pose() arriving at the target but skipping its point turn (the
confirmed real-hardware failure mode) - or hanging, like pycozmo's own
untimed wait. make_robot() also makes turn() rotate the fake pose by the
commanded angle, so pose readbacks after turns are meaningful.
"""

import dataclasses
import threading
import time

import _harness  # noqa: F401 - repo on sys.path, real .env ignored

import pycozmo
from pycozmo.robot import RobotStatusFlag as F  # noqa: F401 - re-exported for tests

from cozmo_brain.config import Settings
from cozmo_brain.robot.base import Pose2D  # noqa: F401 - re-exported for tests
from cozmo_brain.robot.real import PyCozmoRobot


class FakeConn:
    def __init__(self):
        self.sent = []

    def send(self, pkt):
        self.sent.append(type(pkt).__name__)


class FakeCli:
    def __init__(self):
        self.conn = FakeConn()
        self.set_pose(0, 0, 0, origin=1)
        self.calls = []
        self.go_to_pose_behavior = "arrive_bad_heading"
        self.battery_voltage = 3.9

    def set_pose(self, x, y, deg, origin=None):
        origin = self.pose.origin_id if origin is None else origin
        self.pose = pycozmo.util.Pose(x, y, 0.0, angle_z=pycozmo.util.Angle(degrees=deg), origin_id=origin)

    def drive_wheels(self, lwheel_speed, rwheel_speed):
        self.calls.append(("drive_wheels", lwheel_speed, rwheel_speed))

    def set_head_angle(self, angle, duration=0.4):
        self.calls.append(("head", angle))

    def set_lift_height(self, h, duration=0.4):
        self.calls.append(("lift", h))

    def set_all_backpack_lights(self, light):
        self.calls.append(("lights", light.on_color, light.off_color, light.on_frames, light.off_frames))

    def stop_all_motors(self):
        self.calls.append(("stop",))

    def go_to_pose(self, pose):
        self.calls.append(("go_to_pose", round(pose.position.x, 1), round(pose.position.y, 1),
                           round(pose.rotation.angle_z.degrees, 1)))
        if self.go_to_pose_behavior == "hang":
            threading.Event().wait()  # like pycozmo's e.wait() never firing
        time.sleep(0.2)
        # arrive at position but skip the point-turn (the confirmed failure mode)
        self.set_pose(pose.position.x, pose.position.y, pose.rotation.angle_z.degrees + 25.0)


def make_robot(**overrides):
    s = dataclasses.replace(Settings(), robot_stale_after_s=1e9, return_to_pose_timeout_s=1.5, **overrides)
    r = PyCozmoRobot(s)
    cli = FakeCli()
    r._cli = cli
    r._last_seen = time.monotonic()
    r._have_robot_state = True
    # turn(): make the fake pose rotate by the commanded angle, so the final
    # readback is meaningful.
    orig_spin = r.spin_wheels_for

    def spin(seconds, speed):
        res = orig_spin(seconds, speed)
        deg = seconds / s.turn_seconds_per_degree * (1 if speed > 0 else -1)
        cli.set_pose(cli.pose.position.x, cli.pose.position.y, cli.pose.rotation.angle_z.degrees + deg)
        return res
    r.spin_wheels_for = spin
    return r, cli


class Pkt:
    def __init__(self, status):
        self.status = status
        self.accel_x = self.accel_y = self.accel_z = 1000


