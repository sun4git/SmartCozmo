"""The concrete tool set exposed to the LLM, bound to a robot backend + a
speech (STT/TTS) client — whichever providers STT_PROVIDER/TTS_PROVIDER select.

Handlers raise plain exceptions (ValueError, RuntimeError, TimeoutError, ...)
on failure; the orchestrator's dispatch loop catches those and turns them
into a ToolResult(ok=False, ...) tool message, so a bad tool call becomes
feedback the model can react to instead of crashing the session.
"""

from __future__ import annotations

import os
import re
import threading

from cozmo_brain.audio.player import play_wav
from cozmo_brain.config import Settings
from cozmo_brain.imaging import encode_image_b64
from cozmo_brain.llm.chat_client import ChatClient
from cozmo_brain.llm.speech_client import SpeechClient
from cozmo_brain.robot.base import MoveResult, RobotBackend
from cozmo_brain.robot.gestures import GESTURES
from cozmo_brain.robot.moods import MOODS
from cozmo_brain.tools.base import Tool, ToolResult

_MOOD_NAMES = sorted(MOODS)
_GESTURE_NAMES = sorted(GESTURES)

_KNOWN_PHOTO_EXTS = (".jpg", ".jpeg", ".png")

_HAZARD_MESSAGES = {
    "cliff": "detected a cliff/edge partway through and immediately backed away for safety",
    "fall": "detected a fall partway through and stopped immediately for safety",
}

_COMPARE_FACES_PROMPT = (
    "You are comparing two photos taken by a low-resolution, often blurry robot "
    "camera with a warm/reddish color cast — ignore lighting and color entirely "
    "and judge only facial structure. The first photo is a reference photo of a "
    "person named {name}. The second photo was just taken. Is the person in the "
    "second photo the same person as in the first photo? Reply with exactly one "
    "word: yes or no."
)


_CHARGER_RETURN_REASONS = {
    "no_charger_pose": "don't know where the charger is (haven't left it this session, or was picked up/reconnected since)",
    "already_on_charger": "already on the charger",
    "picked_up": "got picked up partway, so no longer know where the charger is",
    "pose_lost": "lost track of position partway (it reset), so no longer know where the charger is",
    "disconnected": "lost the connection to Cozmo partway",
    "timeout": "took too long getting there and stopped",
    "not_on_charger": "drove back and reversed to dock, but the charger contacts aren't reading as docked - may be misaligned",
}


def charger_return_message(result: MoveResult) -> str:
    """Honest, model-facing description of a return_to_charger() outcome -
    shared by the `dock` tool and charger_return.py's autonomous return."""
    if result.completed:
        return "Drove back to the charger and docked."
    if result.hazard:
        return f"Only made partway back to the charger - {_HAZARD_MESSAGES[result.hazard]}."
    if result.reason:
        return f"Didn't make it onto the charger - {_CHARGER_RETURN_REASONS.get(result.reason, result.reason)}."
    return "Didn't move - on the charger and the battery's too low to come off yet."


# extra flag on a ToolResult meaning "this didn't go as planned - the model
# must see it before the turn ends" (a hazard cut a move short, a move was
# refused, a charger return failed). engine.py only ends a turn early after
# a final `say` when no result in that batch carries this - see
# CozmoEngine._turn_is_done().
_ATTENTION = {"needs_attention": True}


def _charger_blocked_result(verb: str) -> ToolResult:
    # extra["blocked_by_charger"] lets the engine guarantee the human hears
    # why nothing happened, even if the model never says so itself (see
    # engine.py's _CHARGER_BLOCKED_FALLBACK).
    return ToolResult(
        True,
        f"Didn't {verb} - on the charger and the battery's too low to come off yet. "
        "Tell them out loud you can't move yet because you need to charge more.",
        extra={"blocked_by_charger": True, **_ATTENTION},
    )


def _sanitize_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_")
    return cleaned.lower() or "unnamed"


