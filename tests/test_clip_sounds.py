"""Test: Cozmo's own sounds in the real clips (robot/clip_sounds.py) - the mu-law
frames, where each sound lands in a clip (PyCozmo plays dense clips slower than
their timeline), the modes and volume, mixing with speech, and the real robot
playing it. Uses synthetic sounds made here; the real ones are not in the repo."""
import dataclasses
import json
import logging
import random
import sys
import tempfile
import wave
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np

import _harness  # noqa: F401 - repo on sys.path, real .env ignored, CLIP_SOUNDS defaults off
from fake_pycozmo import make_robot

import pycozmo
from pycozmo import audio as pyaudio

from cozmo_brain.config import Settings
from cozmo_brain.robot import clips
from cozmo_brain.robot.clip_sounds import ClipSounds, packets_from_pcm, read_wav, ulaw_encode

logging.disable(logging.CRITICAL)
failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


pe = pycozmo.protocol_encoder

# --- 1. mu-law frames identical to PyCozmo's ------------------------------------------
vals = np.arange(-32768, 32768, dtype=np.int16)
mine = ulaw_encode(vals).tolist()
different = [(v, pyaudio.u_law_encoding(v), m) for v, m in zip(vals.tolist(), mine) if pyaudio.u_law_encoding(v) != m]
check(f"mu-law matches PyCozmo for every 16-bit value except its own overflow ({len(different)} values it would crash on)",
      all(ref == 256 and m == 255 for _, ref, m in different))

tmp = Path(tempfile.mkdtemp(prefix="smartcozmo_sounds_"))


def write_wav(path, x, rate=22050):
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def tone(freq, seconds, rate=22050, amp=0.5):
    t = np.arange(int(seconds * rate)) / rate
    return (np.sin(2 * np.pi * freq * t) * amp).astype(np.float32)


rng = np.random.default_rng(3)
for rate in (22050, 48000):
    pcm = (rng.standard_normal(rate * 2) * 3000).astype("<i2")
    p = tmp / f"noise{rate}.wav"
    write_wav(p, pcm.astype(np.float32) / 32768, rate)
    ref = pyaudio.load_wav(str(p))
    got = packets_from_pcm(read_wav(p)[0].__mul__(32768).astype("<i2"), rate)
    check(f"frames for a {rate} Hz WAV match pycozmo.audio.load_wav ({len(ref)} frames)",
          len(ref) == len(got) and all(bytes(a.samples) == bytes(b.samples) for a, b in zip(ref[:-1], got[:-1])))

