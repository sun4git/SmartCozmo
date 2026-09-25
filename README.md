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
    │   └── groq_client.py     # Whisper STT + Orpheus TTS (voice character + gain boost)
    ├── audio/
    │   ├── recorder.py        # fixed-length arecord capture (push-to-talk)
    │   ├── vad.py              # webrtcvad-based hands-free capture
    │   └── wakeword.py         # openWakeWord-based wake word, gates --mode vad
    ├── robot/
    │   ├── __init__.py         # create_robot() factory (real vs. simulated)
    │   ├── base.py             # RobotBackend interface + shared mood/gesture playback
    │   ├── real.py             # PyCozmo-backed implementation (+ health/reconnect)
    │   ├── simulated.py        # console-logging implementation, no hardware needed
    │   ├── wifi.py             # optional nmcli-based Wi-Fi auto-connect
    │   ├── moods.py            # curated expression+light+pose presets
    │   └── gestures.py         # curated multi-step gesture choreography
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
pip install webrtcvad   # only needed for cozmo_brain's --mode vad
pycozmo_resources.py download   # downloads Cozmo's animation/audio resource files — needed for play_animation()
```

**Only if you plan to use `--mode vad`** (hands-free): it also needs
wake-word detection, which is **not** a plain `pip install openwakeword` —
see [Wake word detection](#wake-word-detection-zero-network) below for the
exact install command and why. `--mode voice`, `--mode text`, and
`--simulate` don't need this at all — skip it for now if you just want to
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
| `say(text, mood)` | Speaks via Groq TTS, striking a matching facial expression + backpack light color first. |
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

While in here, `TTS_GAIN` (Cozmo's speaker is quiet even at max hardware
volume) also got a real fix: it used to be a blind fixed multiplier, which
at its default of `3.0` was clipping about 0.5% of samples — audible as
harsh crackle, not the intended "louder" effect — because Orpheus's raw
output already peaks around 60% of full scale. It's now peak-normalizing:
`TTS_GAIN` is a ceiling, automatically backed off to land just under
clipping (~95% of full scale) instead of blowing past it.

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
| `GROQ_API_KEY` | Required. Auth for Groq STT + TTS. |
| `OLLAMA_BASE_URL` | Ollama endpoint chat/tool-calling requests go to. |
| `OLLAMA_MODEL` | Model name; must support `tools` (and ideally `vision` for `look`). |
| `RECORD_SECONDS` / `RECORD_DEVICE` | Push-to-talk recording length and ALSA/PipeWire device. |
| `STT_MODEL` / `TTS_MODEL` / `TTS_VOICE` | Groq model/voice choices. |
| `TTS_GAIN` | Max volume boost for TTS output, peak-normalized to avoid clipping (Cozmo's speaker is quiet). |
| `TTS_PITCH_SHIFT` | Pitch+tempo shift for a smaller/more childlike/robotic voice. 1.0 = off. |
| `TTS_ROBOT_MOD_DEPTH` / `TTS_ROBOT_MOD_HZ` | Optional ring-modulation robotic timbre. Depth 0.0 = off. |
| `ROBOT_BACKEND` | `real` or `simulated` (cozmo_brain/ only; `--simulate` overrides it). |
| `ROBOT_STALE_AFTER_S` | Seconds without robot telemetry before auto-reconnect kicks in. |
| `COZMO_WIFI_SSID` / `COZMO_WIFI_PASSWORD` | Optional Wi-Fi auto-connect (Linux/nmcli only). Password only needed for the first connect. |
| `TURN_SPEED_MMPS` / `TURN_SECONDS_PER_DEGREE` | `turn()` calibration — tune with `--mode calibrate`. |
| `MAX_DRIVE_SPEED_MMPS` / `MAX_DRIVE_DISTANCE_MM` | Safety clamps on the `drive` tool. |
| `MAX_TOOL_ITERATIONS` | Cap on LLM↔tool round-trips per user turn. |
| `CONVERSATION_MAX_MESSAGES` / `CONVERSATION_HISTORY_PATH` | Memory size and persistence path. |
| `VAD_AGGRESSIVENESS` / `VAD_SILENCE_MS` / `VAD_MAX_UTTERANCE_S` | Hands-free listening tuning. |
| `VAD_FOLLOWUP_TIMEOUT_S` | How long a conversation stays open after a reply before the wake word is needed again. |
| `WAKE_WORD_MODEL` / `WAKE_WORD_THRESHOLD` | Wake word gating `--mode vad` — stock name or path to a custom `.onnx`. |
| `VISION_ENABLED` | Whether `look()`'s photo gets attached to the next LLM turn. |
| `KNOWN_PEOPLE_DIR` | Where `remember_person`'s reference photos are stored (experimental). |

---

## Known gotchas

- **Cozmo's AP is flaky to scan.** If `nmcli dev wifi list` doesn't show him
  even though his face shows credentials, rescan and try again before
  assuming anything is broken.
- **Cozmo auto-powers-off** fairly quickly off the charger (small/aged
  battery). Development goes smoother with him on the charger — PyCozmo can
  fully control him while charging.
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

Still open, roughly in priority order:

1. **Smarter conversation trimming.** Current strategy is a hard message-count
   cutoff. Token-aware summarization of older turns would use the 262144
   token context window better on long sessions.
2. **Systemd service** for headless/boot-time operation, now that reconnect
   logic makes a long-running session more viable.
3. **STT backend flexibility** — make the STT backend swappable via config
   if Groq's free tier limits become a problem (Open WebUI's Whisper endpoint
   was identified as a fallback but never wired in).
4. **More real animations, curated.** Once `pycozmo_resources.py download`
   assets are available, consider hand-picking a "greatest hits" subset of
   real clip names to seed into the `gesture` tool's enum, instead of
   requiring the model to call `list_animations` first every time.

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
