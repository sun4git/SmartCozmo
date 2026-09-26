"""Curated mood presets: a facial expression + backpack light + pose, applied as one unit.

Every field here maps to a real, confirmed PyCozmo primitive:
- `expression` is a class name from `pycozmo.expressions.expressions` (Ekman-based
  procedural faces PyCozmo ships and renders locally — no downloaded assets needed).
- `light` is one of PyCozmo's built-in backpack light colors.
- `head_deg` / `lift_mm` are optional pose accents, clamped to the robot's real
  range (head: -25..44.5 degrees, lift: 32..92 mm) by the backend.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mood:
    expression: str
    light: str
    head_deg: float | None = None
    lift_mm: float | None = None


MOODS: dict[str, Mood] = {
    # lift_mm=32 (the real minimum - arm fully down) on every mood below
    # except the four deliberately "arms up" celebratory ones (happy,
    # excited, proud, smug) - those raise the lift as their whole visual
    # point, but every other mood must guarantee the arm is down instead of
    # just leaving whatever height was already set, since the lift arm
    # physically covers the face screen when raised. This used to only be
    # true of "neutral" (see its own comment, kept below) - confirmed on
    # real hardware that a mood applied right after a lift-raising gesture
    # (or reflex, e.g. the cliff/fall hazard reaction's "scared" mood, or
    # pickup_reactor's "surprised") would otherwise leave the face hidden
    # behind an arm raised by whatever ran before it, with no guarantee
    # anything would ever lower it again.
    "neutral": Mood("Neutral", "off", head_deg=0, lift_mm=32),
    "happy": Mood("Happiness", "green", head_deg=15, lift_mm=70),
    "excited": Mood("Excitement", "blue", head_deg=30, lift_mm=90),
    "curious": Mood("Amazement", "white", head_deg=20, lift_mm=32),
    "proud": Mood("Excitement", "green", head_deg=25, lift_mm=92),
    "sad": Mood("Sadness", "off", head_deg=-20, lift_mm=32),
    "sleepy": Mood("Tiredness", "off", head_deg=-25, lift_mm=32),
    "bored": Mood("Boredom", "off", head_deg=-10, lift_mm=32),
    "scared": Mood("Fear", "white", head_deg=30, lift_mm=32),
    "surprised": Mood("Surprise", "white", head_deg=35, lift_mm=32),
    "confused": Mood("Confusion", "off", head_deg=5, lift_mm=32),
    "annoyed": Mood("Annoyance", "red", head_deg=-5, lift_mm=32),
    "angry": Mood("Fury", "red", head_deg=-10, lift_mm=32),
    "suspicious": Mood("Suspicion", "off", head_deg=0, lift_mm=32),
    "embarrassed": Mood("Embarrassment", "off", head_deg=-15, lift_mm=32),
    "smug": Mood("Skepticism", "green", head_deg=20, lift_mm=80),
}
