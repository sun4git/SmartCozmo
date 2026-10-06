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

# A clip's own backpack-light commands. Removed too: seen on the robot, after a
# clip with a lights track was played with audio merged in, the backpack light
# ignored every command from the app (the listening blink, mood colours) for the
# rest of the run - even though the robot no longer reported itself animating,
# and even after the wake word. Clips without a lights track never did this.
# With these gone the app is the only thing that sets the light, so mood colours
# and the blink always show; the clip's own light flashes are the cost.
LIGHT_PACKETS = (pycozmo.protocol_encoder.AnimBackpackLights,)

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


def strip_packets(ppclip, types: tuple) -> int:
    """Remove every packet of these types from a preprocessed clip, in place.

    Returns how many were removed. Idempotent, so it's safe on a clip PyCozmo
    has cached and plays again. Frame timing is untouched (empty frames stay).
    """
    removed = 0
    for t, actions in ppclip.keyframes.items():
        kept = [a for a in actions if not isinstance(a, types)]
        removed += len(actions) - len(kept)
        ppclip.keyframes[t] = kept
    return removed


def strip_wheels(ppclip) -> int:
    """Remove every wheel command from a preprocessed clip, in place (see strip_packets)."""
    return strip_packets(ppclip, WHEEL_PACKETS)


def strip_lights(ppclip) -> int:
    """Remove the clip's own backpack-light commands, in place (see LIGHT_PACKETS)."""
    return strip_packets(ppclip, LIGHT_PACKETS)


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


def build_ticks(ppclip, anim_id: int) -> list[tuple]:
    """The (image_packet, other_packets) pair for every 33 ms tick
    Client.play_anim_ppclip() would queue for this clip, start and end
    included - the same loop, but returned instead of sent, so audio can be put
    into the ticks (see play_with_audio). Wheel commands are not removed here:
    strip them from `ppclip` first (strip_wheels)."""
    pe = pycozmo.protocol_encoder
    ticks: list[tuple] = [(None, (pe.StartAnimation(anim_id=anim_id),))]
    times = sorted(ppclip.keyframes)
    clock = 0
    for i, t in enumerate(times):
        image = None
        pkts = []
        for action in ppclip.keyframes[t]:
            if isinstance(action, pe.DisplayImage):
                image = action
            elif isinstance(action, pe.Packet):
                pkts.append(action)
        ticks.append((image, pkts))
        clock += _FRAME_MS
        if i < len(times) - 1:
            target = clock + times[i + 1] - t
            while target > clock:
                ticks.append((None, None))
                clock += _FRAME_MS
    ticks.append((None, (pe.EndAnimation(),)))
    return ticks


def key_tick_map(ppclip) -> list[tuple[int, int]]:
    """(keyframe time in ms, tick index it is played on) for every keyframe, the
    way build_ticks() lays the clip out. Tick 0 is the StartAnimation tick, the
    first keyframe is tick 1. Because every keyframe costs an extra tick, a clip
    plays slower than its own timeline; this is the map between the two."""
    times = sorted(ppclip.keyframes)
    out: list[tuple[int, int]] = []
    tick = 1
    clock = 0
    for i, t in enumerate(times):
        out.append((t, tick))
        tick += 1
        clock += _FRAME_MS
        if i < len(times) - 1:
            target = clock + times[i + 1] - t
            while target > clock:
                tick += 1
                clock += _FRAME_MS
    return out


def seconds_at(tick_map: list[tuple[int, int]], t_ms: float) -> float:
    """When (in seconds from the start of playback) the clip-timeline moment
    `t_ms` is actually played. Used to put a sound where its picture is: linear
    between keyframes, and at real-time scale before the first / after the last."""
    fps = pycozmo.robot.FRAME_RATE
    if not tick_map:
        return max(0.0, t_ms) / 1000.0
    first_t, first_tick = tick_map[0]
    if t_ms <= first_t:
        return max(0.0, first_tick - (first_t - t_ms) * fps / 1000.0) / fps
    last_t, last_tick = tick_map[-1]
    if t_ms >= last_t:
        return (last_tick + (t_ms - last_t) * fps / 1000.0) / fps
    for (t0, k0), (t1, k1) in zip(tick_map, tick_map[1:]):
        if t0 <= t_ms <= t1:
            span = t1 - t0
            tick = k0 if span == 0 else k0 + (t_ms - t0) * (k1 - k0) / span
            return tick / fps
    return last_tick / fps


def play_with_audio(cli, ppclip, audio_packets) -> None:
    """Play a clip with audio (speech) in its frames, from the first frame.

    PyCozmo has one audio queue shared by speech and clips: Client.play_anim
    clears it, and speech queued after a clip waits behind the clip's frames,
    so the two can't be started separately and overlap. Its own design for
    clip sound is one audio packet per 33 ms frame, so that is what this does:
    packet i goes into frame i. Whatever speech outlasts the clip is queued
    after it. A clip longer than the speech is just silent for the rest."""
    cli.cancel_anim()
    anim_id = cli._next_anim_id
    cli._next_anim_id += 1
    ticks = build_ticks(ppclip, anim_id)
    controller = cli.anim_controller
    for i, (image, pkts) in enumerate(ticks):
        controller.play_anim_frame(audio_packets[i] if i < len(audio_packets) else None, image, pkts)
    rest = audio_packets[len(ticks):]
    if rest:
        controller.play_audio(rest)


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
