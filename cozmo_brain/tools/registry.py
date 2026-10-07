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

from cozmo_brain.assistant_relay import AssistantRelay
from cozmo_brain.audio.player import play_wav
from cozmo_brain.battery_log import BatteryLog
from cozmo_brain.config import Settings
from cozmo_brain.imaging import encode_image_b64
from cozmo_brain.llm.assistant_client import AssistantClient
from cozmo_brain.llm.chat_client import ChatClient
from cozmo_brain.llm.speech_client import SpeechClient
from cozmo_brain.memory import Memory
from cozmo_brain.people import Presence, find_match, known_people
from cozmo_brain.robot.base import MoveResult, RobotBackend
from cozmo_brain.robot.curated import CURATED, describe_for_prompt, model_clips
from cozmo_brain.robot.gestures import GESTURES
from cozmo_brain.robot.moods import MOODS
from cozmo_brain.tools.assistant_tools import build_assistant_tools
from cozmo_brain.tools.base import Tool, ToolResult
from cozmo_brain.tools.memory_tools import build_memory_tools

_MOOD_NAMES = sorted(MOODS)
_GESTURE_NAMES = sorted(GESTURES)

_HAZARD_MESSAGES = {
    "cliff": "detected a cliff/edge partway through and immediately backed away for safety",
    "fall": "detected a fall partway through and stopped immediately for safety",
}

