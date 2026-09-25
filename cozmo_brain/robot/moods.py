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
    # head_deg=0/lift_mm=32 make this a real reset, not a no-op — most other
    # moods raise the lift arm (which covers the face screen) and there's no
    # other reliable way back down between turns otherwise.
    "neutral": Mood("Neutral", "off", head_deg=0, lift_mm=32),
    "happy": Mood("Happiness", "green", head_deg=15, lift_mm=70),
    "excited": Mood("Excitement", "blue", head_deg=30, lift_mm=90),
    "curious": Mood("Amazement", "white", head_deg=20),
    "proud": Mood("Excitement", "green", head_deg=25, lift_mm=92),
    "sad": Mood("Sadness", "off", head_deg=-20, lift_mm=32),
    "sleepy": Mood("Tiredness", "off", head_deg=-25, lift_mm=32),
    "bored": Mood("Boredom", "off", head_deg=-10),
    "scared": Mood("Fear", "white", head_deg=30),
    "surprised": Mood("Surprise", "white", head_deg=35),
    "confused": Mood("Confusion", "off", head_deg=5),
    "annoyed": Mood("Annoyance", "red", head_deg=-5),
    "angry": Mood("Fury", "red", head_deg=-10, lift_mm=32),
    "suspicious": Mood("Suspicion", "off", head_deg=0),
    "embarrassed": Mood("Embarrassment", "off", head_deg=-15),
    "smug": Mood("Skepticism", "green", head_deg=20, lift_mm=80),
}
