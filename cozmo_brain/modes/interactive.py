"""Push-to-talk voice mode: press Enter, speak, Cozmo replies. The original
orchestrator.py's flow, rebuilt on the engine/tools/conversation stack."""

from __future__ import annotations

import logging

from cozmo_brain.audio.recorder import record_fixed
from cozmo_brain.config import Settings
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.llm.speech_client import SpeechClient

logger = logging.getLogger(__name__)


def run(engine: CozmoEngine, speech: SpeechClient, settings: Settings) -> None:
    print("Press Enter to talk, or type 'quit' to exit.\n")
    while True:
        cmd = input("[Enter=talk, quit=exit] > ").strip().lower()
        if cmd == "quit":
            break

        record_fixed(settings.raw_input_wav, settings.record_seconds, settings.record_device)
        text = speech.transcribe(settings.raw_input_wav)
        print(f"You said: {text}")
        if not text:
            print("(heard nothing, try again)\n")
            continue

        summary = engine.handle_turn(text)
        print(summary, "\n")
