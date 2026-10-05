# Wake word detection (zero-network)

[← Back to the README](../README.md)


`--mode vad` originally reacted to *any* detected speech — no wake word, so
it would respond to ambient conversation, a TV, anything near the mic. Fixed
with on-device wake-word detection gating VAD, using
[openWakeWord](https://github.com/dscripka/openWakeWord) — chosen over
Porcupine (Picovoice) specifically because it needs no account and has no
unverified network behavior at runtime. It has no "Hey Cozmo" model out of
the box, so one was trained: `models/hey_cozmo.onnx` (see
[Training a custom wake word](#training-a-custom-wake-word)).

The wake word only gates the *start* of a conversation, not every turn:
after it fires, `vad_mode.py` keeps listening for follow-ups for
`VAD_FOLLOWUP_TIMEOUT_S` (resetting on every reply) without repeating it —
once nothing is heard within that window, the wake word is required again.

**Physical "I heard you" cue:** this mode is hands-free, so a terminal print
alone isn't a real indicator of anything — you're not meant to be watching a
screen. Detecting the wake word switches Cozmo to the `curious` mood (face,
white light, head up) so there's an actual visible/physical signal that he's
listening; going quiet past the follow-up window switches him back to
`neutral`. If the conversation continues instead, the model's own next
`say`/`gesture` naturally takes over the face/lights as part of its normal
response — no separate reset needed there.

**Can he hear me? Watch the backpack light:**

| Backpack | Meaning |
|---|---|
| **Blinking** (any color) | The mic is recording you right now - talk. |
| Steady color | Not recording: thinking or speaking a reply (the color is his mood). |
| Off | Idle: waiting for the wake word. |

While recording, the light blinks in whatever color is currently up (white
if it was off), so a mood or the red low-battery warning arriving mid-
recording still blinks. Once recording stops it goes back to steady. The
blink is done by the firmware (`LightState` on/off frames, ~0.5s each
assuming ~30fps frames - unverified on hardware; see `robot/real.py`).

Steady colors come from the reply's mood (`cozmo_brain/robot/moods.py`),
plus red for a low battery. Every mood has a color, so **off only ever means
idle**: the light is switched off at startup and when the follow-up window
ends, and things that happen while idle (a fidget, the pickup reaction, an
unprompted charger announcement) put it back the way they found it:

| Steady color | Moods / meaning |
|---|---|
| green | neutral (his default reply mood), smug, embarrassed, bored, confused |
| blue | excited, happy, proud |
| white | curious (just heard the wake word), surprised, suspicious |
| red | annoyed, angry, scared, sad, sleepy - or battery low/critical (with the battery icon on his face) |
| off | idle only - waiting for the wake word |

Gestures (`cozmo_brain/robot/gestures.py`) change the light too, and the
color they end on stays until the next mood, gesture, or going idle. When a
gesture plays alongside a reply (`say`'s `gesture`), its colors replace the
reply's mood color.

| Gesture | Light while it plays | Ends on |
|---|---|---|
| `dance` | blue → green → blue (flashing) | blue |
| `cheer` | blue (excited) → green | green |
| `wake_up` | red (sleepy) → blue (happy) | blue |
| `sleep`, `sad_shuffle`, `flinch` | red (sleepy / sad / scared) | red |
| `fist_pump` | blue (proud) | blue |
| `peek`, `sneaky_creep`, `alert` | white (curious / suspicious / surprised) | white |
| `shrug` | green (confused) | green |
| `nod_yes`, `shake_no`, `spin` | no change | the color it started with |

Idle fidgets (`peek`, `shrug`) put the light back afterwards, so an idle
backpack stays off. `wake_up` plays when he connects; `--mode vad` then
turns the light off, since he starts idle.

**Don't `pip install openwakeword` directly** — on Linux it unconditionally
declares a dependency on `tflite-runtime`, which has **no build for Python
3.12 anywhere** (checked both PyPI and piwheels.org — the Raspberry Pi
Foundation's own ARM wheel builder — every version is listed "binary only"
with zero files). Install it this way instead, which sidesteps that
dependency entirely:

```bash
pip install --no-deps openwakeword
pip install onnxruntime scipy scikit-learn requests tqdm
python3 -c "from openwakeword.utils import download_models; download_models(['hey_jarvis_v0.1'])"
```

That last line is a required one-time step separate from the pip installs —
the package ships no model files at all. It is needed even with the custom
`hey_cozmo` model: besides `hey_jarvis` it fetches the shared audio
feature models (`melspectrogram`, `embedding_model`) that every wake word
model runs on, into the openwakeword package's own folder
(`.../site-packages/openwakeword/resources/models/`), not the project. It
needs internet once, during setup; the actual wake-word detection at runtime
never touches the network.

`WAKE_WORD_MODEL` in `.env` is one of:

| Value | Loads |
|---|---|
| `hey_cozmo` (the default) | `models/hey_cozmo.onnx` - a custom model in `models/`, named without the extension |
| `hey_jarvis`, `alexa`, `hey_mycroft`, `hey_rhasspy`, `timer`, `weather` | openWakeWord's free built-in models |
| a path, e.g. `/home/pi/other.onnx` | that file |

A file in `models/` wins over a built-in model with the same name. The log
and the startup message show the bare name: `Wake word 'hey_cozmo' detected
(score 0.81).` Swapping is just changing this one value.

`WAKE_WORD_THRESHOLD` (default `0.5`) is the score a wake needs. The
detection log prints each score, so tune from real ones: raise it if
background noise or conversation wakes him, lower it if he misses you. The
first Pi test of `hey_cozmo` (2026-10-01) woke at 0.81; false-wake testing
in normal room noise is still to do.

## Training a custom wake word

`hey_cozmo.onnx` was trained with `training/wake_word_training.ipynb`, a
copy of openWakeWord's simple Colab notebook
([upstream](https://github.com/dscripka/openWakeWord#training-new-models))
patched to run on Colab as of 2026-10. Training uses synthetic voices, so
no recordings are needed, and takes about 1-1.5 hours on Colab's free T4
GPU. Upstream also has a more thorough `automatic_model_training.ipynb`
(more training data, fewer false wakes); it needs Linux and a GPU and
wasn't tried.

1. Open [colab.research.google.com](https://colab.research.google.com) →
   **File → Upload notebook** → `training/wake_word_training.ipynb`. Use a
   personal Google account; the notebook needs nothing from this repo or
   `.env`.
2. **Runtime → Change runtime type**: **T4 GPU**, Runtime version
   **2025.07**. The first code cell must print Python **3.11**.
3. In cell 1, set the phrase and listen to the sample. In cell 3, set
   `output_name` (the downloaded file's name, e.g. `hey_cozmo`).
4. **Runtime → Run all**. The `.onnx` downloads at the end.
5. Copy it to `models/`, add a `!models/<name>.onnx` line to `.gitignore`
   so it's committed, and set `WAKE_WORD_MODEL=<name>`.

What the notebook works around (each one broke a real run):

| Problem | Symptom | Fix in the notebook |
|---|---|---|
| Newer Colab runtimes are Python 3.12/3.13 | `No matching distribution found for piper-phonemize-cross` / `torchvision==0.20.0` | Use runtime version 2025.07 (Python 3.11) |
| A word not in the pronunciation dictionary (CMUdict) needs a helper model whose S3 bucket now returns 403 | `UnpicklingError: invalid load key, '<'` | The phrase uses dictionary words only: `hey cosmo` sounds the same as "Cozmo", and `cozmo` isn't in CMUdict. No underscores: `hey_cosmo` is read as one unknown word. |
| AudioSet moved from `.tar` to Parquet on Hugging Face | `bal_train09.tar` 404 | Cell 2.1 downloads `data/bal_train/09.parquet` and converts it |
| `train.py` validates against 11 h of audio in one batch | Training killed at 75% (GPU) or at the start of stage 2 (CPU): `Killed`, `exit code: 137` | `sed` patch to batches of 8192 (same result), and training on CPU. If it still happens: **Runtime → Restart session** (keeps files) and rerun only cell 3. |
| TFLite conversion is broken | `No module named 'onnx_tf'` at the very end | Ignored - only the `.onnx` is used (`inference_framework="onnx"`) |

Notes:
- **Phrase choice:** use 3+ syllables. Bare "Cozmo" was ruled out: 2
  syllables wake more often by mistake, and people say "Cozmo" in normal
  conversation around him.
- **Several phrases in one model:** possible (`config["target_phrase"]` is
  a list, e.g. `["hey cosmo", "ok cosmo"]`), but all phrases then share one
  score and threshold, and each phrase gets fewer training examples.
- **License:** the notebook's training data is non-commercial, so models
  trained with it are for personal use only.

## Model files (`models/`)

Model files live in `models/`, and `.env` names them without the folder or
extension (`cozmo_brain/model_files.py`). For `WAKE_WORD_MODEL` and
`LOCAL_TTS_VOICE_PATH`, a value is tried as:

1. a path as given (absolute, or relative to the folder you started from) -
   so older `.env` values like `en_US-lessac-medium.onnx` still work;
2. `models/<value>`;
3. `models/<value>.onnx`.

`models/` is found relative to the project, not the current folder.
`tests/test_model_paths.py` covers the lookup order, and loads the real
`hey_cozmo` model when openwakeword is installed.

**Moving an existing install** (models in the project root) after pulling:

```bash
mkdir -p models
mv en_US-lessac-medium.onnx en_US-lessac-medium.onnx.json models/
rm -f hey_cozmo.onnx    # now comes from git, in models/
```

Then in `.env`: `WAKE_WORD_MODEL=hey_cozmo` and
`LOCAL_TTS_VOICE_PATH=en_US-lessac-medium`. Until you move them, the
files are still found in the root, as long as you start from the project
folder (`run.sh` does).

**What's actually verified vs. not:** installed this exact way, downloaded
`hey_jarvis`, loaded it with `inference_framework="onnx"` (never touching
`tflite_runtime`), and confirmed real predictions come back correctly keyed
— on a dev machine. `hey_cozmo` was confirmed on the Pi with a real mic on
2026-10-01 (woke at score 0.81). **Not verified:** false-wake rate in normal
room noise, or how far away it still hears you.
