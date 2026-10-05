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

- **`standalone/orchestrator.py`** — the original single-file proof of concept. Kept
  as-is on purpose, as a lightweight script for quickly testing mic → STT →
  LLM → PyCozmo → TTS on the Pi without the full app.
- **`cozmo_brain/`** — the real application: conversation memory, a proper
  tool-calling agentic loop, a curated gesture/mood library built on
  confirmed PyCozmo primitives, real animation clip playback, camera-in-
  the-loop vision, hands-free VAD listening, a turn-calibration mode, an
  optional hand-off to your own assistant agent (reminders, web lookups -
  see [Asking your own assistant](#asking-your-own-assistant-ask_assistant)),
  and a simulated robot backend for developing away from the hardware. See
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
├── standalone/              # one-off scripts, not part of cozmo_brain (run from the repo root)
│   ├── orchestrator.py      # lightweight single-file test script (no memory/tools/gestures)
│   ├── battery_report.py    # summarise data/battery.jsonl: charge sessions, time off the dock, minutes-to-low
│   ├── pose_drift_test.py   # pose/dead-reckoning drift test behind return-to-charger (roadmap item 6)
│   ├── show_history.py      # print archived conversations as a readable transcript
│   └── sync_env.py          # after a git pull: shows/appends .env settings missing vs .env.example
├── tests/                   # offline tests (fakes - no robot/mic/keys): python tests/run_all.py
│   └── live/                # opt-in tests that call real LLM providers with your .env keys
├── models/                  # model files, named in .env without folder/extension (see "Model files")
│   ├── hey_cozmo.onnx       # custom "hey Cozmo" wake word (committed)
│   └── en_US-lessac-medium.onnx(.json)  # Piper voice for TTS_PROVIDER=local (downloaded, gitignored)
├── training/
│   └── wake_word_training.ipynb  # Colab notebook that trained hey_cozmo.onnx (see "Training a custom wake word")
├── data/                    # everything Cozmo saves at runtime (gitignored) - see "Conversation history and memory"
│   ├── history/<date>/<time>.jsonl   # one transcript per run, photos next to it
│   ├── memory.md            # facts about people, sent to the model every turn - edit freely
│   ├── battery.jsonl        # battery events: charge curve + every time off the dock (battery_log.py)
│   ├── known_people/        # remember_person reference photos
│   └── look.png             # last camera snapshot
├── run.sh                   # activates cozmo-env + runs cozmo_brain in one step (./run.sh --help)
├── dashboard.sh             # starts the Control Room web dashboard (start/stop Cozmo, live log, history, .env editor)
├── cozmo_dashboard/         # the dashboard: stdlib-only Python server + static page (see "The dashboard")
├── requirements.txt        # pip install -r requirements.txt (core deps only — see setup step 1 for extras)
├── .env.example             # template for every config variable, annotated — copy to .env
├── .env                      # your real config (gitignored, not in repo)
├── README.md
└── cozmo_brain/              # the full application
    ├── main.py               # CLI entry point (--mode voice|vad|text|calibrate, --simulate)
    ├── config.py             # typed Settings, loaded from .env
    ├── conversation.py       # what the model sees: system prompt + recent messages, trimmed; resumes from the archive
    ├── history_archive.py    # data/history/: every run's full transcript + photos, by date
    ├── memory.py             # data/memory.md: facts about people, plus search over facts and transcripts
    ├── memory_suggestions.py # after a conversation: suggests facts worth keeping, for you to approve
    ├── engine.py             # the agentic tool-calling loop (CozmoEngine)
    ├── charger_return.py     # low-battery policy: offer at LOW, return on its own at CRITICAL
    ├── assistant_relay.py    # background ask_assistant: answers spoken when they arrive (ASSISTANT_BACKGROUND)
    ├── personality.py        # Cozmo's system prompt / persona
    ├── imaging.py             # shared base64 image helper (vision attach, who_is_this)
    ├── model_files.py         # finds models/<name>.onnx for WAKE_WORD_MODEL / LOCAL_TTS_VOICE_PATH
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
    │   ├── assistant_client.py  # external assistant agent (ask_assistant), OpenAI-compatible, e.g. OpenClaw
    │   ├── tts_postprocess.py    # shared voice character + gain, used by every TTS provider
    │   └── stt_postprocess.py    # filters known STT hallucination/fallback phrases (Whisper + Wit.ai)
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
    │   ├── registry.py         # the tools exposed to the LLM
    │   ├── memory_tools.py     # remember_fact / forget_fact / search_memory / review_suggestion / clear_suggestions
    │   └── assistant_tools.py  # ask_assistant + its system-prompt section (ASSISTANT_ENABLED)
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

1. **webrtcvad-wheels** (the maintained fork of `webrtcvad`, same
   `import webrtcvad`, prebuilt aarch64/Python 3.12 wheel, no compiler needed):

   ```bash
   pip install webrtcvad-wheels
   ```

   Don't install the original `webrtcvad` package. It's abandoned (since
   2020), has no prebuilt wheel (needs `build-essential python3-dev`), and
   does a module-level `import pkg_resources`, which recent setuptools
   removed, so it only imports with `setuptools<81` pinned. The fork doesn't
   use `pkg_resources` on Python 3.8+, so no pin is needed.

   **Switching an existing venv from `webrtcvad`:** uninstall the old one
   first. Both packages ship the same module files, so having both and then
   removing one breaks the other:

   ```bash
   pip uninstall -y webrtcvad
   pip install webrtcvad-wheels
   python -c "import webrtcvad; print(webrtcvad.__version__)"   # e.g. 2.0.14
   ```

   Rollback is the reverse (`pip uninstall -y webrtcvad-wheels`, then
   `pip install webrtcvad` with the build tools and `setuptools<81`). An
   existing `setuptools<81` pin can stay: it does nothing for the fork.
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

**After a `git pull` that adds settings:** `.env` is gitignored (it holds
your keys), so a pull never updates it. The new settings then just use their
built-in defaults, but you can't see or tune them, and an *old* value still
in `.env` overrides a new default. To check:

```bash
python3 standalone/sync_env.py           # report only: missing, stale, and unknown settings
python3 standalone/sync_env.py --apply   # append the missing ones (asks first, backs up .env)
```

It never changes or removes an existing line, and never prints a value from
your `.env` (unknown settings are listed by name only).

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

`run.sh` is stored executable in git (since 2026-09-29). If an older
checkout says "Permission denied", run `chmod +x run.sh` once.

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
# Quick test script (see "Standalone scripts"):
python3 standalone/orchestrator.py

# Full application:
./run.sh --mode voice          # push-to-talk — press Enter, speak, 'quit' to exit
./run.sh --mode vad             # hands-free — needs the wake-word setup from step 1
./run.sh --help                 # every option, incl. --simulate, --fresh, --log-level
```

**Logging:** `--log-level` sets how much is logged, case-insensitive:

| Level | Shows |
|---|---|
| `INFO` (default) | The useful diagnostics: LLM steps and tool results, STT results and segment scores, speech-gate and capture lines, docking/charger events |
| `WARNING` | Quiet: only problems |
| `ERROR` | Only failures |
| `DEBUG` | Everything, including PyCozmo's own packet chatter |

To change the default without typing the flag every run, set
`LOG_LEVEL=WARNING` (etc.) in `.env`. The flag still overrides it for a
single run.

(See [Modes](#modes) below if you'd rather activate `cozmo-env` and call
`python3 -m cozmo_brain` directly instead of using `run.sh`.)


### The dashboard (Control Room)

A web page for running Cozmo without a terminal: start/stop him in any mode,
live log and conversation, history, files, and a `.env` editor. Separate from
`cozmo_brain/` and standard-library only.

```bash
./dashboard.sh                    # then open http://localhost:30540
```

Listens on this machine only by default; to open it from another computer
you must set `DASHBOARD_HOST` and `DASHBOARD_TOKEN`. There is no default
token; make one with
`python3 -c "import secrets; print(secrets.token_urlsafe(24))"`. Full guide (pages,
security, token, notes): [cozmo_dashboard/README.md](cozmo_dashboard/README.md).

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
forwards all arguments, e.g. `./run.sh --mode voice`. `./run.sh --help` lists
every option.

Every run is archived under `data/history/`, and the next run resumes where
the last one left off; pass `--fresh` to start with an empty context instead
(the run is still archived, and memory still applies). See
[Conversation history and memory](#conversation-history-and-memory).

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
| `dock()` | Goes back onto the charger. If Cozmo remembers where it is (he drove off it earlier and hasn't been picked up or reconnected since), navigates to a staging point in front of it, then reverses on until the contacts engage. Otherwise only reverses in a straight line, so it only works already close and facing away from it. See roadmap item 6. |
| `leave_charger()` | Drives straight off the dock (the `CHARGER_EXIT_*` drive any movement does first when docked). Refuses honestly, with the voltage, if the battery is too low to leave. No timed return afterwards — the low/critical battery rules bring him back. |
| `look()` | Captures a camera frame and attaches it to the next LLM turn for vision. |
| `remember_person(name)` | Captures a reference photo, stored under that name. |
| `who_is_this()` | Captures a photo and asks the vision LLM if it matches anyone remembered. **Experimental.** |
| `remember_fact(fact, person)` | Saves a fact to `data/memory.md`, under the primary user (default) or a named person. Refused when `MEMORY_MAX_FACTS` is reached. |
| `forget_fact(fact)` | Removes the one saved fact that matches; if several match, removes nothing and lists them. |
| `search_memory(query)` | Keyword search over saved facts and every archived conversation, with dates and times. |
| `review_suggestion(suggestion, decision)` | Records your answer after Cozmo asks about a suggested fact: `keep` (saved), `discard` (never suggested again) or `later`. |
| `clear_suggestions(which)` | Empties the `waiting` suggestions, the `declined` ones, or `both`, when you ask. Cozmo says how many and checks first; confirmed facts aren't touched. |
| `ask_assistant(request)` | Only when `ASSISTANT_ENABLED=true`. Hands a request Cozmo can't do himself (a reminder, a web lookup, a task) to your own assistant agent, called by `ASSISTANT_NAME`, and returns its answer. See [Asking your own assistant](#asking-your-own-assistant-ask_assistant). |

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

### Conversation history and memory

Three separate things, all under `data/` (gitignored, `DATA_DIR`):

| | What | Lifetime |
|---|---|---|
| **Context** | What the model sees each turn: the system prompt, then the last `CONVERSATION_MAX_MESSAGES` messages (`conversation.py`) | Trimmed as it goes |
| **Archive** | `data/history/<date>/<time>.jsonl`: one file per run (start to exit), every message with a timestamp, photos saved next to it (`history_archive.py`) | Kept until deleted (`HISTORY_RETENTION_DAYS`) |
| **Memory** | `data/memory.md`: a few facts about people, sent to the model every turn (`memory.py`) | Until removed, by Cozmo or by hand |

**Archive.** Written line by line as things happen, so a crash loses at
most one line; a run that records nothing leaves no file. In `--mode vad`,
each wake word or tap starts a session inside the run's file
(`session_start` with the trigger, then `session_end` when the follow-up
window closes). Photos the model looked at (and `look` photos taken with
`VISION_ENABLED=false`) are saved as `<time>-photo-01.png` etc. next to the
transcript, not inside it. On startup, the context resumes from the last
messages of earlier runs - always behind the *current* system prompt
(the old single-file history also restored its old system prompt, so
prompt changes didn't take effect until `--fresh`). Resumed photos aren't
re-sent; a `[photo: ...]` note stays in their place.

Read it with:

```bash
python3 standalone/show_history.py              # the latest run
python3 standalone/show_history.py 2026-10-01   # every run that day
python3 standalone/show_history.py --list
```

An existing `conversation_history.json` (the old format) is imported once
as its own run, dated by the file, and moved to
`data/conversation_history.imported.json`.

**Moving an existing install** (after pulling): the history import is
automatic. Photos and reference faces move with two `.env` changes - an
explicit old value overrides the new `data/` defaults:

```bash
mkdir -p data
mv known_people data/ 2>/dev/null; rm -f look.png
# in .env: CAMERA_SNAPSHOT_PATH=data/look.png and KNOWN_PEOPLE_DIR=data/known_people
# (or delete both lines). standalone/sync_env.py flags the old values.
```

**Memory.** Plain markdown - open it and edit it; changes apply on the
next turn, no restart:

```markdown
## Suneel (primary user)
- Likes cricket.

## Asha
- Suneel's daughter. Loves the dance gesture.
```

One fact per `- ` line under a `## <name>` heading. Cozmo can't tell
voices apart, so the section with "primary user" in its heading is who he
assumes he's talking to - rename the template's `## Primary user` to your
name, keeping "(primary user)". Text above the first `##` heading is notes
for humans and is never sent. Cozmo edits it himself when asked
("remember that I like cricket", "forget that"), and also saves clearly
lasting things people tell him about themselves, saying so out loud.
`remember_fact` stops at `MEMORY_MAX_FACTS` (default 40) and asks to forget
something first; facts added by hand beyond that are still sent.

**Suggested facts.** Cozmo saves things live, while you talk. What he
misses gets a second chance: when a conversation ends (back to idle in
`--mode vad`, or when the app exits in the other modes), one LLM call
(`memory_suggestions.py`, no tools) reads what was said since the last
check and lists anything worth remembering for weeks or months. Most
conversations add nothing - the model is told that's the normal answer, and
a conversation of only a command or two isn't checked at all. Relative
dates are written as real ones ("the week of 2026-10-05"), so a stale plan
is recognizable later. Anything already known, waiting, or declined is
skipped, so it never duplicates what Cozmo saved live.

Suggestions wait in `memory.md`, below your facts:

```markdown
## Suggested (not yet approved)
- Has a dog named Bruno. [2026-10-01]
- Lives in Pune. [about Asha, 2026-10-01]

## Declined suggestions
- Is learning the guitar. [2026-09-30]
```

They're **never sent as things Cozmo knows**: he sees them in a separate,
clearly marked "noticed earlier but haven't confirmed" list (the newest 5),
and they don't count toward `MEMORY_MAX_FACTS` or show up in searches.
He's told to bring up **one**, in his own words, only when it fits - the
conversation touches on it, or a relaxed moment, never mid-request - and at
most once per conversation; most conversations shouldn't have one. Your
answer goes through `review_suggestion`: **keep** moves it into the
person's section (if memory is full, it stays waiting), **discard** moves it
to Declined so it's never suggested again, **later** leaves it. Ask "what
did you want to remember?" to go through them all. Or edit the file: move a
line up into a section (drop the `[...]` tag) to keep it, or delete it.

To empty a list without editing the file, ask: "clear all the waiting
suggestions", "clear the declined ones", or both (`clear_suggestions`).
Cozmo is told how many are declined (not what they are), so he knows that
list exists; he says how many will go and checks before clearing, since it
can't be undone. Cleared declined facts may be suggested again.
Suggestions don't expire. `MEMORY_SUGGESTIONS_ENABLED=false` turns the
check off.

When you quit (Ctrl+C, `quit` in text mode, or `pkill -f 'cozmo_brain --mode vad'`
from another shell - SIGTERM is handled exactly like Ctrl+C, so systemd stops
it cleanly too; only `kill -9` skips this; it ends with `Stopped.` and exit
code 130, no traceback - real errors still print theirs), the app checks whatever
wasn't checked yet - it prints "Checking this conversation for anything
worth remembering..." and exiting can take a few seconds longer. In
`--mode vad` that's usually nothing (each conversation was already checked
when it ended), so there's no extra call.

**Search.** `search_memory` matches keywords (a trailing "s" ignored, so
"like" finds "likes") against saved facts and everything said in the
archive - the human's words and Cozmo's `say` text, with dates. The system
prompt includes the current date and time, so "what did we talk about
yesterday?" can be placed. It's keyword search, not meaning search: "my
trip" won't find "my vacation".

**Privacy.** Everything in `data/` is plain text on the Pi, never
committed. What goes to the chat provider (Groq/OpenAI), on top of the
conversation itself:
- with *every* request: your confirmed facts and the newest 5 waiting
  suggestions, as part of the system prompt;
- with each after-conversation check: that conversation again, plus your
  facts and the waiting and declined suggestions.

Only `CHAT_PROVIDER=ollama` keeps all of it local. Cozmo is told never to
save passwords, health, money or address details, even if asked, and the
check is told never to suggest them - keep those out of hand edits too.

**What's verified:** offline tests (`tests/test_history_memory.py`:
archive, resume, import, retention, memory edits, search, and the engine
calling the tools; `tests/test_memory_suggestions.py`: the suggestion check
and review) and a simulated text-mode run. Saving facts live was seen
working on the Pi (2026-10-01). **Not verified:** how well each chat model
judges what's worth suggesting, and when it brings one up - that needs real
conversations, on each provider.

