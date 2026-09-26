"""Backend-agnostic robot control surface.

Subclasses (real PyCozmo hardware, or a console-logging simulator) implement
only the low-level primitives. Mood and gesture playback are composed here
on top of those primitives, so every backend gets the full expressive
gesture library for free — including a simulated one, which is what makes
it possible to develop and demo the whole tool-calling loop away from the
physical robot.
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass

from cozmo_brain.robot.gestures import GESTURES, Step
from cozmo_brain.robot.moods import MOODS, Mood


@dataclass(frozen=True)
class MoveResult:
    """Outcome of a drive()/turn()/spin_wheels_for() call - richer than a
    bare bool so a caller (tool handlers, calibrate_mode) can tell "didn't
    move at all" (e.g. still on the charger) apart from "moved, but a
    hazard cut it short" instead of both collapsing into a plain success."""

    moved: bool
    hazard: str | None = None  # None, "cliff", or "fall" - see robot/real.py


class RobotBackend(abc.ABC):
    # --- lifecycle ---
    @abc.abstractmethod
    def connect(self) -> None: ...

    @abc.abstractmethod
    def disconnect(self) -> None: ...

    def __enter__(self) -> "RobotBackend":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()

    # --- primitives every backend must implement ---
    @abc.abstractmethod
    def say_wav(self, wav_path: str) -> None:
        """Play a WAV file through the robot's speaker, blocking until done."""

    @abc.abstractmethod
    def drive(self, distance_mm: float, speed_mmps: float) -> MoveResult:
        """Drive straight for a distance at a speed, blocking until stopped
        (or until a hazard cuts it short and reacts — see robot/real.py).
        `moved=False` if blocked entirely, e.g. still on the charger —
        backends with no such concept always return MoveResult(True)."""

    @abc.abstractmethod
    def turn(self, angle_degrees: float) -> MoveResult:
        """Turn in place by an (approximate, calibrated) angle, blocking
        until stopped. See drive() for the return value."""

    @abc.abstractmethod
    def spin_wheels_for(self, seconds: float, speed_mmps: float) -> MoveResult:
        """Spin wheels in opposite directions for a fixed duration, then
        stop. See drive() for the return value.

        A raw primitive `turn()` is built on — exposed separately so the
        calibration mode can measure real degrees-per-second without going
        through the angle-to-duration formula it's trying to calibrate.
        """

    @abc.abstractmethod
    def set_head_angle_deg(self, angle_deg: float, duration: float = 0.4) -> None: ...

    @abc.abstractmethod
    def set_lift_height_mm(self, height_mm: float, duration: float = 0.4) -> None: ...

    @abc.abstractmethod
    def lower_lift_fully(self, duration: float = 0.4) -> None:
        """Commands the lift as low as it will physically go, rather than
        clamping to a specific documented minimum height - confirmed on
        real hardware that the documented minimum (32mm) can't be relied
        on to visibly/consistently bottom out. Used everywhere a mood or
        gesture means "settle all the way down", instead of
        set_lift_height_mm() with a hardcoded low number."""

    @abc.abstractmethod
    def set_backpack_light(self, color: str) -> None:
        """color is one of: green, red, blue, white, off."""

    @abc.abstractmethod
    def show_expression(self, name: str, duration: float | None = None) -> None:
        """name is a pycozmo.expressions.expressions class name, e.g. 'Happiness'."""

    @abc.abstractmethod
    def capture_photo(self, path: str) -> str:
        """Capture one camera frame and save it to `path`. Returns the path."""

    @abc.abstractmethod
    def list_animations(self) -> list[str]:
        """Real Anki animation clip/group names actually loaded on this robot."""

    @abc.abstractmethod
    def play_animation(self, name: str) -> None:
        """Play a real animation clip or group by exact name."""

    @abc.abstractmethod
    def display_custom_image(self, image, duration: float | None = None) -> None:
        """Display an arbitrary 128x32 PIL image (e.g. the battery icon),
        as opposed to show_expression()'s named procedural faces."""

    @abc.abstractmethod
    def get_battery_voltage(self) -> float | None:
        """Cozmo's current battery voltage, or None if unknown/unavailable
        (e.g. not connected yet, or a backend with no real battery)."""

    @abc.abstractmethod
    def wait_for_tap(self, timeout: float | None = None) -> bool:
        """Blocks until a physical tap on Cozmo's body is detected, or until
        `timeout` seconds elapse (blocks indefinitely if None). Returns
        whether a tap was actually detected (False only on timeout)."""

    @abc.abstractmethod
    def is_picked_up(self) -> bool:
        """Whether Cozmo is currently detected as picked up/carried
        (backends with no such concept always return False)."""

    @abc.abstractmethod
    def is_on_charger(self) -> bool:
        """Whether Cozmo is currently docked on the charger (backends with
        no such concept always return False)."""

    @abc.abstractmethod
    def is_charging(self) -> bool:
        """Whether Cozmo is currently actively charging. `is_on_charger()
        and not is_charging()` means docked but not charging - i.e. full,
        assuming the charge controller stops topping off once full like
        chargers normally do (backends with no such concept always return
        False). drive()/turn() block only while this is True - once full,
        a normal drive/turn is allowed and drives Cozmo off the dock as a
        side effect, rather than any background behavior deciding to leave
        the charger unprompted."""

    # --- connection health (default: always healthy — overridden by real.py) ---
    def is_healthy(self) -> bool:
        """Whether the connection looks alive. Backends with no real
        connection to lose (e.g. simulated) are always healthy."""
        return True

    def reconnect(self) -> bool:
        """Attempt to re-establish a dropped connection. Returns whether it
        succeeded. No-op (trivially succeeds) for backends with no real
        connection to lose."""
        return True

    # --- composed behavior, shared by every backend ---
    def apply_mood(self, mood_name: str) -> Mood:
        mood = MOODS.get(mood_name.lower())
        if mood is None:
            raise ValueError(f"Unknown mood '{mood_name}'. Known moods: {', '.join(sorted(MOODS))}")
        self.show_expression(mood.expression)
        self.set_backpack_light(mood.light)
        if mood.head_deg is not None:
            self.set_head_angle_deg(mood.head_deg)
        if mood.lower_lift:
            self.lower_lift_fully()
        elif mood.lift_mm is not None:
            self.set_lift_height_mm(mood.lift_mm)
        return mood

    def list_moods(self) -> list[str]:
        return sorted(MOODS)

    def list_gestures(self) -> list[str]:
        return sorted(GESTURES)

    def run_gesture(self, name: str) -> str:
        gesture = GESTURES.get(name.lower())
        if gesture is None:
            raise ValueError(f"Unknown gesture '{name}'. Known gestures: {', '.join(sorted(GESTURES))}")
        for step in gesture.steps:
            self._run_step(step)
        return gesture.description

    def _run_step(self, step: Step) -> None:
        if step.kind == "mood":
            self.apply_mood(step.value)
        elif step.kind == "expression":
            self.show_expression(step.value, duration=step.duration)
        elif step.kind == "light":
            self.set_backpack_light(step.value)
        elif step.kind == "head":
            self.set_head_angle_deg(step.value, duration=step.duration)
        elif step.kind == "lift":
            self.set_lift_height_mm(step.value, duration=step.duration)
        elif step.kind == "lower_lift":
            self.lower_lift_fully(duration=step.duration)
        elif step.kind == "turn":
            self.turn(step.value)
        elif step.kind == "drive":
            distance, speed = step.value
            self.drive(distance, speed)
        elif step.kind == "pause":
            time.sleep(step.value)
        else:
            raise ValueError(f"Unknown gesture step kind: {step.kind}")
