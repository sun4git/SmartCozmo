"""Hands-free voice mode: Cozmo listens continuously and reacts whenever you
speak, using voice activity detection instead of a push-to-talk key. Requires
`pip install webrtcvad` on the deployment machine."""

from __future__ import annotations

import logging

from cozmo_brain.audio.vad import record_until_silence
from cozmo_brain.config import Settings
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.llm.groq_client import GroqClient

logger = logging.getLogger(__name__)


def run(engine: CozmoEngine, groq: GroqClient, settings: Settings) -> None:
    print("Listening continuously (hands-free). Press Ctrl+C to exit.\n")
    while True:
        got_speech = record_until_silence(
            settings.raw_input_wav,
            settings.record_device,
            settings.vad_aggressiveness,
            settings.vad_silence_ms,
            settings.vad_max_utterance_s,
        )
        if not got_speech:
            continue

        text = groq.transcribe(settings.raw_input_wav)
        if not text:
            continue
        print(f"You said: {text}")

        summary = engine.handle_turn(text)
        print(summary, "\n")