### Asking your own assistant (`ask_assistant`)

If you already run a personal assistant agent, Cozmo can hand it the things
he can't do himself: reminders, anything that needs the internet (news,
weather, facts he isn't sure of), or tasks outside his desk. It's written
against [OpenClaw](https://docs.openclaw.ai)'s gateway, but any endpoint
that speaks OpenAI's `/v1/chat/completions` works. Off by default:

```
ASSISTANT_ENABLED=true
ASSISTANT_NAME=Sunny                 # what you call it - Cozmo uses the same name
ASSISTANT_URL=http://<host>:18789    # base URL, no /v1/...
ASSISTANT_TOKEN=<gateway token>
ASSISTANT_MODEL=openclaw/main        # OpenClaw: openclaw/<agentId>
```

On OpenClaw, the chat-completions endpoint has to be enabled in the gateway
config first.

**How a request goes.** "Hey Cozmo, remind me at 5 to call mom" -> Cozmo
calls `ask_assistant` with one complete sentence. Agent runs are slow -
measured 27-28s against a real OpenClaw for a web lookup and for setting a
reminder, more when its model is rate-limited - so by default
(`ASSISTANT_BACKGROUND=true`) the request runs in the background: Cozmo
says "I've asked Sunny, I'll let you know" and carries on. When the answer
arrives (`assistant_relay.py`) it's queued, and Cozmo gets a turn of his own
to pass it on, in his own words:

- outside a `--mode vad` listening window (or in text/push-to-talk mode)
  he speaks up straight away, waiting only for any reply in progress
  (`turn_lock`);
- inside one, a recording nobody has started talking in yet is stopped
  early for it, and the window stays open afterwards so you can answer
  without the wake word.
- outside one, a short listening window opens once he's said it
  (`SPEAK_UP_LISTEN_S`, default 10s; `0` = off), so you can answer or
  follow up without the wake word too. A recording that's already capturing someone
  talking is never cut off - the answer comes right after that turn.

Passing an answer on takes one chat-model call. If that turn gets no
`say` out - usually the chat provider failing right then (429 rate limits
are common on free tiers) - it's retried once after 5s, and if that fails
too, Cozmo reads the answer out word for word ("Sunny says: ...", cut to
about 400 characters at a sentence end), or "Sorry, I couldn't get an
answer from Sunny just now." for a failed request. So while the app is
running, an answer that arrives is always heard. This covers the
assistant's immediate replies (an answer, or "I've set your reminder") -
the reminder itself, when it fires later, is sent by the assistant on its
own (WhatsApp) and never reaches Cozmo (spoken reminders: roadmap item 7).
A pending request is lost if the app restarts or stops before it's answered.

`ASSISTANT_BACKGROUND=false` is the simple version: he says "let me ask
Sunny", waits silently with the mic closed (up to `ASSISTANT_TIMEOUT_S`,
default 180s - lower it for this), then answers. At most 3 background
requests can be waiting at once. If the assistant can't be reached, times
out, or rejects the token, Cozmo is told so and says it - he isn't allowed
to make up an answer.

