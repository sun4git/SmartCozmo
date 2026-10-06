"""Helpers for playing real Anki animation clips through PyCozmo
(`Client.play_anim`), shared by robot/real.py and standalone/audition_animations.py.

Three things PyCozmo 0.8.0 needs help with, all found on real hardware:

- Newer Pillow rejects the eyelid "chord" PyCozmo draws for any clip whose
  procedural face has a negative lid bend (`ValueError: y1 must be greater
  than or equal to y0`), so most clips fail to pre-render. See patch_pillow_chord().
- A clip's own wheel commands drive Cozmo around (and off a table or the
  charger), so they're removed before playing. See strip_wheels().
- PyCozmo ignores a clip's audio and sprite-face tracks (marked TODO in its
  source), so clips play silent. Nothing to do about that here.
"""

from __future__ import annotations

import pycozmo

# Real Anki clips for the start and end of a run (hand-picked on the robot;
# see data/animations_picks.tsv). Used by RealRobot.wake_up()/go_to_sleep(),
# which fall back to the built-in `wake_up`/`sleep` gestures if clips are off
# or not downloaded.
WAKE_CLIP = "anim_launch_wakeup_03"
SLEEP_CLIPS = ("anim_gotosleep_getin_01", "anim_gotosleep_sleeping_01")

# Packets a clip uses to move the wheels (see pycozmo/anim.py PreprocessedClip).
# Head, lift, backpack lights and face images are different packet types and
# are left alone.
WHEEL_PACKETS = (
    pycozmo.protocol_encoder.AnimBody,
    pycozmo.protocol_encoder.TurnInPlaceAtSpeed,
    pycozmo.protocol_encoder.DriveWheels,
    pycozmo.protocol_encoder.RecordHeading,
    pycozmo.protocol_encoder.TurnToRecordedHeading,
)

_chord_patched = False


def patch_pillow_chord() -> None:
    """Work around the PyCozmo / newer-Pillow incompatibility described above.

    Swapping the box corners when they're inverted describes the same ellipse,
    so the same chord comes out - but that equivalence is reasoned, not yet
    checked against the old Pillow's output: if a clip's eyelids look wrong,
    that's the first suspect. Safe to call more than once.
    """
    global _chord_patched
    if _chord_patched:
        return
    from PIL import ImageDraw

    original = ImageDraw.ImageDraw.chord

    def chord(self, xy, start, end, fill=None, outline=None, width=1):
        (x0, y0), (x1, y1) = xy
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        return original(self, ((x0, y0), (x1, y1)), start, end, fill=fill, outline=outline, width=width)

    ImageDraw.ImageDraw.chord = chord
    _chord_patched = True


def strip_wheels(ppclip) -> int:
    """Remove every wheel command from a preprocessed clip, in place.

    Returns how many were removed. Idempotent, so it's safe on a clip PyCozmo
    has cached and plays again. Frame timing is untouched (empty frames stay).
    """
    removed = 0
    for t, actions in ppclip.keyframes.items():
        kept = [a for a in actions if not isinstance(a, WHEEL_PACKETS)]
        removed += len(actions) - len(kept)
        ppclip.keyframes[t] = kept
    return removed


def ppclip_duration_s(ppclip) -> float:
    """Approximate play length: the time of the clip's last keyframe."""
    return max(ppclip.keyframes, default=0) / 1000.0
