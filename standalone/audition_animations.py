#!/usr/bin/env python3
"""
Audition real Anki animation groups on the robot, one at a time, and record
which ones are worth keeping for the `play_animation` tool (see docs/roadmap.md,
"More real animations, curated"). Companion to standalone/dump_animations.py.

Why it's needed: group names don't reliably describe what plays (e.g. the
`CodeLabExcited` group plays `anim_reacttocliff_wheely_03`), and nearly every
expressive clip drives the wheels, so each one has to be seen.

Wheels are STRIPPED by default: the clip's wheel/turn commands are removed
before playing, so you see only the face, head, lift and backpack lights and
Cozmo stays where he is. Pass --wheels to play them in full. Each saved row
records which mode it was rated in. Sound: PyCozmo 0.8.0 does not play a
clip's own audio (its audio keyframes are Anki sound-engine event IDs, and
PyCozmo skips them), so expect silence - the has_audio column only says the
clip has such a track.

For each candidate group it picks one member clip (so the clip name is known,
unlike play_anim_group's silent random pick), plays it, waits for it to finish
(EvtAnimationCompleted) and asks what you thought:

    k [tag] [note...]   keep  (e.g. "k happy loud giggle")
    s [note...]         skip
    r                   replay the same clip
    n                   try a different clip from the same group
    q                   quit (what you've rated so far is already saved)

Results append to data/animations_picks.tsv (group, clip, verdict, tag, note,
has_audio, has_body_motion, mode). The newest row for a group wins. Re-running
skips groups already rated, unless you pass --all (everything) or --redo
(only those last rated skip/broken - e.g. to re-try them with wheels stripped).
Rows from before the `mode` column existed have 7 fields and were played with
wheels on.

SAFETY - the main app must NOT be running (only one program can hold the
robot). Put Cozmo on a clear, flat floor/table with room around him and OFF the
charger. With --wheels, many clips drive and some do a wheelie or flip. Table-
edge (cliff) protection is switched on, as in the app.

Usage (from the repo root, on the Pi, after dump_animations.py has worked):
  python3 standalone/audition_animations.py                # built-in shortlist
  python3 standalone/audition_animations.py --only Happy   # names containing "Happy"
  python3 standalone/audition_animations.py --groups CodeLabYes,Shocked
  python3 standalone/audition_animations.py --redo         # re-try skipped/broken ones, wheels stripped
  python3 standalone/audition_animations.py --all          # re-audition rated ones too
  python3 standalone/audition_animations.py --wheels       # play wheel motion too
"""

from __future__ import annotations

import argparse
import random
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pycozmo  # noqa: E402

from cozmo_brain.config import settings  # noqa: E402
from cozmo_brain.robot import wifi  # noqa: E402
from cozmo_brain.robot.clips import patch_pillow_chord, strip_wheels  # noqa: E402

PICKS_PATH = ROOT / "data" / "animations_picks.tsv"
COMPLETE_TIMEOUT_S = 60.0

