"""Test: Whisper hallucination defenses - punctuation-only text, per-segment
confidence filtering, request fields, all three Whisper clients, and the
trimmed silence tail on captured clips. Segment scores below are the real
values measured live on 2026-09-28 (see tests/live/test_stt_hallucination.py)."""
import dataclasses
import io
import logging
import math
import struct
import sys
import types
import wave

import _harness  # noqa: F401 - repo on sys.path, real .env ignored

from cozmo_brain.config import Settings
from cozmo_brain.llm import stt_postprocess as sp

logging.disable(logging.CRITICAL)
failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: failures.append(name)

S = Settings()

# --- punctuation-only / known phrases ---------------------------------------
for text in (".", "...", " , ", "?!", ""):
    check(f"no letters -> treated as not speech: {text!r}", sp.is_likely_hallucination(text))
check("known hallucination still caught: 'Thank you.'", sp.is_likely_hallucination("Thank you."))
check("real short reply kept: 'yes'", not sp.is_likely_hallucination("yes"))
check("real sentence kept", not sp.is_likely_hallucination("Turn left please."))

# --- per-segment filter, with the real measured scores ------------------------
seg = lambda text, nsp, lp, cr: {"text": text, "no_speech_prob": nsp, "avg_logprob": lp, "compression_ratio": cr}
openai_noise = [seg("you", 0.95, -0.93, 0.27), seg("You", 0.93, -0.96, 0.27), seg("you", 0.97, -0.75, 0.27)]
for s_ in openai_noise:
    check(f"OpenAI noise dropped by no_speech ({s_['no_speech_prob']})", sp.filter_whisper_segments([s_], S) == "")
check("OpenAI speech kept (nsp 0.00, lp -0.32)", sp.filter_whisper_segments([seg("Hey Cozmo, turn left", 0.0, -0.32, 0.96)], S) == "Hey Cozmo, turn left")
check("Groq speech kept (nsp 0.00, lp -0.12)", sp.filter_whisper_segments([seg("Hey Cozmo", 0.0, -0.12, 0.97)], S) == "Hey Cozmo")
check("Groq 'Thank you.' passes the default filter (Groq reports nsp 0.00) - left to the phrase filter",
      sp.filter_whisper_segments([seg("Thank you.", 0.0, -0.57, 0.58)], S) == "Thank you.")
tight = dataclasses.replace(S, stt_min_avg_logprob=-0.5)
check("a tightened STT_MIN_AVG_LOGPROB (-0.5) drops Groq's 'Thank you.' (-0.57) but keeps its speech (-0.12)",
      sp.filter_whisper_segments([seg("Thank you.", 0.0, -0.57, 0.58)], tight) == ""
      and sp.filter_whisper_segments([seg("Hey Cozmo", 0.0, -0.12, 0.97)], tight) == "Hey Cozmo")
check("repetitive loop dropped (compression 3.1)", sp.filter_whisper_segments([seg("thank you " * 12, 0.0, -0.2, 3.1)], S) == "")
check("mixed clip: noise segment dropped, speech segment kept",
      sp.filter_whisper_segments([seg("you", 0.95, -0.93, 0.27), seg("turn left", 0.01, -0.2, 0.9)], S) == "turn left")
check("missing scores never cause a drop", sp.filter_whisper_segments([{"text": "hello there"}], S) == "hello there")

# --- request fields / response parsing --------------------------------------
check("whisper-1: language + verbose_json", sp.whisper_request_fields("whisper-1", S) == {"language": "en", "response_format": "verbose_json"})
check("Groq whisper-large-v3-turbo: language + verbose_json", sp.whisper_request_fields("whisper-large-v3-turbo", S) == {"language": "en", "response_format": "verbose_json"})
check("gpt-4o-transcribe: language only (it rejects verbose_json)", sp.whisper_request_fields("gpt-4o-transcribe", S) == {"language": "en"})
check("STT_LANGUAGE empty -> no language hint", "language" not in sp.whisper_request_fields("whisper-1", dataclasses.replace(S, stt_language="")))
check("response with segments -> filtered", sp.text_from_whisper_response({"text": "you", "segments": [seg("you", 0.95, -0.9, 0.3)]}, S) == "")
check("plain-text response (non-Whisper model) -> text as-is", sp.text_from_whisper_response({"text": " Hello "}, S) == "Hello")

# --- the three clients --------------------------------------------------------
wav_path = _harness.tmp("stt.wav")
with wave.open(wav_path, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 1600)

class FakeResp:
    def __init__(self, body): self._b = body
    def raise_for_status(self): pass
    def json(self): return self._b

class FakeSession:
    def __init__(self, body): self.body, self.sent = body, None
    def post(self, url, headers=None, files=None, data=None, timeout=None):
        self.sent = data; return FakeResp(self.body)

from cozmo_brain.llm.groq_client import GroqClient
from cozmo_brain.llm.openai_client import OpenAIClient
KEYED = dataclasses.replace(S, groq_api_key="x", openai_api_key="x")
for name, cls in (("groq", GroqClient), ("openai", OpenAIClient)):
    client = cls(KEYED)
    client._session = FakeSession({"text": "you", "segments": [seg("you", 0.95, -0.9, 0.3)]})
    out = client.transcribe(wav_path)
    check(f"{name}: sends language + verbose_json, filters the noise segment (got {out!r}, sent {client._session.sent})",
          out == "" and client._session.sent.get("language") == "en" and client._session.sent.get("response_format") == "verbose_json")

from cozmo_brain.llm.local_client import LocalClient
calls = {}
class FakeWhisper:
    def transcribe(self, path, **kw):
        calls.update(kw)
        return iter([types.SimpleNamespace(text=" turn left", no_speech_prob=0.02, avg_logprob=-0.2, compression_ratio=1.0),
                     types.SimpleNamespace(text=" you", no_speech_prob=0.9, avg_logprob=-0.9, compression_ratio=0.3)]), None
local = LocalClient(S)
local._whisper_model = FakeWhisper()
out = local.transcribe(wav_path)
check(f"local: vad_filter on, language passed, noise segment dropped (got {out!r}, kwargs {calls})",
      out == "turn left" and calls.get("vad_filter") is True and calls.get("language") == "en")

# --- trimmed silence tail on captured clips ---------------------------------
fake = types.ModuleType("webrtcvad")
class Vad:
    def __init__(self, a): pass
    def is_speech(self, frame, rate): return any(frame)
fake.Vad = Vad
sys.modules["webrtcvad"] = fake
import cozmo_brain.audio.vad as vad
F = vad._FRAME_BYTES
tone = lambda n: b"".join(struct.pack("<h", int(3000 * math.sin(i / 3))) for i in range(n * F // 2))
class P:
    def __init__(self, d): self.stdout = io.BytesIO(d)
    def terminate(self): pass
    def wait(self, timeout=None): pass
    def poll(self): return 0
vad.subprocess.Popen = lambda *a, **k: P(tone(33) + b"\0" * F * 60)   # ~1s speech, then silence
clip = _harness.tmp("trim.wav")
vad.record_until_silence(clip, "dev", 2, 800, 15, 60, 200)
with wave.open(clip, "rb") as w:
    secs = w.getnframes() / 16000
check(f"clip sent with ~200ms of trailing silence, not the full 800ms ({secs:.2f}s for ~1s of speech)", 1.1 < secs < 1.3)

print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
