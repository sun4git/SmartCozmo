"""Test: real Anki wake-up / go-to-sleep clips at start and end, and the
clip-playing path behind them (play_animation: wheels stripped, waits for
completion, lowers the lift, suppresses self-caused taps, falls back to the
built-in gestures). Uses a fake pycozmo client (tests/fake_pycozmo.py); no
robot and no downloaded animation assets needed."""
import dataclasses
import logging
import sys
import time
from collections import defaultdict
from types import SimpleNamespace as NS

import _harness  # noqa: F401 - repo on sys.path, real .env ignored
from fake_pycozmo import make_robot

import pycozmo
from PIL import Image

from cozmo_brain.config import Settings
from cozmo_brain.robot import clips, real
from cozmo_brain.robot.simulated import SimulatedRobot
from cozmo_brain.tools import build_tools

logging.disable(logging.CRITICAL)
failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


pe = pycozmo.protocol_encoder


def fake_ppclip():
    kf = defaultdict(list)
    kf[0] = [pe.AnimHead(duration_ms=100, variability_deg=0, angle_deg=10), pe.DriveWheels(lwheel_speed_mmps=50, rwheel_speed_mmps=50)]
    kf[100] = [pe.TurnInPlaceAtSpeed(wheel_speed_mmps=50, direction=1.0), pe.AnimLift(duration_ms=100, variability_mm=0, height_mm=40)]
    kf[200] = [pe.DriveWheels()]
    return pycozmo.anim.PreprocessedClip(keyframes=kf)


def make(animations=True, fire_completion=True, **overrides):
    """A PyCozmoRobot over a fake client that 'has' the wake/sleep clips."""
    r, cli = make_robot(**overrides)
    r._animations_loaded = animations
    names = (clips.WAKE_CLIP,) + clips.SLEEP_CLIPS
    cli._clip_metadata = {n: NS(has_lift_height_track=True, has_backpack_lights_track=True, fspec="x") for n in names}
    cli._clip_metadata["anim_no_lift"] = NS(has_lift_height_track=False, has_backpack_lights_track=False, fspec="x")
    cli._clips = {}
    cli._ppclips = {n: fake_ppclip() for n in cli._clip_metadata}
    cli.get_anim_names = lambda: set(cli._clip_metadata)
    cli.animation_groups = {"Grp": NS(choose_member=lambda: NS(name=clips.WAKE_CLIP))}
    cli.played = []          # (clip keyframes as {time: [packet type names]}) per play
    handlers = []
    cli.add_handler = lambda evt, fn, one_shot=False: handlers.append(fn)

    def play_anim_ppclip(ppclip):
        cli.played.append({t: [type(a).__name__ for a in v] for t, v in sorted(ppclip.keyframes.items())})
        if fire_completion:
            for fn in handlers:
                fn(None)
    cli.play_anim_ppclip = play_anim_ppclip
    return r, cli


def lift_down(cli):
    return ("lift", 0.0) in cli.calls


# 1. wake_up plays the wake clip, no wheel commands, lift lowered afterwards.
r, cli = make()
r.wake_up()
check("wake_up plays exactly one clip", len(cli.played) == 1)
flat = [n for v in cli.played[0].values() for n in v]
check("wheel commands stripped (no DriveWheels/TurnInPlaceAtSpeed)", not {"DriveWheels", "TurnInPlaceAtSpeed", "AnimBody"} & set(flat))
check("head and lift commands kept", "AnimHead" in flat and "AnimLift" in flat)
check("lift lowered fully after a clip that raised it", lift_down(cli))
check("frame timing kept (all 3 keyframe times still present)", sorted(cli.played[0]) == [0, 100, 200])
check("self-caused taps suppressed while it plays", r._tap_suppress_until > time.monotonic())

# 2. go_to_sleep plays both sleep clips, in order.
r, cli = make()
order = []
orig = r.play_animation
r.play_animation = lambda n, **kw: (order.append(n), orig(n, **kw))[1]
r.go_to_sleep()
check("go_to_sleep plays the sleep clips in order", order == list(clips.SLEEP_CLIPS))
check("...as two plays", len(cli.played) == 2)

# 2b. The long "sleeping" loop is not waited on in full: shutdown carries on while it plays.
r, cli = make(fire_completion=False)
real._CLIP_COMPLETION_SLACK_S = 2.0   # no completion event arrives here, so each full wait costs this much
real._SLEEP_HOLD_S = 0.2
t0 = time.monotonic()
r.go_to_sleep()
took = time.monotonic() - t0
check(f"go_to_sleep: first clip waits in full, second only for the hold ({took:.1f}s; both in full would be ~4.8s)",
      2.0 < took < 3.6)
