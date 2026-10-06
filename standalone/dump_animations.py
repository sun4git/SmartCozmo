#!/usr/bin/env python3
"""
Dump every real Anki animation clip and animation group PyCozmo has loaded, so
a "greatest hits" subset can be hand-picked for the `play_animation` tool
(see docs/roadmap.md, "More real animations, curated").

Does NOT connect to the robot - it only reads the downloaded resource files
(the same ones `load_anims()` reads at startup), so it's safe to run while the
main app is running or with Cozmo switched off.

Writes:
  data/animations_clips.tsv   one row per clip: name + which tracks it uses
                              (head, lift, body motion, lights, face, audio)
  data/animations_groups.tsv  one row per group member: group, clip, mood, weight
and prints a summary grouped by name prefix, so the categories are visible.

The "body" column matters when auditioning: a clip with body motion drives the
wheels, so only try those with Cozmo well away from table edges (and off the
charger).

Usage (from the repo root, on the machine that has the resources):
  python3 standalone/dump_animations.py
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pycozmo  # noqa: E402
from pycozmo import anim, anim_encoder, util  # noqa: E402

from cozmo_brain.robot.clips import clip_playback_seconds  # noqa: E402

OUT_DIR = ROOT / "data"


def clip_durations(clips: dict) -> dict[str, float]:
    """How long PyCozmo actually takes to play each clip, in seconds (see
    clips.playback_seconds - NOT just the last keyframe time: every keyframe
    adds ~33 ms, so dense face clips run about twice their timeline). Needs
    the full clip files, not just the metadata, so each .bin is loaded once."""
    out: dict[str, float] = {}
    for fspec in sorted({c.fspec for c in clips.values()}):
        for clip in anim_encoder.AnimClips.from_fb_file(fspec).clips:
            out[clip.name] = clip_playback_seconds(clip)
    return {name: out.get(name, 0.0) for name in clips}


def main() -> int:
    try:
        util.check_assets()
    except pycozmo.exception.ResourcesNotFound:
        print("Animation resources not found. Run 'pycozmo_resources.py download' first.")
        return 1

    clips = anim_encoder.get_clip_metadata(str(util.get_cozmo_anim_dir()))
    groups = anim.load_animation_groups(str(util.get_cozmo_asset_dir()))

    OUT_DIR.mkdir(exist_ok=True)

    durations = clip_durations(clips)

    clips_path = OUT_DIR / "animations_clips.tsv"
    with clips_path.open("w", encoding="utf-8") as f:
        f.write("name\thead\tlift\tbody\tlights\tface\taudio\tduration_s\n")
        for name in sorted(clips):
            c = clips[name]
            flags = [
                c.has_head_angle_track,
                c.has_lift_height_track,
                c.has_body_motion_track,
                c.has_backpack_lights_track,
                c.has_face_animation_track or c.has_procedural_face_track,
                c.has_robot_audio_track,
            ]
            f.write(name + "\t" + "\t".join("1" if x else "0" for x in flags) + f"\t{durations[name]:.2f}\n")

    groups_path = OUT_DIR / "animations_groups.tsv"
    with groups_path.open("w", encoding="utf-8") as f:
        f.write("group\tclip\tmood\tweight\n")
        for gname in sorted(groups):
            for m in groups[gname].members:
                f.write(f"{gname}\t{m.name}\t{m.mood}\t{m.weight}\n")

    # Summary by prefix (text before the first '_' after an optional 'anim_'),
    # which is usually the category Anki named the clip for.
    by_prefix: dict[str, list[str]] = defaultdict(list)
    for name in clips:
        parts = name.split("_")
        key = parts[1] if parts[0] == "anim" and len(parts) > 1 else parts[0]
        by_prefix[key].append(name)

    print(f"{len(clips)} clips, {len(groups)} groups.")
    print(f"  wrote {clips_path}")
    print(f"  wrote {groups_path}\n")
    print("Clips by prefix (count, example):")
    for key, names in sorted(by_prefix.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(names):4d}  {key:<24} e.g. {sorted(names)[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
