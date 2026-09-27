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
- [Groq](https://console.groq.com) API key — used for STT (Whisper) and TTS
  (Orpheus) by default; `STT_PROVIDER`/`TTS_PROVIDER` can each independently
  switch to OpenAI instead (see [Configuration reference](#configuration-reference))
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
    │   ├── chat_client.py      # ChatClient interface (CHAT_PROVIDER=ollama/groq/openai)
    │   ├── openai_compatible_chat.py  # Shared chat-completions impl for GroqChatClient/OpenAIChatClient (tool-calling + vision wire translation)
    │   ├── speech_client.py    # SpeechClient interface shared by every STT/TTS provider
    │   ├── groq_client.py       # Groq Whisper STT + Orpheus TTS + GroqChatClient
    │   ├── openai_client.py     # OpenAI Whisper STT + TTS + OpenAIChatClient (STT_PROVIDER/TTS_PROVIDER/CHAT_PROVIDER=openai)
    │   ├── local_client.py      # Offline faster-whisper STT + Piper TTS (STT_PROVIDER/TTS_PROVIDER=local)
    │   ├── witai_client.py      # Wit.ai (Meta) STT only, free/no billing (STT_PROVIDER=witai)
    │   ├── edge_client.py       # Microsoft Edge online TTS only, free/no key (TTS_PROVIDER=edge)
    │   ├── tts_postprocess.py    # shared voice character + gain, used by both providers
    │   └── stt_postprocess.py    # filters known Whisper hallucination phrases
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
python3 -m cozmo_brain --mode vad         # hands-free — say the wake word OR tap Cozmo, then talk (needs webrtcvad + openwakeword)
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

`--mode vad` accepts either the wake word or a gentle tap on Cozmo's body as
the activation trigger — whichever comes first (see
`modes/vad_mode.py`/`_wait_for_wake_word_or_tap`). A tap is detected as a
brief accelerometer-magnitude spike above a rolling baseline
(`TAP_THRESHOLD`/`TAP_DEBOUNCE_MS`, see `.env.example` and `robot/real.py`),
gated on Cozmo's own `IS_PICKED_UP` status flag so being picked up/carried
isn't mistaken for a tap — real backend only (`--simulate` falls back to a
keypress in place of a tap; wake-word detection still needs a real mic
either way). Once either trigger fires, everything else — VAD-gated
listening, the follow-up window, hallucination filtering — is the exact
same code path as before; tapping is just a second way in. See [item 4 in
the roadmap](#roadmap--open-work) for how the threshold was picked, and
why this ended up folded into `--mode vad` instead of a standalone mode.

### Tools available to the LLM

| Tool | Does |
|---|---|
| `say(text, mood, gesture)` | Speaks via Groq TTS (routed per `AUDIO_OUTPUT`), striking a matching facial expression + backpack light color first. The optional `gesture` plays a choreography concurrently with the speech (guaranteed overlap — see below), rather than before or after it. A mood that raises the lift (`happy`/`excited`/`proud`/`smug`) is lowered again automatically once the reply finishes. |
| `gesture(name)` | Plays a curated multi-step choreography (face + lights + head/arm + wheels) **on its own, with no speech** — pairs with `say` only sequentially, never simultaneously (use `say`'s own `gesture` argument for that). |
| `play_animation(name)` | Plays a **real** Anki animation clip or group by exact name. |
| `list_animations()` | Lists the real animation/group names actually loaded on this robot. |
| `drive(distance_mm, speed_mmps)` | Drives straight, clamped to safe limits. |
| `turn(angle_degrees)` | Turns in place (calibrated via `TURN_SECONDS_PER_DEGREE`). |
| `dock()` | Reverses onto the charger, with the platform's known false cliff-detection trigger suppressed. Doesn't search for/align to the charger — only works already close and facing away from it. **Not yet verified against real hardware.** |
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
layer instead, so it works regardless of provider — but it took two tries:

The **first attempt** required `VAD_MIN_SPEECH_MS` (300ms) of total
*accumulated* voiced audio — summed across the whole capture — before
accepting it. **Found on real hardware to be the wrong metric**: a real,
short reply logged only 90ms of accumulated speech frames out of a 900ms
capture and got silently discarded exactly like the noise case it was meant
to catch, breaking real hands-free conversation (a pause before speaking
made this much more likely to hit, since `webrtcvad`'s per-frame
classification is genuinely conservative/noisy on a real Bluetooth mic, not
just on noise).

The **corrected version** treats `VAD_MIN_SPEECH_MS` as an onset debounce
instead: it requires that many *consecutive* milliseconds of speech before
committing to a recording at all (default now 60ms, i.e. 2 frames) — still
enough to reject a single isolated 30ms false-positive frame, but with no
further minimum once that debounce is satisfied, since real words can be
legitimately brief. Verified with a scripted fake-mic test covering all four
shapes: a single noise frame (rejected), the exact 90ms real-utterance shape
that was wrongly rejected before (now accepted), a normal longer utterance
(accepted), and total silence (rejected, unchanged).

**Confirmed on real hardware that the VAD-side fix alone isn't sufficient**
in a genuinely noisy environment: the corrected onset debounce fixed the
false-rejection of real short replies, but background noise in a real room
can still reliably clear even a 60ms/2-frame bar, and the resulting
noise-only clips kept hallucinating — with the *same exact* phrases
recurring verbatim across many separate capture windows in a quiet room
with nothing actually said or playing (`"Thank you for watching"` and a
real MBC news anchor's sign-off, both among the most widely-reported
Whisper hallucination phrases). That repetition is itself informative:
these hosted Whisper endpoints decode close to greedily, so a similar
noise/silence profile tends to reproduce the same memorized caption each
time rather than a random one.

Added a second, independent layer as a backstop:
`cozmo_brain/llm/stt_postprocess.py`'s `is_likely_hallucination()` checks
transcribed text against a known-phrase list (both modes call it right
after `speech.transcribe()`, treating a match the same as "heard nothing").
Matching is exact after normalizing case/punctuation/whitespace, not fuzzy
or substring-based — deliberately, so it can never misfire on real speech
that happens to share words with a listed phrase. Verified against the
exact strings reported on real hardware (including the trailing-punctuation
variants actually seen) plus real sentences from the same session,
confirming zero false positives on the latter. **This list is necessarily
incomplete** — it's a pragmatic backstop for known repeat offenders, not a
general hallucination detector; new recurring phrases will need adding as
they show up. **Important limitation, raised directly on real hardware:**
this filter runs *after* the (rate-limited) STT API call already happened —
it hides the wrong reply, but doesn't save any quota. Confirmed on real
hardware that in a genuinely noisy room, noise cleared the onset debounce
often enough to burn through a free-tier STT quota on nothing but ambient
noise.

The actual quota-saving fix has to happen *before* the STT call, at the
capture gate itself: `webrtcvad` only looks at spectral shape, not
loudness, so it can't distinguish quiet background noise that happens to
look speech-shaped from someone actually talking into the mic. `VAD_MIN_RMS`
adds an independent loudness floor a frame must *also* clear (16-bit PCM
RMS scale, 0–32767) before counting as speech — quiet ambient noise
essentially never reaches typical near-mic speech volume. **Unverified
default (150)** — there was no real audio available here to calibrate it;
`cozmo_brain/audio/vad.py` now logs the actual peak RMS seen on both
accepted and rejected captures specifically so this can be tuned from real
data instead of guessed again (compare a rejected-noise log line's peak RMS
against an accepted-speech one to pick a number that sits between them).
Verified the gating logic itself with a scripted test using synthetic PCM
frames at controlled amplitudes (quiet-but-webrtcvad-flagged noise →
rejected; loud real speech → accepted; loud audio webrtcvad doesn't
classify as speech → still rejected). If noise still gets through after
tuning `VAD_MIN_RMS`, also worth trying `VAD_AGGRESSIVENESS=3` (stricter
webrtcvad noise rejection) — untested against this specific environment.

**Found on real hardware, crashed the whole process:** an STT request to
OpenAI (`STT_PROVIDER=openai`) returned `400 Bad Request` mid-`--mode vad`
session, and nothing caught it — `resp.raise_for_status()` in
`llm/openai_client.py`/`llm/groq_client.py` raises on any non-2xx response,
but neither `vad_mode.py` nor `interactive.py` wrapped their
`speech.transcribe()` call in anything, so the exception propagated all the
way up and killed the process (contrast `engine.py`, which already catches
`requests.RequestException` around its Ollama calls the same way). Bad for
push-to-talk, worse for hands-free — nobody's watching an unattended
session to restart it. Both call sites now catch `requests.RequestException`
and treat it the same as "heard something, nothing transcribable" (logs a
warning, keeps the follow-up window/loop open, moves on) instead of
crashing. Also surfaced a second issue while fixing the first:
`raise_for_status()` discards the response body, so the original crash's
traceback showed only a generic `400 Client Error: Bad Request` with no clue
why — the catch now logs `e.response.text`, which for OpenAI/Groq's STT
endpoints carries the actual `error.message` explaining the rejection.
**Root cause of that specific 400 not yet identified** — the fix makes it
survivable and diagnosable next time, not preventable; if it recurs, the
logged response body is the next thing to look at.

**Found on real hardware:** `Tool 'say' failed: Unknown mood 'offended'.`
— the model called `say(mood="offended")`, a word that was never a real
mood (`_MOOD_NAMES`, from `robot/moods.py`, has no such entry). The `say`
tool's JSON schema already restricts `mood` to a proper `enum` of the real
names (`tools/registry.py`), but that alone clearly isn't enough — the
model doesn't reliably respect it. Root cause: `personality.py`'s own
persona description said Cozmo is "easily delighted or offended by the
smallest things" — the model read that, decided "offended" sounded like a
plausible mood, and called it verbatim. Same issue, one line down:
"comically devastated by small setbacks" ("devastated" isn't a mood
either). Fixed both wording spots (swapped for real mood words —
`excited`/`annoyed`, `sad`), and added a second line of defense: the
prompt now also spells out the exact closed vocabulary of valid moods and
gestures at the end, built from `MOODS`/`GESTURES` directly (not
hardcoded, so it can't drift out of sync if either dict changes) rather
than relying on the schema `enum` alone. **Not yet confirmed whether this
fully prevents a recurrence** — it removes the specific prompt wording
that caused this one and adds a stronger hint, but nothing stops the model
from inventing a different plausible-sounding word next time; if it
happens again, that's the next thing to strengthen.

**Found on real hardware, froze the whole process, had to be restarted:**
`aplay` (system-speaker output, `audio/player.py`'s `play_wav()`) hung
indefinitely mid-playback — the log showed it start, then nothing for
several minutes except the unrelated battery monitor still ticking on its
own thread, proving the main thread itself was stuck, not the process as
a whole. Root cause: `subprocess.run(["aplay", ...], check=True)` had no
`timeout` at all, unlike `robot.say_wav()`'s own `cli.wait_for(...,
timeout=30)` for Cozmo's speaker — the system-audio path had no equivalent
escape hatch. A stuck Bluetooth speaker/PipeWire sink (not confirmed which)
could block that call forever, and with `AUDIO_OUTPUT=both`,
`system_thread.join()` (also no timeout) would then block the main thread
right along with it. Fixed by adding `timeout=30` (matching `say_wav()`'s
value, not independently tuned) to the `subprocess.run()` call, catching
`subprocess.TimeoutExpired` (`subprocess.run()` already kills the hung
process by the time this fires) and `CalledProcessError`, and logging a
warning instead of letting either propagate — a stuck playback device
now can't freeze a turn, or the whole hands-free session, over one bad
output. **Root cause of *why* aplay hung not identified** — same caveat
as the STT-crash fix above: this makes it survivable and diagnosable
(the warning log), not preventable.

**Follow-up, ruling out a red herring:** repeated
`AnimationController.IsReadyToPlay.BufferStarved` messages (from
`pycozmo.robot`, decoded firmware debug messages) showed up in the log
around the same incident, raising the question of whether *that* was the
actual cause instead. Traced it: PyCozmo's own reference CLI (`run.py`)
defaults that exact logger to `WARNING` specifically because this message
is routine chatter — the animation engine reporting "nothing queued to
play right now," true any time Cozmo isn't actively mid-playback, logged
too often to be useful at INFO. `main.py` was never applying that same
suppression (only `pycozmo.protocol`'s was), which is why it was visible
at all. Now applied the same way, so this stops being clutter — and,
more importantly, stops being a plausible-looking but wrong lead the next
time something actually hangs.

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

### Switching speech providers (and, separately, chat)

Groq's free-tier rate limits are real — this happened during actual use,
not just as a theoretical risk. `STT_PROVIDER` and `TTS_PROVIDER` in `.env`
each independently pick `groq` (default) or `openai` — they don't have to
match, e.g. Groq STT + OpenAI TTS is valid:

```env
STT_PROVIDER=groq
TTS_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

All three providers implement the same `SpeechClient` interface
(`transcribe()`/`synthesize()`), so nothing else in the app — `tools/registry.py`,
every mode — needs to know which one is actually active. `create_speech_client()`
in `cozmo_brain/llm/__init__.py` builds one client per provider actually needed
(reusing a single instance if `STT_PROVIDER` and `TTS_PROVIDER` match) and
returns a small router that sends `transcribe()` to the STT one and
`synthesize()` to the TTS one.

A third option, `local`, runs fully offline — no API key, no rate limits,
no network after a one-time model download: `faster-whisper` (CTranslate2)
for STT, `Piper` for TTS (`cozmo_brain/llm/local_client.py`). Both engines
load lazily on first actual use, so picking `local` for only one of
STT/TTS never loads the other engine. Trade-off is latency, not
correctness: CPU-only Whisper inference on a Pi is slower than Groq's
cloud Whisper on dedicated hardware — **untested here how much slower**,
since there's no Pi available to benchmark on; `LOCAL_STT_MODEL` and
`LOCAL_TTS_VOICE_PATH` are both easy to drop to a smaller/faster
model/voice via `.env` alone if it's not responsive enough. Also worth
noting: the currently maintained `piper-tts` package is GPL-3.0-or-later
(the original MIT-licensed `rhasspy/piper` repo was archived in favor of
a GPL fork) — see the note in `requirements.txt`.

`LOCAL_STT_MODEL`'s first use downloads a model from Hugging Face — nothing
in `local_client.py` overrides where to, so it lands in `faster-whisper`'s
own default cache (confirmed against its source, `faster_whisper/utils.py`:
`"base"` resolves to the repo `Systran/faster-whisper-base`, fetched via
`huggingface_hub.snapshot_download()`):

| OS | Default cache path |
|---|---|
| Linux (the Pi) | `~/.cache/huggingface/hub/models--Systran--faster-whisper-<size>/` |
| Windows | `%USERPROFILE%\.cache\huggingface\hub\models--Systran--faster-whisper-<size>\` |

Override the location with the `HF_HOME` env var if it needs to go
somewhere else (e.g. a different disk on the Pi). To pre-warm the model
before a real test, so the first STT call isn't slowed by the download:
`python3 -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')"`.

A fourth option, `witai` (`STT_PROVIDER` only — see
`cozmo_brain/llm/witai_client.py`), uses Wit.ai (Meta): free, no billing,
but it needs its own account and `WITAI_ACCESS_TOKEN` (get one at
[wit.ai](https://wit.ai) — log in with a Facebook/Meta account, create an
app, then Settings → API Details → Server Access Token). Wit.ai has no
text-to-speech product at all, so it can only ever be `STT_PROVIDER`;
setting `TTS_PROVIDER=witai` raises `NotImplementedError` from
`synthesize()` instead of silently misbehaving.

**Verified directly against a real account/token** (a synthesized test
WAV, transcribed accurately): `Content-Type: audio/wav` for a plain WAV
file is correct, and the response really is multiple JSON objects
concatenated in one body — but NOT one-object-per-line NDJSON as first
guessed; each object is itself pretty-printed across several lines
(partial transcriptions building up word-by-word, ending in one marked
`"is_final": true`). `_parse_transcript()` decodes each top-level object
in sequence regardless of internal newlines, rather than naively
splitting on lines (which fails on every single line, since none are
valid JSON alone). **An anomaly seen twice during verification, worth
taking seriously rather than dismissing as noise:** 2 of ~9 test calls
(different audio each time, surrounding calls all correct) returned the
exact same unrelated transcript, verbatim — "Hey Facebook." — not two
different garbage strings, the identical phrase both times. That
specificity points away from random corruption and toward something
systematic, e.g. a low-confidence fallback baked into a fresh, completely
untrained Wit.ai app (possibly example/demo content from Wit.ai's own
default app template). **Confirmed recurring on real testing** — "hey
facebook" is now in `stt_postprocess.py`'s known-phrase filter, which
turned out fine to fold directly into the same list as Whisper's own
hallucination phrases: every call site there just wants "is this text
probably not real speech" regardless of which provider produced it, and
each entry's own comment records which provider/mechanism it's actually
from, so the list stays honest about provenance without needing a
parallel function. Latency,
measured from a dev machine (not the Pi — real Pi numbers will differ):
~1-2s per short utterance. Worth weighing before committing to it either
way: it's Meta's servers
processing the audio.

A fifth option, `edge` (`TTS_PROVIDER` only — see
`cozmo_brain/llm/edge_client.py`), uses Microsoft Edge's online TTS via the
unofficial `edge-tts` library: free, no API key, needs internet (an
unofficial, reverse-engineered endpoint, not a published/supported
Microsoft product — can break if Microsoft changes something internally).
Edge has no speech-to-text counterpart, so it can only ever be
`TTS_PROVIDER`; setting `STT_PROVIDER=edge` raises `NotImplementedError`
from `transcribe()` instead of silently misbehaving. `EDGE_TTS_VOICE`
picks a stock voice (list them all with `edge-tts --list-voices`);
`EDGE_TTS_SPEED` uses the same `>1.0`=faster direction as the other
providers, converted to edge-tts's own signed-percentage `rate` string.

**Real integration cost, not just a drop-in:** edge-tts's own service only
ever returns MP3 — confirmed against its source (`communicate.py`): the
output format is hardcoded to `audio-24khz-48kbitrate-mono-mp3`, not
configurable at all. `tts_postprocess.py`'s pitch/gain/ring-mod pipeline is
built on Python's stdlib `wave` module, which can't read MP3, so
`edge_client.py` decodes MP3 → raw PCM → a WAV container via `PyAV` (the
`av` package) before handing it off — same shape every other provider
already produces. PyAV ships FFmpeg bundled in its own wheel (no system
`ffmpeg` install needed), and is already a dependency of `faster-whisper`
(`STT_PROVIDER=local`), so it's often already installed for free. **Verified
directly, not assumed:** measured the decode step in isolation — ~8ms for
a ~4.6s clip (after one-time codec init), negligible next to the ~1.1s
network+synthesis round-trip it followed. The full pipeline (real network
call → MP3 → PyAV decode → WAV → `tts_postprocess.py`'s resample/pitch/
gain → file) was run end-to-end through the actual `create_speech_client()`
factory and produced a correct, correctly-resampled WAV — not just unit
logic. **Confirmed working on real hardware**, including noticeably higher
latency than the other providers on real testing (consistent with the
~1.1s network+synthesis round-trip measured from a dev machine above,
likely compounded by the Pi's own network path and slower MP3 decode) —
worth factoring in if responsiveness matters more than voice quality/cost.

`LOCAL_STT_MODEL` also accepts a full Hugging Face repo id directly (any
string containing a `/`), not just the short size names above — confirmed
against `download_model()`'s source: a `/` only changes how the repo id is
resolved (skips the short-name lookup table, treats the value as a literal
repo id instead), it doesn't change *where* the download goes. Same cache,
same `HF_HOME` override, just a different subfolder name for whichever
repo you pointed at. This makes **distil-whisper** models a drop-in,
zero-code-change option worth trying for exactly the `tiny`/`base`-speed
vs. `small`-accuracy trade-off: they're real CTranslate2 conversions
published under `Systran/faster-distil-whisper-{small,medium,large-v3}.en`
(English-only), claimed ~6x faster than the equivalent full-size Whisper
model at roughly 1% worse WER — i.e. built specifically to close that gap:

```env
LOCAL_STT_MODEL=Systran/faster-distil-whisper-small.en
```

Chat/tool-calling (the "brain" — see [Architecture](#architecture)) has its
own independent `CHAT_PROVIDER` setting and a matching `create_chat_client()`
factory + `ChatClient` interface (`cozmo_brain/llm/chat_client.py`), mirroring
the STT/TTS setup above. `ollama` (default), `groq`, and `openai` are all
implemented — `GroqChatClient`/`OpenAIChatClient` (in `groq_client.py`/
`openai_client.py`) both wrap `OpenAICompatibleChatClient`
(`cozmo_brain/llm/openai_compatible_chat.py`), since Groq's and OpenAI's
chat-completions endpoints share an identical request/response shape —
only the base URL, key, and model name (`GROQ_CHAT_MODEL`/`OPENAI_CHAT_MODEL`)
differ.

This isn't a trivial swap, though: Ollama's own `/api/chat` message
conventions — which `Conversation`/`CozmoEngine` build internally, since
Ollama was the only backend until now — aren't wire-compatible with
OpenAI-compatible endpoints. Concretely, Ollama accepts a plain-dict
`arguments` object and doesn't require pairing a `tool` message to a
specific prior tool call; OpenAI/Groq **require** `function.arguments` as
a JSON-encoded *string* and every `tool` message to carry a `tool_call_id`
matching the originating `tool_calls[].id` — get either wrong and the API
rejects the request outright (400) on the very first tool-calling turn, not
a silent misbehavior. `openai_compatible_chat.py`'s `_to_wire_messages()`
translates at the boundary, and `ToolCall` now carries an `id` (generated
by `OllamaClient` too, if Ollama's own response didn't include one) so
`engine.py`/`conversation.py` can thread it through without needing to know
which provider is actually active.

**Vision is translated too:** `_to_wire_messages()` converts Ollama's own
`"images"` message field (a plain list of base64 strings, used by both
`look`'s vision-in-the-loop attachment and `who_is_this`'s direct
`ollama.chat(...)` face-comparison call) into OpenAI's content-array
`image_url` format, sniffing the real image format from its magic bytes
rather than trusting a file extension — `who_is_this` compares a
`look`-captured PNG against `remember_person`-saved `.jpg` reference
photos in the same call, so a single hardcoded mime type would be wrong
for one side of that comparison.

Each chat provider also gets its own vision-model override —
`OLLAMA_VISION_MODEL` / `GROQ_VISION_MODEL` / `OPENAI_VISION_MODEL` —
used instead of the regular chat model on any turn that actually carries
an image. This matters because a provider's default chat model isn't
necessarily vision-capable: `GROQ_CHAT_MODEL`'s default
(`llama-3.3-70b-versatile`) isn't, so `GROQ_VISION_MODEL` has its own real
default (`qwen/qwen3.8-27b`, currently Groq's vision + tool-calling
model); `OPENAI_CHAT_MODEL`'s default (`gpt-4o-mini`) already handles
vision itself, so `OPENAI_VISION_MODEL` is left empty (falls back) by
default. Same idea for Ollama — pick a vision-capable `OLLAMA_MODEL`
directly, or set `OLLAMA_VISION_MODEL` instead if the chat model you want
for tool-calling isn't vision-capable.

Beyond just a different *model*, `VISION_PROVIDER` can also point at a
completely different *provider* than `CHAT_PROVIDER` — e.g.
`CHAT_PROVIDER=ollama` with `VISION_PROVIDER=groq` keeps regular chat/
tool-calling on Ollama but routes only image-bearing turns to Groq
instead, in any combination of the three. Left empty (default), it falls
back to `CHAT_PROVIDER` (same provider, just possibly a different model,
as above). `create_chat_client()` builds one client per provider actually
needed (same as `create_speech_client()` does for STT/TTS) and, only when
the two providers actually differ, wraps them in a small `_ChatRouter`
that dispatches `chat()` to whichever one the current turn needs — when
they're the same provider, the plain client is returned directly, so
nothing changes from before this existed.

Either way — same-provider model switch or cross-provider routing — the
decision of which to use checks the whole conversation history, not just
the latest turn, so once an image has been attached the vision model/
provider stays selected until that message ages out of
`CONVERSATION_MAX_MESSAGES` — harmless since a vision-capable model still
handles plain text/tool-calling turns fine, just not the cheapest choice
for those turns.

**Not yet verified against a real Groq/OpenAI account** — both the
tool-calling and vision message translation were unit-checked directly
(round-tripped sample exchanges through `_to_wire_messages()`, confirmed
image mime-sniffing against real PNG/JPEG magic bytes, and confirmed the
vision-model switch picks correctly), and client construction was checked
against the real `.env` keys, but no live turn has actually been run
against either endpoint yet.

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
| `GROQ_API_KEY` | Required unless both `STT_PROVIDER` and `TTS_PROVIDER` are `openai`. Auth for Groq STT + TTS. |
| `OPENAI_API_KEY` | Required if `STT_PROVIDER` or `TTS_PROVIDER` is `openai`. |
| `WITAI_ACCESS_TOKEN` | Required if `STT_PROVIDER` is `witai`. Free, no billing — Wit.ai (Meta) has no TTS product, so this is STT-only. |
| `STT_PROVIDER` / `TTS_PROVIDER` | `groq` (default), `openai`, or `local`, set independently — which provider does STT vs. TTS; they don't have to match. `STT_PROVIDER` can also be `witai`; `TTS_PROVIDER` can also be `edge`. |
| `CHAT_PROVIDER` | Which provider does chat/tool-calling: `ollama` (default), `groq`, or `openai`. |
| `VISION_PROVIDER` | Which provider handles image-bearing turns (`look`/`who_is_this`) — independent of `CHAT_PROVIDER`, e.g. `ollama` for chat + `groq` for vision. Empty = same as `CHAT_PROVIDER`. |
| `CHAT_TIMEOUT_S` | Request timeout for `CHAT_PROVIDER`=`groq`/`openai` chat calls (Ollama has its own `OLLAMA_TIMEOUT_S`). |
| `GROQ_CHAT_MODEL` / `OPENAI_CHAT_MODEL` | Model name for `CHAT_PROVIDER`=`groq`/`openai` — must support tool-calling. |
| `OLLAMA_VISION_MODEL` / `GROQ_VISION_MODEL` / `OPENAI_VISION_MODEL` | Model used instead, per chat provider, on any turn with an image attached (`look`/`who_is_this`). Empty = fall back to that provider's regular chat model. |
| `OLLAMA_BASE_URL` | Ollama endpoint chat/tool-calling requests go to. |
| `OLLAMA_MODEL` | Model name; must support `tools`. Doesn't need `vision` too unless `OLLAMA_VISION_MODEL` is left empty. |
| `RECORD_SECONDS` / `RECORD_DEVICE` | Push-to-talk recording length and ALSA/PipeWire device. |
| `AUDIO_OUTPUT` / `PLAYBACK_DEVICE` | Route speech to `cozmo` (default), `system` speaker, or `both` at once. |
| `GROQ_STT_MODEL` / `GROQ_TTS_MODEL` / `GROQ_TTS_VOICE` | Groq model/voice choices (used when `STT_PROVIDER`/`TTS_PROVIDER`=`groq`). |
| `GROQ_TTS_SPEED` | Groq's native speed control (0.5-5.0, default 1.0). |
| `OPENAI_STT_MODEL` / `OPENAI_TTS_MODEL` / `OPENAI_TTS_VOICE` | OpenAI model/voice choices (used when `STT_PROVIDER`/`TTS_PROVIDER`=`openai`). |
| `OPENAI_TTS_SPEED` | OpenAI's native speed control — try ~0.85-0.90 to roughly match Groq's pacing. |
| `LOCAL_STT_MODEL` / `LOCAL_STT_DEVICE` / `LOCAL_STT_COMPUTE_TYPE` | faster-whisper model size/device/compute type (used when `STT_PROVIDER`=`local`). No key, no network after the first model download. |
| `LOCAL_TTS_VOICE_PATH` | Path to a downloaded Piper `.onnx` voice model (used when `TTS_PROVIDER`=`local`). |
| `LOCAL_TTS_SPEED` | Speed, same `>1.0`=faster direction as `GROQ_TTS_SPEED`/`OPENAI_TTS_SPEED` (Piper's own `length_scale` is the inverse — converted in `local_client.py`). |
| `EDGE_TTS_VOICE` | Stock `edge-tts` voice name (used when `TTS_PROVIDER`=`edge`). List them with `edge-tts --list-voices`. |
| `EDGE_TTS_SPEED` | Speed, same `>1.0`=faster direction as the other `*_TTS_SPEED` settings (converted to edge-tts's own signed-percentage `rate` string). |
| `TTS_GAIN` | Max volume boost for TTS output, RMS-targeted with a soft limiter (Cozmo's speaker is quiet). |
| `TTS_LEADIN_MS` | Silent lead-in before speech, works around the first word often being inaudible. |
| `TTS_PITCH_SHIFT` | Pitch+tempo shift for a smaller/more childlike/robotic voice. 1.0 = off. |
| `TTS_ROBOT_MOD_DEPTH` / `TTS_ROBOT_MOD_HZ` | Optional ring-modulation robotic timbre. Depth 0.0 = off. |
| `ROBOT_BACKEND` | `real` or `simulated` (cozmo_brain/ only; `--simulate` overrides it). |
| `ROBOT_STALE_AFTER_S` | Seconds without robot telemetry before auto-reconnect kicks in. |
| `BATTERY_LOW_VOLTAGE` / `BATTERY_CRITICAL_VOLTAGE` | Voltage thresholds for the face battery-warning icon; `BATTERY_LOW_VOLTAGE` also gates whether `drive`/`turn` refuse to move while charging (real backend only). |
| `BATTERY_CHECK_INTERVAL_S` | How often the battery monitor polls voltage. |
| `COZMO_WIFI_SSID` / `COZMO_WIFI_PASSWORD` | Optional Wi-Fi auto-connect (Linux/nmcli only). Password only needed for the first connect. |
| `TURN_SPEED_MMPS` / `TURN_SECONDS_PER_DEGREE` | `turn()` calibration — tune with `--mode calibrate`. |
| `MAX_DRIVE_SPEED_MMPS` / `MAX_DRIVE_DISTANCE_MM` | Safety clamps on the `drive` tool. |
| `CHARGER_EXIT_DISTANCE_MM` / `CHARGER_EXIT_SPEED_MMPS` | How far/fast to drive straight off the charger before performing a requested turn, if still docked (real backend only). |
| `CHARGER_DOCK_DISTANCE_MM` / `CHARGER_DOCK_SPEED_MMPS` | How far/fast the `dock` tool reverses onto the charger (real backend only). |
| `MAX_TOOL_ITERATIONS` | Cap on LLM↔tool round-trips per user turn. |
| `CONVERSATION_MAX_MESSAGES` / `CONVERSATION_HISTORY_PATH` | Memory size and persistence path. |
| `VAD_AGGRESSIVENESS` / `VAD_SILENCE_MS` / `VAD_MAX_UTTERANCE_S` | Hands-free listening tuning. |
| `VAD_FOLLOWUP_TIMEOUT_S` | How long a conversation stays open after a reply before the wake word is needed again. |
| `VAD_MIN_SPEECH_MS` | Onset debounce (consecutive ms of speech needed to start recording, not a minimum utterance length) — filters out noise-triggered hallucinated transcriptions. |
| `VAD_MIN_RMS` | Loudness floor a frame must also clear (in addition to webrtcvad) to count as speech — the actual quota-saving filter, rejects noise before it ever reaches the STT API. |
| `WAKE_WORD_MODEL` / `WAKE_WORD_THRESHOLD` | Wake word gating `--mode vad` — stock name or path to a custom `.onnx`. |
| `TAP_THRESHOLD` / `TAP_DEBOUNCE_MS` | `--mode vad`'s tap-activation tuning — accelerometer-spike threshold and minimum time between accepted taps (real backend only; alternate trigger alongside the wake word). |
| `IDLE_FIDGET_ENABLED` / `IDLE_FIDGET_AFTER_S` | Whether Cozmo plays a small idle gesture after this many quiet seconds with no real conversation turn. |
| `GESTURE_ASYNC_ENABLED` | Whether `gesture` runs in the background so `say` can overlap with it, instead of blocking until the gesture finishes. |
| `GESTURE_SPEECH_SYNC_ENABLED` | `false` (default) starts `say`'s bundled gesture before synthesizing speech (instant reaction, overlap not guaranteed if synthesis is slow). `true` synthesizes first and starts the gesture right as playback begins (overlap guaranteed regardless of synthesis speed, at the cost of a pause before Cozmo reacts). |
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
- ✅ **`turn()` calibration** — `--mode calibrate`, offering a choice of two
  checks. The original (spin the wheels for a fixed 2s, estimate the
  resulting angle, derive `TURN_SECONDS_PER_DEGREE` from scratch) is good
  for an initial calibration, but extrapolates a single linear rate from
  a short sample to any commanded turn — **confirmed on real hardware**
  that this can be noticeably off for a much longer commanded turn (e.g.
  `gestures.py`'s `spin`, a full 360°, ~4x longer than the 2s sample the
  rate was derived from) even when smaller turns feel fine: a small
  estimation error at the 2s scale gets linearly amplified by however much
  longer the real commanded turn is — a plausible root cause for an
  observed ~65°-out-of-360° spin overshoot. Added a second check instead
  of just recalibrating more carefully at the same scale: it turns a real
  360° through the actual `turn()` method (using whatever rate is
  currently configured) and asks how far short of or past the *starting
  mark* it landed, rather than estimating an abstract angle from scratch
  — comparing an end point to a visible start point is both easier and
  more precise for a person to judge, and it measures `turn()` at the same
  scale `spin` actually uses it at. Corrects the existing rate
  proportionally (assumes a linear duration-to-angle relationship near the
  current rate, same assumption the original check already makes, not a
  fixed-offset overshoot independent of commanded duration) rather than
  deriving one from zero, so it's meant to run after some baseline value
  already exists. **Verified as control-flow and correction-math logic
  only** (scripted tests against the simulated backend confirm both
  checks' math and that "short"/"past"/"exact" all produce the expected
  corrected value) — not yet confirmed this actually fixes the real
  overshoot on hardware.
- ✅ **Voice activity detection** — `--mode vad` (`webrtcvad`), gated by a
  zero-network on-device wake word (`openwakeword`, see [Wake word
  detection](#wake-word-detection-zero-network) below).
- ✅ **Camera-in-the-loop** — `look()` tool attaches the photo for vision.
- ✅ **TTS gain boost** — `TTS_GAIN` in config, now clipping-safe (peak-normalizing).
- ✅ **TTS voice/tone polish** — `TTS_PITCH_SHIFT` + `TTS_ROBOT_MOD_DEPTH`, picked by
  ear against sample WAVs (see [Making the voice sound less generic](#making-the-voice-sound-less-generic)).
- ✅ **Robustness** — Ollama request failures are caught gracefully
  mid-conversation; STT request failures (`speech.transcribe()`, in both
  `interactive.py` and `vad_mode.py`) are now caught the same way instead
  of crashing the session — **confirmed on real hardware**: an OpenAI 400
  used to take the whole process down mid-`--mode vad` session, with the
  model never told anything went wrong; Cozmo mid-session disconnects are
  now detected (via a `RobotState` telemetry heartbeat — see [Detecting
  and recovering from a dropped
  connection](#detecting-and-recovering-from-a-dropped-connection)) and
  auto-reconnected, which also re-runs Wi-Fi auto-connect if
  `COZMO_WIFI_SSID` is set. **Untested against a real hardware drop** — this
  environment has no Cozmo to disconnect.
- ✅ **STT/TTS provider flexibility** — `STT_PROVIDER`/`TTS_PROVIDER` set
  independently to `groq` or `openai` in `.env`, for when Groq's free-tier
  rate limits get hit (which they did, during actual use), or to mix
  providers per component. See [Switching speech providers](#switching-speech-providers-and-separately-chat).
- ✅ **Chat provider flexibility, including vision** — `CHAT_PROVIDER=groq`/
  `openai` as an alternative to `ollama`, via `GroqChatClient`/
  `OpenAIChatClient` (`OpenAICompatibleChatClient` in
  `openai_compatible_chat.py`, shared since both are the same wire format),
  including `look`/`who_is_this`'s image attachments translated to OpenAI's
  content-array format, and a per-provider `*_VISION_MODEL` override for
  when the regular chat model isn't itself vision-capable. `VISION_PROVIDER`
  goes further — it can point image-bearing turns at an entirely different
  *provider* than `CHAT_PROVIDER` (e.g. Ollama for chat, Groq for vision),
  via a small `_ChatRouter` in `create_chat_client()`. **Not yet verified
  against a real Groq/OpenAI account** — implemented and unit-checked
  directly (message translation, image mime-sniffing, vision-model/
  provider selection, client construction) but no live turn has actually
  been run against either endpoint yet. See
  [Switching speech providers](#switching-speech-providers-and-separately-chat).
- ✅ **Battery indicator** — `BatteryMonitor` shows a face icon + red backpack
  light a few seconds before Cozmo auto-powers-off. See [Battery
  monitor](#battery-monitor).
- ✅ **Physical sensor reactions** — all built on `RobotState` telemetry
  that was previously only ever used for the connection-health heartbeat
  (nothing in `cozmo_brain` used to *listen* to Cozmo at all): tap-to-talk
  (an alternate `--mode vad` activation trigger alongside the wake word,
  detected via an accelerometer-magnitude spike rather than a touch sensor
  Cozmo's body doesn't actually have); a cliff/fall safety reflex during
  every `drive()`/`turn()` (stops early, shows a scared face, and backs
  away specifically for a cliff); charger-safe wheels (`drive()`/`turn()`
  blocked only while actively charging, not merely resting on the dock —
  full-but-docked is allowed to move); a startle reaction the instant
  Cozmo is picked up; and idle fidgeting after a few quiet minutes with no
  real conversation turn. Auditing all of this for real also surfaced a
  genuine, separate bug: two gestures and 8 of 16 moods could leave
  Cozmo's lift arm raised (which physically covers the face screen) with
  nothing guaranteed to ever lower it again — fixed alongside the rest.
  See item 4 below for the full detail on each, including exactly what's
  confirmed on real hardware vs. still simulated-only.
- ✅ **Concurrent gesture + speech** — `gesture` and `say` used to be
  strictly sequential (a full gesture choreography blocking until it
  finished before speech could even start synthesizing), adding real dead
  air to every reply that used both. Nothing about the *hardware* requires
  that — Cozmo's real animation clips already combine audio + movement as
  one synchronized track — it was purely this program's own tool-dispatch
  loop and blocking calls. `RobotBackend.run_gesture_async()` (`robot/base.py`)
  now validates the gesture name synchronously (still fails fast on an
  unknown name) but runs its steps on a background thread and returns
  immediately, so a `say` call right after actually overlaps with the
  gesture instead of waiting on it. `GESTURE_ASYNC_ENABLED` (default
  `true`) falls back to the old strictly-sequential behavior. A reentrant
  wheel lock (`drive()`/`spin_wheels_for()` in `robot/real.py`) prevents a
  background gesture's own wheel steps from racing a separately-requested
  `drive`/`turn` tool call for control of the wheels — reentrant
  specifically because the charger-exit-before-turning logic above already
  calls `drive()` from inside `spin_wheels_for()` while holding it.
  **Confirmed on real hardware that this alone didn't produce actual
  overlap:** `gesture` being non-blocking only helps if it's called
  *before* `say` — the ordering between two separate tool calls is
  entirely up to the model, and when it called `say` first, `say()`
  fully blocks on its own audio regardless of `gesture`'s async-ness, so
  speech and movement never happened together at all (observed directly:
  audio played fully, *then* the gesture happened). Fixed by not relying
  on the model batching two separate tool calls in the right order at
  all: `say` now takes its own optional `gesture` argument
  (`tools/registry.py`'s `handle_say`), so one call deterministically
  starts the gesture (still via `run_gesture_async()`, gated by the same
  `GESTURE_ASYNC_ENABLED`) and then proceeds straight into TTS
  synthesis + playback while it continues in the background — genuine
  overlap guaranteed by construction instead of hoped for. The standalone
  `gesture` tool still exists for a silent physical reaction with no
  speech; the system prompt now explains the distinction (and that
  calling `gesture` alongside `say`, even in the same turn, never
  actually overlaps them). **Verified, including on real hardware:** the
  transport is genuinely thread-safe (`pycozmo`'s `Connection.send()` is
  a plain thread-safe `queue.Queue.put()`); a scripted test with a fake
  speech client (simulated TTS latency) confirms the gesture's own steps
  run concurrently with synthesis and audio playback begins while the
  gesture is still finishing; a mocked test confirms the reentrant wheel
  lock doesn't deadlock on the charger-exit self-call; and — the actual
  goal — directly observed on the robot itself: the model used `say`'s
  `gesture` argument on its own (no separate `gesture` tool call), and
  Cozmo genuinely spoke while performing the gesture (`peek`), not one
  after the other. The cosmetic race where a background gesture's own
  mood step and a concurrent `say()`'s mood-setting land around the same
  moment remains unverified either way (same class of accepted trade-off
  as `pickup_reactor`'s note above, not new).

  **Raised directly, a separate stuck-arm case — not a gesture failure this
  time, just the mood system working exactly as designed:** `happy`/
  `excited`/`proud`/`smug` are deliberately "arms up" (see `moods.py`) —
  `apply_mood()` raises the lift and leaves it there on purpose, since
  that's the whole visual point while the reply plays. Nothing brought it
  back down afterward, though: confirmed on real hardware that a plain
  "Hi" getting a `happy` reply left the arm raised indefinitely once the
  turn was over — not a bug, exactly the documented behavior, but read as
  a stuck arm rather than an intentional flourish once the moment had
  passed. `handle_say()` (`tools/registry.py`) now checks the same
  `Mood.lift_mm` field `apply_mood()` itself keys off — not a hardcoded
  mood-name list, so it stays correct automatically if a future mood adds
  this same "raised, not auto-lowered" shape — and calls
  `lower_lift_fully()` once the reply finishes if it's set. Deliberately
  scoped to `say()`'s own mood argument, not `apply_mood()` itself: a
  gesture's own internal mood steps still work exactly as before, since
  those sequences already choreograph their own lift movements
  deliberately. **Verified against the simulated backend only so far**
  (confirms `set_lift_height_mm()` then `lower_lift_fully()` fire in that
  order for `happy`/`excited`, and neither an extra nor a missing call for
  moods that already set `lower_lift=True`) — not yet confirmed on real
  hardware that this actually reads right (raised pose visible for the
  whole reply, arm down right after) rather than looking abrupt or racing
  a bundled gesture's own lift steps.

  **Raised directly, a real gap in the "guaranteed by construction" claim
  above:** the guarantee is about *start order* only (gesture starts,
  *then* synthesis begins) — nothing ties the gesture's actual duration to
  how long synthesis takes. Most gestures run only ~1-3s total (see their
  step durations in `gestures.py`); if synthesis takes longer than that —
  plausible with a slower `TTS_PROVIDER` (e.g. `local` on constrained
  hardware, or just a slow network to a cloud provider) — the gesture can
  finish *before* audio even starts playing, silently degrading back to
  sequential despite the code's intent. `GESTURE_SPEECH_SYNC_ENABLED`
  (default `false`, preserves the behavior above unchanged) flips the
  order instead: synthesize first, then start the gesture right as
  playback begins, so the overlap no longer depends on synthesis speed at
  all — at the cost of Cozmo appearing to pause (mood/face still update
  immediately either way, independent of this) for however long synthesis
  takes, rather than reacting the instant `say` is called. **Verified as
  call-order logic only** (a scripted test with a fake speech client
  confirms `synthesize()` and the gesture start happen in the order this
  setting implies, in both positions). **Confirmed on real hardware:**
  toggling `GESTURE_SPEECH_SYNC_ENABLED` true vs. false produces a real,
  observable behavior difference (tested alongside `TTS_PROVIDER=edge`,
  whose higher latency — see below — makes the default's overlap loss
  easy to notice) — which setting actually feels better is a personal/
  use-case call, not settled here. With `true` specifically, confirmed the
  pause is exactly as designed: mood/face changes immediately (unaffected
  by this setting, applied at the very top of `handle_say()` regardless),
  while the gesture's physical movement is what actually waits for
  synthesis to finish.

  **A real bug found while auditing this, before it ever shipped to real
  use:** with `GESTURE_SPEECH_SYNC_ENABLED=true`, the gesture start was
  placed straight after the `speech.synthesize()` call - if synthesis
  raised (a real risk with any provider, `local` included, e.g. a missing
  Piper voice model or an unhandled Piper/faster-whisper error), the
  gesture was skipped entirely, not just delayed. For a gesture that
  raises the lift and only lowers it again as its own final step (see
  `gestures.py`), that meant the arm could get stuck up with nothing left
  to bring it back down - the exact failure mode the recent
  "recover the lift position if a background gesture step fails" fix
  (above) was built to catch, just via a different path it didn't cover.
  Fixed by moving the sync-mode gesture start into a `finally` block, so
  it still runs even when synthesis fails (the original exception still
  propagates afterward, so the tool call is correctly reported as failed
  to the model either way). **Verified as call-order logic only** (a
  scripted test confirms the gesture still starts when a fake speech
  client's `synthesize()` raises, in sync mode) - not yet confirmed this
  was the actual cause of any specific real-hardware report.

  **Raised directly, a real regression from going async:** the lift
  arm was reportedly not reliably ending up fully down anymore after a
  gesture. Root cause: `run_gesture_async()`'s background thread had *no*
  exception handling at all. Several gestures (`wake_up`, `cheer`,
  `shrug`, `fist_pump`, `alert`) raise the lift partway through and only
  lower it again as their own *final* step — in the old blocking
  `run_gesture()`, a mid-gesture failure on any earlier step was already
  visible as a failed tool call and (like any exception) would have
  equally abandoned the remaining steps; on a background thread it
  instead dies silently (Python's default: print a traceback, give up —
  invisible in our own logs), with the arm possibly left raised and
  nothing anywhere saying why. Exactly the one failure mode a background
  thread with physical side effects can't afford to have be silent.
  Fixed by wrapping the background thread's step loop in a try/except
  that logs the failure clearly and attempts `lower_lift_fully()` as a
  recovery action regardless of which step failed. **Verified the
  mechanism** with a scripted test that forces a mid-gesture exception
  (patching `set_head_angle_deg` to raise) and confirms the recovery
  lower-lift call fires right after the logged warning — **not
  confirmed this was the actual real-hardware trigger**, since nothing
  here can reproduce whatever specifically failed (a transient
  connection hiccup during `reconnect()` is a plausible candidate,
  since a background gesture step can now genuinely run concurrently
  with one). Either way, the recovery behavior is correct regardless of
  which step raised.

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
   placed on the charger. Mostly done now** (see the ✅ sub-items below);
   this item started from the observation that nothing in `cozmo_brain`
   *listened* to Cozmo at all — everything was one-way (send commands,
   never read anything back) — even though PyCozmo genuinely supports it,
   confirmed in `protocol_encoder.RobotState` (already used for the
   connection-health heartbeat): real accelerometer/gyroscope data and 4
   raw cliff sensors (not exposed as convenient attributes, but present on
   the packet), and discrete events for `IS_PICKED_UP`, `IS_FALLING`,
   `CLIFF_DETECTED`, `IS_ON_CHARGER`, `IS_CHARGING`, `IS_MOVING`,
   `IS_CARRYING_BLOCK`. No literal "fist bump detected" event exists (that
   was a scripted Anki app behavior, not a discrete hardware signal).
   Still genuinely open: "stuck" detection, tipped-over detection, and a
   proactive low-battery TTS nudge (each needs either real calibration
   data or a bit more design first — see their own sub-item below), and
   tap-to-talk via a light cube specifically (blocked on cube batteries).

   - ✅ **Tap-to-talk, on Cozmo's own body — done, folded into `--mode vad`
     as a second activation trigger alongside the wake word (not a
     standalone mode).**
     `RobotState.backpack_touch_sensor_raw` reads a flat `0` on real
     hardware regardless of touching (confirmed 2026-09-26) — original
     Cozmo, unlike Vector, has no capacitive touch sensor built into its
     body, and that protocol field looks like it's simply never populated
     on Cozmo's board. Pivoted to `accel_x/y/z` instead: real-hardware logs
     showed resting jitter within ~50 of a rolling baseline, with genuine
     taps spiking several hundred to 1000+ — a clean threshold at 150
     (`TAP_THRESHOLD`). The one wrinkle: being picked up off the charger
     produces spikes in the same range as a tap, but as a *sustained*
     multi-sample event, not a single blip — resolved by gating on Cozmo's
     own `IS_PICKED_UP` status flag rather than trying to infer "tap vs.
     pickup" from accelerometer shape alone.

     First built as its own `--mode tap` with fixed-duration recording, like
     push-to-talk. Dropped that once it raised a real question: how do you
     reliably tell "no speech was said" from a tap-triggered capture, to
     end a follow-up conversation? Fixed-duration recording has no
     loudness/spectral gating on it at all, so that mode could only infer
     "no speech" from Whisper's *output* (empty text, or matching a small
     curated hallucination-phrase blocklist) — and Whisper's actual known
     failure mode on silence/noise is to *not* return empty, but to
     hallucinate a plausible-sounding sentence instead (see
     `llm/stt_postprocess.py`), which that blocklist can't fully catch.
     `vad_mode.py` already solves exactly this, properly, with two signals
     checked *before* Whisper is ever called (`webrtcvad` spectral
     classification + the `VAD_MIN_RMS` loudness floor — see
     `audio/vad.py`). So rather than half-rebuild that inside a second
     mode, a tap became just an alternate way to fire `vad_mode`'s existing
     activation (`_wait_for_wake_word_or_tap` in `modes/vad_mode.py`, races
     the wake-word listener against `RobotBackend.wait_for_tap()` on
     separate threads, cancels the loser) — everything downstream
     (listening, follow-up, hallucination filtering) is the same code path
     the wake word already used. See `robot/real.py` and [Modes](#modes).
   - **Tap-to-talk, via a light cube — the real path, blocked on batteries.**
     The protocol has a
     genuine `ObjectTapped`/`ObjectTapFiltered` event (confirmed in
     `protocol_declaration.py`, includes tap count and intensity).
     pycozmo's client already discovers cubes (`ObjectAvailable` →
     `available_objects`) but has no high-level pairing helper — connecting
     one means sending a raw `ObjectConnect(factory_id=..., connect=True)`
     ourselves, then handling `ObjectTapped`. Blocked on replacing the
     cubes' batteries; revisit once a cube is powered on.
   - ✅ **Cliff/fall safety gate — done.** Found while auditing what
     sensor-driven features to build next: nothing in `cozmo_brain` sent
     PyCozmo's `EnableStopOnCliff` command, or read the `CLIFF_DETECTED`/
     `IS_FALLING` status flags, anywhere — `drive()`/`turn()` had zero
     table-edge or fall protection beyond whatever pycozmo's un-configured
     firmware default happens to be (and `EnableStopOnCliff` only ever
     covers the cliff case, not a fall). Fixed two ways in `robot/real.py`:
     `connect()` now sends `EnableStopOnCliff(enable=True)` (firmware-level,
     but this exact command was untested here, so not trusted alone), and
     `drive()`/`spin_wheels_for()` (so `turn()` too) poll
     `CLIFF_DETECTED | IS_FALLING` every 50ms while "sleeping" through a
     commanded move and stop early if either fires — the only protection at
     all for the falling case.

     **Confirmed on real hardware that stopping early wasn't the whole
     story:** the cliff poll fired correctly (`"Cliff detected mid-drive -
     stopping early"` in the log), but `drive()` still returned a bare
     `True`/success, so the `drive` tool reported "Drove 50mm..." verbatim
     and the model — never told anything happened — said something
     completely unrelated next ("Ta-da! Precision movement..."), with
     Cozmo left sitting right at the edge. Fixed by replacing the bare
     bool return with `MoveResult(moved, hazard)` (`robot/base.py`) and
     adding an immediate autonomous reflex, `_react_to_hazard()` in
     `robot/real.py`: shows the `scared` mood, and for a cliff specifically
     (not a fall — orientation/position is unknown then, so driving blind
     could make it worse) backs away ~40mm in the opposite direction from
     whatever was commanded, reusing `gestures.py`'s existing `flinch`
     numbers but calling the raw wheel primitives directly rather than
     `run_gesture()`/`drive()`, to avoid recursing back into this same
     hazard-detection path. This reflex is immediate and unconditional — no
     LLM/TTS round-trip, since that latency is exactly wrong for "about to
     fall". Separately, the `drive`/`turn` **tools** now check the returned
     hazard and report it honestly ("detected a cliff/edge... and backed
     away for safety") instead of a plain success, so the model finds out
     and can react conversationally too — the same principle now applies
     everywhere a `MoveResult` flows: charger-blocked and hazard-shortened
     moves both get told to the model truthfully rather than silently
     collapsing into "success".

     **Confirmed on real hardware, a second time, that this created a new
     problem of its own:** once charger-safe wheels (below) started
     allowing a drive once full, Cozmo still couldn't actually leave the
     charger — the dock's own platform/edge reads as a false
     `CLIFF_DETECTED`, so the exact drive meant to exit the charger
     immediately tripped the cliff reflex and backed him right back onto
     the dock he was trying to leave. Fixed by having `drive()`/
     `spin_wheels_for()` detect when a drive starts while still docked
     (`is_on_charger()` true at the start — only possible once full, since
     the charging check above already blocks it otherwise) and suppress
     *only* `CLIFF_DETECTED` for that one drive, at both layers: the
     firmware's `EnableStopOnCliff` (disabled for the duration, restored
     right after) and the software poll (`_sleep_unless_cliff`'s new
     `ignore_cliff` argument). `IS_FALLING` protection stays fully active
     regardless — unrelated failure mode, no reason to suppress it near
     the charger. **Trade-off worth knowing:** this fully suppresses cliff
     detection for the entire duration of whatever drive left the charger,
     not just the first moment near the dock — there's no cheap way yet to
     tell "still near the dock" from "genuinely clear of it and now near a
     real edge" within the same drive call.

     **A related physical constraint, raised directly:** turning in place
     while still on/near the charger platform risks catching on the dock,
     separately from the cliff-suppression issue above. `spin_wheels_for()`
     (so `turn()`, and any gesture step that turns) now checks
     `is_on_charger()` first and, if still docked, drives straight off
     (`CHARGER_EXIT_DISTANCE_MM`/`CHARGER_EXIT_SPEED_MMPS`, now 150mm at
     60mm/s — **confirmed on real hardware**: the original 100mm guess
     wasn't reliably clearing the dock) via `drive()` itself before
     performing the requested turn at all, rather than turning in place on
     top of the dock with cliff detection merely suppressed. If that
     straight exit hits a real hazard (a fall — cliff is already covered by
     `drive()`'s own suppression), the turn is skipped entirely and that
     hazard is reported instead, rather than compounding it with more
     movement.

   - **Same false cliff trigger, the other direction — found directly on
     real hardware:** reversing *toward* the charger to dock (e.g. "go back
     15cm") hit the identical false `CLIFF_DETECTED` the exit fix above
     already diagnoses, just approached from the opposite side of the same
     platform/edge. The existing fix couldn't cover this — it keys off
     `is_on_charger()` being `true` at the *start* of the drive, which is
     exactly backwards for an approach: Cozmo isn't on the charger yet at
     that point, there's no sensor to say "about to be." Rather than
     suppress cliff detection for *every* reverse drive (which would remove
     real protection backing toward an actual ledge that has nothing to do
     with the charger), added a dedicated `dock()` primitive
     (`RobotBackend.dock()`, a new `dock` tool) that only ever performs one
     specific, intentional action — reverse a fixed distance
     (`CHARGER_DOCK_DISTANCE_MM`/`CHARGER_DOCK_SPEED_MMPS`) with cliff
     suppression on — so the model only takes that trade-off when
     explicitly asked to return Cozmo to the charger, not for arbitrary
     backward movement. `drive()`'s suppression logic was generalized (a
     new `suppress_cliff` parameter) rather than duplicated. **Not yet
     verified against real hardware** — implemented directly from the log/
     diagnosis above, no live redocking attempt confirmed yet.
   - ✅ **Charger-safe wheels — done, gated on "fully charged" not just
     "docked".** `drive()`/`spin_wheels_for()` (so `turn()` too) skip the
     wheel command entirely — returning `MoveResult(moved=False)` instead
     of raising, specifically so a gesture's face/light/head/lift steps
     still play uninterrupted even when its wheel steps get skipped
     (dance/spin etc. mix both — see `robot/gestures.py`) — but only while
     `IS_ON_CHARGER and IS_CHARGING` both hold. Once full
     (`IS_ON_CHARGER` but no longer `IS_CHARGING` — inferred from normal
     charge-controller behavior, not a documented Anki spec, worth
     confirming it doesn't also happen at some in-between state like a
     thermal cutoff), a normal `drive`/`turn` is allowed to proceed exactly
     like any other, which drives Cozmo off the dock as a side effect of
     whatever actually asked for movement. **Deliberately not an autonomous
     "leave the charger once full" behavior of its own** — that decision
     stays with whatever ordinarily triggers a drive (conversation, a tool
     call), not a background reactor moving Cozmo unprompted. `is_on_charger()`/
     `is_charging()` are exposed as their own `RobotBackend` methods (not
     just used internally), so a future `battery_status()` tool can answer
     "are you charged?" without needing new plumbing. The `drive`/`turn`
     **tools** check the returned `MoveResult` and tell the model honestly
     ("still charging") instead of reporting a movement that never
     happened; same threading through `--mode calibrate`, which now warns
     and discards the sample instead of quietly recording a bogus
     degrees-per-second reading if you calibrate while it's still charging.
     **Not yet verified against real hardware.**

     **Raised directly, a real usability gap:** blocking movement for the
     *entire* time `IS_CHARGING` was true — regardless of how much charge
     was already there — made an explicit request ("can you come out?")
     honored too late to matter: the model doesn't see `drive()`'s blocked
     result until *after* it already committed to a `say` in the same
     turn, so "I'm coming!" played and Cozmo never actually moved,
     observed directly on real hardware. `_must_stay_on_charger()`
     (`robot/real.py`) now only blocks while genuinely charging **and**
     the battery is at or below `BATTERY_LOW_VOLTAGE` — above that,
     an explicit drive/turn request is honored even mid-charge, not just
     once `IS_CHARGING` flips off entirely. Unknown voltage (no
     `RobotState` packet read yet) stays conservative and blocks, same as
     before this existed. Still deliberately not autonomous — this only
     changes what an *explicit* request is allowed to do, nothing decides
     to leave the charger on its own. **Verified as boundary-condition
     logic only** (checked `_must_stay_on_charger()` directly against
     every combination of on-charger/charging/voltage, including the
     unknown-voltage and exactly-at-threshold cases) — not yet observed
     against a real battery actually crossing that threshold on hardware.
   - ✅ **Startle reaction on pickup — done.** `RobotBackend.is_picked_up()`
     (real backend: `IS_PICKED_UP`, already read for tap-gating; simulated:
     always `False`) is polled by a small background thread,
     `robot/pickup_reactor.py` — same shape as `battery_monitor.py`, wired
     up alongside it in `main.py`. Reacts on the edge only (picked-up-now
     but wasn't a moment ago): plays the `surprised` mood, then back to
     `neutral` the moment he's set back down. Purely a physical reaction —
     no LLM turn involved. Can cosmetically race with another thread's own
     `apply_mood()` call around the same moment (e.g. `--mode vad`'s
     "curious"/"neutral" listening indicator) — worst case is a flickered
     mood, not a crash. **Not yet verified against real hardware.**
   - ✅ **Idle fidgeting — done.** Timer-based, not sensor-driven, so no
     calibration concern like the items below. `CozmoEngine` now tracks
     `last_interaction_monotonic`, updated at the top of every real
     `handle_turn()` (every mode funnels through it). A new background
     reactor, `idle_fidget.py` (same shape as `battery_monitor.py`/
     `pickup_reactor.py`, but lives alongside `engine.py` since it needs
     `CozmoEngine`, not just a `RobotBackend`) plays a random pick from
     `("peek", "shrug")` — deliberately calm, no-drive gestures, so idle
     Cozmo doesn't go rolling off somewhere on its own — every
     `IDLE_FIDGET_AFTER_S` (default 300s) of continued quiet, resetting
     the moment a real turn happens. `IDLE_FIDGET_ENABLED=false` disables
     it entirely.

     **Auditing this surfaced a real, separate bug, found by request:**
     checked every existing gesture/mood for whether any of them raise the
     lift arm (which physically covers the face screen when up) and leave
     it there. Two gestures did: `wake_up` (ends via the `happy` mood,
     lift=70 — meaning the arm was left up after *every single connect()*,
     since `wake_up` runs there automatically) and `cheer` (its bounce
     sequence ended at 40, not fully down). More broadly, 8 of the 16
     moods had `lift_mm=None` (don't touch the lift at all), so applying
     one of them right after anything that *had* raised the arm — a
     previous gesture, or an autonomous reflex like the cliff/fall hazard
     reaction's `scared` mood or `pickup_reactor`'s `surprised` — left the
     face hidden with nothing guaranteed to ever lower it again (this was
     previously only fixed for `neutral` specifically, per its own comment
     in `moods.py`). Initially fixed by having `wake_up`/`cheer` end with
     an explicit `lift_mm=32` step, and every mood except the four
     deliberately "arms up" celebratory ones (`happy`/`excited`/`proud`/
     `smug`) default to `lift_mm=32` too.

     **Confirmed on real hardware that 32mm itself was the wrong fix:**
     32mm is `pycozmo.MIN_LIFT_HEIGHT` — a documented constant, not
     something ever confirmed to actually bottom out on this unit, and it
     doesn't reliably/visibly do so. Tracked down why: `_clamp(height_mm,
     32, 92)` in `real.py`'s `set_lift_height_mm()` is the *only* thing
     enforcing that floor — `pycozmo.Client.set_lift_height()` itself does
     no clamping at all (confirmed against its source: a plain passthrough
     to the firmware), so our own software was the sole obstacle to ever
     asking for lower. Replaced every "settle all the way down" case with
     a dedicated `RobotBackend.lower_lift_fully()` (a new `Mood.lower_lift`
     flag and a `"lower_lift"` gesture `Step` kind, alongside the existing
     numeric `lift_mm`/`"lift"` for the four raised moods) that sends
     `0.0` directly, unclamped — letting the real mechanical limit decide
     where "fully down" actually is, instead of trusting a documented
     constant that doesn't hold up in practice. **Verified in the
     simulated backend** (`wake_up`'s own sequence, and a forced idle
     fidget, now log `lift height -> fully down` instead of a specific mm
     value) — the real-hardware behavior of sending `0.0` unclamped is
     itself not yet confirmed.
5. **A fully key-free STT+TTS provider**, on top of the existing Groq/OpenAI
   split — for running with literally no API account at all, not just as a
   Groq-rate-limit fallback. Two different properties are easy to conflate
   here, worth being precise about:
   - ✅ **No key AND no network — done.** `STT_PROVIDER`/`TTS_PROVIDER=local`
     (`cozmo_brain/llm/local_client.py`): `faster-whisper` (CTranslate2,
     real CPU speedups over plain Whisper) for STT, **Piper** for TTS —
     confirmed real and specifically built/validated for Raspberry
     Pi-class hardware (used throughout the Home Assistant/Rhasspy
     voice-assistant ecosystem), and already ONNX-based like
     `openwakeword`, so it fits the existing dependency pattern. See
     [Switching speech providers](#switching-speech-providers-and-separately-chat).
     **The real cost is latency, untested against real hardware:** CPU-only
     Whisper inference on a Pi 5 (no GPU) will be slower than Groq's cloud
     Whisper on dedicated hardware, and by how much is unmeasured — no Pi
     was available here to benchmark on. `LOCAL_STT_MODEL`/
     `LOCAL_TTS_VOICE_PATH` are both easy to drop to a smaller/faster
     model/voice from `.env` alone once real latency is known.
   - ✅ **No key, but still needs the internet — done, TTS side.**
     `TTS_PROVIDER=edge` (`cozmo_brain/llm/edge_client.py`) uses Microsoft
     Edge's own cloud TTS via the unofficial `edge-tts` library — no
     account, but not a published/supported Microsoft product, so it can
     break if Microsoft changes something internally (historically fixed
     upstream, not guaranteed). Good natural-sounding voices for free, at
     the cost of that reliability risk and still needing a network. See
     [Switching speech providers](#switching-speech-providers-and-separately-chat)
     for the real integration cost this surfaced (edge-tts only ever
     returns MP3, decoded via `PyAV` before reaching `tts_postprocess.py`)
     and what's actually been verified. **STT side still not built** — the
     Wit.ai option added separately (`STT_PROVIDER=witai`) covers a
     different trade-off (needs its own account, but no billing) rather
     than this exact "no key, needs internet" STT combination; nothing
     currently fills that specific gap.

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