real._CLIP_COMPLETION_SLACK_S = 5.0
r, cli = make()
real._SLEEP_HOLD_S = 3.0
waited = []
orig_wait = r.play_animation
r.play_animation = lambda n, **kw: (waited.append((n, kw)), orig_wait(n, **kw))[1]
r.go_to_sleep()
check("only the last sleep clip is given a max wait",
      waited == [(clips.SLEEP_CLIPS[0], {}), (clips.SLEEP_CLIPS[1], {"max_wait_s": 3.0})])

# 3. Setting off -> built-in gestures, no clip.
r, cli = make(wake_sleep_clips=False)
r.wake_up(); r.go_to_sleep()
check("WAKE_SLEEP_CLIPS=false: no clip played", cli.played == [])
check("...gestures ran instead (head moved)", any(c[0] == "head" for c in cli.calls))

# 4. Assets not downloaded -> gestures.
r, cli = make(animations=False)
r.wake_up(); r.go_to_sleep()
check("no animation assets: no clip, gestures instead", cli.played == [] and any(c[0] == "head" for c in cli.calls))

# 5. A clip that fails falls back to the gesture instead of raising.
r, cli = make()
def boom(ppclip): raise ValueError("y1 must be greater than or equal to y0")
cli.play_anim_ppclip = boom
try:
    r.wake_up(); r.go_to_sleep(); ok = True
except Exception as e:
    ok = False
check("failing clip: wake_up/go_to_sleep don't raise", ok)
check("...and the gestures ran (head moved)", any(c[0] == "head" for c in cli.calls))

# 6. play_animation validation, groups, no-lift clips, and no hang if the robot never says 'completed'.
r, cli = make()
try:
    r.play_animation("nope"); raised = False
except ValueError:
    raised = True
check("unknown clip name raises ValueError", raised)
r.play_animation("group:Grp")
check("group: plays the chosen member's clip", len(cli.played) == 1)
r, cli = make()
r.play_animation("anim_no_lift")
check("clip without a lift track doesn't touch the lift", not lift_down(cli))
real._CLIP_COMPLETION_SLACK_S = 0.1
r, cli = make(fire_completion=False)
t0 = time.monotonic()
r.play_animation(clips.WAKE_CLIP)
check("no 'completed' event: gives up after clip length + slack instead of hanging", time.monotonic() - t0 < 2.0)

# 6b. Suppression covers the clip's real play length, not just its last keyframe.
r, cli = make()
cli._ppclips[clips.WAKE_CLIP] = pycozmo.anim.PreprocessedClip(keyframes=defaultdict(list, {t: [] for t in range(0, 3000, 33)}))
t0 = time.monotonic()
r.play_animation(clips.WAKE_CLIP)
real_len = clips.ppclip_duration_s(cli._ppclips[clips.WAKE_CLIP])
check(f"dense clip (keyframe every 33 ms to 3.0s) plays ~{real_len:.1f}s, about twice its timeline",
      5.5 < real_len < 6.5)
check("taps stay suppressed past the timeline's end (the bug: they were detected mid-clip)",
      r._tap_suppress_until - t0 > 5.5)

# 7. The arithmetic matches PyCozmo's own play_anim_ppclip() exactly.
from pycozmo import anim_encoder as ae
clip = ae.AnimClip(name="t", keyframes=[
    ae.AnimHeadAngle(trigger_time_ms=0, duration_ms=200, angle_deg=10),
    ae.AnimLiftHeight(trigger_time_ms=100, duration_ms=300, height_mm=40),
    ae.AnimBodyMotion(trigger_time_ms=250, duration_ms=400, radius_mm="STRAIGHT", speed=50),
    ae.AnimBackpackLights(trigger_time_ms=500, duration_ms=300),
    ae.AnimFaceAnimation(trigger_time_ms=2000, anim_name="x"),
    ae.AnimRobotAudio(trigger_time_ms=3000),
    ae.AnimEvent(trigger_time_ms=4000, event_id="e"),
] + [ae.AnimHeadAngle(trigger_time_ms=t, duration_ms=30, angle_deg=1) for t in range(900, 1800, 33)])
pp = pycozmo.anim.PreprocessedClip.from_anim_clip(clip)
frames = []
fake = NS(cancel_anim=lambda: None, _next_anim_id=0, anim_controller=NS(play_anim_frame=lambda *a: frames.append(a)))
pycozmo.Client.play_anim_ppclip(fake, pp)
check(f"ppclip_duration_s == frames PyCozmo really queues / 30 ({len(frames)} frames)",
      abs(clips.ppclip_duration_s(pp) - len(frames) / 30) < 1e-9)
