"""Curated mood presets: a facial expression + backpack light + pose, applied as one unit.

Every field here maps to a real, confirmed PyCozmo primitive:
- `expression` is a class name from `pycozmo.expressions.expressions` (Ekman-based
  procedural faces PyCozmo ships and renders locally — no downloaded assets needed).
- `light` is one of PyCozmo's built-in backpack light colors.
- `head_deg` is an optional pose accent, clamped to the robot's real range
  (-25..44.5 degrees) by the backend.
- `lift_mm` is an optional specific raised height (32..92 mm, clamped by the
  backend) for the four deliberately "arms up" moods only. `apply_mood()`
  itself leaves it raised (that's the whole visual point, still true for
  gesture-internal mood steps) - it's specifically `tools/registry.py`'s
  `handle_say()` that resets it back down after the reply finishes,
  checking this same field, confirmed on real hardware to otherwise read
  as a stuck arm rather than an intentional flourish once the turn is over.
- `lower_lift` (every other mood) drives the lift as low as it will
  physically go via `RobotBackend.lower_lift_fully()`, rather than a
  specific numeric height - confirmed on real hardware that the documented
  minimum (32mm) can't be relied on to visibly/consistently bottom out.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mood:
    expression: str
    light: str
    head_deg: float | None = None
    lift_mm: float | None = None
    lower_lift: bool = False


MOODS: dict[str, Mood] = {
    # lower_lift=True on every mood below except the four deliberately
    # "arms up" celebratory ones (happy, excited, proud, smug) - those
    # raise the lift as their whole visual point, but every other mood must
    # guarantee the arm is down instead of just leaving whatever height was
    # already set, since the lift arm physically covers the face screen
    # when raised. This used to only be true of "neutral" (see its own
    # comment, kept below) - confirmed on real hardware that a mood applied
    # right after a lift-raising gesture (or reflex, e.g. the cliff/fall
    # hazard reaction's "scared" mood, or pickup_reactor's "surprised")
    # would otherwise leave the face hidden behind an arm raised by
    # whatever ran before it, with no guarantee anything would ever lower
    # it again.
    "neutral": Mood("Neutral", "off", head_deg=0, lower_lift=True),
    "happy": Mood("Happiness", "green", head_deg=15, lift_mm=70),
    "excited": Mood("Excitement", "blue", head_deg=30, lift_mm=90),
    "curious": Mood("Amazement", "white", head_deg=20, lower_lift=True),
    "proud": Mood("Excitement", "green", head_deg=25, lift_mm=92),
    "sad": Mood("Sadness", "off", head_deg=-20, lower_lift=True),
    "sleepy": Mood("Tiredness", "off", head_deg=-25, lower_lift=True),
    "bored": Mood("Boredom", "off", head_deg=-10, lower_lift=True),
    "scared": Mood("Fear", "white", head_deg=30, lower_lift=True),
    "surprised": Mood("Surprise", "white", head_deg=35, lower_lift=True),
    "confused": Mood("Confusion", "off", head_deg=5, lower_lift=True),
    "annoyed": Mood("Annoyance", "red", head_deg=-5, lower_lift=True),
    "angry": Mood("Fury", "red", head_deg=-10, lower_lift=True),
    "suspicious": Mood("Suspicion", "off", head_deg=0, lower_lift=True),
    "embarrassed": Mood("Embarrassment", "off", head_deg=-15, lower_lift=True),
    "smug": Mood("Skepticism", "green", head_deg=20, lift_mm=80),
}