_CHARGER_RETURN_REASONS = {
    "no_charger_pose": "don't know where the charger is (haven't left it this session, or was picked up/reconnected since)",
    "already_on_charger": "already on the charger",
    "picked_up": "got picked up partway, so no longer know where the charger is",
    "pose_lost": "lost track of position partway (it reset), so no longer know where the charger is",
    "disconnected": "lost the connection to Cozmo partway",
    "timeout": "took too long getting there and stopped",
    "not_on_charger": "drove back and reversed to dock, but the charger contacts aren't reading as docked - may be misaligned",
    "jammed": "drove back, but got caught on the edge of the charger while reversing in (twisted off line) and stopped",
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


def leave_charger(robot: RobotBackend, settings: Settings) -> ToolResult:
    """Drive straight off the dock (the same CHARGER_EXIT_* drive any other
    movement does first when it starts docked - real.py's drive() handles the
    false cliff trigger, records where the charger is, and refuses while the
    battery's too low). Shared by the `leave_charger` tool and the timed
    charger break (charger_break.py)."""
    if not robot.is_on_charger():
        return ToolResult(True, "Already off the charger.")
    voltage = robot.get_battery_voltage()
    result = robot.drive(settings.charger_exit_distance_mm, settings.charger_exit_speed_mmps)
    if not result.moved:
        reading = f" ({voltage:.2f}V)" if voltage is not None else ""
        return ToolResult(
            True,
            f"Didn't leave the charger - the battery{reading} is too low to come off yet. "
            "Tell them out loud you can't come out yet because you need to charge more.",
            extra={"blocked_by_charger": True, **_ATTENTION},
        )
    if result.hazard:
        return ToolResult(
            True, f"Only made partway off the charger - {_HAZARD_MESSAGES[result.hazard]}.", extra=_ATTENTION
        )
    if robot.is_on_charger():
        return ToolResult(True, "Drove forward, but the charger contacts still read as docked.", extra=_ATTENTION)
    return ToolResult(True, "Drove off the charger.")


def _sanitize_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_")
    return cleaned.lower() or "unnamed"


def build_tools(
    robot: RobotBackend,
    speech: SpeechClient,
    ollama: ChatClient,
    settings: Settings,
    memory: Memory | None = None,
    presence: Presence | None = None,
    battery_log: BatteryLog | None = None,
    assistant: AssistantClient | None = None,
    assistant_relay: AssistantRelay | None = None,
) -> list[Tool]:
    """Every tool, plus the memory tools (memory_tools.py) when `memory` is
    given, and ask_assistant (assistant_tools.py) when `assistant` is -
    in the background when `assistant_relay` is given too.
    `presence` (people.py) is told who remember_person/who_is_this
    identify, so the system prompt can use their name."""
    def handle_say(text: str, mood: str = "neutral", gesture: str | None = None) -> ToolResult:
        # Seen on the robot: mood='thinking' (a clip name, not a mood) failed
        # the whole `say` before a word was spoken, costing a retry round
        # trip - the schema enum and the prompt's mood list don't stop it.
        # Like a bad gesture below, the speech matters more than the face:
        # a gesture/clip name in the mood slot moves to the empty gesture
        # slot, and anything else unknown falls back to neutral, with a
        # note so the model still learns.
        mood_note = ""
        if mood and mood.lower() not in MOODS:
            bad_mood = mood
            mood = "neutral"
            if not gesture and (bad_mood in GESTURES or bad_mood in model_clips()):
                gesture = bad_mood
                mood_note = f" ('{bad_mood}' is a gesture/clip, not a mood - used it as one, mood neutral)"
            else:
                mood_note = f" (unknown mood '{bad_mood}' - used neutral; valid moods: {', '.join(_MOOD_NAMES)})"
        gesture_arg = gesture
        # "neutral" now resets pose (see moods.py) rather than being a
        # no-op, so it must actually run, not be skipped like other moods
        # used to be for efficiency.
        applied_mood = robot.apply_mood(mood) if mood else None

        # A curated real clip, not a built-in gesture: it needs the speech
        # audio merged into its frames, so it starts once synthesis is done
        # (the mood above already shows at once) - see RobotBackend.say_wav_with_clip.
        clip_name = gesture if (gesture and gesture not in GESTURES and gesture in model_clips()) else None
        clip_note = ""
        if clip_name:
            gesture = None
            if not (settings.clips_enabled and robot.clips_available()):
                clip_name = None
                clip_note = f" (clip '{gesture_arg}' unavailable here - spoke without it)"

        # Bundled into this one call, rather than relying on the model
        # calling the separate `gesture` tool right before/after, because
        # that ordering is entirely up to the model and unreliable -
        # confirmed on real hardware: when it called `say` before
        # `gesture`, there was zero overlap (say() fully blocks on its own
        # audio regardless of gesture's async-ness), so speech and
        # movement never actually happened together despite gesture()
        # itself being non-blocking. This guarantees the order instead.
        def start_gesture() -> str:
            try:
                if settings.gesture_async_enabled:
                    description = robot.run_gesture_async(gesture)
                    return f" while performing '{gesture}' ({description})"
                # Same fallback GESTURE_ASYNC_ENABLED=false gives the
                # standalone `gesture` tool: fully sequential, gesture
                # finishes before speech starts either way, regardless of
                # GESTURE_SPEECH_SYNC_ENABLED below.
                description = robot.run_gesture(gesture)
                return f" after performing '{gesture}' ({description})"
            except ValueError as e:
                # Seen on the robot: gesture='embarrassed' (a mood-like word,
                # not in the enum) failed the whole `say` before a word was
                # spoken, costing a retry round trip. The speech matters more
                # than the flourish: skip the gesture and say so.
                return f" (no gesture: {e})"

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

        speak_on_robot = robot.say_wav
        clip_id = None
        if clip_name:
            clip_id = CURATED[clip_name].clip
            speak_on_robot = lambda path: robot.say_wav_with_clip(path, clip_id)  # noqa: E731
            description = CURATED[clip_name].description
            gesture_note = f" while playing the '{clip_name}' clip ({description})"
            if not settings.clip_speech_overlap:
                gesture_note = f" then playing the '{clip_name}' clip ({description})"

        output = settings.audio_output.lower()
        if output == "system":
            if clip_id and settings.clip_speech_overlap:
                # Speech comes out of the Pi's speaker, so nothing on the
                # robot's audio queue to collide with: just run the clip beside it.
                clip_thread = threading.Thread(target=robot.play_animation, args=(clip_id,))
                clip_thread.start()
                play_wav(wav_path, settings.playback_device)
                clip_thread.join()
            else:
                play_wav(wav_path, settings.playback_device)
                if clip_id:
                    robot.play_animation(clip_id)
        elif output == "both":
            # Simultaneous, not sequential — a background thread for system
            # audio while the main thread drives Cozmo's own speaker, then
            # wait for both so this call still blocks until speech is done.
            system_thread = threading.Thread(target=play_wav, args=(wav_path, settings.playback_device))
            system_thread.start()
            speak_on_robot(wav_path)
            system_thread.join()
        else:
            speak_on_robot(wav_path)

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

        return ToolResult(True, f"Said (mood={mood}){mood_note}{gesture_note}{clip_note}: {text}")

    def handle_gesture(name: str) -> ToolResult:
        if name not in GESTURES and name in model_clips():
            return handle_clip(name)
        if settings.gesture_async_enabled:
            description = robot.run_gesture_async(name)
            return ToolResult(True, f"Started gesture '{name}': {description}")
        description = robot.run_gesture(name)
        return ToolResult(True, f"Performed gesture '{name}': {description}")

    def handle_clip(name: str) -> ToolResult:
        """A curated real clip on its own (no speech). Always blocks until it
        is done, never in the background: it shares the robot's one audio
        queue with speech, so a `say` that follows would be cut off or delayed."""
        clip = CURATED[name]
        if clip.tier == "long":
            return ToolResult(
                False,
                f"'{name}' is a long clip (~{clip.seconds:.0f}s) and it is silent on its own - "
                f"use it as `say`'s gesture while you are saying something, not as a standalone gesture.",
            )
        if not (settings.clips_enabled and robot.clips_available()):
            return ToolResult(False, f"Animation clips aren't available right now, so '{name}' didn't play.")
        robot.play_animation(clip.clip)
        return ToolResult(True, f"Played the '{name}' clip: {clip.description}")

    def handle_play_animation(name: str) -> ToolResult:
        # Seen on real hardware: asked to dance, the model called
        # play_animation('group:dance') - a gesture's name, not a clip - got an
        # error, and spent two more LLM steps recovering. The intent is
        # obvious, so do it. Exact match: real clip names ("anim_...") and
        # group names (CamelCase) never equal a gesture name.
        gesture_name = name.removeprefix("group:")
        if gesture_name in model_clips() and gesture_name not in GESTURES:
            return handle_clip(gesture_name)
        if gesture_name in GESTURES:
            result = handle_gesture(gesture_name)
            return ToolResult(
                True, f"'{gesture_name}' is a gesture, not an animation clip - did it as a gesture. {result.message}"
            )
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
        if battery_log is not None:
            battery_log.note_return("dock_tool")
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
        if result.reason == "jammed":
            return ToolResult(
                True, "Got caught on the edge of the charger while backing in and stopped - not docked.", extra=_ATTENTION
            )
        return ToolResult(True, "Backed onto the charger to dock.")

    def handle_leave_charger() -> ToolResult:
        if battery_log is not None and robot.is_on_charger():
            battery_log.note_exit("asked")
        return leave_charger(robot, settings)

    def handle_look() -> ToolResult:
        os.makedirs(os.path.dirname(settings.camera_snapshot_path) or ".", exist_ok=True)
        path = robot.capture_photo(settings.camera_snapshot_path)
        return ToolResult(True, "Took a photo.", extra={"image_path": path})

    def handle_remember_person(name: str) -> ToolResult:
        os.makedirs(settings.known_people_dir, exist_ok=True)
        path = os.path.join(settings.known_people_dir, f"{_sanitize_name(name)}.jpg")
        robot.capture_photo(path)
        if presence is not None:
            presence.set_person(name)
        return ToolResult(True, f"Got it - I'll remember this face as {name}.")

    def handle_who_is_this() -> ToolResult:
        people = known_people(settings.known_people_dir)
        if not people:
            return ToolResult(True, "I don't know anyone yet — nobody has been introduced with remember_person.")

        os.makedirs(os.path.dirname(settings.camera_snapshot_path) or ".", exist_ok=True)
        new_path = robot.capture_photo(settings.camera_snapshot_path)
        extra = {"image_path": new_path, "image_caption": "[Cozmo just took a photo to see who this is.]"}

        name = find_match(ollama, people, encode_image_b64(new_path))
        if name:
            if presence is not None:
                presence.set_person(name)
            return ToolResult(True, f"This looks like it might be {name} (not certain — guess only).", extra=extra)
        return ToolResult(True, "I took a look, but I don't recognize this person.", extra=extra)

    # safe_to_end_turn=True marks pure actions only (see tools/base.py). The
    # information tools - list_animations, look, who_is_this - deliberately
    # leave it False: the model has to read their result, so a turn that
    # used one always goes back to the model, even if a `say` came last.
    # When adding a tool, leave it False unless its success result tells the
    # model nothing it needs.
    # Built-in gestures, plus the curated real clips when clips are on.
    gesture_names = list(_GESTURE_NAMES) + (sorted(model_clips()) if settings.clips_enabled else [])
    gesture_hint = (
        "Named clips: " + describe_for_prompt() + ". The long ones are for use only while you are speaking. "
        if settings.clips_enabled else ""
    )

    tools = [
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
                "other, never together. This is Cozmo's only voice: plain text outside a tool "
                "call is never heard. Put everything you want to say in this one call, and "
                "after your final `say`, reply with no text."
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
                        "enum": gesture_names,
                        "description": gesture_hint + "Optional — a gesture or named clip to perform at the same time as speaking.",
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
                        "enum": gesture_names,
                        "description": gesture_hint + "Which gesture or named clip to perform.",
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
                "names vary by robot and may be empty if animation assets aren't installed. "
                "Built-in gestures like dance or cheer are not animation clips: use the `gesture` "
                "tool (or `say`'s gesture) for those."
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
            name="leave_charger",
            description=(
                "Come out of the charger: drives straight off the dock a short, safe distance. "
                "Use this whenever asked to come out, leave the charger, get off the dock, or "
                "give the charger/battery a rest - not drive(). It works whenever the battery "
                "status says it's OK to leave; the result says if the battery is too low. Once "
                "out, Cozmo stays out until the battery gets low or he's asked to dock."
            ),
            parameters={"type": "object", "properties": {}},
            handler=lambda _args: handle_leave_charger(),
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
    if not settings.clips_enabled:
        # No real clips at all: the model must not be offered the clip tools.
        tools = [t for t in tools if t.name not in ("play_animation", "list_animations")]
    if memory is not None:
        tools.extend(build_memory_tools(memory))
    if assistant is not None:
        tools.extend(build_assistant_tools(assistant, assistant_relay))
    return tools