# --- 2. a synthetic set of sounds and a manifest --------------------------------------
(tmp / "wav").mkdir()
write_wav(tmp / "wav" / "1.wav", tone(440, 0.30))   # an effect
write_wav(tmp / "wav" / "2.wav", tone(880, 0.30))   # a voice
write_wav(tmp / "wav" / "3.wav", tone(1320, 0.30, amp=0.25))  # the rarer variant of event 20 (half the level, to tell them apart)
CLIP = "anim_codelab_chicken_01"
manifest = {
    "rate": 22050,
    "events": {
        "10": {"name": "Play__Robot_Sfx__Scrn_Test", "sounds": [{"file": "wav/1.wav", "chance": 1.0}]},
        "20": {"name": "Play__Robot_VO__Test", "sounds": [{"file": "wav/2.wav", "chance": 0.9}, {"file": "wav/3.wav", "chance": 0.1}]},
    },
    "clips": {CLIP: [{"t_ms": 0, "event": 10, "volume": 1.0}, {"t_ms": 1500, "event": 20, "volume": 1.0}],
              "anim_silent": []},
}
(tmp / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def dense_ppclip(end_ms=3000):
    kf = defaultdict(list)
    for t in range(0, end_ms, 33):
        kf[t] = []
    return pycozmo.anim.PreprocessedClip(keyframes=kf)


def sparse_ppclip():
    kf = defaultdict(list)
    kf[0] = []; kf[100] = []; kf[400] = []
    return pycozmo.anim.PreprocessedClip(keyframes=kf)


check("CLIP_SOUNDS=off -> nothing loaded", ClipSounds.load(tmp, "off", 0.6) is None)
check("no manifest -> nothing loaded, no error", ClipSounds.load(tmp / "nope", "full", 0.6) is None)
check("a mode that is not off/effects/full -> off", ClipSounds.load(tmp, "loud", 0.6) is None)
(tmp / "bad").mkdir(); (tmp / "bad" / "manifest.json").write_text("{not json", encoding="utf-8")
check("a broken manifest -> nothing loaded, no error", ClipSounds.load(tmp / "bad", "full", 0.6) is None)
full = ClipSounds.load(tmp, "full", 0.6)
check("a good manifest loads", full is not None and full.has_sounds(CLIP) and not full.has_sounds("anim_silent")
      and not full.has_sounds("anim_unknown"))

# --- 3. where sounds land ---------------------------------------------------------------
pp = dense_ppclip()
tick_map = clips.key_tick_map(pp)
mix, rate = full.mix(CLIP, pp)
t_voice = clips.seconds_at(tick_map, 1500)
check(f"dense clip: a sound at timeline 1.5s lands at {t_voice:.2f}s of playback (PyCozmo plays it ~2x slower)", 2.9 < t_voice < 3.2)
active = np.flatnonzero(np.abs(mix) > 3000)
first_voice = active[active > int(2.0 * rate)][0] if (active > int(2.0 * rate)).any() else None
check("...and in the mix the sound starts there", first_voice is not None and abs(first_voice / rate - t_voice) < 0.005)
check("the first sound (timeline 0) starts at the beginning", abs(np.flatnonzero(np.abs(mix) > 3000)[0]) < 0.05 * rate)
check("volume (no speech): 0.6 of the source peak (0.5) = 0.3", abs(np.abs(mix[:int(0.3 * rate)]).max() / 32767 - 0.3) < 0.01)
quiet_under = ClipSounds.load(tmp, "full", 0.6, 0.2)
sp = (np.zeros(int(4.0 * rate), dtype=np.float32), rate)  # silent "speech": only the sounds show
under, _ = quiet_under.mix(CLIP, pp, sp)
alone, _ = quiet_under.mix(CLIP, pp)
check("volume under speech uses CLIP_SOUND_SPEECH_VOLUME (0.2 -> 0.1), alone still CLIP_SOUND_VOLUME (0.6 -> 0.3)",
      abs(np.abs(under[:int(0.3 * rate)]).max() / 32767 - 0.1) < 0.01 and abs(np.abs(alone[:int(0.3 * rate)]).max() / 32767 - 0.3) < 0.01)
check("with no separate speech volume given, it follows the main one", ClipSounds.load(tmp, "full", 0.5).speech_volume == 0.5)

sparse = sparse_ppclip()
mix_s, _ = full.mix(CLIP, sparse)
t_s = clips.seconds_at(clips.key_tick_map(sparse), 1500)
check("sparse clip: past its last keyframe the sound lands at real-time scale", t_s > 1.5 and len(mix_s) >= int((t_s + 0.3) * rate) - 2)

# --- 4. modes, speech, volume, limiter ----------------------------------------------------
effects = ClipSounds.load(tmp, "effects", 0.6)
speech = (tone(300, 4.0, amp=0.4), 22050)
mixed, _ = effects.mix(CLIP, pp, speech)
voice_start = int(t_voice * 22050)
region = mixed[voice_start:voice_start + int(0.2 * 22050)]
plain_speech = (speech[0][voice_start:voice_start + int(0.2 * 22050)] * 32767).astype("<i2")
check("effects + speech: his voice sounds are left out, the blips stay",
      np.abs(region.astype(int) - plain_speech.astype(int)).max() < 50 and np.abs(mixed[:int(0.3 * 22050)] - (speech[0][:int(0.3 * 22050)] * 32767).astype("<i2")).max() > 3000)
nospeech, _ = effects.mix(CLIP, pp)
check("effects, NO speech: everything plays (nothing for his voice to talk over)", np.abs(nospeech).max() > 0
      and (np.abs(nospeech[int(t_voice * 22050):int(t_voice * 22050) + 2000]) > 3000).any())

with_speech, _ = full.mix(CLIP, pp, speech)
check("full + speech: the speech is there from the first sample, the sounds on top", with_speech[1000] != 0 and len(with_speech) >= len(speech[0]))

speech48 = (tone(300, 4.0, rate=48000, amp=0.4), 48000)
m48, r48 = full.mix(CLIP, pp, speech48)
plain48 = (speech48[0] * 32767).astype("<i2")
changed = np.flatnonzero(np.abs(m48[:len(plain48)].astype(int) - plain48.astype(int)) > 3000)
voice_changed = changed[changed > int(2.0 * 48000)]
check("speech at 48000 Hz: the mix is at 48000 Hz and the voice sound still lands at the right second",
      r48 == 48000 and len(voice_changed) and abs(voice_changed[0] / 48000 - t_voice) < 0.01)

loud = (tone(300, 2.0, amp=0.95), 22050)
mixed_loud, _ = full.mix(CLIP, sparse_ppclip(), loud)
check("a mix that would clip is scaled down, not distorted", np.abs(mixed_loud).max() / 32767 <= 0.981)

quiet = ClipSounds.load(tmp, "full", 0.0)
q = quiet.mix(CLIP, pp)
check("volume 0 -> silent mix (or none)", q is None or np.abs(q[0]).max() == 0)

# weights: variant 2.wav 90%, 3.wav 10%
seeded = ClipSounds(tmp, manifest, "full", 0.6, rng=random.Random(5))
counts = {"common": 0, "rare": 0}
for _ in range(1000):
    x = seeded._sound("20", 22050)
    counts["common" if abs(np.abs(x).max() - 0.5) < 0.01 else "rare"] += 1
check(f"variants are picked by their chance (90/10 -> {counts})", 850 < counts["common"] < 950 and 50 < counts["rare"] < 150)
check("an event with no sounds adds nothing", seeded._sound("99", 22050) is None)

# --- 5. the real robot plays them ----------------------------------------------------------------
real_ppclip = lambda: pycozmo.anim.PreprocessedClip(keyframes=defaultdict(list, {t: [] for t in range(0, 600, 33)}))


def make(**overrides):
    r, cli = make_robot(**{"clip_sounds": "full", "clip_sounds_dir": str(tmp), **overrides})
    r._animations_loaded = True
    cli._clip_metadata = {CLIP: NS(has_lift_height_track=False, has_backpack_lights_track=False, fspec="x"),
                          "anim_silent": NS(has_lift_height_track=False, has_backpack_lights_track=False, fspec="x")}
    cli._clips = {}
    cli._ppclips = {CLIP: real_ppclip(), "anim_silent": real_ppclip()}
    cli.get_anim_names = lambda: set(cli._clip_metadata)
    cli.animation_groups = {}
    cli.log = []
    cli.handlers = defaultdict(list)
    cli.add_handler = lambda evt, fn, one_shot=False: cli.handlers[evt].append(fn)
    cli.cancel_anim = lambda: cli.log.append(("cancel",))
    cli._next_anim_id = 0
    cli.anim_controller = NS(play_anim_frame=lambda audio, image, pkts: cli.log.append(("frame", audio, image, pkts)),
                             play_audio=lambda pkts: cli.log.append(("audio", len(pkts))))
    cli.play_audio = lambda path: cli.log.append(("play_audio", path))
    cli.play_anim_ppclip = lambda pp: cli.log.append(("play_anim_ppclip",))
    r._clip_sounds = ClipSounds.load(r._settings.clip_sounds_dir, r._settings.clip_sounds, r._settings.clip_sound_volume)
    return r, cli


import cozmo_brain.robot.real as real
real._CLIP_COMPLETION_SLACK_S = 0.2
real._CLIP_END_GRACE_S = 0.2
real._CLIP_TAIL_S = 0.05

r, cli = make()
r.play_animation(CLIP)
frames = [e for e in cli.log if e[0] == "frame"]
check("play_animation of a clip with sounds goes through the audio path, audio in its frames",
      frames and any(f[1] is not None for f in frames) and not any(e[0] == "play_anim_ppclip" for e in cli.log))
check("...and the first sound is in the first frame", frames[0][1] is not None)

r, cli = make()
r.play_animation("anim_silent")
check("a clip with no sounds plays as before (no audio)", any(e[0] == "play_anim_ppclip" for e in cli.log)
      and not any(e[0] == "frame" for e in cli.log))

wav = tmp / "speech.wav"
write_wav(wav, tone(300, 1.5, amp=0.4))
r, cli = make()
r.say_wav_with_clip(str(wav), CLIP)
audio_frames = [f for f in cli.log if f[0] == "frame" and f[1] is not None]
check("say_wav_with_clip: speech and sounds go out together in the clip's frames", len(audio_frames) > 20)
check("...speech longer than the clip: the rest is queued after it", any(e[0] == "audio" and e[1] > 0 for e in cli.log))

r, cli = make(clip_sounds="off")
r.say_wav_with_clip(str(wav), CLIP)
check("CLIP_SOUNDS=off: speech merged exactly as before, no sound mixing", r._clip_sounds is None
      and any(f[0] == "frame" and f[1] is not None for f in cli.log))

r, cli = make(clip_sounds_dir=str(tmp / "missing"))
r.play_animation(CLIP)
check("sounds folder missing: the clip plays silent, no error", any(e[0] == "play_anim_ppclip" for e in cli.log))

(tmp / "wav" / "1.wav").write_bytes(b"not a wav")
r, cli = make()
r._clip_sounds._cache.clear()
try:
    r.play_animation(CLIP); ok = True
except Exception:
    ok = False
check("a corrupt sound file never costs the clip", ok and any(e[0] in ("play_anim_ppclip", "frame") for e in cli.log))

print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
