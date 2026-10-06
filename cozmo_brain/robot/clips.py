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


_FRAME_MS = 33  # the step PyCozmo's play_anim_ppclip() advances its own clock by


def playback_seconds(keyframe_times_ms) -> float:
    """How long PyCozmo takes to play a clip with keyframes at these times.

    NOT the time of the last keyframe: play_anim_ppclip() queues one 33 ms
    frame per keyframe on top of the gap to the next one, so every keyframe
    adds ~33 ms (a clip with a keyframe every frame plays about twice as long
    as its timeline says - seen on the robot: a clip whose last keyframe is at
    3.1 s ran for ~6 s). This mirrors that loop, plus the start and end frames.
    """
    times = sorted(set(keyframe_times_ms))
    frames = 2  # StartAnimation + EndAnimation
    clock = 0
    for i, t in enumerate(times):
        frames += 1
        clock += _FRAME_MS
        if i < len(times) - 1:
            target = clock + times[i + 1] - t
            while target > clock:
                frames += 1
                clock += _FRAME_MS
    return frames / pycozmo.robot.FRAME_RATE


def ppclip_duration_s(ppclip) -> float:
    """How long a preprocessed clip takes to play (see playback_seconds)."""
    return playback_seconds(ppclip.keyframes.keys())


def clip_playback_seconds(clip) -> float:
    """playback_seconds() for an un-preprocessed AnimClip, without rendering
    its face frames: derives the same keyframe times PreprocessedClip does
    (wheel and backpack-light keyframes also add one at their end; face-sprite,
    audio and event keyframes add none, PyCozmo skips them)."""
    ae = pycozmo.anim_encoder
    times: set[int] = set()
    for k in clip.keyframes:
        if isinstance(k, (ae.AnimFaceAnimation, ae.AnimRobotAudio, ae.AnimEvent)):
            continue
        times.add(k.trigger_time_ms)
        if isinstance(k, (ae.AnimBodyMotion, ae.AnimBackpackLights)):
            times.add(k.trigger_time_ms + k.duration_ms)
    return playback_seconds(times)
