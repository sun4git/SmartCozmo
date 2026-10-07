# Audio output and voice

[← Back to the README](../README.md)


## Routing speech to a real speaker (noisy environments)

Cozmo's own speaker is small and quiet even with the `TTS_GAIN` fix — in a
noisy room, it may just not be audible. `AUDIO_OUTPUT` in `.env` controls
where `say()`'s speech actually goes:

- `cozmo` (default) — through Cozmo's own speaker only, as before.
- `system` — through this machine's own audio output instead (e.g. the same
  Bluetooth speaker already paired for the mic), via `aplay`.
- `both` — through both **simultaneously**, not one after the other (a
  background thread drives system audio while the main thread drives
  Cozmo's speaker, then both are waited on) — so Cozmo still visibly
  "performs" the line while it's also actually intelligible.

This is a tool-level decision (`handle_say` in `tools/registry.py`), not
part of the `RobotBackend` interface — playing a WAV file on this machine
isn't a robot capability, so it doesn't belong behind that abstraction.
`PLAYBACK_DEVICE` (default `pipewire`) is the ALSA/PipeWire device name for
the `system`/`both` cases; run `aplay -L` to list options.

### Where the clip sounds go

`AUDIO_OUTPUT` routes speech only. The real animation clips, and their own
sounds ([clip-sounds.md](clip-sounds.md)), always play on Cozmo: his face,
head, lift and his speaker.

| `AUDIO_OUTPUT` | Speech | Clip sounds | Clip sound level while he speaks |
|---|---|---|---|
| `cozmo` | Cozmo's speaker | Cozmo's speaker, mixed under the speech | `CLIP_SOUND_SPEECH_VOLUME` (0.15) |
| `system` | This machine's speaker only | Cozmo's speaker, beside the speech | `CLIP_SOUND_VOLUME` (0.5) |
| `both` | Both at once | Cozmo's speaker, mixed under his copy of the speech | `CLIP_SOUND_SPEECH_VOLUME` (0.15) |

With `system`, the clip plays without the speech, so it uses the louder
`CLIP_SOUND_VOLUME`. Next to the speech it can sound louder than you're used
to with `cozmo`. Lower `CLIP_SOUND_VOLUME` if it does. That also makes the
wake-up, sleep and standalone clips quieter. Clips that play with no speech
(wake-up, sleep, a clip on its own) use `CLIP_SOUND_VOLUME` in every mode.

## Making the voice sound less generic

Orpheus's voices are generic human voices, not Cozmo's real (small, squeaky,
quasi-robotic) one — that voice processing is Anki's proprietary and
unavailable here. `cozmo_brain/llm/groq_client.py` applies two cheap,
well-known DSP tricks to the raw PCM instead. Defaults below were picked by
generating sample WAVs at several settings and listening on real hardware —
not a faithful recreation of Cozmo's actual voice (the ring-mod effect alone
doesn't really read as "Cozmo"), just the best cheap approximation found so
far. Adjust further in `.env` to taste:

- **`TTS_PITCH_SHIFT`** (default `1.10`) — a "chipmunk" resample trick that
  raises pitch and speeds up playback together, reading as smaller/more
  childlike/robotic. `1.0` disables it.
- **`TTS_ROBOT_MOD_DEPTH`** (default `0.25`) — ring modulation: mixes in a
  fixed-frequency carrier tone (`TTS_ROBOT_MOD_HZ`, default 35Hz) for a
  metallic timbre, the classic cheap sci-fi robot-voice effect. `0.0` disables it.

`TTS_GAIN` (Cozmo's speaker is quiet even at max hardware volume) has gone
through two real fixes so far. First: it used to be a blind fixed
multiplier, which at its default of `3.0` was clipping about 0.5% of
samples — audible as harsh crackle, not the intended "louder" effect —
because Orpheus's raw output already peaks around 60% of full scale. Made
peak-normalizing: a ceiling, automatically backed off to land just under
clipping (~95% of full scale) instead of blowing past it.

**Second fix, found by comparing OpenAI's output to Groq's directly**:
peak-normalizing two voices to the *same peak* doesn't make them *equally
loud*. Measured directly: OpenAI's default voice sits around an 8:1
peak-to-RMS ratio (crest factor) — peakier, mostly quiet with occasional
spikes — clearly higher than Groq's Orpheus, so matching their peaks left
OpenAI sounding quieter overall despite an identical peak. RMS (average
loudness), not the single loudest instant, is what's actually perceived as
volume, and a linear gain can't fix a crest-factor mismatch by itself — it
scales peak and RMS by the same factor. `_normalize_gain` now targets RMS
directly, with a `tanh` soft limiter (compresses samples smoothly as they
approach full scale, rather than a hard peak ceiling) protecting against
overflow instead. This made a higher `TTS_GAIN` ceiling safe where it
wasn't before — verified directly: even at `TTS_GAIN=4.0` (the new
default, up from `3.0`), zero samples come within 90% of full scale on
real OpenAI output.
