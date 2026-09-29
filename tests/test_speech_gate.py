"""Test: the Silero speech gate (audio/speech_gate.py) - decisions, the
"faster-whisper not installed" fallback, and that --mode vad / push-to-talk
never call STT for a clip the gate rejects. Uses a fake faster_whisper.vad;
tests/live/test_speech_gate_silero.py runs the real model."""
import dataclasses
import logging
import sys
import types
import wave

import _harness  # noqa: F401 - repo on sys.path, real .env ignored

from cozmo_brain.config import Settings

failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: failures.append(name)

records = []
logging.getLogger("cozmo_brain.audio.speech_gate").addHandler(
    type("H", (logging.Handler,), {"emit": lambda s, r: records.append(r.getMessage())})())
logging.getLogger("cozmo_brain.audio.speech_gate").setLevel(logging.INFO)

wav = _harness.tmp("gate.wav")
with wave.open(wav, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 16000)

S = Settings()

# --- faster-whisper not installed: skipped, everything sent, one warning ------
import cozmo_brain.audio.speech_gate as gate
sys.modules["faster_whisper"] = None          # makes `import faster_whisper...` raise ImportError
sys.modules["faster_whisper.vad"] = None
gate._vad = None
check("not installed: clip is still sent", gate.clip_has_speech(wav, S))
check("not installed: sent again, no crash", gate.clip_has_speech(wav, S))
check("not installed: warned exactly once", sum("unavailable" in m for m in records) == 1)

# --- a fake Silero returning chosen speech timestamps ------------------------
detected = {"samples": 0}
seen_opts = {}
fake_vad = types.ModuleType("faster_whisper.vad")
@dataclasses.dataclass
class VadOptions:
    threshold: float = 0.5
    min_speech_duration_ms: int = 0
    min_silence_duration_ms: int = 2000
    speech_pad_ms: int = 400
def get_speech_timestamps(audio, vad_options=None, sampling_rate=16000):
    seen_opts["o"] = vad_options
    n = detected["samples"]
    return [{"start": 0, "end": n}] if n else []
fake_vad.VadOptions, fake_vad.get_speech_timestamps = VadOptions, get_speech_timestamps
sys.modules["faster_whisper"] = types.ModuleType("faster_whisper")
sys.modules["faster_whisper.vad"] = fake_vad
gate._vad = None

for secs, expect in [(0.0, False), (0.03, False), (0.14, False), (0.15, True), (0.45, True), (3.7, True)]:
    detected["samples"] = int(secs * 16000)
    check(f"{secs:.2f}s of speech -> {'sent' if expect else 'not sent'} (min 150ms)", gate.clip_has_speech(wav, S) == expect)
check("speech padding disabled so short words aren't inflated", seen_opts["o"].speech_pad_ms == 0)
check("Silero threshold comes from SPEECH_GATE_THRESHOLD", seen_opts["o"].threshold == S.speech_gate_threshold)
check("every decision is logged with the measured seconds", any("Speech gate (Silero): 0.45s" in m for m in records))

detected["samples"] = 0
check("SPEECH_GATE_ENABLED=false -> always sent", gate.clip_has_speech(wav, dataclasses.replace(S, speech_gate_enabled=False)))
check("lower SPEECH_GATE_MIN_SPEECH_MS respected", (detected.__setitem__("samples", int(0.1 * 16000)) or True)
      and gate.clip_has_speech(wav, dataclasses.replace(S, speech_gate_min_speech_ms=80)))

def boom(*a, **k): raise RuntimeError("onnx exploded")
fake_vad.get_speech_timestamps = boom
gate._vad = None
check("a crash inside the check never blocks speech (clip sent)", gate.clip_has_speech(wav, S))
fake_vad.get_speech_timestamps = get_speech_timestamps
gate._vad = None

# --- --mode vad: a rejected clip never reaches STT, and listening continues ----
import cozmo_brain.modes.vad_mode as vm
from fake_engine import make, SAY, DONE, wait_idle
engine, robot, chat = make("sync", [SAY("Hello!"), DONE])
records_seq = iter([True, True, False])        # noise clip, then real speech, then window closes
vm.record_until_silence = lambda *a, **k: next(records_seq)
gate_answers = iter([False, True])             # Silero: first clip no speech, second has speech
vm.clip_has_speech = lambda path, settings: next(gate_answers)
stt_calls = []
speech_client = types.SimpleNamespace(transcribe=lambda p: (stt_calls.append(p), "hello cozmo")[1])
vm._run_listening_window(engine, robot, speech_client, engine._settings)
wait_idle(engine)
check(f"vad: noise clip not sent to STT, speech clip sent (STT calls={len(stt_calls)})", len(stt_calls) == 1)
check("vad: the speech clip still became a conversation turn", any(m["role"] == "user" for m in engine.conversation.messages))

# --- push-to-talk: rejected recording never reaches STT -----------------------
import cozmo_brain.modes.interactive as im
inputs = iter(["", "quit"])
im.input = lambda prompt="": next(inputs)
im.record_fixed = lambda *a, **k: None
im.clip_has_speech = lambda path, settings: False
stt_calls.clear()
im.run(engine, speech_client, engine._settings)
check("push-to-talk: silent recording not sent to STT", stt_calls == [])

print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
