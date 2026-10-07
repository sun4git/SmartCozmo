# Real animation clips and Cozmo's own sounds

[← Back to the README](../README.md)

Cozmo can play hand-picked clips from the original Cozmo app (`happy`,
`chicken`, `fist_bump`, `dunno`...) with the speech, and the clips can make
**Cozmo's own sounds**: his giggles, snores, the cluck, the screen and servo
blips. This page covers getting the sounds working from scratch, keeping a
backup, and redoing it when the clip list changes. How it works inside is in
[Architecture](architecture.md) ("Real animation clips", "Cozmo's own sounds").

## What you get, and what you don't

- **Clips without sounds work out of the box.** Once
  [`pycozmo_resources.py download`](setup.md) has been run on the Pi, the
  curated clips play, minus their wheel and backpack-light commands (see
  Architecture for why).
- **The sounds need a one-off extraction.** PyCozmo plays clips silent: a
  clip's sound keyframes point at events in the app's Wwise sound engine,
  which PyCozmo skips. The sound files themselves are on the Pi already, as
  part of the same download. Turning them into something the robot can play
  takes the steps below, on a PC.
- **The sounds are not in the repo, and must not be committed.** They are
  Anki/DDL's material from the Cozmo app. The repo has the tools that recover
  them. Everything they produce lives under `data/`, which is gitignored.
  Treat them as copyrighted unless you have seen terms that say otherwise.
- **Not covered:** the song (`sing`), which lives in the app's music bank, and
  one placeholder sound (`External_Cozmo_Processing`) that has no audio in the
  banks.

## Where the files come from

Neither this repo nor PyCozmo contains any of Cozmo's animations or sounds.
They come from the **Cozmo Android app**, which PyCozmo's
`pycozmo_resources.py download` fetches and unpacks. Read from PyCozmo
0.8.0's own script (`pycozmo_resources.py`, installed next to the `pycozmo`
package):