# Hand-picked by group NAME only - none of these have been seen yet, which is
# the point of this script. Groups that don't exist on this robot are skipped
# with a note. Excluded on purpose: cube/game/pounce/onboarding/needs/repair/
# driving-loop clips and the picked-up/cliff/flipped-over reactions.
SHORTLIST = [
    # emotions and reactions
    "CodeLabHappy", "CodeLabExcited", "CodeLabCelebrate", "CodeLabDejected", "CodeLabUnhappy",
    "CodeLabFrustrated", "CodeLabSurprise", "CodeLabWhoa", "CodeLabYuck", "CodeLabWhew",
    "CodeLabScaredCozmo", "Shocked", "Surprise", "Shiver",
    # answers
    "CodeLabYes", "CodeLabNo", "CodeLabIDK", "CodeLabThinking", "CodeLabWondering", "CodeLabCurious",
    # social
    "NamedFaceInitialGreeting", "FistBumpSuccess", "PeekABooSurprised", "PetDetectionCat",
    "PetDetectionDog", "MajorWin", "MajorFail",
    # fun
    "DanceMambo", "CodeLabPartyTime", "CodeLabSneeze", "Hiccup", "DizzyReactionSoft", "CodeLabBored",
    "NothingToDoBoredEvent", "Singing_100bpm",
    # animal sounds
    "CodeLabCat", "CodeLabDog", "CodeLabCow", "CodeLabDuck", "CodeLabFrog", "CodeLabSheep",
    "CodeLabTiger", "CodeLabChicken", "CodeLabElephant", "CodeLabRooster", "CodeLabRattleSnake",
    # sleep / wake (they have sound)
    "GoToSleepGetIn", "GoToSleepSleeping", "ConnectWakeUp",
    # candidates for a "taking a photo" cue
    "CodeLabStaring", "CodeLabSquint1", "CodeLabSquint2", "CodeLabBlink", "OnboardingEyesOn",
    "MeetCozmoScanningIdle",
]


def last_verdicts() -> dict[str, str]:
    """group -> verdict from its newest row in the picks file."""
    if not PICKS_PATH.exists():
        return {}
    out: dict[str, str] = {}
    with PICKS_PATH.open(encoding="utf-8") as f:
        for line in f.read().splitlines()[1:]:
            cols = line.split("\t")
            if len(cols) >= 3:
                out[cols[0]] = cols[2]
    return out


def save_pick(row: list[str]) -> None:
    new = not PICKS_PATH.exists()
    PICKS_PATH.parent.mkdir(exist_ok=True)
    with PICKS_PATH.open("a", encoding="utf-8") as f:
        if new:
            f.write("group\tclip\tverdict\ttag\tnote\thas_audio\thas_body\tmode\n")
        f.write("\t".join(c.replace("\t", " ").replace("\n", " ") for c in row) + "\n")


