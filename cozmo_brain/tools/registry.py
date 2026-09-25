"""The concrete tool set exposed to the LLM, bound to a robot backend + a
speech (STT/TTS) client — whichever provider AUDIO_PROVIDER selects.

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
from cozmo_brain.llm.ollama_client import OllamaClient
from cozmo_brain.llm.speech_client import SpeechClient
from cozmo_brain.robot.base import RobotBackend
from cozmo_brain.robot.gestures import GESTURES
from cozmo_brain.robot.moods import MOODS
from cozmo_brain.tools.base import Tool, ToolResult

_MOOD_NAMES = sorted(MOODS)
_GESTURE_NAMES = sorted(GESTURES)

_KNOWN_PHOTO_EXTS = (".jpg", ".jpeg", ".png")

_COMPARE_FACES_PROMPT = (
    "You are comparing two photos taken by a low-resolution, often blurry robot "
    "camera with a warm/reddish color cast — ignore lighting and color entirely "
    "and judge only facial structure. The first photo is a reference photo of a "
    "person named {name}. The second photo was just taken. Is the person in the "
    "second photo the same person as in the first photo? Reply with exactly one "
    "word: yes or no."
)


def _sanitize_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_")
    return cleaned.lower() or "unnamed"


def build_tools(robot: RobotBackend, speech: SpeechClient, ollama: OllamaClient, settings: Settings) -> list[Tool]:
    def handle_say(text: str, mood: str = "neutral") -> ToolResult:
        # "neutral" now resets pose (see moods.py) rather than being a
        # no-op, so it must actually run, not be skipped like other moods
        # used to be for efficiency.
        if mood:
            robot.apply_mood(mood)
        wav_path = speech.synthesize(text, settings.tts_output_wav)

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

        return ToolResult(True, f"Said (mood={mood}): {text}")

    def handle_gesture(name: str) -> ToolResult:
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
        robot.drive(distance_mm, speed_mmps)
        return ToolResult(True, f"Drove {distance_mm:.0f}mm at {speed_mmps:.0f}mm/s.")

    def handle_turn(angle_degrees: float) -> ToolResult:
        robot.turn(angle_degrees)
        return ToolResult(True, f"Turned {angle_degrees:.0f} degrees.")

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

    return [
        Tool(
            name="say",
            description=(
                "Speak a short sentence out loud through Cozmo's speaker, with an optional "
                "mood that sets Cozmo's face and backpack lights while speaking. Use this for "
                "almost every turn — Cozmo should talk, not just act silently."
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
                },
                "required": ["text"],
            },
            handler=lambda args: handle_say(args["text"], args.get("mood", "neutral")),
        ),
        Tool(
            name="gesture",
            description=(
                "Perform a physical gesture: a short choreographed combination of face, "
                "lights, head/arm movement, and driving. Use this to be funny and expressive "
                "alongside or instead of speaking."
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
