#!/usr/bin/env python3
"""
Recover the ORIGINAL Cozmo sounds for animation clips and write them as small
WAV files, so a clip can make its own noises (see docs/architecture.md: PyCozmo
plays clips silent). Runs offline on a PC that has the sound assets copied from
the Pi (any machine with `pycozmo_resources.py download` done works too); the
robot only ever needs the OUTPUT folder.

Needs:
  data/sound/                 the Pi's ~/.pycozmo/assets/cozmo_resources/sound/ folder
                              (incl. the English(US)/ subfolder with Cozmo.bnk)
  data/clip_sounds.tsv        written by standalone/dump_clip_sounds.py on the Pi:
                              which sound events each clip fires, and when
  vgmstream-cli               decodes Wwise .wem to WAV. Not part of the repo;
                              https://github.com/vgmstream/vgmstream/releases
                              (default: data/tools/vgmstream/vgmstream-cli.exe)

Writes data/clip_sounds/:
  wav/<file id>.wav           every candidate sound: mono, 16-bit, 22050 Hz (the
                              rate PyCozmo plays), peak-limited
  manifest.json               event id -> name + its candidate sounds with the
                              chance one play picks each; clip -> [(time_ms, event)]
  listen/                     one sample per clip sound, named
                              <clip>__<time>ms__<event>.wav, to audition by ear

The sounds are Anki/DDL's copyrighted assets: keep the output LOCAL (data/ is
gitignored) - never commit it.

Usage (from the repo root):
  python standalone/extract_clip_sounds.py
  python standalone/extract_clip_sounds.py --clips anim_codelab_chicken_01 anim_hiccup_02
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import subprocess
import sys
import tempfile
import wave
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cozmo_brain.wwise_banks import load_bank, pick  # noqa: E402

RATE = 22050  # PyCozmo plays 16-bit mono at 22050 or 48000 Hz
PEAK = 0.9


def to_mono_22k(wav_path: Path) -> np.ndarray:
    """Decode a WAV from vgmstream to a mono float array at RATE, low-passed
    before resampling so the downsample doesn't alias."""
    with wave.open(str(wav_path)) as w:
        channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(frames)
    if width != 2:
        raise ValueError(f"unexpected sample width {width}")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    if rate != RATE and len(x):
        if rate > RATE:  # windowed-sinc low-pass at 0.45 * the target rate
            fc = 0.45 * RATE / rate
            n = np.arange(-32, 33)
            h = np.sinc(2 * fc * n) * np.hamming(len(n))
            x = np.convolve(x, h / h.sum(), mode="same")
        n_out = int(round(len(x) * RATE / rate))
        x = np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x)
    peak = float(np.abs(x).max()) if len(x) else 0.0
    if peak > PEAK:
        x = x * (PEAK / peak)
    return x


