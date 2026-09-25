# SmartCozmo

Turn an old Anki/DDL **Cozmo** robot into an LLM-driven assistant — speak to
it, an LLM decides what it should say/do, and [PyCozmo](https://github.com/zayfod/pycozmo)
executes it on the real robot. No phone/Cozmo app involved; everything runs
headless from a Raspberry Pi.

```
Bluetooth mic
     │  (arecord, 16kHz mono)
     ▼
Groq Whisper STT  (speech → text)
     │
     ▼
Ollama, tool-calling model  (decides what to do)
     │  tool_calls: say() / drive() / turn()
     ▼
PyCozmo  (talks to Cozmo over its own Wi-Fi AP)
     ├─ drive/turn → cli.drive_wheels(...)
     └─ say(text)  → Groq TTS (Orpheus) → cli.play_audio(...)
```

**Status:** Two things live here now:

- **`orchestrator.py`** — the original single-file proof of concept. Kept
  as-is on purpose, as a lightweight script for quickly testing mic → STT →
  LLM → PyCozmo → TTS on the Pi without the full app.
- **`cozmo_brain/`** — the real application: conversation memory, a proper
  tool-calling agentic loop, a curated gesture/mood library built on
  confirmed PyCozmo primitives, real animation clip playback, camera-in-
  the-loop vision, hands-free VAD listening, a turn-calibration mode, and a
  simulated robot backend for developing away from the hardware. See
  [The cozmo_brain/ application](#the-cozmo_brain-application) below.

See [Roadmap](#roadmap--open-work) for what's still open.

---

## How this project is meant to be worked on

- **Cozmo only talks to whatever machine is joined to his own Wi-Fi access
  point.** In practice that's the Raspberry Pi, since it also needs a
  Bluetooth mic/speaker paired to it and a second network interface for
  internet access. Treat the Pi as the **deployment target**.
- Everything that isn't a `pycozmo.Client` call or a local `arecord`
  invocation is plain HTTP (Groq, Ollama) and can be developed and tested on
  any machine.
- `cozmo_brain/` has a **simulated robot backend** (`--simulate`, or
  `ROBOT_BACKEND=simulated` in `.env`) that logs every action to the console
  instead of touching hardware — the entire tool-calling loop, conversation
  memory, and gesture library can be developed and demoed on a plain dev
  machine with no Cozmo, no PyCozmo Wi-Fi link, and no Bluetooth mic
  attached. `--mode text` pairs well with this, since it also skips the mic.
- Suggested loop: edit/prototype on your dev machine with `--simulate --mode
  text` → sync to the Pi → run for real for anything that needs to actually
  touch the robot, the mic, or the speaker.

---

## Requirements

**Hardware**
- Anki/DDL **Cozmo** robot (any firmware that still exposes its own Wi-Fi AP
  when you raise/lower its lift)
- A machine that can join Cozmo's Wi-Fi AP *and* stay on the internet at the
  same time — a **Raspberry Pi 5** running Ubuntu with wired Ethernet + Wi-Fi
  is what this project targets, but any Linux box with two network
  interfaces works
- A Bluetooth mic/speaker (this project used a Bose SoundLink Flex)

**Accounts / services**
- [Groq](https://console.groq.com) API key — used for both STT (Whisper) and
  TTS (Orpheus)
- An [Ollama](https://ollama.com) endpoint reachable from the deployment
  machine, serving a model that supports **tool-calling** (this project uses
  a cloud-hosted model proxied through a local Ollama instance)

---

## Repo layout

```
.
├── orchestrator.py         # lightweight single-file test script (unchanged, no memory/tools/gestures)
├── run.sh                   # activates cozmo-env + runs cozmo_brain in one step (chmod +x once)
├── requirements.txt        # pip install -r requirements.txt (core deps only — see setup step 1 for extras)
├── .env.example             # template for every config variable, annotated — copy to .env
├── .env                      # your real config (gitignored, not in repo)
├── README.md
└── cozmo_brain/              # the full application
    ├── main.py               # CLI entry point (--mode voice|vad|text|calibrate, --simulate)
    ├── config.py             # typed Settings, loaded from .env
    ├── conversation.py       # chat history with trimming + save/load to disk
    ├── engine.py             # the agentic tool-calling loop (CozmoEngine)
    ├── personality.py        # Cozmo's system prompt / persona
    ├── imaging.py             # shared base64 image helper (vision attach, who_is_this)
    ├── llm/
    │   ├── ollama_client.py   # /api/chat wrapper with tool-calling
    │   ├── speech_client.py    # SpeechClient interface shared by every provider
    │   ├── groq_client.py       # Groq Whisper STT + Orpheus TTS
    │   ├── openai_client.py     # OpenAI Whisper STT + TTS (AUDIO_PROVIDER=openai)
    │   └── tts_postprocess.py    # shared voice character + gain, used by both providers
    ├── audio/
    │   ├── recorder.py        # fixed-length arecord capture (push-to-talk)
    │   ├── vad.py              # webrtcvad-based hands-free capture
    │   ├── wakeword.py         # openWakeWord-based wake word, gates --mode vad
    │   └── player.py           # optional system-speaker audio output (AUDIO_OUTPUT)
    ├── robot/
    │   ├── __init__.py         # create_robot() factory (real vs. simulated)
    │   ├── base.py             # RobotBackend interface + shared mood/gesture playback
    │   ├── real.py             # PyCozmo-backed implementation (+ health/reconnect)
    │   ├── simulated.py        # console-logging implementation, no hardware needed
    │   ├── wifi.py             # optional nmcli-based Wi-Fi auto-connect
    │   ├── moods.py            # curated expression+light+pose presets
    │   ├── gestures.py         # curated multi-step gesture choreography
    │   ├── battery_face.py     # renders the low-battery face icon
    │   └── battery_monitor.py  # background thread: checks voltage, shows the icon
    ├── tools/
    │   ├── base.py             # Tool/ToolResult contract
    │   └── registry.py         # the 9 tools exposed to the LLM
    └── modes/
        ├── interactive.py       # --mode voice  (push-to-talk)
        ├── vad_mode.py           # --mode vad    (hands-free, wake word + follow-up)
        ├── text_mode.py          # --mode text   (typed input, dev-friendly)
        └── calibrate_mode.py     # --mode calibrate (measure turn() accuracy)
```

---

## Setup from scratch

### 1. Python environment (on the deployment machine)

```bash
mkdir -p ~/sunwork/projects/cozmo && cd ~/sunwork/projects/cozmo
git clone https://github.com/sun4git/SmartCozmo.git .
python3 -m venv cozmo-env
source cozmo-env/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pycozmo_resources.py download   # downloads Cozmo's animation/audio resource files — needed for play_animation()
```

**Only if you plan to use `--mode vad`** (hands-free), two more installs, both
with real gotchas:

1. **webrtcvad** needs a C compiler + Python headers to build (it has no
   prebuilt wheel for aarch64/Python 3.12):

   ```bash
   sudo apt install -y build-essential python3-dev
   pip install webrtcvad
   pip install "setuptools<81"   # required — see below
   ```

   `setuptools<81` is a **required pin, not optional** (order relative to
   `pip install webrtcvad` doesn't matter — this fixes an *import-time*
   failure, not a build-time one): webrtcvad (abandoned since 2020) does a
   module-level `import pkg_resources` for its version check, and
   pkg_resources was removed from setuptools itself in a recent release — a
   fresh venv's default (latest) setuptools installs webrtcvad fine but then
   breaks *importing* it with `ModuleNotFoundError: No module named
   'pkg_resources'`. Later running `pip install --upgrade setuptools` in
   this venv reintroduces the same failure — reinstall the pin if that
   happens.
2. **Wake-word detection**, which is **not** a plain `pip install
   openwakeword` — see [Wake word detection](#wake-word-detection-zero-network)
   below for the exact install command and why.

`--mode voice`, `--mode text`, and `--simulate` don't need either of these —
skip them for now if you just want to
get something running first.

### 2. Configure `.env`

```bash
cp .env.example .env
```

Edit `.env` and fill in at least `GROQ_API_KEY`. Adjust `OLLAMA_BASE_URL` /
`OLLAMA_MODEL` to match your Ollama setup. See `.env.example` for what every
variable does.

### 3. Join Cozmo's Wi-Fi AP (do this every time Cozmo has been power-cycled)

1. Power Cozmo on, raise the lift fully, then lower it — his face shows an
   SSID (`Cozmo_XXXXXX`) and password. These regenerate on power cycle, so
   always re-check his face.
2. If the deployment machine has a wired connection you want to keep as the
   default route (recommended, so it stays on the internet while also
   talking to Cozmo):

   ```bash
   sudo nmcli radio wifi on                      # if Wi-Fi radio was off
   sudo nmcli dev wifi rescan
   nmcli dev wifi list                           # look for Cozmo_XXXXXX (rescan again if missing — the AP is flaky to scan)
   sudo nmcli dev wifi connect "Cozmo_XXXXXX" password "<password-from-face>"
   sudo nmcli connection modify "Cozmo_XXXXXX" ipv4.never-default yes   # CRITICAL — keeps your wired link as the default route
   ping -c 3 172.31.1.1                          # Cozmo's usual self-IP on his own AP
   ```

3. If turning Wi-Fi on auto-connects to some other saved network instead of
   Cozmo's AP, take that network off autoconnect:

   ```bash
   sudo nmcli connection down "<other-network-name>"
   sudo nmcli connection modify "<other-network-name>" connection.autoconnect no
   ```

4. Sanity check both interfaces are up and the wired one is still the
   default route:

   ```bash
   ip -4 route show table all
   nmcli device status
   ```

   If the wired connection's default route ever goes missing (seen once,
   root cause unclear — possibly a NetworkManager re-evaluation on Wi-Fi
   reconnect), bounce it:

   ```bash
   sudo nmcli connection down <wired-connection-name> && sudo nmcli connection up <wired-connection-name>
   ```

**Optional: let `cozmo_brain` do this step for you.** Set `COZMO_WIFI_SSID`
in `.env` and it'll run `nmcli connection up <ssid>` itself before every
connect — using the saved profile from the manual steps above, so no
password is needed after the first time. If you'd rather it handle the very
first connect too, also set `COZMO_WIFI_PASSWORD`; it'll run the equivalent
of the `nmcli dev wifi connect ... password ...` + `ipv4.never-default yes`
commands above and save the profile itself. This is entirely opt-in — leave
`COZMO_WIFI_SSID` empty and nothing changes from doing it manually. See
`cozmo_brain/robot/wifi.py`.

Two things to know:
- **It needs the same `sudo`/polkit access nmcli already needs on this
  machine** (see the gotcha below) — this module never adds `sudo` itself,
  since silently blocking on an interactive password prompt from a
  non-interactive process is worse than just failing loudly. See
  [Passwordless nmcli access](#passwordless-nmcli-access-for-wi-fi-auto-connect)
  below to set that up.
- **It's best-effort.** If Cozmo's SSID has regenerated since the saved
  profile was created, this falls back to logging exactly what to run
  manually, instead of guessing.

#### Passwordless nmcli access (optional, for Wi-Fi auto-connect)

Skip this whole subsection if you're not using `COZMO_WIFI_SSID` — it only
matters for the optional auto-connect above. `nmcli` needing `sudo` on this
machine (per the gotcha below) means plain, unprivileged `nmcli connection
up ...` calls from `wifi.py` will fail unless you grant passwordless access
one of two ways.

**Option A: polkit rule (recommended)** — grants your user direct,
unprivileged `nmcli` access, which is what `wifi.py` already calls (no code
change needed). Scoped to just the NetworkManager actions `wifi.py` actually
uses:

```bash
whoami   # confirm your username — replace "suneel" below if different
sudo nano /etc/polkit-1/rules.d/50-nmcli-cozmo.rules
```

```js
polkit.addRule(function(action, subject) {
    var nmActions = [
        "org.freedesktop.NetworkManager.network-control",
        "org.freedesktop.NetworkManager.settings.modify.own",
        "org.freedesktop.NetworkManager.settings.modify.system",
        "org.freedesktop.NetworkManager.enable-disable-wifi",
        "org.freedesktop.NetworkManager.wifi.scan"
    ];
    if (nmActions.indexOf(action.id) !== -1 && subject.user == "suneel") {
        return polkit.Result.YES;
    }
});
```

Takes effect immediately (polkitd watches the directory); if not, `sudo
systemctl restart polkit`. Test as your normal user, no `sudo`:

```bash
nmcli connection up "Cozmo_XXXXXX"
```

If that succeeds instantly with no password prompt, it worked. (A broader,
simpler version of this rule — matching *any* `org.freedesktop.NetworkManager.*`
action instead of the specific list above — is fine too for a single-user Pi
like this; the scoped list is just tighter least-privilege.)

**Option B: sudoers** — if you'd rather keep using `sudo nmcli ...`
explicitly:

```bash
sudo visudo -f /etc/sudoers.d/cozmo-nmcli
```
```
suneel ALL=(root) NOPASSWD: /usr/bin/nmcli connection up *, /usr/bin/nmcli connection modify *, /usr/bin/nmcli dev wifi connect *, /usr/bin/nmcli dev wifi rescan
```
(`visudo -f` validates syntax and sets correct file permissions — don't edit
that file with a plain editor.) Note: `wifi.py` calls plain `nmcli`, not
`sudo nmcli` — this option alone doesn't help unless the code is changed to
call `sudo -n nmcli ...` (`-n` fails fast instead of hanging if passwordless
sudo isn't actually configured). Option A needs no such change.

### 4. Verify PyCozmo connectivity

```bash
python3 -c "import pycozmo; cli = pycozmo.Client(); cli.start(); cli.connect(); cli.wait_for_robot(); print('Connected!'); cli.disconnect(); cli.stop()"
```

Clone [pycozmo](https://github.com/zayfod/pycozmo) separately if you want its
`examples/` folder for reference (`minimal.py`, `backpack_lights.py`,
`camera.py`, etc.) — it's not part of this repo.

**Cozmo's audio requirement:** WAV files sent to `cli.play_audio()` must be
**22050Hz or 48000Hz, 16-bit, mono**. `set_volume()` takes 0–65535.

### 5. Pair the Bluetooth mic/speaker

```bash
bluetoothctl
  power on
  agent on
  default-agent
  scan on
  # put your speaker/mic into pairing mode
  pair <MAC_ADDRESS>
  trust <MAC_ADDRESS>
  connect <MAC_ADDRESS>
  exit
```

Install `pactl` if missing (PipeWire itself is usually already running):

```bash
sudo apt install pulseaudio-utils
```

**Important:** most Bluetooth headsets connect in `a2dp-sink` profile by
default, which is speaker-only and exposes no mic. Force the handsfree
profile to get a mic input:

```bash
pactl list cards                        # find the profile names your device supports
pactl set-card-profile bluez_card.<MAC_WITH_UNDERSCORES> headset-head-unit-msbc
pactl list sources short                 # confirm a bluez_input... source appears
pactl set-default-source bluez_input.<MAC_WITH_UNDERSCORES>.0
```

Test recording/playback (use the `pipewire` ALSA device name — `arecord -D
pulse` typically fails with "Unknown PCM pulse" unless you have the ALSA
pulse plugin configured):

```bash
arecord -D pipewire -f S16_LE -r 16000 -c 1 -d 5 test.wav
aplay -D pipewire test.wav
```

### 6. Groq setup

- Get an API key at [console.groq.com](https://console.groq.com), put it in
  `.env` as `GROQ_API_KEY`.
- TTS uses **Orpheus** (`canopylabs/orpheus-v1-english`) — `playai-tts` is
  deprecated. Orpheus requires accepting the model's terms once at
  `https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english`
  before it will work — if TTS fails with `model_terms_required`, that's why.
- Request `sample_rate: 22050` directly in the TTS API call rather than
  post-processing with ffmpeg — Orpheus's default (24000Hz) isn't one of the
  rates Cozmo's `play_audio()` accepts.
- Available Orpheus voices: `autumn`, `diana`, `hannah` (female), `austin`,
  `daniel`, `troy` (male).

### 7. Run it

One-time, before the first `./run.sh` call below (git on this repo doesn't
preserve the executable bit — it was committed from a Windows checkout):

```bash
chmod +x run.sh
```

**Recommended first run — sanity-check `.env`/Groq/Ollama before touching
the robot at all.** This isolates config problems (bad API key, unreachable
Ollama) from hardware problems (Wi-Fi, Bluetooth) — if this doesn't work,
nothing past this point will either, so fix it here first:

```bash
./run.sh --simulate --mode text
```

Type something and press Enter. If you get a reply back (even a weird one),
your `.env` is good and it's time to try the real robot. If it hangs or
errors, re-check step 2 (`GROQ_API_KEY`, `OLLAMA_BASE_URL`) before going
any further.

**Then, for real:**

```bash
# Quick test script:
python3 orchestrator.py

# Full application:
./run.sh --mode voice          # push-to-talk — press Enter, speak, 'quit' to exit
./run.sh --mode vad             # hands-free — needs the wake-word setup from step 1
```

(See [Modes](#modes) below if you'd rather activate `cozmo-env` and call
`python3 -m cozmo_brain` directly instead of using `run.sh`.)

---

## The cozmo_brain/ application

### Architecture

```
                    ┌────────────────────────────────────────────┐
                    │                CozmoEngine                  │
mic/typed text ───► │  Conversation ◄──► Ollama (tool-calling)    │
                    │        ▲                  │                │
                    │        │            tool_calls              │
                    │        │                  ▼                │
                    │   tool results ◄──── Tools (say/gesture/... )│
                    └────────────────────────────│─────────────────┘
                                                  ▼
                                     RobotBackend (real | simulated)
                                        │                    │
                              PyCozmo (Wi-Fi)        console logging
                                        │
                                    Cozmo 🤖
```

Every user turn runs through `CozmoEngine.handle_turn()`: the message is
added to conversation history, sent to Ollama with the full tool schema, and
if the model returns tool calls, each one is executed against the active
`RobotBackend` and its result is fed back to the model — repeating (up to
`MAX_TOOL_ITERATIONS`) until the model stops calling tools. This lets Cozmo
chain actions in one turn (e.g. `gesture` then `say` then `look`) instead of
doing exactly one thing per user utterance.

### Modes

```bash
python3 -m cozmo_brain --mode voice       # push-to-talk (default) — press Enter, speak
python3 -m cozmo_brain --mode vad         # hands-free — say the wake word, then talk (needs webrtcvad + openwakeword)
python3 -m cozmo_brain --mode text        # type instead of speak — great for dev/testing
python3 -m cozmo_brain --mode calibrate   # measure real turn() degrees-per-second
python3 -m cozmo_brain --simulate         # force the console-logging robot backend (any mode)
```

`python3 -m cozmo_brain` requires `cozmo-env` to be activated first (or call
`cozmo-env/bin/python -m cozmo_brain` directly, which is equivalent without
activating). `./run.sh` does both in one step — activates `cozmo-env` and
forwards all arguments, e.g. `./run.sh --mode voice`. One-time setup:
`chmod +x run.sh` (git on this repo doesn't preserve the executable bit,
since it was committed from a Windows checkout).

Conversation history persists to `CONVERSATION_HISTORY_PATH` between runs;
pass `--fresh` to start clean instead.

### Tools available to the LLM

| Tool | Does |
|---|---|
| `say(text, mood)` | Speaks via Groq TTS (routed per `AUDIO_OUTPUT`), striking a matching facial expression + backpack light color first. |
| `gesture(name)` | Plays a curated multi-step choreography (face + lights + head/arm + wheels). |
| `play_animation(name)` | Plays a **real** Anki animation clip or group by exact name. |
| `list_animations()` | Lists the real animation/group names actually loaded on this robot. |
| `drive(distance_mm, speed_mmps)` | Drives straight, clamped to safe limits. |
| `turn(angle_degrees)` | Turns in place (calibrated via `TURN_SECONDS_PER_DEGREE`). |
| `look()` | Captures a camera frame and attaches it to the next LLM turn for vision. |
| `remember_person(name)` | Captures a reference photo, stored under that name. |
| `who_is_this()` | Captures a photo and asks the vision LLM if it matches anyone remembered. **Experimental.** |

**Moods** (used by `say` and inside gestures): `neutral`, `happy`, `excited`,
`curious`, `proud`, `sad`, `sleepy`, `bored`, `scared`, `surprised`,
`confused`, `annoyed`, `angry`, `suspicious`, `embarrassed`, `smug` — each is
a real `pycozmo.expressions` procedural face paired with a backpack light
color and an optional head/lift pose (`cozmo_brain/robot/moods.py`).
`neutral` (the default `say` mood) deliberately resets head/lift to level
— several moods and gestures raise the lift arm, which physically covers
the face screen, and without a real reset the arm was staying up almost
permanently on real hardware.

**Gestures** (multi-step choreography, `cozmo_brain/robot/gestures.py`):
`nod_yes`, `shake_no`, `wake_up`, `sleep`, `cheer`, `dance`, `spin`,
`flinch`, `peek`, `shrug`, `fist_pump`, `sneaky_creep`, `sad_shuffle`,
`alert`.

**Real animation clips** (`play_animation`/`list_animations`) use PyCozmo's
actual `play_anim`/`play_anim_group` API against Anki's original animation
assets — this only works once you've run `pycozmo_resources.py download`
(step 1 above). If those assets aren't present, `list_animations()` just
returns empty and the model is expected to fall back to `gesture`/`say`
moods instead — nothing crashes.

### Recognizing people (experimental)

The original Cozmo app could meet someone, learn their name, and greet them
by name later — that ran on Anki's own on-device face-detection/enrollment
system. PyCozmo hasn't reverse-engineered that: it only knows the NVRAM
storage slot addresses exist (`NVEntry_FaceEnrollData`, `NVEntry_FaceAlbumData`
in `protocol_encoder.py`), not the actual protocol to drive it. So
`remember_person`/`who_is_this` don't use that — they're a much simpler,
much less reliable substitute built entirely on the vision LLM already in
the loop, no new dependency:

- `remember_person(name)` just saves a reference photo under
  `KNOWN_PEOPLE_DIR/<name>.jpg` — no processing at all.
- `who_is_this()` captures a new photo, then for each saved reference photo
  sends *both* images to Ollama in one message (`images: [reference, new]`)
  and asks it to judge whether they're the same person.

This is a real, working feature, but manage expectations: general-purpose
vision LLMs aren't built for fine-grained face re-identification the way a
dedicated recognizer is, and Cozmo's camera is low-resolution, often blurry,
and has a strong warm/red color cast — a combination that makes false
matches (or false "I don't know you"s) genuinely likely, not just a
theoretical caveat. Treat any match as Cozmo's fun guess, not a fact, and
say so — the persona prompt already leans into this ("his eyesight isn't
great") rather than hiding it.

### Real bugs this uncovered

While wiring up `drive()`/`turn()`, we found that this version of PyCozmo's
`Client.drive_wheels(..., duration=...)` accepts a `duration` keyword but
**never actually uses it** — the robot keeps driving until told otherwise.
`orchestrator.py`'s original `drive()`/`turn()` pass `duration` expecting it
to auto-stop the robot, which does not happen. `cozmo_brain/robot/real.py`
works around this properly: it sleeps for the computed duration itself, then
calls `cli.stop_all_motors()` explicitly. Worth keeping in mind if you write
any other timed movement — always stop the robot explicitly.

**Found on real hardware** (this one only showed up once actually running
against a physical Cozmo, not in any offline testing): `say`/`gesture`/every
mood failed with `Invalid image dimensions. Only 128x32 images are
supported. 128x64 given.` PyCozmo's own `procedural_face.DEFAULT_WIDTH` /
`DEFAULT_HEIGHT` are `128x64`, but `image_encoder.ImageEncoder` — what
actually talks to Cozmo's real screen — hard-requires exactly `128x32`
(Cozmo's real face display is a short, wide strip, not square). PyCozmo's
own default doesn't match its own hardware constraint. Fixed in
`show_expression()` by constructing the expression class with
`width=128, height=32` explicitly instead of the library's default — the
face geometry scales by width/height, so this renders correctly proportioned
for the real screen rather than a squashed crop. Verified against all 25
expression classes.

**Also only found on real hardware:** photos from `look()`/`remember_person()`/
`who_is_this()` came back visibly torn/glitchy (interleaved garbage across
part of the frame) — bad enough that face comparison couldn't work at all.
`capture_photo()` was registering the capture handler and grabbing
whichever frame arrived first, immediately after `enable_camera()`.
PyCozmo's own `camera.py` example does the same `enable_camera()` call but
then explicitly **sleeps 2 seconds "to let the image stabilize"** before
ever registering a handler — the first frame(s) off a just-started stream
come back mid-transition, torn. Added the same 2-second wait. This adds a
real, noticeable delay to every photo now, which is the deliberate
trade-off for a usable image.

**Likely the same class of issue, this time on audio output:** the first
word of `say()`'s speech was often inaudible while the rest of the
sentence played fine. Traced through `anim_controller.py`'s actual
playback loop (a background thread polling a queue at a fixed frame rate,
sending either real audio or silence) — nothing there structurally drops
the first packets, which points at Cozmo's own audio hardware needing a
brief moment to actually start outputting sound after a run of silence,
not a software queueing bug. `TTS_LEADIN_MS` (default 200ms) prepends
silence before the real speech so the warm-up eats that instead of the
first word — verified precisely that the prepended block is genuinely
silent and the real audio starts exactly at the configured mark, and that
a real STT round-trip still transcribes the first word correctly with the
lead-in present. **Not verified: whether 200ms is actually the right
amount on real hardware** — there's no Cozmo here to confirm the audible
result, only that the mechanism itself works exactly as intended.

**A latency bug, not a hardware quirk:** `cozmo_brain` felt noticeably
slower per response than `orchestrator.py`, even though both do the same
STT → LLM → TTS round trip. Traced to `show_expression()`'s default
`duration=2.0`, passed straight through to PyCozmo's own
`cli.display_image(im, duration=2.0)` — which does `time.sleep(duration)`
whenever `duration is not None`. Since `apply_mood()` (now called on every
`say()`, including the default "neutral" mood — see above) never
overrode that default, **every single response was blocking for 2 full
seconds just to show the face**, on top of ~0.4s each for any head/lift
movement the mood also sets — before the TTS audio even played.
`orchestrator.py` has no mood/face system at all, so none of this existed
there. Fixed by defaulting `show_expression()`'s duration to `None`
instead: the face is set and persists until the next mood change, rather
than being held for a fixed 2s and auto-cleared — which was never actually
the intended behavior for routine mood-setting, only for a deliberately
timed gesture beat (no gesture currently uses this path directly).

**Found in `--mode vad` use:** Cozmo would occasionally "hear" and act on
entire sentences that were never said — reported symptoms included fully
formed sentences in other languages (e.g. Korean) out of what should have
been silence. Root cause: `record_until_silence()` (`cozmo_brain/audio/vad.py`)
started a full recording the moment **a single** 30ms frame was classified
as speech by `webrtcvad` — cheap Bluetooth mics + background noise trip this
easily. That mostly-silent clip still got sent to Whisper, and Whisper
doesn't reliably return empty text on a noise-only clip — it hallucinates a
plausible-sounding sentence from its training data instead (a well-known,
widely-reported Whisper failure mode; confirmed neither Groq's nor OpenAI's
hosted Whisper endpoint expose a documented way to suppress this — Groq's
`verbose_json` does expose `no_speech_prob`/`avg_logprob` for post-hoc
filtering, but OpenAI's `whisper-1` API doesn't reliably surface those
fields despite them existing in the underlying model). Fixed at the capture
layer instead, so it works regardless of provider: `VAD_MIN_SPEECH_MS`
(default 300ms) now requires that much total *voiced* audio — frames
webrtcvad actually classified as speech, not just total recording length —
before a capture is accepted; anything shorter is discarded exactly like "no
speech heard," never reaching the STT call. Verified with a scripted
fake-mic test (single false-positive frame → discarded; a real multi-frame
utterance → accepted); **not yet verified against a real recurrence on
hardware** — worth confirming the hallucinated transcriptions stop showing
up in practice.

### Routing speech to a real speaker (noisy environments)

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

### Making the voice sound less generic

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

### Switching speech providers

Groq's free-tier rate limits are real — this happened during actual use,
not just as a theoretical risk. `AUDIO_PROVIDER` in `.env` switches both
STT and TTS together, `groq` (default) or `openai`:

```env
AUDIO_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

Both providers implement the same `SpeechClient` interface
(`transcribe()`/`synthesize()`), so nothing else in the app — `tools/registry.py`,
every mode — needs to know which one is actually active;
`create_speech_client()` in `cozmo_brain/llm/__init__.py` picks the right
one from config.

Model and voice names are **not interchangeable between providers** —
Groq's Orpheus voices (`austin`, `troy`, ...) don't exist on OpenAI, and
vice versa (`alloy`, `nova`, ...); see `.env.example` for both lists.

One real difference this surfaced: **Groq's Orpheus endpoint accepts a
`sample_rate` parameter directly in the request; OpenAI's TTS endpoint has
no such parameter at all** (confirmed against OpenAI's own Python SDK
source, not guessed). Since Cozmo's `play_audio()` only accepts 22050 or
48000 Hz, `tts_postprocess.py` (shared by both providers, extracted from
what used to be Groq-only code) now reads whatever rate a provider's
response actually declares and resamples to `TTS_SAMPLE_RATE` — the same
`audioop.ratecv()` trick already used for the pitch-shift effect, just
used here for a plain format conversion. This makes the rate correct
regardless of provider, without needing to know OpenAI's exact native
output rate in advance.

**OpenAI's speech sounds noticeably faster than Groq's** — measured
directly, not assumed: same sentence, both providers, raw output before
any of our own processing. Groq's Orpheus ("austin") took 5.14s; OpenAI's
default voice ("alloy") took 4.41s for identical text — **OpenAI's voice
genuinely speaks about 14% faster on its own**, which is an inherent
difference between the two voices/engines, not a bug. There was also a
smaller, real one: resampling OpenAI's 24000Hz output down to 22050Hz
introduced an *extra* ~4-5% speedup beyond the intended `TTS_PITCH_SHIFT`
effect — likely `audioop.ratecv()`'s known imprecision as a simple
resampler, especially on a non-round rate ratio (24000:22050). Small
enough not to chase further for now. For the dominant (voice-pace) effect,
`OPENAI_TTS_SPEED` uses OpenAI's own native `speed` parameter to
compensate — confirmed it actually changes output duration (tested
`0.87` vs `1.0` directly) — rather than fighting a whole-engine pacing
difference via our own pitch-shift math.

### Detecting and recovering from a dropped connection

Nothing handled this until it was asked about directly, and the answer
required actually reading PyCozmo's connection code rather than guessing:
`cli.send()` (used by `drive`, head/lift, lights, `display_image`, ...)
just enqueues onto a background thread and **never raises**, even on a
fully dead link — most tool calls would silently report success while the
robot does nothing. PyCozmo's own ping mechanism doesn't help either; its
reply handler is a literal `# TODO: Calculate round-trip time` stub, so
`conn.state` never reflects an abrupt drop on its own.

What *does* work: the robot streams `RobotState` telemetry packets
continuously while genuinely connected. `PyCozmoRobot.is_healthy()`
(`cozmo_brain/robot/real.py`) tracks the time since the last one arrived and
treats a gap longer than `ROBOT_STALE_AFTER_S` (default 5s) as a dropped
connection. `CozmoEngine` checks this before every tool call and, if stale,
calls `reconnect()` — which disconnects and reconnects from scratch,
re-running Wi-Fi auto-connect too if `COZMO_WIFI_SSID` is set — before
retrying. If reconnecting fails, the tool call fails cleanly with a message
instead of hanging or crashing the session.

**This is inferred from reading the protocol implementation, not verified
against an actual disconnect** — there's no real Cozmo here to power off
mid-session and watch it recover. Worth deliberately testing (power-cycle
Cozmo mid-conversation, or walk him out of Wi-Fi range) before trusting it
for an unattended long-running session.

### Battery monitor

Cozmo streams his own `battery_voltage` (a real field on PyCozmo's
`RobotState` telemetry packet) continuously while connected, but there's no
discrete "low battery" status flag — just the raw voltage. `BatteryMonitor`
(`cozmo_brain/robot/battery_monitor.py`) runs on a background thread from
`main.py`, polling `robot.get_battery_voltage()` every
`BATTERY_CHECK_INTERVAL_S` (default 30s). Below `BATTERY_LOW_VOLTAGE`
(default 3.7V) it shows a battery icon on Cozmo's face plus a red backpack
light for a few seconds; below `BATTERY_CRITICAL_VOLTAGE` (default 3.5V) the
icon switches from a shrinking fill level to a solid warning mark, since a
proportional fill isn't legible as "urgent" at 128x32. The icon itself is
drawn by `cozmo_brain/robot/battery_face.py` as a plain PIL image, sent via
a new backend-agnostic `display_custom_image()` primitive (alongside the
existing `show_expression()`, which only knows named procedural faces).

The two threshold voltages aren't invented: 3.7V matches a "seek charger"
check used in a real community Cozmo autonomy script, and 3.5V is reported
as the official Cozmo SDK's own low-battery warning level. Neither has been
verified against this specific robot's actual discharge curve — if the
warning fires too early/late in practice, adjust the two `.env` values
rather than assuming the thresholds are wrong in general.

`SimulatedRobot.get_battery_voltage()` always returns a constant healthy
value, so the monitor is effectively a no-op with `--simulate` — there's no
fake battery to drain.

### Wake word detection (zero-network)

`--mode vad` originally reacted to *any* detected speech — no wake word, so
it would respond to ambient conversation, a TV, anything near the mic. Fixed
with on-device wake-word detection gating VAD, using
[openWakeWord](https://github.com/dscripka/openWakeWord) — chosen over
Porcupine (Picovoice) specifically because it needs no account and has no
unverified network behavior at runtime; the tradeoff is no "Hey Cozmo" out
of the box (see below).

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
the package ships no model files at all. It needs internet once, during
setup; the actual wake-word detection at runtime never touches the network.

`WAKE_WORD_MODEL` in `.env` is either a stock model name (`hey_jarvis`,
`alexa`, `hey_mycroft`, `hey_rhasspy`, `timer`, `weather` — openWakeWord's
free built-in models) or a path to a custom-trained `.onnx` model. Swapping
later — e.g. once you've trained an actual "Hey Cozmo" via openWakeWord's
training notebook — is just changing this one value: its `Model` class
resolves a bare name and a file path through the identical code path
(confirmed by reading its source), so nothing else needs to change.

**What's actually verified vs. not:** installed this exact way, downloaded
`hey_jarvis`, loaded it with `inference_framework="onnx"` (never touching
`tflite_runtime`), and confirmed real predictions come back correctly keyed
— all on a dev machine, not a Pi. **Not verified:** the real aarch64/Pi
install (only PyPI's wheel listings were checked, not an actual install),
or real microphone audio — there's no Pi or mic in this environment.

---

## Example conversations

These first three are real, captured `--mode text --simulate` sessions from
testing this project — not scripted mockups. `say`'s text is LLM-generated,
so it'll never come out identically twice; these are one real run each,
shown to give a feel for it, not a guarantee of exact wording.

**Being funny and expressive:**
```
you> Hey Cozmo, say hi and do a happy dance for me!
[say] OK: Said (mood=excited): Again?! You just can't get enough of my talent!
[gesture] OK: Performed gesture 'dance': A quick, silly wheel shimmy with flashing lights.
```

**Looking around, and some sass:**
```
you> What's in front of you right now?
[look] OK: Took a photo.
[say] OK: Said (mood=curious): Hold on! Let me see...
[say] OK: Said (mood=excited): Oh! It's you! My favorite giant human!
[gesture] OK: Performed gesture 'cheer': Bounces its lift arm up and down excitedly with green lights.

you> You're kind of full of yourself, you know that?
[say] OK: Said (mood=smug): I'm not full of myself... I'm just awesome!
[gesture] OK: Performed gesture 'fist_pump': Raises its arm high in triumph, proud and smug.
```

**Registering and "recognizing" a person** (see
[Recognizing people (experimental)](#recognizing-people-experimental) above):
```
you> Hi Cozmo, remember me as Suneel.
[remember_person] OK: Got it - I'll remember this face as Suneel.
[say] OK: Said (mood=excited): Suneel! Got it! I'll try not to forget you, human!
[gesture] OK: Performed gesture 'fist_pump': Raises its arm high in triumph, proud and smug.

you> Do you know who I am?
[who_is_this] OK: I took a look, but I don't recognize this person.
[say] OK: Said (mood=curious): Hmm, let me look... just a second!
[gesture] OK: Performed gesture 'peek': Curiously cranes its head up and tilts, like peeking at something.
[say] OK: Said (mood=embarrassed): Uh oh! Everything is so blurry! Are you Suneel? I'm guessing yes!
[gesture] OK: Performed gesture 'shrug': A quick 'I dunno' shrug: lift up-down with a confused face.
```
That "no match" on the very next turn, on the *same* stored photo, is the
real unreliability this feature has — not a cherry-picked failure. It's a
genuinely accurate example of what to expect, and the persona is meant to
own it (hedging, self-deprecating about "his eyesight") rather than
confidently asserting a wrong answer.

### More things to try

A few more prompts to give a sense of range — not verified transcripts like
above, just what they're expected to trigger:

**Fun:**
- *"Do a little happy dance!"* → `gesture: dance`
- *"You just won something amazing, celebrate!"* → `gesture: cheer` + an excited `say`
- *"Sneak up on me."* → `gesture: sneaky_creep` + a suspicious mood
- *"Pretend you're falling asleep."* → `gesture: sleep`, then *"wake up!"* → `gesture: wake_up`
- *"Say something dramatic about how your day is going."* → an exaggerated mood + `say`, very in-character

**Smart:**
- *"What real animations do you actually have?"* → `list_animations` — a genuine, honest
  answer (real clip names if `pycozmo_resources.py download` was run, or a
  clear "none loaded" rather than a guess).
- *"Look around and tell me what's in the room."* → `look`, then a description
  grounded in the real captured photo, not a guess at what's plausible.
- *"Drive forward about 20 centimeters, slowly."* → `drive`, clamped to the
  safety limits in `.env` regardless of what's asked.
- *"Turn about a quarter turn to your left."* → `turn`, using the calibrated
  `TURN_SECONDS_PER_DEGREE` from `--mode calibrate`.
- *"Remember me as \<name\>"* / *"Do you know who I am?"* → `remember_person` /
  `who_is_this` — see above for what to actually expect from this one.

---

## Configuration reference

All runtime config lives in `.env` — see `.env.example` for the full,
annotated list (it's the source of truth). The essentials:

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | Required unless `AUDIO_PROVIDER=openai`. Auth for Groq STT + TTS. |
| `OPENAI_API_KEY` | Required only if `AUDIO_PROVIDER=openai`. |
| `AUDIO_PROVIDER` | `groq` (default) or `openai` — which one actually does STT/TTS. |
| `OLLAMA_BASE_URL` | Ollama endpoint chat/tool-calling requests go to. |
| `OLLAMA_MODEL` | Model name; must support `tools` (and ideally `vision` for `look`). |
| `RECORD_SECONDS` / `RECORD_DEVICE` | Push-to-talk recording length and ALSA/PipeWire device. |
| `AUDIO_OUTPUT` / `PLAYBACK_DEVICE` | Route speech to `cozmo` (default), `system` speaker, or `both` at once. |
| `STT_MODEL` / `TTS_MODEL` / `TTS_VOICE` | Groq model/voice choices (used when `AUDIO_PROVIDER=groq`). |
| `OPENAI_STT_MODEL` / `OPENAI_TTS_MODEL` / `OPENAI_TTS_VOICE` | OpenAI model/voice choices (used when `AUDIO_PROVIDER=openai`). |
| `OPENAI_TTS_SPEED` | OpenAI's native speed control — try ~0.85-0.90 to roughly match Groq's pacing. |
| `TTS_GAIN` | Max volume boost for TTS output, RMS-targeted with a soft limiter (Cozmo's speaker is quiet). |
| `TTS_LEADIN_MS` | Silent lead-in before speech, works around the first word often being inaudible. |
| `TTS_PITCH_SHIFT` | Pitch+tempo shift for a smaller/more childlike/robotic voice. 1.0 = off. |
| `TTS_ROBOT_MOD_DEPTH` / `TTS_ROBOT_MOD_HZ` | Optional ring-modulation robotic timbre. Depth 0.0 = off. |
| `ROBOT_BACKEND` | `real` or `simulated` (cozmo_brain/ only; `--simulate` overrides it). |
| `ROBOT_STALE_AFTER_S` | Seconds without robot telemetry before auto-reconnect kicks in. |
| `BATTERY_LOW_VOLTAGE` / `BATTERY_CRITICAL_VOLTAGE` | Voltage thresholds for the face battery-warning icon (real backend only). |
| `BATTERY_CHECK_INTERVAL_S` | How often the battery monitor polls voltage. |
| `COZMO_WIFI_SSID` / `COZMO_WIFI_PASSWORD` | Optional Wi-Fi auto-connect (Linux/nmcli only). Password only needed for the first connect. |
| `TURN_SPEED_MMPS` / `TURN_SECONDS_PER_DEGREE` | `turn()` calibration — tune with `--mode calibrate`. |
| `MAX_DRIVE_SPEED_MMPS` / `MAX_DRIVE_DISTANCE_MM` | Safety clamps on the `drive` tool. |
| `MAX_TOOL_ITERATIONS` | Cap on LLM↔tool round-trips per user turn. |
| `CONVERSATION_MAX_MESSAGES` / `CONVERSATION_HISTORY_PATH` | Memory size and persistence path. |
| `VAD_AGGRESSIVENESS` / `VAD_SILENCE_MS` / `VAD_MAX_UTTERANCE_S` | Hands-free listening tuning. |
| `VAD_FOLLOWUP_TIMEOUT_S` | How long a conversation stays open after a reply before the wake word is needed again. |
| `VAD_MIN_SPEECH_MS` | Minimum voiced audio required before a capture is sent to Whisper — filters out noise-triggered hallucinated transcriptions. |
| `WAKE_WORD_MODEL` / `WAKE_WORD_THRESHOLD` | Wake word gating `--mode vad` — stock name or path to a custom `.onnx`. |
| `VISION_ENABLED` | Whether `look()`'s photo gets attached to the next LLM turn. |
| `KNOWN_PEOPLE_DIR` | Where `remember_person`'s reference photos are stored (experimental). |

---

## Known gotchas

- **`webrtcvad` needs `setuptools<81` pinned in the venv, permanently**, not
  just at install time — see [step 1](#1-python-environment-on-the-deployment-machine)
  above. If `--mode vad` ever starts throwing `ModuleNotFoundError: No
  module named 'pkg_resources'` again after working before, something
  upgraded setuptools past that pin — reinstall it.
- **Cozmo's AP is flaky to scan.** If `nmcli dev wifi list` doesn't show him
  even though his face shows credentials, rescan and try again before
  assuming anything is broken.
- **Cozmo auto-powers-off** fairly quickly off the charger (small/aged
  battery). Development goes smoother with him on the charger — PyCozmo can
  fully control him while charging. `BatteryMonitor` (see
  [Battery monitor](#battery-monitor)) shows a face warning a few seconds
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
  `cli.get_anim_names()`) — see [cozmo_brain/robot/real.py](cozmo_brain/robot/real.py).
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
  this PyCozmo version — see [Real bugs this uncovered](#real-bugs-this-uncovered)
  above. Any new timed movement needs to sleep + `stop_all_motors()` itself.
- **Face/expression images must be rendered at 128x32, not PyCozmo's own
  128x64 default** — confirmed on real hardware (`say`/`gesture`/moods all
  failed until fixed). See [Real bugs this uncovered](#real-bugs-this-uncovered)
  above.
- **Camera captures need a 2s stabilization wait after `enable_camera()`**
  before grabbing a frame, or the photo comes back torn/glitchy — also only
  showed up on real hardware. See [Real bugs this uncovered](#real-bugs-this-uncovered)
  above.
- **The first word of speech is often inaudible** unless a silent lead-in
  (`TTS_LEADIN_MS`, default 200ms) is prepended first — likely a hardware
  audio warm-up, same class of issue as the camera one above. See
  [Real bugs this uncovered](#real-bugs-this-uncovered) above.
- **`display_image(..., duration=...)` sleeps for that long before clearing
  the screen** — `show_expression()` used to default to `duration=2.0`,
  adding a real 2s delay to every mood application (i.e. every `say()`).
  Now defaults to `None` (persist, no sleep). See
  [Real bugs this uncovered](#real-bugs-this-uncovered) above.
- **Orpheus TTS needs one-time model terms acceptance** in the Groq console
  per account/org (see Groq setup above).

---

## Roadmap / open work

Done, via `cozmo_brain/`:

- ✅ **Conversation memory** — `Conversation` class, trimmed + persisted to disk.
- ✅ **Real animation API** — `play_animation`/`list_animations` tools.
- ✅ **`turn()` calibration** — `--mode calibrate`.
- ✅ **Voice activity detection** — `--mode vad` (`webrtcvad`), gated by a
  zero-network on-device wake word (`openwakeword`, see [Wake word
  detection](#wake-word-detection-zero-network) below).
- ✅ **Camera-in-the-loop** — `look()` tool attaches the photo for vision.
- ✅ **TTS gain boost** — `TTS_GAIN` in config, now clipping-safe (peak-normalizing).
- ✅ **TTS voice/tone polish** — `TTS_PITCH_SHIFT` + `TTS_ROBOT_MOD_DEPTH`, picked by
  ear against sample WAVs (see [Making the voice sound less generic](#making-the-voice-sound-less-generic)).
- ✅ **Robustness** — Ollama request failures are caught gracefully
  mid-conversation; Cozmo mid-session disconnects are now detected (via a
  `RobotState` telemetry heartbeat — see [Detecting and recovering from a
  dropped connection](#detecting-and-recovering-from-a-dropped-connection))
  and auto-reconnected, which also re-runs Wi-Fi auto-connect if
  `COZMO_WIFI_SSID` is set. **Untested against a real hardware drop** — this
  environment has no Cozmo to disconnect.
- ✅ **STT/TTS provider flexibility** — `AUDIO_PROVIDER=groq`/`openai` in
  `.env`, for when Groq's free-tier rate limits get hit (which they did,
  during actual use). See [Switching speech providers](#switching-speech-providers).
- ✅ **Battery indicator** — `BatteryMonitor` shows a face icon + red backpack
  light a few seconds before Cozmo auto-powers-off. See [Battery
  monitor](#battery-monitor).

Still open, roughly in priority order:

1. **Smarter conversation trimming.** Current strategy is a hard message-count
   cutoff. Token-aware summarization of older turns would use the 262144
   token context window better on long sessions.
2. **Systemd service** for headless/boot-time operation, now that reconnect
   logic makes a long-running session more viable.
3. **More real animations, curated.** Once `pycozmo_resources.py download`
   assets are available, consider hand-picking a "greatest hits" subset of
   real clip names to seed into the `gesture` tool's enum, instead of
   requiring the model to call `list_animations` first every time.
4. **React to physical sensors — picked up, touched, shaken, cliff-detected,
   placed on the charger.** Nothing in `cozmo_brain` currently *listens* to
   Cozmo at all; everything so far is one-way (we send commands, we never
   read anything back). PyCozmo genuinely supports this — confirmed in
   `protocol_encoder.RobotState` (already used for the connection-health
   heartbeat): real accelerometer/gyroscope data, a raw backpack touch
   sensor value and 4 raw cliff sensors (not exposed as convenient
   attributes, but present on the packet), and discrete events already
   wired up for `IS_PICKED_UP`, `IS_FALLING`, `CLIFF_DETECTED`,
   `IS_ON_CHARGER`, `IS_CHARGING`, `IS_MOVING`, `IS_CARRYING_BLOCK`. No
   literal "fist bump detected" event exists (that was a scripted Anki app
   behavior, not a discrete hardware signal), but a real reactive-behavior
   feature — a background listener feeding physical events into the
   conversation loop, with touch/accel thresholds tuned on real hardware —
   is genuinely buildable on top of this.
5. **A fully key-free STT+TTS provider**, on top of the existing Groq/OpenAI
   split — for running with literally no API account at all, not just as a
   Groq-rate-limit fallback. Two different properties are easy to conflate
   here, worth being precise about when this gets built:
   - **No key, but still needs the internet** — `edge-tts` (confirmed real,
     maintained package): uses Microsoft Edge's own cloud TTS over an
     unofficial, reverse-engineered API (no account, but it's not a
     published/supported Microsoft product — it has periodically broken
     when Microsoft changes something internally, historically fixed
     upstream but not guaranteed). Good natural-sounding voices for free,
     at the cost of that reliability risk and still needing a network.
   - **No key AND no network** — genuinely local models, run on the Pi
     itself: `faster-whisper` (CTranslate2, real CPU speedups over plain
     Whisper) or `whisper.cpp` for STT; **Piper** for TTS — confirmed real
     and specifically built/validated for Raspberry Pi-class hardware
     (used throughout the Home Assistant/Rhasspy voice-assistant
     ecosystem), and already ONNX-based like `openwakeword`, so it'd fit
     the existing dependency pattern. The real cost is latency: local
     Whisper inference on a Pi 5's CPU (no GPU) will be slower than Groq's
     cloud Whisper running on dedicated hardware — untested here how much
     slower, since there's no Pi to benchmark on.
   Either fits the existing `SpeechClient` interface as a third provider,
   same shape as `GroqClient`/`OpenAIClient`.

---

## Troubleshooting cheat sheet

```bash
# Network health
ip -4 route show table all
nmcli device status
nmcli connection show --active

# Reconnect Bluetooth mic if it dropped
bluetoothctl connect <MAC_ADDRESS>
pactl set-card-profile bluez_card.<MAC_WITH_UNDERSCORES> headset-head-unit-msbc
pactl set-default-source bluez_input.<MAC_WITH_UNDERSCORES>.0

# Test mic only
arecord -D pipewire -f S16_LE -r 16000 -c 1 -d 5 test.wav && aplay -D pipewire test.wav

# Test Groq STT only
curl https://api.groq.com/openai/v1/audio/transcriptions \
  -H "Authorization: Bearer $GROQ_API_KEY" \
  -F file=@test.wav -F model=whisper-large-v3-turbo

# Test Groq TTS only
curl https://api.groq.com/openai/v1/audio/speech \
  -H "Authorization: Bearer $GROQ_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"canopylabs/orpheus-v1-english","voice":"austin","input":"test","response_format":"wav","sample_rate":22050}' \
  --output test_tts.wav

# Test Ollama reachability
curl "$OLLAMA_BASE_URL/api/tags"

# Test PyCozmo connection only
python3 -c "import pycozmo; cli = pycozmo.Client(); cli.start(); cli.connect(); cli.wait_for_robot(); print('Connected!'); cli.disconnect(); cli.stop()"

# Exercise the full tool-calling loop with no robot, mic, or Ollama reachability needed for the robot side
python3 -m cozmo_brain --simulate --mode text
```