check("clip_playback_seconds (no rendering) agrees", abs(clips.clip_playback_seconds(clip) - len(frames) / 30) < 1e-9)
check("...and is well over the last keyframe time (1.8s)", clips.ppclip_duration_s(pp) > 3.0)

# 7b. strip_wheels is idempotent.
c = fake_ppclip()
first, second = clips.strip_wheels(c), clips.strip_wheels(c)
check("strip_wheels removes 3 wheel commands, then nothing", (first, second) == (3, 0))

# 8. The Pillow workaround: negative eyelid bend used to raise.
from pycozmo.procedural_face import ProceduralFace
clips.patch_pillow_chord(); clips.patch_pillow_chord()
try:
    face = ProceduralFace()
    for lid in face.eyes[0].lids:
        lid.y, lid.bend = 0.5, -0.8
        lid.render(Image.new("1", (128, 32)))
    ok = True
except ValueError:
    ok = False
check("negative-bend eyelid renders after patch_pillow_chord()", ok)

# 9. Simulated backend: the defaults are the built-in gestures.
sim = SimulatedRobot()
ran = []
sim.run_gesture = lambda name, **kw: ran.append(name) or ""
sim.wake_up(); sim.go_to_sleep()
check("base wake_up/go_to_sleep run the wake_up and sleep gestures", ran == ["wake_up", "sleep"])

# 9b. connect(): the idle face (open eyes) stays off until the wake-up has played.
from fake_pycozmo import FakeCli
import dataclasses as _dc

events = []

class FakeClient(FakeCli):
    def __init__(self, **kw):
        super().__init__()
        self.face = kw.get("enable_procedural_face", True)
        events.append(("init", dict(kw)))
        names = (clips.WAKE_CLIP,) + clips.SLEEP_CLIPS
        self._clip_metadata = {n: NS(has_lift_height_track=False, has_backpack_lights_track=False, fspec="x") for n in names}
        self._clips, self._ppclips = {}, {n: fake_ppclip() for n in names}
        self.animation_groups = {}
        self.handlers = []
    def start(self): pass
    def connect(self): pass
    def wait_for_robot(self): pass
    def set_volume(self, v): pass
    def add_handler(self, evt, fn, one_shot=False): self.handlers.append(fn)
    def load_anims(self): pass
    def get_anim_names(self): return set(self._clip_metadata)
    def enable_procedural_face(self, on=True):
        self.face = on
        events.append(("face", on))
    def play_anim_ppclip(self, pp):
        events.append(("clip", "face_on" if self.face else "face_off"))
        for fn in self.handlers: fn(None)

from fake_pycozmo import PyCozmoRobot
real_client = pycozmo.Client
pycozmo.Client = FakeClient
try:
    for label, overrides in (("clips on", {}), ("clips off (gesture wake-up)", {"wake_sleep_clips": False})):
        events.clear()
        robot = PyCozmoRobot(_dc.replace(Settings(), **overrides))
        robot.connect()
        check(f"connect, {label}: client created with the idle face OFF", events[0] == ("init", {"enable_procedural_face": False}))
        check(f"connect, {label}: idle face switched back ON afterwards", events[-1] == ("face", True))
        if not overrides:
            check("connect, clips on: the wake-up clip played while the idle face was off", ("clip", "face_off") in events)
finally:
    pycozmo.Client = real_client

# 10. The model asking play_animation for a gesture ("dance") gets the gesture, not an error.
class R(SimulatedRobot):
    def show_expression(self, name, duration=None): pass
sr = R()
ran = []
sr.run_gesture = lambda name, **kw: ran.append(name) or "A quick wheel shimmy."
sr.run_gesture_async = sr.run_gesture
S = dataclasses.replace(Settings())
tools = {t.name: t for t in build_tools(sr, NS(synthesize=lambda t, p: p), NS(), S)}
res = tools["play_animation"].handler({"name": "group:dance"})
check("play_animation('group:dance') -> performs the dance gesture, ok result", res.ok and ran == ["dance"])
check("...and tells the model it was a gesture", "gesture" in res.message)
clip_name = sr.list_animations()[0]
res = tools["play_animation"].handler({"name": clip_name})
check("a real clip name still goes to play_animation", res.ok and clip_name in res.message and ran == ["dance"])

print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
