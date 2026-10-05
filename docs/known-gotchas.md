# Known gotchas

[← Back to the README](../README.md)


- **`ModuleNotFoundError: No module named 'pkg_resources'` in `--mode vad`**
  (or a `pkg_resources is deprecated` warning) means the original, abandoned
  `webrtcvad` package is installed, not `webrtcvad-wheels`. Swap it - see
  [step 1](setup.md#1-python-environment-on-the-deployment-machine) above.
- **Cozmo's AP is flaky to scan.** If `nmcli dev wifi list` doesn't show him
  even though his face shows credentials, rescan and try again before
  assuming anything is broken.
- **Cozmo auto-powers-off** fairly quickly off the charger (small/aged
  battery). Development goes smoother with him on the charger — PyCozmo can
  fully control him while charging. `BatteryMonitor` (see
  [Battery monitor](connection-and-battery.md#battery-monitor)) shows a face warning a few seconds
  before this happens instead of him just going dark unannounced, but the
  underlying power-off itself isn't something this app can prevent.
- **Wi-Fi and Bluetooth typically share radio hardware** on small boards like
  a Pi. Running Cozmo's Wi-Fi link and a Bluetooth mic simultaneously is a
  plausible source of audio glitches if you see flakiness — not confirmed as
  an actual problem yet, but the first suspect.
- **`nmcli radio`/`connection` commands may need `sudo`** if polkit isn't
  configured for passwordless user access — avoids re-debugging "Not
  authorized" errors.
- **The real animation API has been found** (`cli.play_anim` /
  `cli.play_anim_group`, backed by `cli.load_anims()` /
  `cli.get_anim_names()`) — see [cozmo_brain/robot/real.py](../cozmo_brain/robot/real.py).
  It requires `pycozmo_resources.py download` to have been run; without it,
  `play_animation`/`list_animations` degrade to an empty list rather than
  erroring. PyCozmo's `expressions` module (Anger, Happiness, Surprise,
  etc.) is a separate, always-available system — procedural faces, not
  animation clips — and is what the mood/gesture library is built on.
- **`orchestrator.py`'s `turn()` is still an uncalibrated placeholder** — it
  approximates degrees using a guessed `seconds_per_degree` constant.
  `cozmo_brain/`'s equivalent is the same formula but centralized in config
  and measurable with `python -m cozmo_brain --mode calibrate`.
- **`cli.drive_wheels(..., duration=...)` silently ignores `duration`** in
  this PyCozmo version — see [Real bugs this uncovered](hardware-findings.md#real-bugs-this-uncovered)
  above. Any new timed movement needs to sleep + `stop_all_motors()` itself.
- **Face/expression images must be rendered at 128x32, not PyCozmo's own
  128x64 default** — confirmed on real hardware (`say`/`gesture`/moods all
  failed until fixed). See [Real bugs this uncovered](hardware-findings.md#real-bugs-this-uncovered)
  above.
- **Camera captures need a 2s stabilization wait after `enable_camera()`**
  before grabbing a frame, or the photo comes back torn/glitchy — also only
  showed up on real hardware. See [Real bugs this uncovered](hardware-findings.md#real-bugs-this-uncovered)
  above.
- **The first word of speech is often inaudible** unless a silent lead-in
  (`TTS_LEADIN_MS`, default 200ms) is prepended first — likely a hardware
  audio warm-up, same class of issue as the camera one above. See
  [Real bugs this uncovered](hardware-findings.md#real-bugs-this-uncovered) above.
- **`display_image(..., duration=...)` sleeps for that long before clearing
  the screen** — `show_expression()` used to default to `duration=2.0`,
  adding a real 2s delay to every mood application (i.e. every `say()`).
  Now defaults to `None` (persist, no sleep). See
  [Real bugs this uncovered](hardware-findings.md#real-bugs-this-uncovered) above.
- **Orpheus TTS needs one-time model terms acceptance** in the Groq console
  per account/org (see Groq setup above).
