"""Test: the curated real clips by friendly name (robot/curated.py) - how `say`
and `gesture` use them, the settings that switch them off (CLIPS_ENABLED,
CLIP_SPEECH_OVERLAP), and the clip-plus-speech player on a fake pycozmo client
(tests/fake_pycozmo.py). No robot and no downloaded animation assets needed."""
import csv
import dataclasses
import logging
import os
import sys
import time
import wave
from collections import defaultdict
from types import SimpleNamespace as NS

import _harness  # noqa: F401 - repo on sys.path, real .env ignored
from fake_pycozmo import make_robot

import pycozmo

from cozmo_brain.config import Settings
from cozmo_brain.personality import build_system_prompt, clips_prompt_section
from cozmo_brain.robot import clips, curated, real
from cozmo_brain.robot.curated import CURATED, model_clips
from cozmo_brain.robot.gestures import GESTURES
from cozmo_brain.robot.simulated import SimulatedRobot
from cozmo_brain.tools import build_tools

logging.disable(logging.CRITICAL)
failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


pe = pycozmo.protocol_encoder

# --- 1. the table itself -----------------------------------------------------
check("no curated name shadows a built-in gesture", not set(CURATED) & set(GESTURES))
check("the model is offered the short clips only for now - not the long, cue or idle ones",
      {c.tier for c in model_clips().values()} == {"short"} and "dance_mambo" not in model_clips()
      and "eyes_on" not in model_clips())
check("long clips are the 12s+ ones", all(c.seconds >= 12 for c in CURATED.values() if c.tier == "long")
      and all(c.seconds < 8 for c in CURATED.values() if c.tier == "short"))
clips_tsv = os.path.join(os.path.dirname(__file__), "..", "data", "animations_clips.tsv")
if os.path.exists(clips_tsv):  # data/ is not in the repo - only checked where the dump has been run
    rows = {r["name"]: r for r in csv.DictReader(open(clips_tsv, encoding="utf-8"), delimiter="\t")}
    bad = [n for n, c in CURATED.items() if c.clip not in rows
           or abs(float(rows[c.clip]["duration_s"]) - c.seconds) > 0.06
           or not any(rows[c.clip][k] == "1" for k in ("head", "lift", "lights", "face"))]
    check(f"every curated clip exists, has its listed length, and plays something with wheels stripped (bad: {bad})", not bad)
else:
    print("SKIP curated clips vs data/animations_clips.tsv (not present)")


# --- 2. the real robot's clip-plus-speech player -------------------------------
def fake_ppclip():
    kf = defaultdict(list)
    kf[0] = [pe.AnimHead(duration_ms=100, variability_deg=0, angle_deg=10), pe.DriveWheels(lwheel_speed_mmps=50, rwheel_speed_mmps=50),
             pe.AnimBackpackLights(colors=(1, 1, 1, 1, 1))]
    kf[100] = [pe.AnimLift(duration_ms=100, variability_mm=0, height_mm=40)]
    kf[400] = [pe.DisplayImage(image=b"\x3f\x3f")]
    return pycozmo.anim.PreprocessedClip(keyframes=kf)


def make(**overrides):
    r, cli = make_robot(**overrides)
    r._animations_loaded = True
    cli._clip_metadata = {c.clip: NS(has_lift_height_track=True, has_backpack_lights_track=True, fspec="x") for c in CURATED.values()}
    cli._clips = {}
    cli._ppclips = {c.clip: fake_ppclip() for c in CURATED.values()}
    cli.get_anim_names = lambda: set(cli._clip_metadata)
    cli.animation_groups = {}
    cli.log = []              # everything sent to the animation controller, in order
    cli.handlers = defaultdict(list)
    cli.add_handler = lambda evt, fn, one_shot=False: cli.handlers[evt].append(fn)
    cli.cancel_anim = lambda: cli.log.append(("cancel",))
    cli._next_anim_id = 0
    cli.anim_controller = NS(
        play_anim_frame=lambda audio, image, pkts: cli.log.append(("frame", audio, image, pkts)),
        play_audio=lambda pkts: cli.log.append(("audio", len(pkts))),
    )
    cli.play_audio = lambda path: cli.log.append(("play_audio", path))
    cli.wait_for = lambda evt, timeout=None: None

    def play_anim_ppclip(pp):
        cli.log.append(("play_anim_ppclip",))
        for fn in cli.handlers[pycozmo.event.EvtAnimationCompleted]:
            fn(None)
    cli.play_anim_ppclip = play_anim_ppclip
    return r, cli