def play_and_wait(cli: pycozmo.Client, clip: str, *, wheels: bool = False) -> str | None:
    """Play one clip and block until the robot reports it finished.

    Returns None on success, or a short error string if the clip couldn't be
    played. Some clips fail while PyCozmo pre-renders their face frames (seen
    on the Pi: Pillow raises "y1 must be greater than or equal to y0"); that
    happens before anything is sent to the robot, so nothing moved.
    """
    done = threading.Event()
    handler = lambda *_: done.set()  # noqa: E731
    cli.add_handler(pycozmo.event.EvtAnimationCompleted, handler, one_shot=True)
    try:
        if wheels:
            cli.play_anim(clip)
        else:
            # Same preparation as Client.play_anim(), minus the wheel commands.
            if clip not in cli._ppclips:
                if clip not in cli._clips:
                    cli._load_clips(cli._clip_metadata[clip].fspec)
                cli._ppclips[clip] = pycozmo.anim.PreprocessedClip.from_anim_clip(cli._clips[clip])
            strip_wheels(cli._ppclips[clip])
            cli.play_anim_ppclip(cli._ppclips[clip])
    except Exception as e:  # noqa: BLE001 - report and carry on with the next clip
        return f"{type(e).__name__}: {e}"
    if not done.wait(COMPLETE_TIMEOUT_S):
        print(f"  (no completion event after {COMPLETE_TIMEOUT_S:.0f}s - moving on)")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--groups", help="comma-separated group names instead of the built-in shortlist")
    ap.add_argument("--only", help="only groups whose name contains this text (case-insensitive)")
    ap.add_argument("--all", action="store_true", help="include groups already rated in animations_picks.tsv")
    ap.add_argument("--redo", action="store_true", help="re-audition groups whose newest rating is skip or broken")
    ap.add_argument("--wheels", action="store_true", help="play wheel motion too (default: wheels stripped)")
    ap.add_argument("--no-patch", action="store_true", help="don't apply the Pillow eyelid workaround")
    args = ap.parse_args()
    if not args.no_patch:
        patch_pillow_chord()

    names = [g.strip() for g in args.groups.split(",") if g.strip()] if args.groups else list(SHORTLIST)
    if args.only:
        names = [n for n in names if args.only.lower() in n.lower()]

    wifi.ensure_connected(settings.cozmo_wifi_ssid, settings.cozmo_wifi_password)
    cli = pycozmo.Client()
    cli.start()
    try:
        cli.connect()
        cli.wait_for_robot()
    except pycozmo.exception.PyCozmoException as e:
        cli.stop()
        print(f"Could not connect to Cozmo: {e}\nIs the main app still running? Only one program can hold the robot.")
        return 1

    try:
        cli.set_volume(65535)
        cli.conn.send(pycozmo.protocol_encoder.EnableStopOnCliff(enable=True))
        cli.load_anims()
        groups = cli.animation_groups
        clips = cli._clip_metadata  # has_robot_audio_track / has_body_motion_track per clip

        missing = [n for n in names if n not in groups]
        if missing:
            print(f"Not on this robot, skipped: {', '.join(missing)}")
        names = [n for n in names if n in groups]
        if not args.all:
            verdicts = last_verdicts()
            redo = {"skip", "broken"} if args.redo else set()
            names = [n for n in names if n not in verdicts or verdicts[n] in redo]
        if not names:
            print("Nothing to audition (everything is rated already - use --redo or --all to repeat).")
            return 0

        mode = "wheels" if args.wheels else "nowheels"
        print(f"\n{len(names)} groups to audition, mode: {mode}. Cozmo should be on clear floor, OFF the charger.")
        input("Press Enter to start (Ctrl+C any time to stop)... ")

        for i, group in enumerate(names, 1):
            members = [m.name for m in groups[group].members]
            clip = random.choice(members)
            failed: set[str] = set()
            while True:
                meta = clips[clip]
                print(f"\n[{i}/{len(names)}] {group}  ->  {clip}   "
                      f"(variants: {len(members)}, audio track: {'yes' if meta.has_robot_audio_track else 'no'}, "
                      f"wheel track: {'yes' if meta.has_body_motion_track else 'no'}"
                      f"{', wheels stripped' if not args.wheels else ''})")
                error = play_and_wait(cli, clip, wheels=args.wheels)
                if error:
                    print(f"  FAILED to play: {error}")
                    failed.add(clip)
                    others = [m for m in members if m not in failed]
                    if others:
                        print("  trying another clip from this group...")
                        clip = random.choice(others)
                        continue
                    save_pick([group, clip, "broken", "", error,
                               "1" if meta.has_robot_audio_track else "0",
                               "1" if meta.has_body_motion_track else "0", mode])
                    break
                answer = input("  k [tag] [note] / s [note] / r replay / n other variant / q quit > ").strip()
                cmd, _, rest = answer.partition(" ")
                cmd = cmd.lower()
                if cmd == "r":
                    continue
                if cmd == "n":
                    others = [m for m in members if m != clip]
                    if others:
                        clip = random.choice(others)
                    else:
                        print("  (only one clip in this group)")
                    continue
                if cmd == "q":
                    return 0
                if cmd in ("k", "s"):
                    tag, _, note = rest.partition(" ") if cmd == "k" else ("", "", rest)
                    save_pick([group, clip, "keep" if cmd == "k" else "skip", tag, note.strip(),
                               "1" if meta.has_robot_audio_track else "0",
                               "1" if meta.has_body_motion_track else "0", mode])
                    break
                print("  ?? use k, s, r, n or q")
        print(f"\nDone. Results are in {PICKS_PATH}")
        return 0
    except KeyboardInterrupt:
        print("\nStopped. Ratings so far are saved.")
        return 0
    finally:
        try:
            cli.disconnect()
        finally:
            cli.stop()


if __name__ == "__main__":
    sys.exit(main())