def build_tools(robot: RobotBackend, speech: SpeechClient, ollama: ChatClient, settings: Settings) -> list[Tool]:
    def handle_say(text: str, mood: str = "neutral", gesture: str | None = None) -> ToolResult:
        # "neutral" now resets pose (see moods.py) rather than being a
        # no-op, so it must actually run, not be skipped like other moods
        # used to be for efficiency.
        applied_mood = robot.apply_mood(mood) if mood else None

        # Bundled into this one call, rather than relying on the model
        # calling the separate `gesture` tool right before/after, because
        # that ordering is entirely up to the model and unreliable -
        # confirmed on real hardware: when it called `say` before
        # `gesture`, there was zero overlap (say() fully blocks on its own
        # audio regardless of gesture's async-ness), so speech and
        # movement never actually happened together despite gesture()
        # itself being non-blocking. This guarantees the order instead.
        def start_gesture() -> str:
            if settings.gesture_async_enabled:
                description = robot.run_gesture_async(gesture)
                return f" while performing '{gesture}' ({description})"
            # Same fallback GESTURE_ASYNC_ENABLED=false gives the
            # standalone `gesture` tool: fully sequential, gesture
            # finishes before speech starts either way, regardless of
            # GESTURE_SPEECH_SYNC_ENABLED below.
            description = robot.run_gesture(gesture)
            return f" after performing '{gesture}' ({description})"

        gesture_note = ""
        # Default: start the gesture immediately, before synthesis - Cozmo
        # reacts the instant `say` is called. Overlap with speech isn't
        # guaranteed if synthesis takes longer than the gesture itself
        # (see GESTURE_SPEECH_SYNC_ENABLED's config.py comment).
        if gesture and not settings.gesture_speech_sync_enabled:
            gesture_note = start_gesture()

        try:
            wav_path = speech.synthesize(text, settings.tts_output_wav)
        finally:
            # GESTURE_SPEECH_SYNC_ENABLED=true: hold the gesture until speech
            # is actually ready to play, so the overlap is guaranteed
            # regardless of how long synthesis took - at the cost of no
            # movement (mood/face already applied above, independent of
            # this) until synthesis finishes. In `finally` specifically so a
            # synthesis failure (e.g. a local TTS error) still lets the
            # gesture run instead of silently skipping it entirely - a
            # gesture that raises the lift and only lowers it again as its
            # own final step (see gestures.py) would otherwise leave the arm
            # stuck up with nothing that ever brings it back down.
            if gesture and settings.gesture_speech_sync_enabled:
                gesture_note = start_gesture()

        output = settings.audio_output.lower()
        if output == "system":
            play_wav(wav_path, settings.playback_device)
        elif output == "both":
            # Simultaneous, not sequential — a background thread for system
            # audio while the main thread drives Cozmo's own speaker, then
            # wait for both so this call still blocks until speech is done.
            system_thread = threading.Thread(target=play_wav, args=(wav_path, settings.playback_device))
            system_thread.start()
            robot.say_wav(wav_path)
            system_thread.join()
        else:
            robot.say_wav(wav_path)

        # "Arms up" moods (happy/excited/proud/smug - see moods.py) raise
        # the lift and deliberately don't lower it as part of apply_mood()
        # itself, so the pose is still visible for the whole reply. Left
        # alone, nothing brings it back down until the next mood/turn
        # resets it, or idle_fidget.py eventually does after
        # IDLE_FIDGET_AFTER_S (default 5 min) - confirmed on real hardware
        # to read as a stuck arm, not an intentional flourish, once the
        # turn is actually over. Checking mood.lift_mm here (rather than a
        # hardcoded mood-name list) stays correct automatically if a future
        # mood adds this same "raised, not auto-lowered" shape.
        if applied_mood is not None and applied_mood.lift_mm is not None:
            robot.lower_lift_fully()

        return ToolResult(True, f"Said (mood={mood}){gesture_note}: {text}")

    def handle_gesture(name: str) -> ToolResult:
        if settings.gesture_async_enabled:
            description = robot.run_gesture_async(name)
            return ToolResult(True, f"Started gesture '{name}': {description}")
        description = robot.run_gesture(name)
        return ToolResult(True, f"Performed gesture '{name}': {description}")

    def handle_play_animation(name: str) -> ToolResult:
        robot.play_animation(name)
        return ToolResult(True, f"Played animation '{name}'.")

    def handle_list_animations() -> ToolResult:
        names = robot.list_animations()
        if not names:
            return ToolResult(
                True,
                "No real animation clips are loaded (resources not downloaded on this robot). "
                "You can still use the gesture and mood tools.",
            )
        shown = names[:40]
        more = f" (+{len(names) - 40} more)" if len(names) > 40 else ""
        return ToolResult(True, f"Available animations: {', '.join(shown)}{more}")

    def handle_drive(distance_mm: float, speed_mmps: float) -> ToolResult:
        result = robot.drive(distance_mm, speed_mmps)
        if not result.moved:
            return _charger_blocked_result("drive")
        if result.hazard:
            return ToolResult(
                True, f"Only drove partway toward {distance_mm:.0f}mm - {_HAZARD_MESSAGES[result.hazard]}.", extra=_ATTENTION
            )
        return ToolResult(True, f"Drove {distance_mm:.0f}mm at {speed_mmps:.0f}mm/s.")

    def handle_turn(angle_degrees: float) -> ToolResult:
        result = robot.turn(angle_degrees)
        if not result.moved:
            return _charger_blocked_result("turn")
        if result.hazard:
            return ToolResult(True, f"Only turned partway - {_HAZARD_MESSAGES[result.hazard]}.", extra=_ATTENTION)
        return ToolResult(True, f"Turned {angle_degrees:.0f} degrees.")

    def handle_dock() -> ToolResult:
        if robot.is_on_charger():
            return ToolResult(True, "Already on the charger - no need to dock.")
        # Known charger location (recorded when Cozmo last drove off it, and
        # still valid - no pickup/reconnect since): navigate back to the
        # staging point first, then dock. Otherwise fall back to the plain
        # blind reverse, which only works if he's already lined up.
        if robot.has_charger_pose():
            result = robot.return_to_charger()
            return ToolResult(True, charger_return_message(result), extra=None if result.completed else _ATTENTION)
        result = robot.dock()
        if not result.moved:
            return ToolResult(True, "Already on the charger - no need to dock.")
        if result.hazard:
            return ToolResult(
                True, f"Only made partway back to the charger - {_HAZARD_MESSAGES[result.hazard]}.", extra=_ATTENTION
            )
        return ToolResult(True, "Backed onto the charger to dock.")

    def handle_look() -> ToolResult:
        path = robot.capture_photo(settings.camera_snapshot_path)
        return ToolResult(True, "Took a photo.", extra={"image_path": path})

    def handle_remember_person(name: str) -> ToolResult:
        os.makedirs(settings.known_people_dir, exist_ok=True)
        path = os.path.join(settings.known_people_dir, f"{_sanitize_name(name)}.jpg")
        robot.capture_photo(path)
        return ToolResult(True, f"Got it - I'll remember this face as {name}.")

    def handle_who_is_this() -> ToolResult:
        known_dir = settings.known_people_dir
        known_files = (
            sorted(f for f in os.listdir(known_dir) if f.lower().endswith(_KNOWN_PHOTO_EXTS))
            if os.path.isdir(known_dir)
            else []
        )
        if not known_files:
            return ToolResult(True, "I don't know anyone yet — nobody has been introduced with remember_person.")

        new_path = robot.capture_photo(settings.camera_snapshot_path)
        new_b64 = encode_image_b64(new_path)
        extra = {"image_path": new_path, "image_caption": "[Cozmo just took a photo to see who this is.]"}

        for filename in known_files:
            name = os.path.splitext(filename)[0]
            reference_b64 = encode_image_b64(os.path.join(known_dir, filename))
            prompt = _COMPARE_FACES_PROMPT.format(name=name)
            response = ollama.chat([{"role": "user", "content": prompt, "images": [reference_b64, new_b64]}])
            if response.content.strip().lower().startswith("yes"):
                return ToolResult(True, f"This looks like it might be {name} (not certain — guess only).", extra=extra)

        return ToolResult(True, "I took a look, but I don't recognize this person.", extra=extra)

    # safe_to_end_turn=True marks pure actions only (see tools/base.py). The
    # information tools - list_animations, look, who_is_this - deliberately
    # leave it False: the model has to read their result, so a turn that
    # used one always goes back to the model, even if a `say` came last.
    # When adding a tool, leave it False unless its success result tells the
    # model nothing it needs.
    return [
        Tool(
            name="say",
            description=(
                "Speak a short sentence out loud through Cozmo's speaker, with an optional "
                "mood that sets Cozmo's face and backpack lights while speaking. Use this for "
                "almost every turn — Cozmo should talk, not just act silently. Pass `gesture` "
                "here (not a separate `gesture` tool call) whenever you want Cozmo to move "
                "WHILE talking, e.g. dancing while cheering — this is the only way to guarantee "
                "the gesture and the speech actually happen at the same time, since calling the "
                "separate `gesture` tool before or after `say` only ever runs one before the "
                "other, never together."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "What Cozmo should say. Keep it short."},
                    "mood": {
                        "type": "string",
                        "enum": _MOOD_NAMES,
                        "description": "Cozmo's emotional expression while saying this.",
                    },
                    "gesture": {
                        "type": "string",
                        "enum": _GESTURE_NAMES,
                        "description": "Optional — a gesture to perform at the same time as speaking.",
                    },
                },
                "required": ["text"],
            },
            handler=lambda args: handle_say(args["text"], args.get("mood", "neutral"), args.get("gesture")),
            safe_to_end_turn=True,
        ),
        Tool(
            name="gesture",
            description=(
                "Perform a physical gesture on its own, with no speech: a short choreographed "
                "combination of face, lights, head/arm movement, and driving. Use this for a "
                "silent physical reaction. If Cozmo should talk WHILE gesturing, use `say`'s "
                "own `gesture` argument instead — calling this tool alongside `say` never "
                "actually overlaps them, only one runs before the other."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": _GESTURE_NAMES,
                        "description": "Which gesture to perform.",
                    }
                },
                "required": ["name"],
            },
            handler=lambda args: handle_gesture(args["name"]),
            safe_to_end_turn=True,
        ),
        Tool(
            name="play_animation",
            description=(
                "Play one of Cozmo's real built-in animation clips or groups by exact name. "
                "Call list_animations first to see what's actually available on this robot — "
                "names vary by robot and may be empty if animation assets aren't installed."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Exact animation or 'group:<name>' from list_animations."}
                },
                "required": ["name"],
            },
            handler=lambda args: handle_play_animation(args["name"]),
            safe_to_end_turn=True,
        ),
        Tool(
            name="list_animations",
            description="List the real animation clip/group names available on this specific robot.",
            parameters={"type": "object", "properties": {}},
            handler=lambda _args: handle_list_animations(),
        ),
        Tool(
            name="drive",
            description="Drive forward or backward in a straight line.",
            parameters={
                "type": "object",
                "properties": {
                    "distance_mm": {
                        "type": "number",
                        "description": "Distance in millimeters. Positive = forward, negative = backward.",
                    },
                    "speed_mmps": {"type": "number", "description": "Speed in mm/s. Keep this modest, e.g. 50-100."},
                },
                "required": ["distance_mm", "speed_mmps"],
            },
            handler=lambda args: handle_drive(args["distance_mm"], args["speed_mmps"]),
            safe_to_end_turn=True,
        ),
        Tool(
            name="turn",
            description="Turn in place, left or right.",
            parameters={
                "type": "object",
                "properties": {
                    "angle_degrees": {
                        "type": "number",
                        "description": "Positive = turn left (counterclockwise), negative = turn right.",
                    }
                },
                "required": ["angle_degrees"],
            },
            handler=lambda args: handle_turn(args["angle_degrees"]),
            safe_to_end_turn=True,
        ),
        Tool(
            name="dock",
            description=(
                "Go back onto the charger. Use this whenever asked to return to the charger/dock/"
                "base (including agreeing to an earlier offer to go charge), instead of drive() "
                "with a negative distance - a plain reverse drive() gets falsely stopped early by "
                "the charger platform's own edge, which this works around. If Cozmo remembers "
                "where the charger is (he drove off it earlier and hasn't been picked up since), "
                "this drives all the way back and docks on its own. Otherwise it only reverses "
                "in a straight line, so it only works when Cozmo is already close to and facing "
                "away from the charger - the result says which happened."
            ),
            parameters={"type": "object", "properties": {}},
            handler=lambda _args: handle_dock(),
            safe_to_end_turn=True,
        ),
        Tool(
            name="look",
            description=(
                "Take a photo with Cozmo's camera to see what's in front of him. The photo is "
                "attached to the conversation so you can describe or react to what you see."
            ),
            parameters={"type": "object", "properties": {}},
            handler=lambda _args: handle_look(),
        ),
        Tool(
            name="remember_person",
            description=(
                "Remember the person currently in front of Cozmo under a name, by taking a "
                "reference photo. Use when someone introduces themselves or asks Cozmo to "
                "remember them. Call who_is_this later to check for a match."
            ),
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "The person's name."}},
                "required": ["name"],
            },
            handler=lambda args: handle_remember_person(args["name"]),
            safe_to_end_turn=True,
        ),
        Tool(
            name="who_is_this",
            description=(
                "Take a photo and check whether it matches anyone remembered via "
                "remember_person. Experimental and NOT reliable — Cozmo's camera is low "
                "resolution and blurry, so treat any match as a guess, never a certainty, "
                "and say so if asked."
            ),
            parameters={"type": "object", "properties": {}},
            handler=lambda _args: handle_who_is_this(),
        ),
    ]