def fire_when_frames_queued(cli):
    """Complete the clip and the audio as soon as the player has queued everything."""
    orig = cli.anim_controller.play_anim_frame

    def frame(audio, image, pkts):
        orig(audio, image, pkts)
        if pkts and any(isinstance(p, pe.EndAnimation) for p in pkts):
            for evt in (pycozmo.event.EvtAnimationCompleted, pycozmo.event.EvtAudioCompleted):
                for fn in cli.handlers[evt]:
                    fn(None)
    cli.anim_controller.play_anim_frame = frame


def make_wav(path, seconds):
    with wave.open(path, "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
        w.writeframes(b"\x10\x00" * int(22050 * seconds))
    return path


real._CLIP_COMPLETION_SLACK_S = 0.3
wav_short = make_wav(_harness.tmp("speech_short.wav"), 0.5)
wav_long = make_wav(_harness.tmp("speech_long.wav"), 6.0)
chicken = CURATED["chicken"].clip

r, cli = make()
fire_when_frames_queued(cli)
r.say_wav_with_clip(wav_long, chicken)
frames = [e for e in cli.log if e[0] == "frame"]
with_audio = [i for i, e in enumerate(frames) if e[1] is not None]
check("overlap: the speech sits in the clip's FIRST frames (starts with it)", with_audio and with_audio[0] == 0)
check("overlap: nothing is queued to the audio queue before the clip starts (no cut-off)",
      cli.log[0] == ("cancel",) and not any(e[0] in ("play_audio",) for e in cli.log))
check("overlap: speech longer than the clip -> the rest is queued after it",
      any(e[0] == "audio" and e[1] > 0 for e in cli.log))
flat = [type(p).__name__ for e in frames for p in (e[3] or ())]
check("overlap: wheel commands are stripped from the clip", "DriveWheels" not in flat)
check("overlap: the clip's own backpack-light commands are stripped (the app owns the light)", "AnimBackpackLights" not in flat)
check("overlap: lift lowered afterwards (the clip raised it)", ("lift", 0.0) in cli.calls)
check("overlap: self-caused taps suppressed for the clip", r._tap_suppress_until > time.monotonic())

r, cli = make()
fire_when_frames_queued(cli)
r.say_wav_with_clip(wav_short, chicken)
frames = [e for e in cli.log if e[0] == "frame"]
n_audio = sum(1 for e in frames if e[1] is not None)
check(f"speech shorter than the clip -> audio only in the first {n_audio} frames, silence after, nothing queued extra",
      0 < n_audio < len(frames) and not any(e[0] == "audio" for e in cli.log))

# Seen on the robot: with speech merged in, the robot's "animation completed" event did not
# always arrive, and every reply then waited out clip length + the full slack (9+ s).
# Merged playback must go by the clock (expected length + a short grace) instead.
real._CLIP_COMPLETION_SLACK_S = 5.0
real._CLIP_END_GRACE_S = 0.2
r, cli = make()          # no events are ever fired
t0 = time.monotonic()
r.say_wav_with_clip(wav_short, chicken)
took = time.monotonic() - t0
check(f"merged clip, completion events never arrive: returns after ~expected length, not the 5s slack ({took:.1f}s)", took < 2.5)
check("...and still lowers the lift afterwards", ("lift", 0.0) in cli.calls)
real._CLIP_COMPLETION_SLACK_S = 0.3
real._CLIP_END_GRACE_S = 1.0

# A clip's own lights track overwrites the backpack light and switches it off at its end. Seen on the
# robot: the blinking "listening" light never came back after such a clip. It must be re-sent.
r, cli = make()
fire_when_frames_queued(cli)
r._listening_light = True
r._light_color = "off"
cli.calls.clear()
r.say_wav_with_clip(wav_short, chicken)
lights = [c for c in cli.calls if c[0] == "lights"]
check("clip with a lights track: the listening blink is re-sent afterwards",
      lights and lights[-1][3] == real._BLINK_ON_FRAMES and lights[-1][4] == real._BLINK_OFF_FRAMES)
cli._clip_metadata[chicken].has_backpack_lights_track = False
cli.calls.clear(); cli.log.clear()
r.say_wav_with_clip(wav_short, chicken)
check("clip without a lights track: lights are left alone", not [c for c in cli.calls if c[0] == "lights"])

# Seen after that: with speech merged in, only the AUDIO-completed event arrives (right at the end of
# the speech); the animation-completed one never does. The reply must not sit out the grace for it.
real._CLIP_END_GRACE_S = 3.0
real._CLIP_TAIL_S = 0.1
r, cli = make()
speech_len = len(__import__("pycozmo").audio.load_wav(wav_short)) / 30.0
import threading as _th
orig_add = cli.add_handler
def add(evt, fn, one_shot=False):
    orig_add(evt, fn, one_shot)
    if evt is pycozmo.event.EvtAudioCompleted:
        _th.Timer(speech_len, lambda: fn(None)).start()
cli.add_handler = add
t0 = time.monotonic()
r.say_wav_with_clip(wav_short, chicken)
took = time.monotonic() - t0
expected = max(clips.ppclip_duration_s(cli._ppclips[chicken]), speech_len)
check(f"merged clip, only the audio-completed event arrives: done at ~the clip/speech end, not the 3s grace ({took:.1f}s, expected {expected:.1f}s)",
      took < expected + 1.0)
real._CLIP_END_GRACE_S = 1.0
real._CLIP_TAIL_S = 0.25

# The robot never reports a merged clip as ended: end it explicitly (and the lights come back after).
r, cli = make()
fire_when_frames_queued(cli)
cancels_before = [e for e in cli.log if e == ("cancel",)]
r.say_wav_with_clip(wav_short, chicken)
check("merged clip, event seen: no extra EndAnimation needed", [e for e in cli.log if e == ("cancel",)] == cancels_before + [("cancel",)])
r, cli = make()          # no animation-completed event ever
real._CLIP_END_GRACE_S = 0.2   # (no audio event either here, so the grace is waited out)
t0 = time.monotonic()
r.say_wav_with_clip(wav_short, chicken)
took = time.monotonic() - t0
real._CLIP_END_GRACE_S = 1.0
cancels = [i for i, e in enumerate(cli.log) if e == ("cancel",)]
check("merged clip, animation event never seen: the animation is ended explicitly after playing",
      len(cancels) == 2 and cancels[-1] > max(i for i, e in enumerate(cli.log) if e[0] == "frame"))
check(f"...without waiting for the robot's confirmation ({took:.1f}s total, ~0.6s clip + 0.2s grace + 0.4s lift)", took < 1.7)

# The robot's own IS_ANIMATING status after a merged clip decides what is done to settle it.
ANIMATING = pycozmo.RobotStatusFlag.IS_ANIMATING
real._CLIP_END_GRACE_S = 0.2


def settled(still_animating_after_end, reset_clears):
    """Run a merged clip with the robot reporting it is animating; returns (empty clips played, final flag)."""
    r, cli = make()
    r._latest_status = ANIMATING
    played = []
    orig_cancel = cli.cancel_anim

    def cancel():
        orig_cancel()
        if not still_animating_after_end:
            r._latest_status = 0
    cli.cancel_anim = cancel

    def play_plain(pp):
        cli.log.append(("play_anim_ppclip",))
        played.append(sorted(pp.keyframes))
        if reset_clears:
            r._latest_status = 0
        for fn in cli.handlers[pycozmo.event.EvtAnimationCompleted]:
            fn(None)
    cli.play_anim_ppclip = play_plain
    r.say_wav_with_clip(wav_short, chicken)
    return played, bool(r._latest_status & ANIMATING)


played, flag = settled(still_animating_after_end=False, reset_clears=True)
check("robot stops animating after EndAnimation: nothing more is done", played == [] and not flag)
played, flag = settled(still_animating_after_end=True, reset_clears=True)
check("robot still animating after EndAnimation: an empty clip is played to close the cycle, and it clears", played == [[0]] and not flag)
played, flag = settled(still_animating_after_end=True, reset_clears=False)
check("...and if that doesn't clear it either, it is not retried forever", len(played) == 1 and flag)
r, cli = make()
r._latest_status = 0
r.say_wav_with_clip(wav_short, chicken)
check("robot never reports animating: no empty clip", not any(e == ("play_anim_ppclip",) for e in cli.log))
real._CLIP_END_GRACE_S = 1.0

# overlap off: the speech is spoken normally, THEN the clip
r, cli = make(clip_speech_overlap=False)
r.say_wav_with_clip(wav_short, chicken)
order = [e[0] for e in cli.log]
check("CLIP_SPEECH_OVERLAP=false: speech first, then the clip plays on its own",
      order.index("play_audio") < order.index("play_anim_ppclip") and "frame" not in order)

# a clip that can't be prepared never costs the speech
r, cli = make()
cli._ppclips.pop(chicken); cli._clip_metadata.pop(chicken)
r.say_wav_with_clip(wav_short, chicken)
check("unknown/unpreparable clip: the speech is still spoken", ("play_audio", wav_short) in cli.log)
r, cli = make(clips_enabled=False)
r.say_wav_with_clip(wav_short, chicken)
check("CLIPS_ENABLED=false: speech only", ("play_audio", wav_short) in cli.log and not any(e[0] == "frame" for e in cli.log))
check("CLIPS_ENABLED=false: clips_available() is False", not r.clips_available())
cli.calls.clear(); cli.log.clear()
r.wake_up(); r.go_to_sleep()
check("CLIPS_ENABLED=false: wake-up and go-to-sleep fall back to the gestures",
      not any(e[0] == "frame" for e in cli.log) and any(c[0] == "head" for c in cli.calls))

# prewarm: prepares every curated clip, off the calling thread, without error
r, cli = make()
prepared = []
r._prepared_clip = lambda clip: prepared.append(clip)
r.prewarm_clips([c.clip for c in CURATED.values()])
time.sleep(2.0)
check("prewarm prepares the curated clips in the background", len(prepared) == len(CURATED))

# --- 3. the tools --------------------------------------------------------------
class Sim(SimulatedRobot):
    def __init__(self):
        super().__init__()
        self.events = []
        self.available = True
    def show_expression(self, name, duration=None): pass
    def clips_available(self): return self.available
    def say_wav(self, path): self.events.append(("say_wav", path))
    def say_wav_with_clip(self, path, clip): self.events.append(("say_with_clip", clip))
    def play_animation(self, name, **kw): self.events.append(("play", name))
    def run_gesture(self, name, **kw):
        if name not in GESTURES:
            raise ValueError(f"Unknown gesture '{name}'. Known gestures: {', '.join(sorted(GESTURES))}")
        self.events.append(("gesture", name)); return "desc"
    def run_gesture_async(self, name):
        if name not in GESTURES:
            raise ValueError(f"Unknown gesture '{name}'. Known gestures: {', '.join(sorted(GESTURES))}")
        self.events.append(("gesture_async", name)); return "desc"


class Speech:
    def synthesize(self, text, path): return "/tmp/x.wav"


def tools_for(robot, **overrides):
    S = dataclasses.replace(Settings(), **{"audio_output": "cozmo", **overrides})
    return {t.name: t for t in build_tools(robot, Speech(), NS(), S)}, S


sim = Sim()
tools, S = tools_for(sim)
say_enum = tools["say"].parameters["properties"]["gesture"]["enum"]
check("say/gesture offer the curated names next to the built-in gestures",
      set(GESTURES) <= set(say_enum) and set(model_clips()) <= set(say_enum) and "eyes_on" not in say_enum)
check("...with a description of each", "chicken: Struts and clucks" in tools["say"].parameters["properties"]["gesture"]["description"])

check("the long clips are not in the enum or the prompt while MODEL_TIERS is short-only",
      "dance_mambo" not in say_enum and "dance_mambo" not in clips_prompt_section())
res = tools["say"].handler({"text": "Cluck!", "mood": "happy", "gesture": "chicken"})
check("say(gesture=clip) -> speech merged with the clip", res.ok and ("say_with_clip", chicken) in sim.events
      and not any(e[0] in ("gesture", "gesture_async") for e in sim.events) and "chicken" in res.message)
sim.events.clear()
res = tools["say"].handler({"text": "Hi", "mood": "happy", "gesture": "cheer"})
check("say(gesture=built-in) still runs the gesture, speech as before",
      ("gesture_async", "cheer") in sim.events and ("say_wav", "/tmp/x.wav") in sim.events
      and not any(e[0] == "say_with_clip" for e in sim.events))

# --- the long tier, switched on (it's built but off by default) ---
curated.MODEL_TIERS = ("short", "long")
tools_long, _ = tools_for(sim)
check("with the long tier switched on, the long clips are offered and described",
      "dance_mambo" in tools_long["say"].parameters["properties"]["gesture"]["enum"]
      and "dance_mambo" in clips_prompt_section() and "asked for that" in clips_prompt_section())
tools = tools_long
sim.events.clear()
res = tools["say"].handler({"text": "Mambo time!", "mood": "excited", "gesture": "dance_mambo"})
check("a long clip is fine inside say", res.ok and ("say_with_clip", CURATED["dance_mambo"].clip) in sim.events)

sim.events.clear()
res = tools["gesture"].handler({"name": "dance_mambo"})
check("a long clip as a standalone gesture is refused, with the reason", not res.ok and "say" in res.message and sim.events == [])
res = tools["gesture"].handler({"name": "yes"})
check("a short clip as a standalone gesture plays, blocking", res.ok and ("play", CURATED["yes"].clip) in sim.events)
sim.events.clear()
res = tools["play_animation"].handler({"name": "group:chicken"})
check("play_animation('chicken') (a friendly name) works too", res.ok and ("play", chicken) in sim.events)
sim.events.clear()
res = tools["play_animation"].handler({"name": "group:dance"})
check("play_animation('group:dance') (a gesture) still performs the gesture", res.ok and ("gesture_async", "dance") in sim.events)

curated.MODEL_TIERS = ("short",)  # back to the default

# clips not available right now (resources missing): speech still happens
sim.available = False
sim.events.clear()
res = tools["say"].handler({"text": "Cluck!", "mood": "happy", "gesture": "chicken"})
check("clips unavailable: say still speaks, tells the model the clip was skipped",
      res.ok and ("say_wav", "/tmp/x.wav") in sim.events and "unavailable" in res.message)
res = tools["gesture"].handler({"name": "yes"})
check("clips unavailable: standalone clip reports it didn't play", not res.ok)

# A made-up gesture name must not cost the whole reply (seen: gesture='embarrassed').
sim.available = True
sim.events.clear()
res = tools["say"].handler({"text": "Oops!", "mood": "embarrassed", "gesture": "embarrassed"})
check("say with an unknown gesture still speaks, and says the gesture was skipped",
      res.ok and ("say_wav", "/tmp/x.wav") in sim.events and "no gesture" in res.message)

# CLIPS_ENABLED=false: none of it is offered
sim2 = Sim()
tools2, S2 = tools_for(sim2, clips_enabled=False)
check("CLIPS_ENABLED=false: only the built-in gestures are offered",
      tools2["say"].parameters["properties"]["gesture"]["enum"] == sorted(GESTURES))
check("CLIPS_ENABLED=false: play_animation and list_animations are not offered",
      "play_animation" not in tools2 and "list_animations" not in tools2)
check("CLIPS_ENABLED=false: a clip name in say is ignored, not an error", tools2["say"].handler({"text": "x", "gesture": "chicken"}).ok)

# system-audio output: the clip runs beside the speech on its own thread
sim3 = Sim()
import cozmo_brain.tools.registry as registry
played = []
registry.play_wav = lambda path, dev: (time.sleep(0.2), played.append("wav"))[1]
tools3, S3 = tools_for(sim3, audio_output="system")
tools3["say"].handler({"text": "Hi", "mood": "happy", "gesture": "chicken"})
check("AUDIO_OUTPUT=system: speech on the Pi's speaker, clip plays alongside", played == ["wav"] and ("play", chicken) in sim3.events)

# --- 4. the prompt -------------------------------------------------------------
section = clips_prompt_section()
check("prompt section names the clips and says to use them while speaking",
      "chicken" in section and "WHILE you speak" in section)
check("section included in the system prompt when given, absent otherwise",
      section in build_system_prompt("x", clips_section=section) and "chicken" not in build_system_prompt("x"))

print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
