#!/usr/bin/env python3
"""
Find out whether Cozmo's ORIGINAL sounds can be recovered for the animation
clips. Read-only; does not connect to the robot, plays nothing, changes nothing.

Background: PyCozmo plays no sound for clips - a clip's audio keyframes hold
Wwise sound-engine event IDs, and PyCozmo skips them (a TODO in its source).
But `pycozmo_resources.py download` also extracts the Cozmo app's sound assets
(assets/cozmo_resources/sound/, from AudioAssets.zip), and PyCozmo ships a
partial reader for Wwise files (SoundbanksInfo.xml and .bnk banks; its .wem
reader is an empty stub). So the question is how far that gets us.

What this reports, step by step (each step tolerates the previous failing):
  1. what is in the sound folder (file counts / sizes by extension);
  2. whether SoundbanksInfo.xml parses, and how many events it names;
  3. for each clip (the keeps in data/animations_picks.tsv, plus the wake and
     sleep clips, plus any names given on the command line): its audio
     keyframes - time, event IDs, and the event NAMES, which say what the
     sound is;
  4. best effort: whether each event leads straight to a sound file (SFX) in
     its .bnk bank, or into a Wwise container PyCozmo's reader doesn't follow.

Writes data/clip_sounds.tsv (one row per clip audio event) and prints a summary.
Sound files are copyrighted Anki/DDL assets: keep anything you extract LOCAL,
never in the repo.

Usage (from the repo root, on the machine that has the resources):
  python3 standalone/dump_clip_sounds.py
  python3 standalone/dump_clip_sounds.py anim_codelab_chicken_01 anim_hiccup_02
"""

from __future__ import annotations

import csv
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pycozmo  # noqa: E402
from pycozmo import anim_encoder, util  # noqa: E402
from pycozmo.audiokinetic import soundbank as sb  # noqa: E402
from pycozmo.audiokinetic import soundbanksinfo as sbi  # noqa: E402

from cozmo_brain.robot import clips as clip_consts  # noqa: E402

PICKS = ROOT / "data" / "animations_picks.tsv"
OUT = ROOT / "data" / "clip_sounds.tsv"


def wanted_clips(extra: list[str]) -> list[str]:
    names: list[str] = [clip_consts.WAKE_CLIP, *clip_consts.SLEEP_CLIPS]
    if PICKS.exists():
        last: dict[str, list[str]] = {}
        with PICKS.open(encoding="utf-8") as f:
            for row in csv.reader(f, delimiter="\t"):
                if row and row[0] != "group" and len(row) >= 3:
                    last[row[0]] = row
        names += [r[1] for r in last.values() if r[2] == "keep"]
    names += extra
    return list(dict.fromkeys(names))  # unique, in order


def step1_listing(sound_dir: Path) -> None:
    print(f"== 1. Sound folder: {sound_dir}")
    if not sound_dir.exists():
        print("   NOT FOUND - the download may not have extracted AudioAssets.zip.\n")
        return
    count: Counter[str] = Counter()
    size: Counter[str] = Counter()
    for root, _dirs, files in os.walk(sound_dir):
        for name in files:
            ext = Path(name).suffix.lower() or "(none)"
            count[ext] += 1
            size[ext] += os.path.getsize(os.path.join(root, name))
    for ext, n in count.most_common():
        print(f"   {ext:10s} {n:6d} files  {size[ext] / 1e6:8.1f} MB")
    top = sorted(os.listdir(sound_dir))
    print(f"   top level ({len(top)} entries): {', '.join(top[:25])}{' ...' if len(top) > 25 else ''}")
    zips = [str(p.relative_to(sound_dir)) for p in sound_dir.rglob("*.zip")]
    if zips:
        print(f"   NOT EXTRACTED? zip files still present: {', '.join(zips)}")
    print()


def step2_info(sound_dir: Path) -> dict:
    print("== 2. SoundbanksInfo.xml")
    path = sound_dir / "SoundbanksInfo.xml"
    if not path.exists():
        found = list(sound_dir.rglob("SoundbanksInfo.xml")) if sound_dir.exists() else []
        if not found:
            print("   not found.\n")
            return {}
        path = found[0]
    try:
        info = sbi.load_soundbanksinfo(str(path))
    except Exception as e:  # noqa: BLE001 - report and carry on
        print(f"   failed to parse {path}: {type(e).__name__}: {e}\n")
        return {}
    events = [o for o in info.values() if isinstance(o, sbi.EventInfo)]
    banks = [o for o in info.values() if isinstance(o, sbi.SoundBankInfo)]
    print(f"   {path}: {len(banks)} sound banks, {len(events)} named events.\n")
    return info


