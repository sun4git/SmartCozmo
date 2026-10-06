"""The hand-picked real Anki clips Cozmo can use by a friendly name, next to the
built-in gestures (gestures.py). Chosen on the robot with
standalone/audition_animations.py (see data/animations_picks.tsv); the
descriptions are written from the clip/group names and what was seen, not from
anything Anki documented.

Tiers:
  short  - brief enough to use freely, alone or while speaking.
  long   - 12 s or more: only worth it WHILE Cozmo says something (it's the
           speech that fills the time - PyCozmo plays clips silent), so these
           are refused as a standalone gesture. Not offered to the model yet
           (see MODEL_TIERS).
  cue    - short "look at me" faces for events (before a photo), not for the
           model to pick in conversation.
  idle   - calm, mostly face-only, for idle fidgets.

Every clip is played with its wheel commands removed (clips.strip_wheels).
`seconds` is how long PyCozmo takes to play it (data/animations_clips.tsv).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CuratedClip:
    clip: str  # real Anki clip name, as in list_animations()
    description: str
    seconds: float
    tier: str = "short"


CURATED: dict[str, CuratedClip] = {
    # reactions
    "surprised": CuratedClip("anim_explorer_driving01_turbo_01", "A quick startled, surprised look.", 2.1),
    "disappointed": CuratedClip("anim_reacttoblock_frustrated_int2_01", "A short sad, disappointed slump.", 1.9),
    "yuck": CuratedClip("anim_codelab_kitchen_yucky_01", "Screws up its face: yuck, gross.", 3.7),
    "phew": CuratedClip("anim_hiking_rtnewarea_03", "A relieved 'phew'.", 3.9),
    "whoa": CuratedClip("anim_reacttocliff_pickup_01", "A startled 'whoa!'.", 6.1),
    "scared": CuratedClip("anim_codelab_frightenedcozmo_01", "Cowers, frightened.", 6.6),
    # positive
    "win": CuratedClip("anim_memorymatch_solo_successgame_player_01", "A short victory celebration.", 2.7),
    "happy": CuratedClip("anim_meetcozmo_celebration_02", "Happy, pleased with itself.", 4.5),
    "celebrate": CuratedClip("anim_sparking_success_02", "A bigger celebration, for real appreciation.", 6.1),
    "fist_bump": CuratedClip("anim_fistbump_success_02", "Offers a fist bump and giggles when it lands.", 4.3),
    "lets_play": CuratedClip("anim_rtpkeepaway_ideatoplay_02", "Gets an idea: 'let's play!'.", 4.8),
    "peekaboo": CuratedClip("anim_peekaboo_success_03", "A delighted peekaboo.", 5.3),
    "greeting": CuratedClip("anim_greeting_happy_03_head_angle_40", "A happy hello, head tilted up.", 5.7),
    # answers
    "yes": CuratedClip("anim_rtpmemorymatch_yes_02", "An enthusiastic yes.", 2.5),
    "nope": CuratedClip("anim_cozmosays_badword_01", "A grumpy 'nope'.", 4.3),
    "thinking": CuratedClip("anim_hiking_rtpmarker_01", "Thinks it over.", 4.8),
    "dunno": CuratedClip("anim_gif_idk_01", "'I don't know' - a puzzled shrug.", 5.0),
    "inquisitive": CuratedClip("anim_codelab_magicfortuneteller_inquistive", "Leans in, inquisitive.", 5.0),
    # attention and playful (the sounds the originals make are not played)
    "ribbit": CuratedClip("anim_codelab_frog_01", "Hops like a frog - ribbit.", 2.4),
    "rooster": CuratedClip("anim_codelab_rooster_01", "A rooster crowing: wake up, look here.", 4.0),
    "hiccup": CuratedClip("anim_hiccup_02", "Gets the hiccups.", 4.9),
    "elephant": CuratedClip("anim_codelab_elephant_01", "Trumpets like an elephant to get attention.", 5.4),
    "chicken": CuratedClip("anim_codelab_chicken_01", "Struts and clucks like a chicken.", 5.7),
    # long: only while speaking
    "dance_mambo": CuratedClip("anim_dancing_mambo_01", "A full mambo dance routine.", 23.5, "long"),
    "sing": CuratedClip("anim_cozmosings_100_song_01", "Sways along as if singing a song.", 16.0, "long"),
    "tiger": CuratedClip("anim_codelab_tiger_01", "A fierce, scary tiger.", 14.1, "long"),
    "duck": CuratedClip("anim_codelab_duck_01", "Waddles and quacks like a duck.", 12.2, "long"),
    "bored_event": CuratedClip("anim_bored_event_03", "Bored, entertaining itself with an imaginary ping-pong game.", 17.7, "long"),
    # cues for events (not offered to the model)
    "eyes_on": CuratedClip("anim_launch_eyeson_01", "Eyes snap on: look at me.", 0.9, "cue"),
    "squint": CuratedClip("anim_sparking_driving_loop_01", "Squints, looking closely.", 4.0, "cue"),
    # idle fidgets (not offered to the model)
    "staring": CuratedClip("anim_codelab_staring_loop", "Stares blankly ahead.", 7.7, "idle"),
    "scanning": CuratedClip("anim_meetcozmo_lookface_02", "Scans around as if looking for a face.", 8.5, "idle"),
    "sheep": CuratedClip("anim_codelab_sheep_01", "Wide-eyed and curious.", 6.3, "idle"),
}

# The tiers a model may ask for by name in a `say` / `gesture` call. "long" is
# built (a long clip is only accepted inside a `say`) but NOT switched on: the
# first batch is the short clips only. Add "long" here to offer them.
MODEL_TIERS = ("short",)


def model_clips() -> dict[str, CuratedClip]:
    """The curated clips offered to the model by name."""
    return {name: c for name, c in CURATED.items() if c.tier in MODEL_TIERS}


def describe_for_prompt() -> str:
    """One line per model-visible clip, for tool descriptions."""
    lines = []
    for name, c in model_clips().items():
        note = f" (~{c.seconds:.0f}s, only while speaking)" if c.tier == "long" else ""
        lines.append(f"{name}: {c.description}{note}")
    return "; ".join(lines)
