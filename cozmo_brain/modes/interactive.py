"""Push-to-talk voice mode: press Enter, speak, Cozmo replies. The original
orchestrator.py's flow, rebuilt on the engine/tools/conversation stack."""

from __future__ import annotations

import logging

import requests

from cozmo_brain.audio.recorder import record_fixed
from cozmo_brain.audio.speech_gate import clip_has_speech
from cozmo_brain.audio.speech_gate import warm_up as speech_gate_warm_up
from cozmo_brain.config import Settings
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.llm.speech_client import SpeechClient
from cozmo_brain.llm.stt_postprocess import is_likely_hallucination

logger = logging.getLogger(__name__)


def run(engine: CozmoEngine, speech: SpeechClient, settings: Settings) -> None:
    print("Press Enter to talk, or type 'quit' to exit.\n")
    speech_gate_warm_up(settings)
    while True:
        cmd = input("[Enter=talk, quit=exit] > ").strip().lower()
        if cmd == "quit":
            break

        record_fixed(settings.raw_input_wav, settings.record_seconds, settings.record_device)
        # A fixed-length recording is sent even if nothing was said - exactly
        # what Whisper hallucinates on. Same neural check as --mode vad.
        if not clip_has_speech(settings.raw_input_wav, settings):
            print("(heard no speech, try again)\n")
            continue
        try:
            text = speech.transcribe(settings.raw_input_wav)
        except requests.RequestException as e:
            # Same fix as vad_mode.py's - an STT request failure used to
            # crash the whole process. e.response.text carries the
            # provider's actual error detail, which raise_for_status()
            # alone discards.
            detail = e.response.text.strip() if e.response is not None else str(e)
            logger.warning("Speech-to-text request failed: %s", detail)
            print(f"(speech-to-text request failed, try again: {detail})\n")
            continue
        print(f"You said: {text}")
        if not text:
            print("(heard nothing, try again)\n")
            continue
        if is_likely_hallucination(text):
            print("(that's a known Whisper artifact from background noise, not real speech - try again)\n")
            continue

        summary = engine.handle_turn(text)
        print(summary, "\n")
