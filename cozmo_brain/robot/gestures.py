"""Curated gesture library: short choreographed sequences built from real,
confirmed PyCozmo primitives (mood/expression, backpack lights, head, lift,
wheels). No fabricated animation-clip names are used here — that's what
`play_animation()` / `list_animations()` are for, which discover real Anki
clips at runtime instead of guessing at them.

Each gesture is a list of `Step`s that `RobotBackend.run_gesture()` executes
in order against whichever backend (real or simulated) is active.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Step:
    kind: str  # "mood" | "expression" | "light" | "head" | "lift" | "lower_lift" | "turn" | "drive" | "pause"
    value: Any
    duration: float = 0.4


@dataclass(frozen=True)
class Gesture:
    description: str
    steps: list[Step] = field(default_factory=list)


GESTURES: dict[str, Gesture] = {
    "nod_yes": Gesture(
        "Nods head up and down, like saying yes.",
        [
            Step("head", 30, duration=0.25),
            Step("head", -15, duration=0.25),
            Step("head", 30, duration=0.25),
            Step("head", 0, duration=0.25),
        ],
    ),
    "shake_no": Gesture(
        "Wiggles left-right in place, like saying no.",
        [
            Step("turn", 20, duration=0.2),
            Step("turn", -40, duration=0.2),
            Step("turn", 20, duration=0.2),
        ],
    ),
    "wake_up": Gesture(
        "Lifts head and arm up as if waking up from a nap, then settles back down.",
        [
            Step("mood", "sleepy", duration=0.3),
            Step("pause", 0.6),
            Step("head", 30, duration=0.6),
            Step("lift", 70, duration=0.6),
            Step("mood", "happy", duration=0.3),
            Step("pause", 0.4),
            Step("lower_lift", None, duration=0.3),  # all the way down — don't leave the arm blocking the screen
        ],
    ),
    "sleep": Gesture(
        "Lowers head and arm and dims the lights, ready for a nap.",
        [
            Step("head", -25, duration=0.6),
            Step("lower_lift", None, duration=0.6),
            Step("mood", "sleepy", duration=0.3),
        ],
    ),
    "cheer": Gesture(
        "Bounces its lift arm up and down excitedly with green lights.",
        [
            Step("mood", "excited", duration=0.2),
            Step("lift", 92, duration=0.2),
            Step("lift", 40, duration=0.2),
            Step("lift", 92, duration=0.2),
            Step("lower_lift", None, duration=0.2),  # all the way down — don't leave the arm blocking the screen
            Step("light", "green"),
        ],
    ),
    "dance": Gesture(
        "A quick, silly wheel shimmy with flashing lights.",
        [
            Step("light", "blue"),
            Step("turn", 25, duration=0.15),
            Step("light", "green"),
            Step("turn", -25, duration=0.15),
            Step("light", "blue"),
            Step("turn", 25, duration=0.15),
            Step("light", "off"),
        ],
    ),
    "spin": Gesture(
        "A full playful spin in place.",
        [Step("turn", 360, duration=1.5)],
    ),
    "flinch": Gesture(
        "Startles backward with a scared face.",
        [
            Step("mood", "scared", duration=0.1),
            Step("drive", (-40, 80), duration=0.5),
        ],
    ),
    "peek": Gesture(
        "Curiously cranes its head up and tilts, like peeking at something.",
        [
            Step("mood", "curious", duration=0.2),
            Step("head", 44, duration=0.5),
            Step("turn", 15, duration=0.2),
            Step("turn", -15, duration=0.2),
        ],
    ),
    "shrug": Gesture(
        "A quick 'I dunno' shrug: lift up-down with a confused face.",
        [
            Step("mood", "confused", duration=0.2),
            Step("lift", 92, duration=0.3),
            Step("lower_lift", None, duration=0.3),
        ],
    ),
    "fist_pump": Gesture(
        "Raises its arm high in triumph, proud and smug.",
        [
            Step("lift", 92, duration=0.4),
            Step("mood", "proud", duration=0.2),
            Step("pause", 0.5),
            Step("lower_lift", None, duration=0.3),  # all the way down — don't leave the arm blocking the screen
        ],
    ),
    "sneaky_creep": Gesture(
        "Creeps forward slowly with a suspicious look, very mischievous.",
        [
            Step("mood", "suspicious", duration=0.2),
            Step("drive", (60, 30), duration=1.5),
        ],
    ),
    "sad_shuffle": Gesture(
        "Droops its head and arm and shuffles backward sadly.",
        [
            Step("mood", "sad", duration=0.2),
            Step("drive", (-30, 40), duration=0.8),
        ],
    ),
    "alert": Gesture(
        "Snaps to attention: head and arm up, surprised face, white lights.",
        [
            Step("mood", "surprised", duration=0.1),
            Step("head", 44, duration=0.3),
            Step("lift", 92, duration=0.3),
            Step("pause", 0.5),
            Step("lower_lift", None, duration=0.3),  # all the way down — don't leave the arm blocking the screen
        ],
    ),
}
