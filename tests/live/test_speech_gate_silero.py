# LIVE TEST - needs faster-whisper installed (it provides the Silero model;
# on the Pi it already is) and an OpenAI key in .env (one TTS request per
# spoken test word). Not run by tests/run_all.py - run it yourself from the
# repo root:
#   python tests/live/test_speech_gate_silero.py
"""Live: the real Silero speech gate (cozmo_brain/audio/speech_gate.py) on
real spoken words - including short replies and a quiet one - and on
synthetic noise, with your .env's SPEECH_GATE_* settings. Also prints how
long each check takes on this machine (run it on the Pi to see Pi timing)."""
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

from cozmo_brain.audio import speech_gate
from cozmo_brain.config import Settings

logging.basicConfig(level=logging.INFO, format="      %(message)s")
logging.getLogger("urllib3").setLevel(logging.WARNING)
S = Settings()
OUT = Path(tempfile.gettempdir()) / "smartcozmo_gate_clips"
OUT.mkdir(exist_ok=True)
rng = np.random.default_rng(3)


def save(name, samples):
    samples = np.clip(samples, -32768, 32767).astype(np.int16)
    path = OUT / f"{name}.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(samples.tobytes())
    return path


def spoken(text, gain=1.0):
    r = requests.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {S.require_openai_key()}"},
        json={"model": "gpt-4o-mini-tts", "voice": "alloy", "input": text, "response_format": "pcm"},
        timeout=60,
    )
    r.raise_for_status()
    p24 = np.frombuffer(r.content, dtype=np.int16).astype(float)
    pcm = np.interp(np.linspace(0, len(p24) - 1, int(len(p24) * 16000 / 24000)), np.arange(len(p24)), p24) * gain
    return np.concatenate([rng.normal(0, 60, 1600), pcm, rng.normal(0, 60, 3200)])


if speech_gate._load() is False:
    sys.exit("faster-whisper isn't installed here - nothing to test (pip install faster-whisper).")

speech = {
    "sentence": spoken("Hey Cozmo, can you turn left and then go back to your charger please?"),
    "yes": spoken("Yes."),
    "no": spoken("No."),
    "okay": spoken("Okay."),
    "yes_quiet (25% volume)": spoken("Yes.", gain=0.25),
}
t = np.arange(16000) / 16000
burst = np.zeros(24000)
for s0 in (3000, 9000, 15000):
    burst[s0:s0 + 800] = rng.normal(0, 3000, 800) * np.hanning(800)
noise = {
    "room hiss": rng.normal(0, 80, 24000),
    "clatter": burst + rng.normal(0, 60, 24000),
    "motor hum": (1200 * np.sin(2 * math.pi * 180 * t) + 600 * np.sin(2 * math.pi * 360 * t + 1)) * (0.7 + 0.3 * np.sin(2 * math.pi * 3 * t)),
}

speech_gate.speech_seconds(str(save("warmup", np.zeros(16000))), S.speech_gate_threshold)  # model load
wrong = []
for label, clips, expect in (("speech", speech, True), ("noise", noise, False)):
    for name, samples in clips.items():
        path = save(name.split(" ")[0], samples)
        t0 = time.perf_counter()
        secs = speech_gate.speech_seconds(str(path), S.speech_gate_threshold)
        ms = (time.perf_counter() - t0) * 1000
        sent = speech_gate.clip_has_speech(str(path), S)
        print(f"  {label:6s} {name:24s} speech={secs:.2f}s  check={ms:5.1f}ms  -> {'SENT' if sent else 'not sent'}")
        if sent != expect:
            wrong.append(name)
print("\nRESULT:", "all as expected (every word sent, all noise blocked)" if not wrong else f"WRONG: {wrong}")
sys.exit(1 if wrong else 0)
