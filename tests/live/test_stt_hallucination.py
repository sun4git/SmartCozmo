# LIVE TEST - makes real API calls with the keys in your .env (one OpenAI TTS
# request to make the speech clip, then one transcription per clip per
# provider; Groq's free tier can rate-limit with 429s). Not run by
# tests/run_all.py - run it yourself from the repo root, e.g.:
#   python tests/live/test_stt_hallucination.py [groq] [openai] [witai]
"""Live: what each STT provider returns for real speech vs silence/noise,
through the project's own STT clients (so STT_LANGUAGE and the STT_*
confidence filters in your .env apply), and whether the result would reach
the LLM or be ignored. Speech: an OpenAI TTS sentence resampled to 16kHz
mono like the Pi's mic. Noise: room hiss, clatter bursts, motor hum -
synthetic stand-ins for what Cozmo's mic actually picks up."""
import logging
import math
import os
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import numpy as np
import requests

from cozmo_brain.config import Settings
from cozmo_brain.llm.stt_postprocess import is_likely_hallucination

logging.basicConfig(level=logging.INFO, format="      %(message)s")
logging.getLogger("urllib3").setLevel(logging.WARNING)
S = Settings()
OUT = Path(tempfile.gettempdir()) / "smartcozmo_stt_clips"
OUT.mkdir(exist_ok=True)
rng = np.random.default_rng(1)


def save(name, samples):
    samples = np.clip(samples, -32768, 32767).astype(np.int16)
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(samples.tobytes())


def make_clips():
    r = requests.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {S.require_openai_key()}"},
        json={"model": "gpt-4o-mini-tts", "voice": "alloy", "response_format": "pcm",
              "input": "Hey Cozmo, can you turn left and then go back to your charger please?"},
        timeout=60,
    )
    r.raise_for_status()
    pcm24 = np.frombuffer(r.content, dtype=np.int16).astype(float)
    pcm = np.interp(np.linspace(0, len(pcm24) - 1, int(len(pcm24) * 16000 / 24000)), np.arange(len(pcm24)), pcm24)
    room = lambda n, lvl: rng.normal(0, lvl, n)
    tail = int(0.2 * 16000)
    save("speech", np.concatenate([pcm, np.zeros(tail)]) + room(len(pcm) + tail, 60))
    save("room_hiss", room(24000, 80))
    burst = np.zeros(24000)
    for s0 in (3000, 9000, 15000):
        burst[s0:s0 + 800] = rng.normal(0, 3000, 800) * np.hanning(800)
    save("clatter", burst + room(24000, 60))
    t = np.arange(16000) / 16000
    hum = 1200 * np.sin(2 * math.pi * 180 * t) + 600 * np.sin(2 * math.pi * 360 * t + 1) + 300 * np.sin(2 * math.pi * 540 * t)
    save("motor_hum", hum * (0.7 + 0.3 * np.sin(2 * math.pi * 3 * t)) + room(16000, 60))


def client_for(provider):
    if provider == "groq":
        from cozmo_brain.llm.groq_client import GroqClient
        return GroqClient(S)
    if provider == "openai":
        from cozmo_brain.llm.openai_client import OpenAIClient
        return OpenAIClient(S)
    from cozmo_brain.llm.witai_client import WitAIClient
    return WitAIClient(S)


make_clips()
bad = []
for provider in sys.argv[1:] or ["groq", "openai", "witai"]:
    print(f"===== {provider}")
    try:
        client = client_for(provider)
    except Exception as e:  # missing key etc.
        print(f"  SKIPPED - {e}")
        continue
    for clip in ("speech", "room_hiss", "clatter", "motor_hum"):
        text = None
        for _ in range(3):
            try:
                text = client.transcribe(str(OUT / f"{clip}.wav"))
                break
            except requests.RequestException as e:
                print(f"  (retrying after: {e})")
                time.sleep(4)
        ignored = text is not None and (not text or is_likely_hallucination(text))
        verdict = "IGNORED" if ignored else "-> would reach the LLM"
        print(f"  {clip:10s} {text!r:70.70s} {verdict}")
        if (clip == "speech") == ignored:
            bad.append(f"{provider}/{clip}")
        if provider == "groq":
            time.sleep(3.5)  # stay under Groq's free-tier rate limit
print("\nRESULT:", "all as expected (speech through, noise ignored)" if not bad else f"unexpected: {bad}")