**Sessions.** OpenClaw's endpoint starts a new session for every request
unless the request carries an OpenAI `user` string; then all requests with
that string share one session ([docs](https://docs.openclaw.ai/gateway/openai-http-api)).
Every call sends `user` = `ASSISTANT_SESSION_USER` (default `cozmo`), so the
assistant remembers earlier requests from Cozmo - change it to start a fresh
session. Only the one request is sent, never Cozmo's own conversation;
it's prefixed with a short note that it was relayed by a robot from speech
(so the answer should be one to three plain spoken sentences, and a
garbled-looking request should be questioned, not acted on).

**Reminders** are set and delivered by the assistant (on OpenClaw, as a
message on your phone), so they work even while Cozmo is off. Cozmo
speaking them out loud himself is still roadmap item 7.

**Safety.** Anything Cozmo hears can reach the assistant, including
misheard speech and other people in the room - and an assistant like
OpenClaw can message people and run tools. So the prompt tells Cozmo to read
back, and wait for a yes, before anything that reaches or affects someone
else (messaging a person or group, buying, deleting, changing settings);
reminders for you and plain questions go straight through. That's a prompt
rule, not a hard block: the real limits are whatever the assistant's own
agent is allowed to do, so point `ASSISTANT_MODEL` at an agent with only the
permissions you're happy for anyone near Cozmo to use. The token lives only
in `.env` and is never logged. The connection is plain HTTP unless your
`ASSISTANT_URL` is `https://` - fine on a home network, not across the
internet.

Verified offline against a fake endpoint (`tests/test_assistant.py`,
background delivery and the vad stop rule included). Against the real
OpenClaw gateway (2026-10-04): the session behavior (two curl calls with
the same `user` remembered a word), and through `AssistantClient` a web
lookup (27.0s, a clean one-sentence answer) and a WhatsApp reminder (set in
28.3s, and received on WhatsApp 3 minutes later as asked). **Not yet run end to end through Cozmo on real hardware.**

### How a turn ends (`FINAL_LLM_CALL`)

A turn is a loop: the LLM returns tool calls, the engine runs them and sends
the results back, and so on until the LLM replies with **no** tool calls.
Cozmo's actual answer is itself a tool call (`say`). So after almost every
reply the loop makes one more LLM call, just to hear "done". In `--mode vad`
the mic stays closed during that call (~1–2s). `FINAL_LLM_CALL` decides what
happens to it:

| Setting | That last call | Mic reopens | Catches a model that acts *after* its `say`? |
|---|---|---|---|
| `async` (default) | made in the background | right after the `say` | ✅ (pauses the mic while it acts) |
| `sync` | made, and waited for | after it returns | ✅ |
| `skip` | not made | right after the `say` | ❌ the action is lost |

**When a turn is allowed to stop early at all.** This is the same rule for
`async` and `skip` (`CozmoEngine._turn_is_done()`). A step qualifies only if
**all** of these hold:
- its **last** call is a successful `say`;
- every tool in that step is a pure action (`Tool.safe_to_end_turn`: `say`,
  `gesture`, `play_animation`, `drive`, `turn`, `dock`, `remember_person`);
- nothing needs the model's attention: no error, no cliff/fall, no move
  refused on the charger, no failed charger return, no photo.

Until a step qualifies, everything runs synchronously, with the mic closed,
exactly like `sync`. **New tools default to `safe_to_end_turn=False`**, so a
turn that uses one never ends before the model has seen that tool's result.
Only opt a tool in if its success result tells the model nothing new.

**Examples** (verified in scripted tests with a fake model). "Before mic
reopens" is what you wait through; "background" happens while Cozmo is
already listening:

| You say | Model's steps | `sync`: before mic reopens | `async`: before mic reopens | `async`: background |
|---|---|---|---|---|
| "Hi!" | `say` → *done* | `say`, *done* | `say` | *done* |
| "Turn left" (batching model) | `say`+`turn` → *done* | both | both (last call isn't `say`) | — |
| "What do you see?" | `say`+`look` → `say` → *done* | all 3 | `say`+`look`, `say` | *done* |
| "What moves can you do?" | `list_animations`+`say` → `say` → *done* | all 3 | first 2 (must read the list) | *done* |
| "Drive forward" (hits a cliff) | `say`+`drive` → `say` → *done* | all 3 | first 2 (must react to the cliff) | *done* |
| "Turn left" (**one-tool-per-step model**, e.g. Groq `openai/gpt-oss-120b`) | `say` → `turn` → *done* | all 3 | `say` | `turn` (mic paused), *done* |
| "Look around" (same kind of model) | `say` → `look` → `say` → *done* | all 4 | `say` | `look`, `say`, *done* (mic paused from `look`) |

With `skip`, the last two rows would stop right after the first `say`: he'd
announce the turn and never make it. That's exactly what happened with
gpt-oss-120b in a live test, and why `skip` isn't the default.

**What `async` looks like when the model continues** (the "one-tool-per-step" rows):

```
you: "turn left"
  ├─ LLM step 1: say("Sure, turning!")   ← Cozmo speaks
  ├─ handle_turn() returns → mic reopens, listening...
  │     (background) LLM step 2 → turn  ← model continued
  │        ├─ followup_interrupt set → recording stops within ~30ms
  │        │   (a capture you'd already started is discarded)
  │        ├─ turn(90) runs, mic closed
  │        └─ LLM step 3 → done, turn_lock released
  └─ "(Cozmo continued his reply - listening again)" → same listening window resumes
```

**Rules that hold in every mode:**
- A turn never returns while a gesture is still running (`spin` is ~7s), so
  the mic never reopens into motor noise.
- The background call keeps holding `turn_lock`. Your next turn, an
  autonomous charger return, or an idle fidget waits for it, so the
  conversation history always stays in order.
- `async` only applies in `--mode vad`. Text and push-to-talk modes don't
  reopen a mic on their own, so they always behave like `sync`.
- `async` still makes the extra LLM call every turn. It saves waiting, not
  API usage (a live test hit Groq's free-tier `429` limit). Only `skip`
  saves the call.

The history this leaves behind (a turn ending on a tool result, then the
next user message) was live-tested and accepted by every provider: Ollama,
OpenAI, and Groq.

**Reading the log.** Every LLM step is logged, including the final "done"
one, with how long the call took. Unless marked `(background)`, that's time
the mic was closed:

```
# async (default) - a plain reply
LLM step 1 (1.10s): say
tool say (11.2s): OK: Said (mood=proud) while performing 'nod_yes' ...: I feel POWERFUL!
LLM step 2 (background) (0.90s): no tool calls - turn done   <- listening meanwhile

# sync - same reply
LLM step 1 (1.10s): say
tool say (11.2s): OK: Said ...
LLM step 2 (0.90s): no tool calls - turn done        <- mic closed for this

# async - a model that says first, then acts
LLM step 1 (1.10s): say
tool say (2.4s): OK: Said ...: Sure, turning!
Model continued its reply - pausing listening while it runs.
LLM step 2 (background) (0.95s): turn
tool turn (1.8s): OK: Turned 90 degrees.
LLM step 3 (background) (0.80s): no tool calls - turn done
```

(Timings are illustrative.) In `--mode vad` each tool's result is logged
the moment it finishes (`CozmoEngine.log_steps_live`), and the old
end-of-turn `[say] OK: ...` recap isn't printed. Printed after the whole
turn, that recap used to appear *after* `LLM step 2`, which read as if
Cozmo spoke last. Text and push-to-talk modes still print the recap, since
there it's the reply itself. If step 1 shows only `say` right after you asked
for a movement, and step 2 shows the movement, that model is one of the
"one-tool-per-step" kind above. `sync` and `async` handle it; `skip` would
not.

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
  `KNOWN_PEOPLE_DIR/<name>.jpg` (default `data/known_people/`) — no
  processing at all. That's a face only; facts about the person go to
  `data/memory.md` via `remember_fact` (see
  [Conversation history and memory](#conversation-history-and-memory)).
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

#### Presence check: knowing who's at the desk (`PRESENCE_CHECK_ENABLED`)

Cozmo sits on the desk with the person in the chair in front of him, so he
can occasionally look up and work out who he's talking to, without being
asked. **Off by default** (see privacy below). When on, `presence.py` takes
the place of an idle fidget (`idle_fidget.py`), so it only ever happens after
`IDLE_FIDGET_AFTER_S` of quiet — never mid-conversation, never while picked
up — and holds `turn_lock` like a fidget does:

1. He tilts his head up (`PRESENCE_HEAD_ANGLE_DEG`, default 35°), takes one
   photo, lowers his head, and **deletes the photo** straight away.
2. The vision model is asked whether a real face is visible, then whether it
   matches each `remember_person` reference photo (shared helpers in
   `people.py`, also used by `who_is_this`).
3. What happens next is deliberately low-key:

| Result | What Cozmo does | Next look |
|---|---|---|
| Nobody in view | Nothing (clears who he last saw) | after `PRESENCE_EMPTY_RETRY_S` (300s) |
| A known person | Nothing out loud. The name goes into the system prompt as "a guess from a blurry photo", so he can use it naturally | after `PRESENCE_RECHECK_S` (900s) |
| A stranger | One casual spoken line ("Hi there! I don't think I know you yet. What's your name?"), at most once per `PRESENCE_ASK_COOLDOWN_S` (3600s). A note tells the model to call `remember_person` if they give a name, to drop it if they brush it off, and — if someone is already enrolled — to casually ask once where that person is | after `PRESENCE_ASK_COOLDOWN_S` |

`remember_person` and `who_is_this` feed the same "who's here" state, and
while a stranger is the last thing he saw, the prompt tells him not to assume
it's the primary user.

Caveats:

- **Privacy:** every look sends a photo of whoever is there to
  `VISION_PROVIDER` (the chat provider if blank). With a local Ollama that
  stays on your machine; with Groq/OpenAI it is a cloud upload of whoever is
  at the desk. Enable it only if everyone in the room is fine with that.
- **Reliability:** same as `who_is_this` — a vision model comparing blurry
  photos, so expect misses and the odd needless "who are you?".
- **Answering him:** in `--mode vad` the person may need to say the wake word
  before replying to his question (no listening window opens after it).
- **Head angle is a guess** — tune `PRESENCE_HEAD_ANGLE_DEG` once you've seen
  what he sees. Not yet verified on real hardware.
- It works even with `IDLE_FIDGET_ENABLED=false` (the look still runs; the
  gestures don't).

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

### Keeping speech-to-text from hallucinating

Whisper doesn't return empty text for noise. It invents something from its
training data instead ("Thank you.", "you", even a Korean news sign-off).
The defenses are layered, cheapest first:

1. **Nothing is recorded until activation:** the wake word or a tap opens a
   listening window.
2. **Local gate, on the Pi, before anything is sent:** `webrtcvad` must call
   it speech-shaped **and** it must clear `VAD_MIN_RMS`, for
   `VAD_MIN_SPEECH_MS` in a row (`audio/vad.py`). Cozmo's own motors can't
   trigger it: taps are ignored while he moves, there's no fidgeting while
   listening, and a turn waits for gestures to finish.
3. **Neural speech check before sending** (`audio/speech_gate.py`): Silero
   VAD, the model bundled in faster-whisper (already on the Pi; no extra
   download). If it finds under `SPEECH_GATE_MIN_SPEECH_MS` (150ms) of real
   speech in the clip, the clip **isn't sent to any provider**. There's no
   STT call and nothing to hallucinate on. Measured with the real model:
   hiss and clatter 0.00s, motor hum 0.03–0.06s, versus one-word replies
   ("yes", "no", "okay", including a quiet "yes") 0.42–0.67s and a sentence
   ~3.5s. The check takes ~10–20ms. The model is loaded in the background
   when listening starts, so the first clip doesn't pay the ~1s load. If
   faster-whisper isn't installed, the check is skipped and clips are sent
   as before. Each clip logs `Speech gate (Silero): 0.48s of speech
   detected ... - sending to STT` (or `not sent`).
4. **Only 200ms of the end-of-speech silence is sent.** The 800ms used to
   *detect* that you stopped is trimmed, so Whisper gets no long silent tail
   to invent an ending over.
5. **Whisper is told the language** (`STT_LANGUAGE=en`), so it doesn't
   guess one on noise.
6. **Whisper's own confidence is used** (`verbose_json`, per segment). A
   segment is dropped if its no-speech estimate is above
   `STT_NO_SPEECH_PROB`, its decoding confidence is below
   `STT_MIN_AVG_LOGPROB`, or it's a repetitive loop
   (`STT_MAX_COMPRESSION_RATIO`). Local faster-whisper also gets its
   built-in Silero voice filter (`vad_filter=True`).
7. **After the fact:** text with no letters or digits (Groq returns a bare
   `"."` for clatter) and exact known phrases (`llm/stt_postprocess.py`) are
   ignored.

**Measured live (2026-09-28)**, same clips through each provider's client
(`tests/live/test_stt_hallucination.py`):

| Clip | Groq `whisper-large-v3-turbo` | OpenAI `whisper-1` | Wit.ai |
|---|---|---|---|
| Spoken sentence | correct (no_speech 0.00, logprob -0.12) | correct (0.00, -0.32) | correct |
| Room hiss | "Thank you." (0.00, -0.57) → phrase filter | "you" (**0.95**) → dropped | empty |
| Clatter | "." (0.00, -0.97) → no-letters rule | "You" (**0.93**) → dropped | empty |
| Motor hum | "." (0.00, -0.59) → no-letters rule | "you" (**0.97**) → dropped | empty |

**Groq's no-speech estimate is always 0.00**, so on Groq layer 6 only has
the logprob floor, and nothing new gets caught at the default -1.0. An
unfamiliar hallucinated phrase can still get through there. Every
transcription now logs each segment:

```
STT segment kept (no_speech=0.00 logprob=-0.21 compression=0.97): 'turn left please'
STT segment DROPPED (no_speech=0.95 logprob=-0.93 compression=0.27): 'you' - no_speech_prob 0.95 > 0.6
```

Once your real speech's `logprob` is known from those lines, tighten
`STT_MIN_AVG_LOGPROB` to just below it (e.g. -0.5). On the test clips that
drops Groq's "Thank you." (-0.57) and keeps speech (-0.12). Test-voice
speech is cleaner than a real mic, so don't set it tighter than the logs
support. Most noise never gets that far now: layer 3 stops it before it's
sent. Live checks: `tests/live/test_speech_gate_silero.py` (the real model,
on this machine or the Pi) and `tests/live/test_stt_hallucination.py`.

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

Piper needs a voice downloaded once. Save it into `models/`, from the
project folder:

```bash
python3 -m piper.download_voices en_US-lessac-medium --data-dir models
```

That creates `models/en_US-lessac-medium.onnx` (the voice) and
`models/en_US-lessac-medium.onnx.json` (its settings, which Piper reads from
next to the `.onnx`). `.env` then names it without the folder or extension:
`LOCAL_TTS_VOICE_PATH=en_US-lessac-medium`. Without `--data-dir`, the
downloader saves into whatever folder you run it from. Voice files are
large and re-downloadable, so `.gitignore` keeps them out of git. See
[Model files](#model-files-models).

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
necessarily vision-capable. `GROQ_CHAT_MODEL`'s old default
(`llama-3.3-70b-versatile`) wasn't, and has since been removed from Groq
entirely (`model_not_found`, confirmed 2026-09-28). Its default is now
`qwen/qwen3.8-27b`, which does both. `GROQ_VISION_MODEL` still has its own
explicit default (the same model), so that pointing `GROQ_CHAT_MODEL` at a
text-only model doesn't silently break `look`; `OPENAI_CHAT_MODEL`'s default (`gpt-4o-mini`) already handles
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
`BATTERY_CHECK_INTERVAL_S` (default 15s). Below `BATTERY_LOW_VOLTAGE`
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

The monitor also skips checks entirely while the connection looks stale
(`is_healthy()` false) — during a drop, pycozmo keeps reporting the last
voltage it received, a frozen value that could otherwise falsely trigger
(or mask) a low-battery reaction. Every valid reading is also handed to
`charger_return.py`'s `ChargerReturner`, which decides when to offer or start
a return to the charger — see roadmap item 6 below.

### Coming off the charger: battery line and charger break

**The model now sees the real battery.** Every turn ends with a
`[Battery: 3.92V, docked for 12 min.]` line (next to the existing charger
status), so "are you charged?" / "come out of the charger" are answered from
the voltage, not guessed — before this, the model only had a one-line
"on charger / charging / not charging (probably full)" flag and said things
like "Now I'm fully charged!" with nothing behind it. There is still no
battery percentage, so the persona prompt tells it never to claim "full".
Each turn's status lines are also logged at debug level. When asked to come
out, the model calls the `leave_charger` tool (above) rather than guessing a
`drive`.

**Charger break (`CHARGER_BREAK_ENABLED`, off by default).** For an old
device where sitting on the charger for hours isn't ideal (there is no
temperature sensor, so this is a time limit, not an overheat detector),
`charger_break.py` steps Cozmo off the dock now and then:

1. After `CHARGER_BREAK_AFTER_MIN` (30) *continuous* minutes docked — the
   timer resets whenever he leaves the dock for any reason or is picked up —
   when it's quiet (`IDLE_FIDGET_AFTER_S`, no listening window, no turn
   running) and the battery isn't too low to leave (the same rule as every
   movement: docked at or below `BATTERY_LOW_VOLTAGE` stays put), he says
   he's going to stretch his wheels and drives off with `leave_charger`.
   There's deliberately no separate "high enough" voltage: the dock reads
   high and nobody knows this robot's curve yet, so a number would only be a
   guess — `data/battery.jsonl` (below) collects the real data first.
2. After a random `CHARGER_BREAK_STRETCH_MIN`–`MAX` (5–10) minutes he says
   the stretch is over and drives back with `return_to_charger()`. If he
   can't (picked up, charger location lost) he just stays out and the
   normal battery rules take over.

Deliberately narrow, so existing behaviour is unchanged: the idle fidget is
untouched (a `peek` still drives him off the dock at random once charging has
stopped), and **only a break-initiated exit gets a timed return** — a peek exit
or a "come out" request stays out until the low-battery offer (3.7V) /
automatic return (3.5V), exactly as before. Because the break exits via the
same `drive()` as every other movement, the false-cliff handling, charger
position recording and low-battery block all still apply. Mind the table edge
when first enabling it.

**Battery history (`data/battery.jsonl`).** So a later "can I come out, and
for how long?" decision can be based on *this* robot's battery instead of a
guessed threshold, `battery_log.py` records events from the battery
monitor's readings (every `BATTERY_CHECK_INTERVAL_S`, so times are to about
15s) — events only, a few dozen lines a day, nothing personal:

- `run_start`, `docked` / `undocked`, `charging_started` / `charging_stopped`
  (with minutes docked so far) — the on-charger charge curve.
- `stretch` — one per time off the dock, written when he's back: how he left
  (`break`, `asked` = `leave_charger`, `moved` = an idle peek or any other
  movement, `picked_up`), the voltage on the dock just before and the first
  reading off it, minutes off, lowest voltage, minutes until the first
  low/critical reading, and how it ended (`break_over`, `dock_tool`,
  `critical`, `run_end`, `other` = e.g. put back by hand).

The same events go to the normal log at info level (`Battery: ...`). It's
kept until you delete it — a retention setting of its own comes with roadmap
item 8, the feature that will use it. Nothing in the app uses these numbers
yet — `standalone/battery_report.py` summarises them, see
[Standalone scripts](#standalone-scripts).

### Wake word detection (zero-network)

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

#### Training a custom wake word

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

#### Model files (`models/`)

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

**Asking the assistant, answer in the background** (see
[Asking your own assistant](#asking-your-own-assistant-ask_assistant)). A
real run on 2026-10-04 against a real OpenClaw agent named Sunny, with
Groq chat and the simulated robot, driven through the engine directly
rather than `--mode text`; log lines trimmed:
```
you> Hey Cozmo, what's the latest news headline in India today?
LLM step 1 (0.89s): ask_assistant
Asking Sunny: Tell me the latest / top news headline in India today, October 4 2026.
tool ask_assistant (0.0s): OK: Asked Sunny: "...". The answer comes later, on its own - ...
(user turn returned after 1.0s - Cozmo is free to talk and listen)
Sunny replied (35.5s): Top headlines today include a student protest march in New Delhi ...
Speaking up on my own: [Sunny just answered the request you sent earlier (...): ...]
[say] OK: Said (mood=curious) while performing 'alert': Oh, the news is in! According to
      Sunny, there are two big ones today: First, students are marching in New Delhi ...
```
In that run the chat model's step after `ask_assistant` hit Groq's
free-tier rate limit (429), so the usual "I've asked Sunny" line was never
said; the answer was still delivered on its own.

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

**Memory:**
- *"Remember that I like cricket."* → `remember_fact`, then a line under
  your section in `data/memory.md`.
- *"What do you know about me?"* → answered from the memory in the prompt,
  no tool needed.
- *"Actually, forget that I like cricket."* → `forget_fact`.
- *"What did we talk about yesterday?"* / *"When did I mention Goa?"* →
  `search_memory`, answered with dates from the archive.

**Your own assistant** (needs `ASSISTANT_ENABLED=true`; the name is your
`ASSISTANT_NAME`):
- *"Remind me in 10 minutes to stretch."* → `ask_assistant`; the reminder
  arrives as a message from the assistant (confirmed on WhatsApp with
  OpenClaw), even if Cozmo is off by then.
- *"What's the weather in Pune?"* / *"Any big news today?"* → `ask_assistant`,
  "I've asked Sunny", and the answer ~30s later, spoken on its own.
- *"Tell my friends group I'll be late."* → affects other people, so Cozmo
  should read it back and wait for a yes before asking.
- *"What's Sunny?"* → answered from the prompt: your assistant, by its name.

---

## Standalone scripts

One-off tools in `standalone/`, separate from the app. Run them from the
project folder, inside `cozmo-env`.

| Script | What it's for | Needs |
|---|---|---|
| `sync_env.py` | After a `git pull`: which `.env` settings are missing, stale, or unknown | Nothing |
| `show_history.py` | Read archived conversations as a transcript | Nothing |
| `battery_report.py` | How this robot's battery behaves on and off the charger (`data/battery.jsonl`) | Nothing |
| `orchestrator.py` | The original single-file proof of concept: mic → STT → LLM → Cozmo → TTS | Robot, mic, `GROQ_API_KEY`, Ollama |
| `pose_drift_test.py` | How far Cozmo's own position tracking drifts from reality | Robot, tape, a ruler |

### `sync_env.py` - check `.env` after a pull

`.env` is gitignored, so a pull never updates it. This compares it with
`.env.example`:

```bash
python3 standalone/sync_env.py                # report only, changes nothing
python3 standalone/sync_env.py --apply        # append missing settings (asks first, backs up .env)
python3 standalone/sync_env.py --apply --yes  # same, without asking
```

It lists settings missing from `.env` (with their example values), flags
known-stale values (e.g. a removed Groq model, or `look.png`/`known_people`
from before `data/`), and lists settings `.env.example` doesn't know - by
name only, never printing a value. It never changes or removes an existing
line; `--apply` only appends, with the comments from `.env.example`, after
saving a timestamped backup.

### `battery_report.py` - how the battery actually behaves

Reads `data/battery.jsonl` (see
[Coming off the charger](#coming-off-the-charger-battery-line-and-charger-break))
and prints charge sessions (voltage on docking, minutes until `IS_CHARGING`
went off, total time docked), every stretch off the dock (how he left,
voltages, minutes off, minutes until low/critical, how it ended), and —
once at least 3 stretches actually reached low — a straight-line fit of
"minutes until low" against the voltage on leaving. That's the evidence for
deciding how a smarter "can I come out, and for how long?" should work; with
less data it says so instead of guessing.

```bash
python3 standalone/battery_report.py          # data/battery.jsonl
python3 standalone/battery_report.py --demo   # made-up numbers, just to see the format
```

### `show_history.py` - read past conversations

```bash
python3 standalone/show_history.py              # the latest run
python3 standalone/show_history.py 2026-10-01   # every run that day
python3 standalone/show_history.py --list       # which days/runs exist
python3 standalone/show_history.py data/history/2026-10-01/15-39-51.jsonl
```

Output looks like this (one run, `--mode vad`):

```
===== 2026-10-01 15:39:51  (mode: vad, simulated: False, chat_provider: groq, ...)

--- 15:39:51 woke up (wake word)
15:39:55 you: hey cozmo, remember that I like cricket
15:39:57   (remember_fact fact=Likes cricket.)
15:39:57 Cozmo [happy]: Cricket fan, got it! I'll remember that.
--- 15:40:20 back to idle
===== 15:52:03 stopped
```

It shows what was heard, what Cozmo said (with mood and gesture), the
other tools he used, failed tools, and the photo files saved next to the
transcript. The `.jsonl` itself has everything, including every tool
result. It reads `DATA_DIR` from `.env`. See
[Conversation history and memory](#conversation-history-and-memory).

### `orchestrator.py` - minimal end-to-end test

```bash
python3 standalone/orchestrator.py
```

Press Enter, speak for `RECORD_SECONDS` (default 5), and Cozmo answers;
type `quit` to exit. Groq Whisper for STT and Groq Orpheus for TTS, Ollama
(`OLLAMA_BASE_URL`/`OLLAMA_MODEL`) for chat, PyCozmo for the robot. It reads
the same `.env`, but only its own few settings (`GROQ_API_KEY` is
required). No memory, history, gestures, or provider switching - useful for
checking the mic → STT → LLM → robot → speaker chain on the Pi without the
full app. Its `turn()` is uncalibrated (see [Known gotchas](#known-gotchas)).

### `pose_drift_test.py` - position tracking drift

```bash
python3 standalone/pose_drift_test.py
```

An exploratory hardware test behind return-to-charger (roadmap item 6),
not a routine calibration. It drives a fixed path (`DRIVE_STEPS` at the top
of the script - edit it for your own test), prints where Cozmo *thinks* he
is, drives back to the start with `go_to_pose()` plus a heading correction,
and asks you to measure the real position by hand at each step. Mark the
start on the floor with tape first; it waits for Enter before moving. Uses
`TURN_SPEED_MMPS`/`TURN_SECONDS_PER_DEGREE` from `.env`; no LLM, STT, TTS or
API keys. The protocol and past results are in the script's docstring and
roadmap item 6.

## Running the tests

```bash
python tests/run_all.py        # from the repo root, inside the project's venv
```

Runs every offline test in `tests/`, each in its own process, and prints one
line per file (`ok`/`FAIL`, checks passed, time); it exits non-zero on any
failure. These need **no robot, mic, network, or API keys**. They drive
the real `cozmo_brain` code against fakes: `tests/fake_pycozmo.py` stands in
for the robot (a fake `pycozmo.Client`) and `tests/fake_engine.py` for the
LLM (a scripted chat client). They also **ignore your `.env`**
(`tests/_harness.py`) and run against the code's built-in defaults, so
personal settings can't make them fail. A single test runs directly too,
e.g. `python tests/test_async_followup.py`.

What they cover: charger pose recording and invalidation, navigation and
docking (`test_return_real`, `test_dock_*`, `test_rotation_direction`), the
LOW/CRITICAL return policy (`test_return_policy`), fidget and charger
interplay (`test_fidget_charger`, `test_dock_dropoff`), self-caused-tap
suppression and VAD limits (`test_abc`), the charger-blocked reply
(`test_blocked_speech`), how turns end under every `FINAL_LLM_CALL`
mode (`test_final_say`, `test_async_*`), model files found by name in
`models/` and the real `hey_cozmo` wake word loading (`test_model_paths`),
and the conversation archive, resume, memory edits and search, including
the engine calling the memory tools (`test_history_memory`), and suggested
facts: the after-conversation check, the Suggested/Declined sections, and
keep/discard/later and clearing the lists (`test_memory_suggestions`),
the presence check — head tilt, silent for known/empty, one casual ask for a
stranger, how it slots into the idle fidget (`test_presence`), the charger
break, `leave_charger`, the per-turn battery line and who-moved-him labels
(`test_charger_break`), and the battery history records and analysis
(`test_battery_log`), SIGTERM stopping the app like Ctrl+C
(`test_sigterm`), and `ask_assistant` against a fake endpoint - the
session `user` field, failures, waiting for the answer, and background
answers delivered on their own (with the retry and word-for-word fallback
when the chat model fails), including only stopping a vad recording
nobody is talking in (`test_assistant`), and the short no-wake-word
window after Cozmo speaks up on his own (`test_speak_up_listen`).

`tests/live/` holds **opt-in** tests that make real API calls with the keys
in your `.env`: which providers accept the conversation history shape, how
each model batches tool calls, and `async` end to end. `run_all.py` never
runs them. Run one yourself, e.g. `python tests/live/test_providers_history.py
groq`. They cost a few requests each, and Groq's free tier can answer 429.

All of these are logic checks. Anything involving the physical robot
(docking accuracy, real turn rates, real mic levels) still needs a
real-hardware run.

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
| `LOCAL_TTS_VOICE_PATH` | Piper voice for `TTS_PROVIDER`=`local`: its name in `models/` (`en_US-lessac-medium`) or a path to its `.onnx`. See [Model files](#model-files-models). |
| `LOCAL_TTS_SPEED` | Speed, same `>1.0`=faster direction as `GROQ_TTS_SPEED`/`OPENAI_TTS_SPEED` (Piper's own `length_scale` is the inverse — converted in `local_client.py`). |
| `EDGE_TTS_VOICE` | Stock `edge-tts` voice name (used when `TTS_PROVIDER`=`edge`). List them with `edge-tts --list-voices`. |
| `EDGE_TTS_SPEED` | Speed, same `>1.0`=faster direction as the other `*_TTS_SPEED` settings (converted to edge-tts's own signed-percentage `rate` string). |
| `TTS_GAIN` | Max volume boost for TTS output, RMS-targeted with a soft limiter (Cozmo's speaker is quiet). |
| `TTS_LEADIN_MS` | Silent lead-in before speech, works around the first word often being inaudible. |
| `TTS_PITCH_SHIFT` | Pitch+tempo shift for a smaller/more childlike/robotic voice. 1.0 = off. |
| `TTS_ROBOT_MOD_DEPTH` / `TTS_ROBOT_MOD_HZ` | Optional ring-modulation robotic timbre. Depth 0.0 = off. |
| `LOG_LEVEL` | Default log level (`DEBUG`/`INFO`/`WARNING`/`ERROR`, case-insensitive; default `INFO`). `--log-level` overrides it per run. |
| `ROBOT_BACKEND` | `real` or `simulated` (cozmo_brain/ only; `--simulate` overrides it). |
| `ROBOT_STALE_AFTER_S` | Seconds without robot telemetry before auto-reconnect kicks in. |
| `BATTERY_LOW_VOLTAGE` / `BATTERY_CRITICAL_VOLTAGE` | Voltage thresholds for the face battery-warning icon; `BATTERY_LOW_VOLTAGE` also gates whether `drive`/`turn` refuse to move while charging (real backend only). |
| `BATTERY_CHECK_INTERVAL_S` | How often the battery monitor polls voltage. |
| `COZMO_WIFI_SSID` / `COZMO_WIFI_PASSWORD` | Optional Wi-Fi auto-connect (Linux/nmcli only). Password only needed for the first connect. |
| `TURN_SPEED_MMPS` / `TURN_SECONDS_PER_DEGREE` | `turn()` calibration — tune with `--mode calibrate`. |
| `MAX_DRIVE_SPEED_MMPS` / `MAX_DRIVE_DISTANCE_MM` | Safety clamps on the `drive` tool. |
| `CHARGER_EXIT_DISTANCE_MM` / `CHARGER_EXIT_SPEED_MMPS` | How far/fast to drive straight off the charger before performing a requested turn, if still docked (real backend only). |
| `CHARGER_DOCK_DISTANCE_MM` / `CHARGER_DOCK_SPEED_MMPS` | How far/fast the `dock` tool reverses onto the charger (real backend only). The distance also sets how far out from the recorded charger pose the return-to-charger staging point is. |
| `CHARGER_DOCK_OVERSHOOT_MM` | How much further than `CHARGER_DOCK_DISTANCE_MM` the dock reverse may continue; it stops the moment the charger contacts engage. |
| `CHARGER_CONTACT_WAIT_S` | How long `dock()` waits after reversing for the charger contacts to read as engaged before calling it a miss (the flag can lag behind actually being seated). |
| `CHARGER_BREAK_ENABLED` | Default `false`. `true` lets Cozmo step off the charger after `CHARGER_BREAK_AFTER_MIN` continuous docked minutes (30) while quiet and not too low to leave (`BATTERY_LOW_VOLTAGE`), then drive back after a random `CHARGER_BREAK_STRETCH_MIN`–`MAX` minutes (5–10). See [Coming off the charger](#coming-off-the-charger-battery-line-and-charger-break). |
| `AUTO_RETURN_TO_CHARGER_ENABLED` | Low-battery return-to-charger: offer at `BATTERY_LOW_VOLTAGE`, go on its own at `BATTERY_CRITICAL_VOLTAGE`, ask for help if the charger location isn't known (default `true`). |
| `RETURN_TO_POSE_TIMEOUT_S` | Upper bound on one `go_to_pose()` navigation leg before it's aborted (pycozmo's own has no timeout). |
| `MAX_TOOL_ITERATIONS` | Cap on LLM↔tool round-trips per user turn. |
| `FINAL_LLM_CALL` | The LLM call after a reply ending in a clean `say` (usually just "done"): `async` (default) reopens the mic right away and makes it in the background, pausing the recording if the model continues (`--mode vad` only; other modes behave as `sync`); `sync` waits for it before listening again (the fallback if `async` misbehaves); `skip` drops it (fastest, but a model that says "sure!" alone and acts in its *next* step, confirmed live with Groq's `openai/gpt-oss-120b`, loses the action). Early end only happens when every tool in the batch is a pure action (`Tool.safe_to_end_turn`, opt-in per tool, so new tools default to asking again) and nothing needs the model's attention (errors, hazards, refused moves, photos). A turn never returns until background gestures finish, so the mic doesn't reopen mid-`spin`. Worked examples: [How a turn ends](#how-a-turn-ends-final_llm_call). |
| `CONVERSATION_MAX_MESSAGES` | How many messages the model sees (system prompt + recent history). |
| `DATA_DIR` | Where runtime data goes (default `data`, relative to the project): history, memory, photos. |
| `HISTORY_RETENTION_DAYS` | Delete archived days older than this at startup (default `0` = keep everything). |
| `MEMORY_MAX_FACTS` | Most facts `remember_fact` may save (default 40, read at startup); the memory is sent with every turn, so raising it makes every request longer. |
| `MEMORY_SUGGESTIONS_ENABLED` | After each conversation, one LLM call suggests facts worth keeping, for Cozmo to ask you about (default `true`). |
| `CONVERSATION_HISTORY_PATH` | The old single-file history - only read once, to import it into the archive. |
| `VAD_AGGRESSIVENESS` / `VAD_SILENCE_MS` / `VAD_MAX_UTTERANCE_S` | Hands-free listening tuning. |
| `VAD_FOLLOWUP_TIMEOUT_S` | How long a conversation stays open after a reply before the wake word is needed again. |
| `SPEAK_UP_LISTEN_S` | After Cozmo speaks up on his own with something you may answer (a background `ask_assistant` answer, the low-battery offer), `--mode vad` listens this many seconds without the wake word (default 10; `0` = off). |
| `STT_LANGUAGE` | Language hint for Whisper (Groq/OpenAI/local), default `en`; empty = auto-detect. Stops foreign-language hallucinations. |
| `STT_NO_SPEECH_PROB` / `STT_MIN_AVG_LOGPROB` / `STT_MAX_COMPRESSION_RATIO` | Per-segment Whisper confidence filters; see [Keeping speech-to-text from hallucinating](#keeping-speech-to-text-from-hallucinating). Every segment's scores are logged for tuning. |
| `SPEECH_GATE_ENABLED` / `SPEECH_GATE_MIN_SPEECH_MS` / `SPEECH_GATE_THRESHOLD` | Silero speech check on every clip before any STT call (skipped if faster-whisper isn't installed). A clip with less than the minimum detected speech (default 150ms) isn't sent. |
| `VAD_MIN_SPEECH_MS` | Onset debounce (consecutive ms of speech needed to start recording, not a minimum utterance length) — filters out noise-triggered hallucinated transcriptions. |
| `VAD_MIN_RMS` | Loudness floor a frame must also clear (in addition to webrtcvad) to count as speech — the actual quota-saving filter, rejects noise before it ever reaches the STT API. |
| `WAKE_WORD_MODEL` / `WAKE_WORD_THRESHOLD` | Wake word gating `--mode vad` — a model's name in `models/` (default `hey_cozmo`), a stock name (`hey_jarvis`, …), or a path to an `.onnx`. |
| `TAP_THRESHOLD` / `TAP_DEBOUNCE_MS` | `--mode vad`'s tap-activation tuning — accelerometer-spike threshold and minimum time between accepted taps (real backend only; alternate trigger alongside the wake word). Cozmo's own lift movements (e.g. the `shrug` idle-fidget gesture) are suppressed separately, not via this threshold. |
| `IDLE_FIDGET_ENABLED` / `IDLE_FIDGET_AFTER_S` | Whether Cozmo plays a small idle gesture after this many quiet seconds with no real conversation turn. Default 30s — kept short since Cozmo's own hardware-level inactivity disconnect/power-off happens well before the original 5min default ever fired. |
| `GESTURE_ASYNC_ENABLED` | Whether `gesture` runs in the background so `say` can overlap with it, instead of blocking until the gesture finishes. |
| `GESTURE_SPEECH_SYNC_ENABLED` | `false` (default) starts `say`'s bundled gesture before synthesizing speech (instant reaction, overlap not guaranteed if synthesis is slow). `true` synthesizes first and starts the gesture right as playback begins (overlap guaranteed regardless of synthesis speed, at the cost of a pause before Cozmo reacts). |
| `VISION_ENABLED` | Whether `look()`'s photo gets attached to the next LLM turn. |
| `KNOWN_PEOPLE_DIR` | Where `remember_person`'s reference photos are stored (default `data/known_people`; experimental). |
| `PRESENCE_CHECK_ENABLED` | Default `false`. `true` lets Cozmo occasionally look up when idle to see who's at the desk, and ask strangers who they are (see [Presence check](#presence-check-knowing-whos-at-the-desk-presence_check_enabled)). Sends a photo to `VISION_PROVIDER` each look. |
| `PRESENCE_HEAD_ANGLE_DEG` | Head tilt for the look (default 35°; robot max ≈ 44°). |
| `ASSISTANT_ENABLED` | Default `false`. `true` adds the `ask_assistant` tool (needs `ASSISTANT_URL`). See [Asking your own assistant](#asking-your-own-assistant-ask_assistant). |
| `ASSISTANT_NAME` | What Cozmo calls the assistant, out loud and in the tool description (default `Assistant`). |
| `ASSISTANT_URL` / `ASSISTANT_TOKEN` | Base URL of the OpenAI-compatible endpoint (no `/v1/...`) and its bearer token. |
| `ASSISTANT_MODEL` | The request's `model` field; for OpenClaw it picks the agent, `openclaw/<agentId>` (default `openclaw/default`). |
| `ASSISTANT_SESSION_USER` | Sent as `user`; OpenClaw keeps one session per value (default `cozmo`). |
| `ASSISTANT_BACKGROUND` | `true` (default): ask in the background and speak up with the answer when it arrives. `false`: wait silently for it. |
| `ASSISTANT_TIMEOUT_S` | Longest wait for an answer (default 180; lower it with `ASSISTANT_BACKGROUND=false`). |
| `PRESENCE_RECHECK_S` / `PRESENCE_EMPTY_RETRY_S` / `PRESENCE_ASK_COOLDOWN_S` | Seconds before the next look after a known person (900) / nobody (300) / a stranger (3600, also the minimum gap between "who are you?"s). |

---

## Known gotchas

- **`ModuleNotFoundError: No module named 'pkg_resources'` in `--mode vad`**
  (or a `pkg_resources is deprecated` warning) means the original, abandoned
  `webrtcvad` package is installed, not `webrtcvad-wheels`. Swap it - see
  [step 1](#1-python-environment-on-the-deployment-machine) above.
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

- ✅ **Conversation memory** — `Conversation` class, trimmed; every run
  archived by date under `data/history/` (with photos) and resumed on the
  next start; `data/memory.md` for facts about people, with
  `remember_fact`/`forget_fact`/`search_memory`. See
  [Conversation history and memory](#conversation-history-and-memory).
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
  deliberately. **Confirmed on real hardware:** the lift now visibly comes
  back down after a `happy`-style reply, matching the simulated-backend
  check (`set_lift_height_mm()` then `lower_lift_fully()` fire in that
  order, with neither an extra nor a missing call for moods that already
  set `lower_lift=True`).

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
   token context window better on long sessions. Related, not started: an
   end-of-run summary ("last time we talked about...") to start a new run
   from instead of resuming raw messages - see
   [Conversation history and memory](#conversation-history-and-memory).
   (Suggested memory facts for the user to approve: done, same section.)
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

     **A third self-caused source, found the same way — raised directly on
     real hardware:** the `shrug` idle-fidget gesture (lift up, then down)
     was registering as a false tap. Unlike a pickup, there's no firmware
     status flag for "our own lift motor is moving" to gate on — but unlike
     an externally-caused spike, *we* always know exactly when it's
     happening, since we're the ones commanding it. `set_lift_height_mm()`/
     `lower_lift_fully()` (`robot/real.py`) now set a suppression deadline
     (their own commanded duration, plus a small `_LIFT_TAP_SUPPRESS_GRACE_S`
     grace period for residual motor vibration/settling after it stops) that
     `_update_tap_detection()` checks alongside `IS_PICKED_UP`, rather than
     raising `TAP_THRESHOLD` globally — which would've traded away
     sensitivity to genuine light taps everywhere, not just during a lift
     move, and might not have fully solved it if the motor jolt and a real
     tap turn out to be comparable in magnitude. **Verified directly**
     against real accelerometer-shaped test packets: a genuine tap-sized
     spike registers normally on its own, the same spike is suppressed
     immediately after a commanded lift move, and detection resumes
     normally once the suppression window passes — not yet confirmed this
     was the sole cause of the real-hardware false taps observed.

     **It wasn't the sole cause. Confirmed on real hardware (2026-09-28):**
     head moves and wheel turns trigger it too. An idle `peek` fidget (head
     up, small turns) kept starting listening windows on its own, and once
     docked-but-full it drove Cozmo off the charger in the process. Its
     motor noise then got recorded and sent to STT as blank clips. Three
     fixes:
     - The suppression window now covers *every* movement we command
       (`_suppress_taps()` in `robot/real.py`): head, lift, drive, turn,
       the hazard back-away, and `go_to_pose()` navigation (extended
       continuously while the firmware drives). It deliberately relies on
       our own command timing, not the firmware's
       `IS_MOVING`/`ARE_WHEELS_MOVING` flags: what those report on this
       robot is unverified, and one stuck on would silently disable taps
       for good.
     - `idle_fidget.py` doesn't fidget while a `--mode vad` listening
       window is open (`CozmoEngine.listening_window_open`). Listening
       isn't idle, and the motor noise lands in the recording. Closing a
       window counts as activity, so the next fidget waits a full
       `IDLE_FIDGET_AFTER_S`. The wake word is unaffected by either fix.
     - Unrelated but found in the same logs: `record_until_silence()`'s
       single time budget covered both waiting for speech to start *and*
       the utterance itself, so speech starting late in a window was cut
       off at the window's end ("0.4s captured, hit the 15s cap"). The
       wait (`VAD_MAX_UTTERANCE_S` first, then `VAD_FOLLOWUP_TIMEOUT_S`)
       and the utterance cap (`VAD_MAX_UTTERANCE_S` from speech onset) are
       now separate.
     **Verified as logic only** (scripted: taps ignored during and just
     after head/drive/turn/lift moves and registered again after; no
     fidget while a window is open; late-starting speech captured whole;
     wait and utterance caps each enforced). Not yet re-run on real
     hardware.

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
     whatever actually asked for movement. (This section originally also
     said leaving the charger should never be a background behavior's
     decision - that was an assumption written into the code, not a rule
     the user set. **Clarified directly, 2026-09-28:** conversation-driven
     movement and gestures may leave the charger freely; the *only*
     restriction is on idle fidgeting, which skips its wheel steps while
     docked and still charging - see Idle fidgeting below. Nothing ever
     spins or moves on the dock without driving straight off first.) `is_on_charger()`/
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
     before this existed. **Verified as boundary-condition
     logic only** (checked `_must_stay_on_charger()` directly against
     every combination of on-charger/charging/voltage, including the
     unknown-voltage and exactly-at-threshold cases) — not yet observed
     against a real battery actually crossing that threshold on hardware.

     **Follow-up, raised directly (2026-09-28):** the block itself stays
     (at/below `BATTERY_LOW_VOLTAGE` while charging, e.g. right after an
     autonomous low-battery return), but Cozmo must *say* he can't come
     rather than promise to and silently not move, the same `say`-before-
     `drive` ordering problem as above. Fixed in two layers.
     `RobotBackend.is_movement_blocked()` is now public, and
     `CozmoEngine.handle_turn()` appends a short status note to the user's
     message whenever it's true, so the model knows *before* it replies.
     As a deterministic backstop, a refused `drive`/`turn` result carries
     `extra["blocked_by_charger"]`; if the turn ends with no successful
     `say` after that refusal (the model ignored it, or ran out of
     `MAX_TOOL_ITERATIONS`), the engine itself says "Sorry, I can't come
     out yet - my battery's too low...". **Verified as logic only**
     (scripted fake model: note present only when blocked; fallback spoken
     after an unexplained refusal; no fallback when the model corrects
     itself or when not blocked). Not yet observed on real hardware.
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
     `IDLE_FIDGET_AFTER_S` (default 30s — see below) of continued quiet,
     resetting the moment a real turn happens. `IDLE_FIDGET_ENABLED=false`
     disables it entirely.

     **Charger-aware (2026-09-28):** `peek` includes `turn` steps, and a
     turn on the dock drives Cozmo straight off first. Since charging
     voltage quickly reads above `BATTERY_LOW_VOLTAGE`, a fidget could
     knock him off the charger minutes after docking, including right
     after an autonomous low-battery return (roadmap item 6). While docked
     **and still charging**, fidgets now skip their wheel steps
     (`run_gesture(..., wheels=False)` in `robot/base.py`): face, head,
     and lift still play, so he doesn't look dead on the dock. Once
     charging finishes (docked, not charging), the full gesture runs and
     may drive him off like any other movement. This is the only
     restriction on leaving the charger; conversation-driven movement is
     unaffected. **Verified as logic only** (scripted, fake robot).

     **Raised directly on real hardware:** the original 300s (5 min)
     default never actually got a chance to fire — Cozmo disconnects/
     powers off (its own hardware-level inactivity behavior, not anything
     in this codebase) well before 5 minutes of quiet elapses, so idle
     fidgeting was effectively dead in practice, never once observed.
     Dropped the default to 30s — roughly `VAD_FOLLOWUP_TIMEOUT_S`'s 15s
     wake-word window plus another 15s — specifically so it gets a real
     chance to run before whatever cuts the session short does.

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
6. **Autonomous return-to-charger on low battery** — built (see the
   "Implementation" sub-item at the end), **not yet run on real hardware**.
   `BatteryMonitor` already read real voltage and `dock()` already handled
   the final blind approach, but neither knew *where* the charger actually
   is; this only matters once Cozmo is somewhere else entirely.
   - **PyCozmo already tracks real pose — confirmed via source, unused
     until now.** Every `RobotState` packet (~33Hz) includes `pose_x`/
     `pose_y`/`pose_angle_rad`, computed by Cozmo's own firmware from
     wheel/tread monitoring (confirmed via the official, deprecated Anki
     SDK's own docstring: *"the robot understands position by monitoring
     its tread movement"* — not pure IMU integration).
     `pycozmo.Client` also already has a working `go_to_pose()` navigation
     primitive (`client.py`) — nothing to build at the protocol level, just
     a `RobotBackend` wrapper. `cozmo_brain/robot/real.py` reads none of
     this today (only `status`/`battery_voltage`/raw `accel_x/y/z`).
     **Real gotcha, confirmed in source:** pose silently resets (new
     `pose_frame_id`/`origin_id`) every time Cozmo is picked up and set
     back down — any "remember where the charger was" scheme has to treat
     that as "last known position is gone," not stale-but-usable.
   - **`go_to_pose()`'s heading correction is unreliable — confirmed on
     real hardware, across two separate runs of `standalone/pose_drift_test.py`
     (a standalone diagnostic script, not part of `cozmo_brain`, same
     spirit as `standalone/orchestrator.py`).** One run: the point-turn segment never rotated
     at all — Cozmo ended up facing exactly the straight-line segment's
     own direction of travel (matched to within 0.5°, and physically
     confirmed: *"it turned and came back to the point it started... it
     didn't turn after it stopped"*), with zero contribution from the
     commanded point-turn. The very next run, with an almost identical
     path: the point-turn corrected the heading correctly on its own.
     Likely a race condition in how PyCozmo's multi-segment path
     completion event fires relative to when each segment actually
     finishes, not a deterministic failure — `pycozmo`'s own official
     `examples/path.py` uses the identical single-event-wait pattern with
     an even longer path (4 lines + 1 point turn), so this isn't obviously
     a `go_to_pose()`-specific bug either. **Position accuracy itself was
     excellent both times** (within a few mm of the true origin), and
     `cli.pose`'s own heading *readback* tracked physical reality correctly
     in both runs — trustworthy even when the point-turn *command* wasn't.
     Fixed pragmatically: read the actual heading back after `go_to_pose()`,
     compute the delta to the desired heading, and issue a separate,
     already-calibrated `turn()` for it regardless — a negligible
     near-zero correction when `go_to_pose()` already got it right, the
     real fix when it didn't. **Verified end-to-end on real hardware**:
     landed within a few mm and a fraction of a degree of true origin
     after a two-leg ~300mm+turn+~300mm test path.
   - ✅ **Room-scale path tested — genuinely promising.** A ~2.1m path with
     two turns (900mm → turn 90° → 700mm → turn −60° → 500mm) accumulated
     only **~64mm of position drift (~3% of distance traveled) and ~5.6°
     of heading drift** from the ideal expected pose — confirmed on real
     hardware, computed directly against the logged numbers, not
     estimated. Notably better than the earlier research's worst-case
     caution ("could plausibly leave Cozmo... well over half a meter
     off"). After `go_to_pose()` + the heading correction, position landed
     within ~5mm of the true start.
   - **A real bug found and fixed while checking these numbers:** the test
     script hardcoded the return target's heading to exactly `0°`, but a
     fresh pose origin's heading isn't guaranteed to actually be `0°`
     (confirmed on real hardware: one run's true starting heading was
     `3.7°`, not `0°` — position reliably reads exactly `(0,0)` at a fresh
     origin, confirmed across every run, but heading doesn't get the same
     guarantee). This made that run's "landed within 0.1°" result look
     better than it actually was — the true drift from the *real* starting
     heading was closer to 3.6°, not 0.1°. Fixed: the script now reads the
     actual starting heading back and uses it as the real target for both
     `go_to_pose()` and the corrective turn, instead of assuming `0°`.
   - ✅ **Second room-scale run, with the fix in place — confirms the first
     wasn't a fluke.** Same ~2.1m/2-turn path, different starting heading
     (`-2.2°` this time): outbound-leg drift was `~95mm` (~4.5% of
     distance) and `~0.2°` heading — consistent order of magnitude with
     the first run's `~3%`/`~5.6°`, not a one-off. After `go_to_pose()` +
     the (now correctly targeted) heading correction: **`~3.7mm` position
     error and `~0.5°` heading error from the true start** — verified by
     independently recomputing against the actual logged starting heading,
     matching the script's own reported `0.5°` exactly. Two consistent,
     independently-verified runs now support the same conclusion: dead-
     reckoning + this heading correction gets Cozmo back within single-
     digit millimeters and well under a degree over a real room-scale path.
   - ✅ **Third run, desk-scale and turn-heavy — same conclusion via a
     different, arguably harder path.** Cozmo mostly stays on a desk in
     actual use, not crossing a room, so this tested the opposite shape of
     stress: `~800mm` total across 4 tighter turns (90°/−120°/45°/−90°)
     instead of a long path with only 2. **Outbound-leg drift was
     proportionally *worse* here — `~63mm` over `~800mm` (`~7.9%`),
     notably higher than the room-scale runs' `~3-4.5%`** — confirming
     that more turns over a shorter distance really is a harder test for
     accumulated heading error, exactly as expected (each turn is its own
     source of calibration error, and a shorter path has less distance to
     "dilute" that error across proportionally). **But the return accuracy
     was the best of all three runs regardless: `~0.5mm` position error
     and `~0.1°` heading error.** This isn't a contradiction — `go_to_pose()`
     doesn't retrace or care about the outbound path's drift at all, it
     just drives directly from wherever `cli.pose` currently believes
     Cozmo is, back to the recorded target. What actually matters for a
     real return-to-charger feature isn't how messy the outbound wandering
     was, only whether `cli.pose` keeps an accurate *current* estimate
     regardless of path — which it does, confirmed across three
     meaningfully different paths now (2-turn room-scale ×2, 4-turn
     desk-scale ×1), all landing within a few mm and well under a degree.
   - **Remaining open question:** whether this holds up over an even
     longer, more convoluted path than any tested so far (a real
     multi-minute desk-interaction session could involve dozens of turns,
     not just 2-4 — cumulative pose-tracking error could plausibly behave
     differently at that scale, not just proportionally), and whether
     accuracy this tight is enough to hand off directly to `dock()`, or
     still needs a vision-loop correction step (reusing the existing
     `look()` + vision-capable-chat pipeline, no new PyCozmo capability
     needed) for final fine alignment first — though given how tight all
     three runs landed, the vision-loop may end up being a rarely-needed
     safety net rather than doing most of the work.
   - **PyCozmo has no built-in charger/marker vision at all — confirmed
     absent, not just unused.** The charger has a printed visual marker
     (same mechanism as the light cubes, confirmed via the official Anki
     SDK's `Charger`/`ObservableObject` docstrings), but that marker
     *recognition* ran on the paired phone app in the original product,
     never reimplemented by PyCozmo (confirmed: PyCozmo's own docs
     explicitly split "on-board firmware" functions it reimplements from
     "off-board engine" functions — including object/marker recognition —
     it doesn't). Camera streaming itself works fine (confirmed: QVGA
     320×240 at ~15fps), just with zero built-in CV. Light-cube BLE
     discovery also exists but is proximity-only (`rssi`, no bearing/
     distance) — confirmed no use for navigation.
   - **Implementation — built, not yet verified on real hardware.**
     - **Navigation primitive:** `RobotBackend.return_to_pose(Pose2D)`
       (`robot/real.py`) wraps `go_to_pose()` plus the validated heading
       correction (read `cli.pose` back, `turn()` the delta
       unconditionally). Two things found in pycozmo's `client.py` while
       building it: `go_to_pose()` blocks on an `Event` with **no timeout**,
       and our own cliff poll doesn't run during a firmware-driven path. So
       it runs on a helper thread while the caller watches for
       `IS_FALLING`/`CLIFF_DETECTED`/`IS_PICKED_UP`, a pose-origin change,
       a stale connection, and `RETURN_TO_POSE_TIMEOUT_S`. On any of those
       it sends `ClearPath` (a real protocol packet, but its effect on an
       in-progress path is unverified) plus `stop_all_motors()`, and
       reports why via `MoveResult.hazard`/the new `MoveResult.reason`.
       After `go_to_pose()` returns, it also waits (bounded) for the
       firmware's `IS_PATHING` flag to clear before correcting the heading.
       `go_to_pose()`'s completion wait fires on *any* non-`PATH_STARTED`
       event, which is a plausible contributor to the intermittent
       point-turn failure. If already on the charger, it drives straight
       off first, the same way `spin_wheels_for()` does.
     - **Charger location:** recorded in `drive()` at the moment a drive
       starts while `is_on_charger()` (every self-powered route off the
       charger goes through there), **in memory only**. Pycozmo re-sends
       `SetOrigin` on every connect, so nothing persisted could be valid
       after a restart. It's forgotten on pickup (the moment `IS_PICKED_UP`
       is seen, not just once the origin changes on set-down), on any
       pose-origin change, and on `disconnect()`/`reconnect()`. The last
       one has to be explicit: a new client restarts at `origin_id` 1, so
       old and new frames can share an id and an origin comparison alone
       would miss it.
     - **Where it drives to:** not the docked pose itself (driving
       `go_to_pose()` nose-first onto the dock and point-turning on the
       platform is exactly the false-cliff/snagging geometry already
       worked around). Instead it goes to a staging point
       `CHARGER_DOCK_DISTANCE_MM` straight out along the docked heading,
       facing away. `return_to_charger()` then hands off to `dock()`'s
       existing blind reverse, and checks `is_on_charger()` afterward,
       reporting `not_on_charger` honestly if the contacts don't read as
       docked. Assumes pycozmo's usual pose convention (heading CCW from
       +x, forward along it) for that offset, which is consistent with
       every drift-test reading but not independently confirmed for this.
     - **Trigger policy** (`cozmo_brain/charger_return.py`, fed every
       valid reading by `BatteryMonitor`). At `BATTERY_LOW_VOLTAGE` Cozmo
       *offers* out loud to head back. The offer is written into the
       conversation as an assistant message, so a "yes" makes sense to the
       model, which calls `dock`. In `--mode vad` a listening window opens
       right after the offer for `SPEAK_UP_LISTEN_S` (default 10s), so a
       plain "yes" works without the wake word; other modes (and
       `SPEAK_UP_LISTEN_S=0`) need the usual activation. At `BATTERY_CRITICAL_VOLTAGE` he
       announces it and goes on his own, the backstop for an unanswered
       offer. Either level needs 2 consecutive readings off the charger
       (so motor-load voltage sag mid-drive can't trigger it), fires at
       most once per episode, and an episode ends only once he's seen on
       the charger again. With no valid charger location, or a return
       that fails partway, he asks once per episode to be put on the
       charger. Speech and driving take a new `CozmoEngine.turn_lock`
       (held for all of `handle_turn()`), so this never talks over a reply
       in progress or edits the conversation mid-turn.
       `AUTO_RETURN_TO_CHARGER_ENABLED=false` turns all of this off.
     - **`dock` tool** now navigates first whenever a valid charger
       location is known, and otherwise falls back to the plain blind
       reverse as before.
     - **Verified as logic only**, with scripted tests against a fake
       pycozmo client plus fake robot/engine. Covered: pose recording on
       exit; the staging offset following the setting; forgetting on
       pickup/origin change/disconnect; the heading correction fixing a
       skipped point-turn; a hung `go_to_pose()` timing out with motors
       stopped; pickup mid-navigation aborting; the full navigate→dock
       sequence aiming at the right staging point; `not_on_charger` on a
       misaligned dock; the LOW/CRITICAL streak/once-per-episode rules;
       waiting for an in-progress turn; the stale-connection skip; the
       `dock` tool's three branches. **Nothing here has driven the real
       robot yet.** Still open: whether the staging offset direction
       matches reality, whether `ClearPath` actually cancels a path,
       whether the 150mm reverse from staging actually lands on the
       contacts, and the real discharge curve against the two thresholds.
   - **First real-hardware runs (2026-09-28): navigation works, docking
     fell short.** The staging offset direction is confirmed right: every
     return headed to the correct spot in front of the charger. Landings
     against the staging point: `~2mm`/`~5mm` off with `0.2°` heading
     error, then `~6mm`/`~21mm` (by Cozmo's own pose readback) after a
     very short leg. The second may mean `go_to_pose()` is less precise
     over tiny distances; that's one data point, not yet acted on. The
     final `dock()` reverse missed the contacts every time. In the third
     run, a `flinch` gesture that happened to reverse ~40mm more seated
     it immediately, so the reverse itself was falling short: open-loop
     timing, with the acceleration ramp from standstill (and likely the
     charger's own ramp) covering less than distance/speed. Raising
     `CHARGER_DOCK_DISTANCE_MM` couldn't fix that, since the staging
     offset uses the same setting. Fixed: `dock()` now reverses up to
     `CHARGER_DOCK_DISTANCE_MM + CHARGER_DOCK_OVERSHOOT_MM` (default 50,
     a first guess just above the 40mm that worked) and stops the moment
     `IS_ON_CHARGER` reads true. It also logs its final pose, and
     `return_to_charger()` logs the offset from the recorded docked pose
     (mm short, mm sideways, heading) so the next miss says *why*.
   - **Also found in those runs:** a gesture drove Cozmo off the charger
     mid-conversation (allowed), but the model was never told, and later
     insisted "I'm already here!" when asked to go back. `CozmoEngine`
     now adds a one-line charger status note (off / on and charging / on,
     not charging) to the user's message on the first turn and whenever
     that state changes. The conversational side of the return was
     observed working on real hardware: the model called `dock`, which
     navigated back on its own. The CRITICAL autonomous return (no
     model involved) hasn't clearly been observed firing yet.
   - **Verified as logic only (the fixes above):** stop-on-contact ends
     the reverse early; with no contact it runs the full distance plus
     overshoot and logs "NOT engaged"; the offset math is right in
     rotated frames; the status note goes out on the first turn and on
     changes only. All earlier scripted checks still pass. **Not yet
     re-run on real hardware.**
   - **Next real run (2026-09-28): docked correctly, but reported a miss.**
     The autonomous return seated Cozmo on the charger (visibly), but
     `IS_ON_CHARGER` wasn't set yet 0.3s after the reverse stopped. So it
     reported "not_on_charger" and asked for help, and the flag only
     showed as set at the next 30s battery check. The flag never fired
     during the reverse either, so stop-on-contact didn't trigger and the
     full distance + overshoot ran (logged `-16.1mm short`, i.e. 16mm
     *past* the recorded docked pose: pushed against the charger's back,
     with some tread slip likely inflating that reading). The flag
     clearly lags behind the physical contact. Why is unconfirmed
     (possibly the firmware waits for the motors to stop, or needs the
     contact to hold). Fixed: `dock()` now waits up to
     `CHARGER_CONTACT_WAIT_S` (default 10s, returns as soon as the flag
     appears) before anything treats "not yet" as "missed", and logs the
     real delay ("Charger contacts engaged X.XXs after the dock reverse
     stopped") so the bound can be tuned. **Verified as logic only**
     (scripted: a flag arriving 1.5s late is now reported as docked, with
     the delay logged). Not yet re-run on real hardware.
   - **The post-reply LLM call: `FINAL_LLM_CALL=sync|async|skip`
     (2026-09-28).** Behavior with worked examples: [How a turn ends](#how-a-turn-ends-final_llm_call). Cozmo's answer is itself a tool call (`say`), so the
     generic loop always made one more LLM call after it just to hear
     "done", with the mic closed meanwhile. Skipping that call (`skip`) was
     tried first, then **live-tested against every provider**. All accept
     a history ending on a tool result (Ollama gemma, OpenAI gpt-4o-mini,
     Groq qwen and gpt-oss). But Groq's `openai/gpt-oss-120b` does one tool
     per step, and on "turn left" it said "sure!" alone and would only
     have turned in its *next* step, so `skip` lost the action entirely.
     `async` keeps the call but makes it in the background: in `--mode vad`
     the mic reopens right after the `say` (`handle_turn(...,
     allow_async_followup=True)`). If the model continues, the follow-up
     sets `CozmoEngine.followup_interrupt`, which stops the in-progress
     recording within one 30ms frame before anything moves or speaks (a
     capture already under way is discarded). The action runs, then
     listening resumes in the same window. The follow-up keeps holding
     `turn_lock`, so the next turn, a charger return, or a fidget waits
     for it, and history stays in order. Other modes treat `async` as
     `sync`. **Default is now `async`** (switched 2026-09-29 at the user's request; it was `sync` until then). It's still to be confirmed on real
     hardware. **Verified:** scripted (early return, lock hand-off, pause
     and resume in a simulated listening window, turn ordering, hazards
     still consulted synchronously, follow-up failure releases the lock)
     plus live with real models: gpt-oss's "say, then turn" now turns,
     with listening reopened ~1.5s in instead of after the whole turn.
     Cost: `async` still makes the extra call every turn. The live test
     hit Groq's free-tier rate limit (429).
   - **Always turning counterclockwise, raised directly (2026-09-28).**
     During a return Cozmo always rotated left, even when right was far
     shorter. Our own heading correction was never the cause: it already
     took the shortest signed turn. The suspect is the firmware.
     pycozmo's `AppendPathSegPointTurn` always sends a positive speed, plus
     a boolean pycozmo only calls `unknown`, possibly Anki's
     shortest-direction flag. That's **unconfirmed**: nothing in pycozmo's
     docs or Anki's published SDKs names it, and the firmware's line
     segment may also rotate on its own before driving. So
     `return_to_pose()` now does **every** rotation itself, the shortest
     way:
     1. face the target with our own `turn()`;
     2. ask `go_to_pose()` for a straight line ending in that same
        direction, so its own point turn is ~0;
     3. make the final heading with the existing correction turn.
     Targets within 10mm skip the drive and only correct heading. A new
     log line reports what the firmware *actually* rotated during
     `go_to_pose()`, e.g. `go_to_pose() firmware rotation: net +3deg
     (...)`. A large number there on real hardware would mean the firmware
     still turns on its own. Separately, `spin` now picks left or right at
     random each time (`Step.random_direction`); every other gesture is
     unchanged. **Verified as logic only** (scripted: short-way turns
     chosen, `go_to_pose()` asked for the travel direction, final heading
     still exact, tracker unwraps across ±180°, `spin` goes both ways), not
     yet on real hardware.
   - **Next run (2026-09-28, Pi on `1a9dc6b`): the autonomous CRITICAL
     return fully worked, then an idle fidget undid it.** At 3.26V he
     navigated, docked, and reported success correctly. The contact flag
     came 0.85s after the reverse stopped, well inside the 10s wait; the
     staging landing was again ~20mm off, and docking still seated. In
     the *same millisecond* the return released the wheels, a drive
     started from the charger. An idle fidget (then unaware of both
     listening windows and returns) had fired mid-return, and its `peek`
     turn step queued behind the return's wheel lock. It ran on docking:
     the fidget's wheels decision predated docking, and the low-battery
     hold required `IS_CHARGING`, which likely lags like `IS_ON_CHARGER`.
     He drove off at 3.2V, the model (never told, since the charger note
     was only sent on a change between turns) insisted "I'm already on
     my charger", and the battery died (3.00V) during the next return.
     Three fixes: fidgets only run if they can take
     `CozmoEngine.turn_lock` without waiting, so they never overlap a
     turn or a return; `_must_stay_on_charger()` no longer needs
     `IS_CHARGING` when the voltage is known (docked + low = stay;
     trade-off: on an unpowered charger with a low battery an explicit
     "come out" is refused too); and the charger status line goes out on
     every turn. **Verified as logic only** (scripted reproduction of
     this exact sequence now stays docked), not yet re-run on real
     hardware.
7. **Reminders - via your assistant: built; spoken by Cozmo: planned.**
   With `ASSISTANT_ENABLED=true`, "remind me at 5 to call mom" goes to your
   own assistant agent through `ask_assistant`, which sets the reminder and
   delivers it (OpenClaw: a message on your phone) - works while Cozmo is
   off. See [Asking your own assistant](#asking-your-own-assistant-ask_assistant).
   Not run end to end on real hardware yet. What follows is the still-open
   part: Cozmo saying a reminder out loud himself.

   Today Cozmo knows the date and
   time (it's in his prompt every turn), but he only thinks when spoken
   to: "remember my dentist appointment is Monday at 5" is saved as a
   memory fact, and he may mention it if you talk to him that day, but he
   never speaks up on his own at 4:55. Real reminders need:
   - a reminder list (e.g. `data/reminders.json`: time, message, done);
   - tools to set ("remind me at 5 to call mom"), list, and cancel them;
   - a background check (every ~30s) that says a due reminder out loud,
     through the same `turn_lock` + `engine.speak()` path the low-battery
     offer uses (`charger_return.py`), so it never talks over a reply.

   Limits to design around: Cozmo has to be on, connected and running the
   app - a reminder due while he's off or disconnected can only be said
   late, when he's back; you have to be in the room to hear it (no phone
   notification without a separate service); repeating reminders ("every
   day at 9") are more work - start with one-off ones. To brainstorm
   before building: what happens to a missed reminder, whether he repeats
   one until it's acknowledged, and whether quiet hours are needed.
8. **"Can I come out, and for how long?" from real battery data - planned,
   data collection only so far.** Today leaving the charger (asked, idle
   peek, or the charger break) is allowed by one fixed rule: not docked at
   or below `BATTERY_LOW_VOLTAGE`. A guessed "high enough" voltage was
   deliberately not added, since the dock reads high and this robot's curve
   is unknown. Instead `data/battery.jsonl` (`battery_log.py`, see
   [Coming off the charger](#coming-off-the-charger-battery-line-and-charger-break))
   records every charge session and every stretch off the dock. To build,
   once a few stretches have actually reached low:
   - check the numbers with `python3 standalone/battery_report.py`, then
     decide the rule (e.g. the minutes-to-low fit vs voltage on leaving);
   - use it when asked to come out ("sure, I've got about 10 minutes") and
     for the charger break, falling back to today's rule with too little data;
   - add a separate retention setting for `battery.jsonl` (it is kept
     forever until then - deliberately not tied to `HISTORY_RETENTION_DAYS`).

---

## Troubleshooting cheat sheet

```bash
# Stop the app cleanly from another shell (same cleanup as Ctrl+C; avoid kill -9)
pkill -f 'cozmo_brain --mode vad'

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

# Test the assistant endpoint (ask_assistant) - same request shape Cozmo sends;
# repeat with the same "user" to check the session remembers
curl -X POST "$ASSISTANT_URL/v1/chat/completions"   -H "Content-Type: application/json" -H "Authorization: Bearer <ASSISTANT_TOKEN>"   -d '{"model": "openclaw/main", "user": "cozmo", "messages": [{"role": "user", "content": "Hi"}]}'

# Test PyCozmo connection only
python3 -c "import pycozmo; cli = pycozmo.Client(); cli.start(); cli.connect(); cli.wait_for_robot(); print('Connected!'); cli.disconnect(); cli.stop()"

# Exercise the full tool-calling loop with no robot, mic, or Ollama reachability needed for the robot side
python3 -m cozmo_brain --simulate --mode text
```
