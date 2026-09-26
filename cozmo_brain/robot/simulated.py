"""A console-logging stand-in for the real robot.

Implements the exact same primitive surface as PyCozmoRobot, so the entire
tool-calling loop, conversation logic, and full gesture library can be
developed and demoed on any machine with no Cozmo, no PyCozmo Wi-Fi link,
and no Bluetooth mic/speaker attached. Select it with ROBOT_BACKEND=simulated
in .env, or --simulate on the CLI.
"""

from __future__ import annotations

import logging
import threading
import time

from cozmo_brain.robot.base import MoveResult, RobotBackend

logger = logging.getLogger(__name__)

_KNOWN_EXPRESSIONS = {
    "Neutral", "Anger", "Sadness", "Happiness", "Surprise", "Disgust", "Fear", "Pleading",
    "Vulnerability", "Despair", "Guilt", "Disappointment", "Embarrassment", "Horror",
    "Skepticism", "Annoyance", "Fury", "Suspicion", "Rejection", "Boredom", "Tiredness",
    "Asleep", "Confusion", "Amazement", "Excitement",
}

# A handful of made-up clip/group names, just so play_animation()/list_animations()
# have something plausible to demo against without a real robot connected.
_FAKE_ANIMATIONS = [
    "anim_greeting_happy_01",
    "anim_greeting_happy_02",
    "anim_reacttocliff_stuck_01",
    "group:GreetAfterLongTime",
    "group:CubePounceFail",
]


class SimulatedRobot(RobotBackend):
    def __init__(self, *_args, **_kwargs):
        self._connected = False
        # Matches real.py's wheel lock, purely so --simulate testing of
        # async gestures (run_gesture_async(), see base.py) doesn't produce
        # interleaved log lines from two "wheel" calls at once - no actual
        # hardware resource to protect here.
        self._wheel_lock = threading.RLock()

    def connect(self) -> None:
        self._connected = True
        logger.info("[sim] Connected to simulated Cozmo.")
        self.run_gesture("wake_up")

    def disconnect(self) -> None:
        self._connected = False
        logger.info("[sim] Disconnected.")

    def say_wav(self, wav_path: str) -> None:
        logger.info("[sim] \U0001f50a playing audio: %s", wav_path)
        time.sleep(0.1)

    def drive(self, distance_mm: float, speed_mmps: float) -> MoveResult:
        with self._wheel_lock:
            duration = abs(distance_mm) / max(abs(speed_mmps), 1.0)
            logger.info("[sim] \U0001f697 drive %.0fmm at %.0fmm/s (~%.1fs)", distance_mm, speed_mmps, duration)
            time.sleep(min(duration, 0.3))
            return MoveResult(moved=True)

    def turn(self, angle_degrees: float) -> MoveResult:
        with self._wheel_lock:
            logger.info("[sim] \U0001f504 turn %.0f degrees", angle_degrees)
            time.sleep(0.1)
            return MoveResult(moved=True)

    def spin_wheels_for(self, seconds: float, speed_mmps: float) -> MoveResult:
        with self._wheel_lock:
            logger.info("[sim] \U0001f504 spin wheels for %.1fs at %.0fmm/s", seconds, speed_mmps)
            time.sleep(min(seconds, 0.3))
            return MoveResult(moved=True)

    def set_head_angle_deg(self, angle_deg: float, duration: float = 0.4) -> None:
        logger.info("[sim] \U0001f440 head angle -> %.1f deg", angle_deg)

    def set_lift_height_mm(self, height_mm: float, duration: float = 0.4) -> None:
        logger.info("[sim] \U0001f4aa lift height -> %.1f mm", height_mm)

    def lower_lift_fully(self, duration: float = 0.4) -> None:
        logger.info("[sim] \U0001f4aa lift height -> fully down")

    def set_backpack_light(self, color: str) -> None:
        logger.info("[sim] \U0001f4a1 backpack light -> %s", color)

    def show_expression(self, name: str, duration: float | None = None) -> None:
        if name not in _KNOWN_EXPRESSIONS:
            raise ValueError(f"Unknown expression '{name}'. Known: {', '.join(sorted(_KNOWN_EXPRESSIONS))}")
        logger.info("[sim] \U0001f642 face -> %s", name)

    def capture_photo(self, path: str) -> str:
        # Writes a real (if fake) image rather than just logging, so anything
        # downstream that reads the file back (vision attach, who_is_this's
        # comparison) has something real to work with in simulated mode too.
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (320, 240), color=(90, 90, 90))
        ImageDraw.Draw(img).text((10, 110), "[simulated camera frame]", fill=(255, 255, 255))
        img.save(path)
        logger.info("[sim] \U0001f4f7 captured a placeholder photo -> %s", path)
        return path

    def display_custom_image(self, image, duration: float | None = None) -> None:
        logger.info("[sim] \U0001f5bc displaying a custom %dx%d image", *image.size)

    def get_battery_voltage(self) -> float | None:
        # No real battery to read — a plausible healthy constant is enough
        # to exercise the battery-monitor code path without hardware.
        return 4.0

    def wait_for_tap(self, timeout: float | None = None) -> bool:
        # No real accelerometer to read — stand in with a keypress so
        # --mode vad's tap trigger is still exercisable with --simulate.
        # Ignores `timeout`: this blocks on input() either way, so a caller
        # polling with a short timeout (see vad_mode._wait_for_wake_word_or_tap)
        # won't get the fast reaction time it would from real hardware.
        del timeout
        input("[sim] Press Enter to simulate a tap > ")
        return True

    def is_picked_up(self) -> bool:
        # No real accelerometer/status flag to read - never "picked up".
        return False

    def is_on_charger(self) -> bool:
        # No real charger contacts to read - never "on the charger", so
        # drive()/turn() are never blocked by it either.
        return False

    def is_charging(self) -> bool:
        return False

    def list_animations(self) -> list[str]:
        return list(_FAKE_ANIMATIONS)

    def play_animation(self, name: str) -> None:
        if name not in _FAKE_ANIMATIONS:
            raise ValueError(f"Unknown animation '{name}'. Call list_animations() first.")
        logger.info("[sim] \U0001f3ac playing animation: %s", name)
        time.sleep(0.2)
