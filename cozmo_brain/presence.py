"""Who is Cozmo talking to? An occasional, quiet look at the person in front
of him, so he can use their name - and, when it's a stranger, ask who they
are the way a curious pet would, once in a while.

PresenceChecker.run() is called by idle_fidget.py in place of a fidget (so it
only ever happens after IDLE_FIDGET_AFTER_S of quiet, never mid-conversation,
and holds turn_lock like a fidget does). It tilts the head up (Cozmo sits on
the desk looking at the chair from below), takes one photo, and asks the
vision model two things: is a real face visible, and is it one of the
reference photos from remember_person. The photo is deleted straight after.

Outcomes, deliberately low-key:
  - nobody there: stay silent, don't look again for PRESENCE_EMPTY_RETRY_S
  - a known person: stay silent, just remember who it is (Presence ->
    system prompt) so the model can use their name; recheck after
    PRESENCE_RECHECK_S
  - a stranger: say one casual line asking who they are, at most once per
    PRESENCE_ASK_COOLDOWN_S, and leave a note for the model on how to follow up

Recognition is the vision model comparing blurry photos, so every match is a
guess - the prompt section says so, and a wrong guess is corrected by the
person saying who they are.
"""

from __future__ import annotations

import logging
import os
import random
import time

from cozmo_brain.config import Settings
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.imaging import encode_image_b64
from cozmo_brain.llm.chat_client import ChatClient
from cozmo_brain.people import Presence, face_visible, find_match, known_people
from cozmo_brain.robot.base import RobotBackend

logger = logging.getLogger(__name__)

_REST_HEAD_DEG = 0.0  # the "neutral" mood's head angle (robot/moods.py)

# Fixed lines rather than model-generated ones: the check runs unprompted, and
# a fixed line is fast, cheap and can't wander off-script.
_STRANGER_LINES = (
    "Oh, hello! I don't think we've met. I'm Cozmo - who are you?",
    "Hi there! I don't think I know you yet. What's your name?",
    "Well hello! New face. I'm Cozmo - and you are?",
)


class PresenceChecker:
    def __init__(
        self, robot: RobotBackend, engine: CozmoEngine, chat: ChatClient, settings: Settings, presence: Presence
    ):
        self._robot = robot
        self._engine = engine
        self._chat = chat
        self._settings = settings
        self._presence = presence
        self._next_check = 0.0  # monotonic; 0 = due as soon as Cozmo is idle

    def due(self) -> bool:
        return self._settings.presence_check_enabled and time.monotonic() >= self._next_check

    def run(self) -> None:
        """One look. Caller holds engine.turn_lock. Never raises."""
        s = self._settings
        retry_s = s.presence_empty_retry_s
        try:
            if self._robot.is_picked_up():
                self._next_check = time.monotonic() + retry_s
                return
            people = known_people(s.known_people_dir)
            photo_b64 = self._capture()
            if not face_visible(self._chat, photo_b64):
                logger.info("Presence: nobody in view.")
                self._presence.clear()
                self._next_check = time.monotonic() + retry_s
                return
            name = find_match(self._chat, people, photo_b64)
            if name:
                logger.info("Presence: looks like %s.", name)
                self._presence.set_person(name)
                self._next_check = time.monotonic() + s.presence_recheck_s
                return
            logger.info("Presence: a face I don't recognise.")
            self._presence.set_stranger()
            self._next_check = time.monotonic() + s.presence_ask_cooldown_s
            self._ask_who([n for n, _ in people])
        except Exception as e:  # noqa: BLE001 - a background look must never crash the process
            logger.warning("Presence check failed: %s", e)
            self._next_check = time.monotonic() + retry_s

    def _capture(self) -> str:
        """Photo of the chair, as base64. The file is deleted straight away -
        nothing from this unprompted look is kept on disk."""
        path = os.path.join(os.path.dirname(self._settings.camera_snapshot_path) or ".", "presence.png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            self._robot.set_head_angle_deg(self._settings.presence_head_angle_deg, duration=0.6)
            self._robot.capture_photo(path)
            return encode_image_b64(path)
        finally:
            try:
                self._robot.set_head_angle_deg(_REST_HEAD_DEG)
            except Exception as e:  # noqa: BLE001 - still delete the photo below
                logger.debug("Couldn't lower the head after a presence look: %s", e)
            if os.path.exists(path):
                os.remove(path)

    def _ask_who(self, known_names: list[str]) -> None:
        line = random.choice(_STRANGER_LINES)
        with self._robot.keep_backpack_light():
            self._engine.speak(line, mood="curious")
        others = (
            f" Known faces: {', '.join(known_names)}. If they aren't one of those and the new person has "
            f"told you their name, you can casually ask where {known_names[0]} is - once, as small talk."
            if known_names
            else ""
        )
        self._engine.add_note(
            f'[I noticed an unfamiliar face and said out loud, on my own: "{line}" If they tell me '
            f"their name, call remember_person with it (they need to be facing me) and greet them by name. "
            f"If they brush it off, drop it - never push.{others}]"
        )
