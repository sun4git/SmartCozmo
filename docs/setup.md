# Setup from scratch

[← Back to the README](../README.md)


## 1. Python environment (on the deployment machine)

```bash
mkdir -p ~/SmartCozmo && cd ~/SmartCozmo
git clone https://github.com/sun4git/SmartCozmo.git .
python3 -m venv cozmo-env
source cozmo-env/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pycozmo_resources.py download   # downloads Cozmo's animation/audio resource files — needed for play_animation()
```

What that downloads, from where, and how to back it up:
[Real animation clips and Cozmo's own sounds](clip-sounds.md#where-the-files-come-from).

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
   openwakeword` — see [Wake word detection](wake-word.md#wake-word-detection-zero-network)
   below for the exact install command and why.

`--mode voice`, `--mode text`, and `--simulate` don't need either of these —
skip them for now if you just want to
get something running first.

## 2. Configure `.env`

```bash
cp .env.example .env
```

Edit `.env` and fill in at least `GROQ_API_KEY`. Adjust `OLLAMA_BASE_URL` /
`OLLAMA_MODEL` to match your Ollama setup. The model must support
tool-calling. It can be a local model you've pulled, or one of Ollama's cloud
models (like the `-cloud` model `.env.example` starts with), which need you
to sign in first with `ollama signin`. See `.env.example` for what every
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

## 3. Join Cozmo's Wi-Fi AP (do this every time Cozmo has been power-cycled)

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
  [Passwordless nmcli access](#passwordless-nmcli-access-optional-for-wi-fi-auto-connect)
  below to set that up.
- **It's best-effort.** If Cozmo's SSID has regenerated since the saved
  profile was created, this falls back to logging exactly what to run
  manually, instead of guessing.

### Passwordless nmcli access (optional, for Wi-Fi auto-connect)

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
whoami   # confirm your username — replace "pi" below if different
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
    if (nmActions.indexOf(action.id) !== -1 && subject.user == "pi") {
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
pi ALL=(root) NOPASSWD: /usr/bin/nmcli connection up *, /usr/bin/nmcli connection modify *, /usr/bin/nmcli dev wifi connect *, /usr/bin/nmcli dev wifi rescan
```
(`visudo -f` validates syntax and sets correct file permissions — don't edit
that file with a plain editor.) Note: `wifi.py` calls plain `nmcli`, not
`sudo nmcli` — this option alone doesn't help unless the code is changed to
call `sudo -n nmcli ...` (`-n` fails fast instead of hanging if passwordless
sudo isn't actually configured). Option A needs no such change.

## 4. Verify PyCozmo connectivity

```bash
python3 -c "import pycozmo; cli = pycozmo.Client(); cli.start(); cli.connect(); cli.wait_for_robot(); print('Connected!'); cli.disconnect(); cli.stop()"
```

Clone [pycozmo](https://github.com/zayfod/pycozmo) separately if you want its
`examples/` folder for reference (`minimal.py`, `backpack_lights.py`,
`camera.py`, etc.) — it's not part of this repo.

**Cozmo's audio requirement:** WAV files sent to `cli.play_audio()` must be
**22050Hz or 48000Hz, 16-bit, mono**. `set_volume()` takes 0–65535.

## 5. Pair the Bluetooth mic/speaker

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

## 6. Groq setup

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

## 7. Run it

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

(See [Modes](architecture.md#modes) below if you'd rather activate `cozmo-env` and call
`python3 -m cozmo_brain` directly instead of using `run.sh`.)