- **Source:** one file,
  `https://media.githubusercontent.com/media/cristobalraya/cozmo-archive/master/applications/com.anki.cozmo_3.4.0-1204_plus_OBB.zip`.
  That is the Cozmo app, version **3.4.0 (build 1204)**, with its OBB expansion
  file, the Android download that holds the app's assets. It is hosted in
  `cristobalraya/cozmo-archive`, a community archive on GitHub (served from
  GitHub's large-file host). It is not run by Anki, Digital Dream Labs, or
  PyCozmo's author, and PyCozmo's README says it is not affiliated with Anki
  or DDL.
- **Where it goes:** `~/.pycozmo/assets/` on the Pi (Linux/macOS), or
  `~/pycozmo/assets/` on Windows. Set the `PYCOZMO_DIR` environment variable to
  use another folder. `assets/resources.txt` is how PyCozmo tells the download
  has been done.
- **What it does, in order:**
  1. stops if `assets/resources.txt` already exists ("already available");
  2. downloads the zip to `assets/obb.zip`;
  3. unzips it to `assets/obb/`, then deletes `obb.zip`;
  4. unzips the app's OBB, `assets/obb/Android/obb/com.anki.cozmo/main.1204.com.anki.cozmo.obb` (itself a zip), into the PyCozmo folder. That is what creates `assets/cozmo_resources/`, with `cozmo_resources/assets/animations/` (the clips) and `cozmo_resources/sound/`. Then it deletes `assets/obb/`;
  5. unzips `assets/cozmo_resources/sound/AudioAssets.zip` into that `sound/` folder. This is where the `.wem` and `.bnk` files come from. The zip itself is left behind.
- **Other commands:** `pycozmo_resources.py status` says whether the download
  is there; `pycozmo_resources.py remove` deletes the whole `assets/` folder.
- **No integrity check.** The script turns off HTTPS certificate verification
  for its download (`ssl._create_unverified_context`), and it doesn't check
  a checksum. Nothing confirms the file is the one you expect. That's one more
  reason to keep a backup with your own checksum (below) once you have a
  download that works. Since it deletes the downloaded zip, the only copy you
  keep is the unpacked `assets/` folder, unless you download the URL above
  yourself and save it.

## Getting the sounds working

You need: the Pi with `pycozmo_resources.py download` done, and a PC (the
steps below use Windows PowerShell) with this repo and its Python deps. The
extraction runs on the PC because the decoder, `vgmstream-cli`, has a ready
Windows build. It hasn't been tried on the Pi.

Below, `<PI_HOST>` is your Pi's hostname or IP, the Pi's repo is
`/home/suneel/sunwork/projects/cozmo/SmartCozmo`, and the PC's is
`C:\SUNEEL\Workspaces\Cozmo\SmartCozmo`. Adjust both to your own.

### 1. On the Pi: list which sounds the clips use

```bash
python3 standalone/dump_clip_sounds.py
```

It reads the clips (the keeps in `data/animations_picks.tsv`, plus the
wake-up and sleep clips) and writes `data/clip_sounds.tsv`: every sound
event each clip fires, and when. It also prints a check of the sound folder.
Expect about **2,214 `.wem`, 6 `.bnk`, `SoundbanksInfo.xml`**, and a leftover
`AudioAssets.zip` (already extracted; ignore it). To add clips beyond the
picks, list them: `python3 standalone/dump_clip_sounds.py anim_x anim_y`.

### 2. Copy the sound folder and the list to the PC

From the PC's repo folder:

```powershell
mkdir -Force data\sound
cd data\sound
scp -C "suneel@<PI_HOST>:/home/suneel/.pycozmo/assets/cozmo_resources/sound/*.wem" `
       "suneel@<PI_HOST>:/home/suneel/.pycozmo/assets/cozmo_resources/sound/*.bnk" `
       "suneel@<PI_HOST>:/home/suneel/.pycozmo/assets/cozmo_resources/sound/*.xml" `
       "suneel@<PI_HOST>:/home/suneel/.pycozmo/assets/cozmo_resources/sound/*.txt" .
scp -r -C "suneel@<PI_HOST>:/home/suneel/.pycozmo/assets/cozmo_resources/sound/English*" .
cd ..\..
scp "suneel@<PI_HOST>:/home/suneel/sunwork/projects/cozmo/SmartCozmo/data/clip_sounds.tsv" data\
```

**Don't skip the second `scp`.** Most of the voice sounds and the main bank
(`English(US)\Cozmo.bnk`) are in the `English(US)` subfolder, and the wildcard
copy above it only takes the top level. Check:

```powershell
(Get-ChildItem data\sound -Recurse -Filter *.wem).Count   # 2214
Test-Path "data\sound\English(US)\Cozmo.bnk"             # True
```

### 3. Get the decoder (`vgmstream-cli`)

Download `vgmstream-win64.zip` from the official releases
(<https://github.com/vgmstream/vgmstream/releases>). Check its SHA-256 against
the `sha256:` digest GitHub shows next to the file, then unzip it to
`data\tools\vgmstream\`. This setup used release **r2117**, digest
`6c4a8a3813864fefed081bbd337dbc0ad93bf88e0b92f5db98d7ab258b22dc6c`.

```powershell
mkdir -Force data\tools\vgmstream
Invoke-WebRequest https://github.com/vgmstream/vgmstream/releases/download/r2117/vgmstream-win64.zip -OutFile data\tools\vgmstream-win64.zip
(Get-FileHash data\tools\vgmstream-win64.zip -Algorithm SHA256).Hash   # must match the digest
Expand-Archive data\tools\vgmstream-win64.zip -DestinationPath data\tools\vgmstream -Force
```

### 4. Extract and convert

```powershell
python standalone\extract_clip_sounds.py
```

It follows each event through the sound banks to its sound files, decodes
them, and writes `data\clip_sounds\`:

| What | |
|---|---|
| `wav\<id>.wav` | every sound the clips can play: mono, 16-bit, 22,050 Hz (~540 files, ~21 MB) |
| `manifest.json` | which clip plays which sounds, when, and each variant's chance |
| `listen\` | one sample per clip sound, named `<clip>__<time>ms__<event>.wav`, to listen to on the PC |

It takes about 20 seconds. The expected summary is `93 play events`,
`Decoded 540 of 541 sounds; 1 failed`. The one failure is the placeholder
mentioned above. Play a few files in `listen\` before going further.

### 5. Copy the converted sounds to the Pi

```powershell
ssh suneel@<PI_HOST> "mkdir -p /home/suneel/sunwork/projects/cozmo/SmartCozmo/data/clip_sounds"
scp -r data\clip_sounds\wav data\clip_sounds\manifest.json suneel@<PI_HOST>:/home/suneel/sunwork/projects/cozmo/SmartCozmo/data/clip_sounds/
```

`listen\` isn't needed on the Pi.

### 6. Settings and check

In the Pi's `.env` (or run `python3 standalone/sync_env.py --apply` after
pulling):

| Setting | Default | |
|---|---|---|
| `CLIPS_ENABLED` | `true` | All real clips on or off |
| `CLIP_SPEECH_OVERLAP` | `true` | A clip plays while he speaks (`false`: right after) |
| `CLIP_SOUNDS` | `full` | `off`, `effects` (only screen/servo blips under speech, never his voice) or `full`. With no speech, both play everything. |
| `CLIP_SOUND_VOLUME` | `0.5` | Sound level when nothing is being said (wake-up, sleep, a standalone clip) |
| `CLIP_SOUND_SPEECH_VOLUME` | `0.15` | Sound level under speech, so the speech stays the main thing |
| `CLIP_SOUNDS_DIR` | `data/clip_sounds` | Where step 5 put them |

Clip sounds always play on Cozmo's speaker, whatever `AUDIO_OUTPUT` is. With
`AUDIO_OUTPUT=system` the speech goes to the other speaker, so clips play at
`CLIP_SOUND_VOLUME` even while he talks: see
[Where the clip sounds go](audio-output.md#where-the-clip-sounds-go).

Start the app. Near the start, the log should say:

```
Clip sounds: 46 clips with sounds, mode 'full', volume 0.50 alone / 0.15 under speech.
```

Then the wake-up should make his waking noises, and a reply using
`chicken`, `hiccup` or `happy` should carry their sounds under the speech.

### If it doesn't work

| The log says / you see | Likely cause | Fix |
|---|---|---|
| `Clip sounds: no .../manifest.json` | Step 5 copied to the wrong folder | The file must be at `CLIP_SOUNDS_DIR/manifest.json` |
| No `Clip sounds:` line at all | `CLIP_SOUNDS=off`, `CLIPS_ENABLED=false`, or the animations weren't downloaded | Check `.env`, and that the start says `Loaded 993 real animation clips.` |
| Step 4: `streamed .wem not found` for many sounds | The `English(US)` folder wasn't copied | Step 2's second `scp` |
| Step 4: everything unresolved, `Cozmo.bnk` missing | Same | Same |
| Sounds drown the speech | Volume | Lower `CLIP_SOUND_SPEECH_VOLUME`, or `CLIP_SOUNDS=effects` |
| `Could not mix the sounds for clip ...` | A damaged WAV | Re-run step 4 and step 5 |

## When the clip list changes

The sounds are per clip. After adding a clip to the curated list (or keeping
new ones in `audition_animations.py`), repeat steps 1, 2 (just the `.tsv`),
4 and 5. Step 4 only decodes sounds it doesn't already have.

## Keeping a backup

Everything here can be recreated, except your **picks** and anything the
download can no longer fetch. The PyCozmo downloader pulls the Cozmo app
package from a third-party GitHub archive that could disappear. Keep the
backup **private**: an external drive or a personal cloud folder, never the
repo or anything shared publicly.

| What | Where | Size (here) | Why keep it |
|---|---|---|---|
| The original download | Pi: `~/.pycozmo/assets/` | the whole folder; check with `du -sh` | Everything comes from this: animations and sounds. The hardest to get again, since it depends on a third-party archive (see "Where the files come from"). |
| The sound folder | PC: `data\sound\` | ~140 MB | A copy of the Pi's sound folder, minus the zip |
| The converted sounds | PC: `data\clip_sounds\` | ~30 MB with `listen\` | What the robot plays; saves redoing steps 1-4 |
| Your picks and dumps | PC/Pi: `data\animations_*.tsv`, `data\clip_sounds.tsv` | small | `animations_picks.tsv` is your own audition work |
| The decoder | PC: `data\tools\vgmstream-win64.zip` | ~4.5 MB | The exact build used, in case a later release behaves differently |

**On the Pi:** one archive of the original download:

```bash
tar czf ~/cozmo-assets-backup-$(date +%Y%m%d).tar.gz -C ~/.pycozmo assets
sha256sum ~/cozmo-assets-backup-*.tar.gz > ~/cozmo-assets-backup.sha256
```

Copy both files off the Pi (e.g. `scp suneel@<PI_HOST>:~/cozmo-assets-backup-* .`).
To restore: `tar xzf cozmo-assets-backup-....tar.gz -C ~/.pycozmo`.

**On the PC:** one archive of the rest:

```powershell
$d = Get-Date -Format yyyyMMdd
Compress-Archive -Path data\sound, data\clip_sounds, data\tools\vgmstream-win64.zip, data\animations_clips.tsv, data\animations_groups.tsv, data\animations_picks.tsv, data\clip_sounds.tsv `
  -DestinationPath "$HOME\cozmo-sounds-backup-$d.zip"
Get-FileHash "$HOME\cozmo-sounds-backup-$d.zip" -Algorithm SHA256 | Format-List > "$HOME\cozmo-sounds-backup-$d.sha256.txt"
```

Keep the `.sha256` files next to the archives, so you can tell later that a
copy is intact. Note the versions this was built from: PyCozmo 0.8.0, Cozmo
app 3.4.0 (build 1204), vgmstream r2117.
