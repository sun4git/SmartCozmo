"""Hands-free voice mode: Cozmo listens continuously and reacts whenever you
say the wake word, using on-device wake-word detection (openWakeWord) gating
voice activity detection instead of a push-to-talk key. Requires
`pip install webrtcvad` and openwakeword (see cozmo_brain/audio/wakeword.py
for the install command — it's not a plain `pip install openwakeword`).

After the wake word fires, the conversation stays open for follow-up turns
(VAD_FOLLOWUP_TIMEOUT_S, resets each turn) without repeating it — once
nothing is heard within that window, the wake word is required again.

Also gives a physical "I heard you" cue on the robot itself (curious face +
light + head up on wake, back to neutral when the window closes) — this
mode is meant to be hands-free, so a terminal print alone isn't a real
indicator of anything."""

from __future__ import annotations

import logging

from cozmo_brain.audio.vad import record_until_silence
from cozmo_brain.audio.wakeword import wait_for_wake_word
from cozmo_brain.config import Settings
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.llm.speech_client import SpeechClient
from cozmo_brain.robot.base import RobotBackend

logger = logging.getLogger(__name__)


def _apply_mood_safely(robot: RobotBackend, mood: str) -> None:
    try:
        robot.apply_mood(mood)
    except Exception as e:  # noqa: BLE001 - a listening-indicator hiccup shouldn't kill this loop
        logger.warning("Could not show '%s' mood: %s", mood, e)


def run(engine: CozmoEngine, robot: RobotBackend, speech: SpeechClient, settings: Settings) -> None:
    print(f"Listening for the wake word ('{settings.wake_word_model}'). Press Ctrl+C to exit.\n")
    while True:
        wait_for_wake_word(settings.record_device, settings.wake_word_model, settings.wake_word_threshold)
        print("Wake word heard - listening...")
        _apply_mood_safely(robot, "curious")

        # The first capture after the wake word uses the normal per-utterance
        # cap; every capture after that is a "follow-up" and uses the (often
        # longer) follow-up window instead, so the conversation can continue
        # without repeating the wake word until the human actually goes quiet.
        listen_timeout = settings.vad_max_utterance_s

        while True:
            got_speech = record_until_silence(
                settings.raw_input_wav,
                settings.record_device,
                settings.vad_aggressiveness,
                settings.vad_silence_ms,
                listen_timeout,
            )
            if not got_speech:
                print("(no follow-up heard - wake word needed again)\n")
                _apply_mood_safely(robot, "neutral")
                break

            text = speech.transcribe(settings.raw_input_wav)
            if not text:
                continue  # heard something, but nothing transcribable - keep the conversation open
            print(f"You said: {text}")

            summary = engine.handle_turn(text)
            print(summary, "\n")

            listen_timeout = settings.vad_followup_timeout_s