def write_wav(path: Path, x: np.ndarray) -> None:
    pcm = (np.clip(x, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sound-dir", default=str(ROOT / "data" / "sound"))
    ap.add_argument("--tsv", default=str(ROOT / "data" / "clip_sounds.tsv"))
    ap.add_argument("--tool", default=str(ROOT / "data" / "tools" / "vgmstream" / "vgmstream-cli.exe"))
    ap.add_argument("--out", default=str(ROOT / "data" / "clip_sounds"))
    ap.add_argument("--clips", nargs="*", help="only these clips (default: every clip in the tsv)")
    args = ap.parse_args()

    sound_dir, out = Path(args.sound_dir), Path(args.out)
    bank = load_bank(sound_dir / "English(US)" / "Cozmo.bnk")
    print(f"Cozmo.bnk: bank version {bank.version}, {len(bank.sounds)} sounds, {len(bank.events)} events.")

    # clip -> its audio keyframes, from the dump
    clip_events: dict[str, list[tuple[int, int, str, float]]] = defaultdict(list)
    names: dict[int, str] = {}
    with open(args.tsv, encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if args.clips and r["clip"] not in args.clips:
                continue
            eid = int(r["event_id"])
            names[eid] = r["event_name"]
            if r["event_name"].startswith("Play__"):  # Stop__ events end loops; we play each sound once
                clip_events[r["clip"]].append((int(r["time_ms"]), eid, r["event_name"], float(r["volume"] or 1.0)))

    # event -> candidate sounds
    events: dict[int, list[tuple]] = {}
    for eid in sorted({e for evs in clip_events.values() for _, e, _, _ in evs}):
        events[eid] = bank.event_sounds(eid)
    unresolved = [names[e] for e, s in events.items() if not s]
    wanted = {s.file_id: s for sounds in events.values() for s, _ in sounds}
    print(f"{len(clip_events)} clips, {len(events)} play events, {len(wanted)} distinct sound files to decode.")
    if unresolved:
        print(f"No sound found for: {', '.join(unresolved)}")

    (out / "wav").mkdir(parents=True, exist_ok=True)
    (out / "listen").mkdir(parents=True, exist_ok=True)
    tool = Path(args.tool)
    if not tool.exists():
        print(f"vgmstream-cli not found at {tool} - see the docstring.")
        return 1

    # decode each distinct file once
    failed: dict[int, str] = {}
    decoded = 0
    with tempfile.TemporaryDirectory() as tmp:
        for k, (file_id, sound) in enumerate(sorted(wanted.items()), 1):
            target = out / "wav" / f"{file_id}.wav"
            if target.exists():
                decoded += 1
                continue
            if sound.stream == 0:
                blob = bank.media_bytes(file_id)
                if blob is None:
                    failed[file_id] = "embedded but not in the bank"
                    continue
                src = Path(tmp) / f"{file_id}.wem"
                src.write_bytes(blob)
            else:
                src = next((p for p in (sound_dir / f"{file_id}.wem", sound_dir / "English(US)" / f"{file_id}.wem") if p.exists()), None)
                if src is None:
                    failed[file_id] = "streamed .wem not found"
                    continue
            raw_wav = Path(tmp) / f"{file_id}.wav"
            res = subprocess.run([str(tool), "-o", str(raw_wav), str(src)], capture_output=True, text=True)
            if res.returncode != 0 or not raw_wav.exists():
                failed[file_id] = (res.stderr or res.stdout).strip().splitlines()[-1][:80] if (res.stderr or res.stdout) else "decode failed"
                continue
            try:
                write_wav(target, to_mono_22k(raw_wav))
                decoded += 1
            except Exception as e:  # noqa: BLE001 - report and carry on
                failed[file_id] = f"{type(e).__name__}: {e}"
            raw_wav.unlink(missing_ok=True)
            if k % 100 == 0:
                print(f"  ...{k}/{len(wanted)}")
    print(f"Decoded {decoded} of {len(wanted)} sounds; {len(failed)} failed.")
    for fid, why in list(failed.items())[:8]:
        print(f"   {fid}: {why}")

    # manifest
    manifest = {"rate": RATE, "events": {}, "clips": {}}
    for eid, sounds in events.items():
        usable = [(s, p) for s, p in sounds if s.file_id not in failed]
        total = sum(p for _, p in usable) or 1.0
        manifest["events"][str(eid)] = {
            "name": names[eid],
            "sounds": [{"file": f"wav/{s.file_id}.wav", "chance": round(p / total, 4)} for s, p in usable],
        }
    for clip, evs in clip_events.items():
        manifest["clips"][clip] = [{"t_ms": t, "event": e, "volume": v} for t, e, _, v in sorted(evs)]
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")

    # one sample per clip sound, to listen to
    rng = random.Random(0)
    n = 0
    for clip, evs in clip_events.items():
        for t, eid, name, _ in sorted(evs):
            sounds = [(s, p) for s, p in events[eid] if s.file_id not in failed]
            chosen = pick(sounds, rng)
            if chosen is None:
                continue
            src = out / "wav" / f"{chosen.file_id}.wav"
            dst = out / "listen" / f"{clip}__{t:05d}ms__{name.split('__', 1)[-1]}.wav"
            dst.write_bytes(src.read_bytes())
            n += 1
    total_mb = sum(p.stat().st_size for p in (out / "wav").glob("*.wav")) / 1e6
    print(f"Wrote {out}: {len(list((out / 'wav').glob('*.wav')))} wav files ({total_mb:.1f} MB), "
          f"manifest.json, and {n} samples in listen/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
