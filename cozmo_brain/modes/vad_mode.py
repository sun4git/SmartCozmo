"""Hands-free voice mode: Cozmo listens continuously and reacts whenever you
say the wake word OR gently tap Cozmo's body, using on-device wake-word
detection (openWakeWord) gating voice activity detection instead of a
push-to-talk key. Requires `pip install webrtcvad` and openwakeword (see
cozmo_brain/audio/wakeword.py for the install command — it's not a plain
`pip install openwakeword`). The tap trigger needs no extra dependency —
it's RobotBackend.wait_for_tap(), backed by a real accelerometer spike on
the real backend (see robot/real.py).

After either trigger fires, the conversation stays open for follow-up turns
(VAD_FOLLOWUP_TIMEOUT_S, resets each turn) without repeating it — once
nothing is heard within that window, a wake word or tap is required again.

Also gives a physical "I heard you" cue on the robot itself (curious face +
light + head up on wake, back to neutral when the window closes) — this
mode is meant to be hands-free, so a terminal print alone isn't a real
indicator of anything."""

from __future__ import annotations

import logging
import threading

from cozmo_brain.audio.vad import record_until_silence
from cozmo_brain.audio.wakeword import wait_for_wake_word
from cozmo_brain.config import Settings
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.llm.speech_client import SpeechClient
from cozmo_brain.llm.stt_postprocess import is_likely_hallucination
from cozmo_brain.robot.base import RobotBackend

logger = logging.getLogger(__name__)


def _apply_mood_safely(robot: RobotBackend, mood: str) -> None:
    try:
        robot.apply_mood(mood)
    except Exception as e:  # noqa: BLE001 - a listening-indicator hiccup shouldn't kill this loop
        logger.warning("Could not show '%s' mood: %s", mood, e)


def _wait_for_wake_word_or_tap(robot: RobotBackend, settings: Settings) -> None:
    """Blocks until either the wake word is heard or Cozmo is tapped,
    whichever comes first. Races the two on separate threads (wake-word
    listening owns a mic subprocess + model inference, so it's given a
    cancel signal rather than just abandoned once a tap wins)."""
    stop_wake_word = threading.Event()
    wake_word_done = threading.Event()

    def _listen_for_wake_word() -> None:
        wait_for_wake_word(
            settings.record_device, settings.wake_word_model, settings.wake_word_threshold, stop_wake_word
        )
        wake_word_done.set()

    thread = threading.Thread(target=_listen_for_wake_word, daemon=True)
    thread.start()

    while not wake_word_done.is_set():
        if robot.wait_for_tap(timeout=0.2):
            stop_wake_word.set()
            thread.join(timeout=2)
            return

    thread.join()


def run(engine: CozmoEngine, robot: RobotBackend, speech: SpeechClient, settings: Settings) -> None:
    print(
        f"Listening for the wake word ('{settings.wake_word_model}') or a tap. "
        "Press Ctrl+C to exit.\n"
    )
    while True:
        _wait_for_wake_word_or_tap(robot, settings)
        print("Wake word or tap heard - listening...")
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
                settings.vad_min_speech_ms,
                settings.vad_min_rms,
            )
            if not got_speech:
                print("(no follow-up heard - wake word needed again)\n")
                _apply_mood_safely(robot, "neutral")
                break

            text = speech.transcribe(settings.raw_input_wav)
            if not text:
                continue  # heard something, but nothing transcribable - keep the conversation open
            print(f"You said: {text}")
            if is_likely_hallucination(text):
                print("(that's a known Whisper artifact from background noise, not real speech - ignoring)\n")
                continue

            summary = engine.handle_turn(text)
            print(summary, "\n")

            listen_timeout = settings.vad_followup_timeout_s