def clip_audio(clip_names: list[str]) -> dict[str, list]:
    """clip name -> its AnimRobotAudio keyframes."""
    meta = anim_encoder.get_clip_metadata(str(util.get_cozmo_anim_dir()))
    wanted_files: dict[str, str] = {n: meta[n].fspec for n in clip_names if n in meta}
    out: dict[str, list] = {}
    for fspec in sorted(set(wanted_files.values())):
        for clip in anim_encoder.AnimClips.from_fb_file(fspec).clips:
            if clip.name in wanted_files:
                out[clip.name] = [k for k in clip.keyframes if isinstance(k, anim_encoder.AnimRobotAudio)]
    for n in clip_names:
        if n not in meta:
            print(f"   (no such clip on this robot: {n})")
    return out


def resolve_event(info: dict, loaded: dict, sound_dir: Path, event_id: int) -> str:
    """Best effort: where does this event's sound live?  Returns a short description."""
    ev_info = info.get(event_id)
    if not isinstance(ev_info, sbi.EventInfo):
        return "event not in SoundbanksInfo"
    bank_info = info.get(ev_info.soundbank_id)
    if not isinstance(bank_info, sbi.SoundBankInfo):
        return "bank unknown"
    if bank_info.id not in loaded:
        path = sound_dir / bank_info.path
        if not path.exists():
            hits = list(sound_dir.rglob(Path(bank_info.path).name))
            if not hits:
                loaded[bank_info.id] = f"no file named {Path(bank_info.path).name} under {sound_dir} (looked for {path})"
                return f"bank {bank_info.name} not readable ({loaded[bank_info.id]})"
            path = hits[0]
        try:
            loaded[bank_info.id] = sb.SoundBankReader(info).load(str(path))
        except Exception as e:  # noqa: BLE001 - the reader asserts on objects it doesn't know
            loaded[bank_info.id] = f"{type(e).__name__}: {e}"
    bank = loaded[bank_info.id]
    if isinstance(bank, str):
        return f"bank {bank_info.name} not readable ({bank})"
    event = bank.objs.get(event_id)
    if not isinstance(event, sb.Event):
        return f"event not found in bank {bank_info.name}"
    parts = []
    for action_id in event.action_ids:
        action = bank.objs.get(action_id)
        target = bank.objs.get(action.reference_id) if isinstance(action, sb.EventAction) else None
        if isinstance(target, sb.SFX):
            parts.append(f"SFX file {target.file_id} (location {target.location})")
        else:
            parts.append("-> container/other (not followed by PyCozmo's reader)")
    return f"bank {bank_info.name}: " + "; ".join(parts) if parts else f"bank {bank_info.name}: no actions"


def main() -> int:
    try:
        util.check_assets()
    except pycozmo.exception.ResourcesNotFound:
        print("Animation resources not found. Run 'pycozmo_resources.py download' first.")
        return 1

    sound_dir = util.get_cozmo_asset_dir() / "cozmo_resources" / "sound"
    step1_listing(sound_dir)
    info = step2_info(sound_dir)

    names = wanted_clips(sys.argv[1:])
    print(f"== 3. Audio keyframes in {len(names)} clips")
    audio = clip_audio(names)
    loaded: dict = {}
    OUT.parent.mkdir(exist_ok=True)
    silent = []
    direct = other = 0
    with OUT.open("w", encoding="utf-8") as f:
        f.write("clip\ttime_ms\tevent_id\tevent_name\tvolume\tprobability\tresolves_to\n")
        for name in names:
            kfs = audio.get(name)
            if not kfs:
                silent.append(name)
                continue
            shown = []
            for k in kfs:
                probs = list(k.probabilities) or [""] * len(k.audio_event_ids)
                for event_id, prob in zip(k.audio_event_ids, probs):
                    ev = info.get(event_id)
                    ev_name = ev.name if isinstance(ev, sbi.EventInfo) else "?"
                    where = resolve_event(info, loaded, sound_dir, event_id) if info else "no SoundbanksInfo"
                    direct += "SFX file" in where
                    other += "SFX file" not in where
                    f.write(f"{name}\t{k.trigger_time_ms}\t{event_id}\t{ev_name}\t{k.volume}\t{prob}\t{where}\n")
                    shown.append(f"{k.trigger_time_ms}ms {ev_name}")
            print(f"   {name}: " + ("; ".join(shown[:4]) + (" ..." if len(shown) > 4 else "")))
    if silent:
        print(f"   no audio keyframes: {', '.join(silent)}")
    print(f"\n== 4. Of the events used by these clips: {direct} lead straight to a sound file, "
          f"{other} do not (container, missing, or unreadable).")
    print(f"\nWrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
