"""Scratch test for A (self-motion tap suppression), B (no fidget while
listening), C (wait timeout separate from utterance cap)."""
import dataclasses, io, logging, math, struct, sys, time, types

import _harness  # noqa: F401 - repo on sys.path, real .env ignored

logging.disable(logging.CRITICAL)
failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: failures.append(name)

# ---------- A ----------
import contextlib
with contextlib.redirect_stdout(io.StringIO()):
    from fake_pycozmo import make_robot  # fake pycozmo client
from cozmo_brain.robot.real import _SELF_MOTION_TAP_GRACE_S

class Pkt:
    def __init__(self, mag, status=0):
        self.status, self.accel_x, self.accel_y, self.accel_z = status, mag, 0, 0

def tap_registered(r):
    r._tap_event.clear()
    r._last_tap_time = 0.0
    r._on_robot_state(None, Pkt(1000))      # baseline
    r._on_robot_state(None, Pkt(1600))      # +600 spike, well over TAP_THRESHOLD
    hit = r._tap_event.is_set()
    r._accel_baseline = 1000.0
    return hit

r, cli = make_robot()
check("A: genuine tap at rest registers", tap_registered(r))
r.set_head_angle_deg(20, duration=0.2)
check("A: tap right after a head move is ignored", not tap_registered(r))
time.sleep(0.2 + _SELF_MOTION_TAP_GRACE_S + 0.05)
check("A: tap registers again once head move + grace passes", tap_registered(r))

r, cli = make_robot()
import threading
seen = []
orig_sleep = r._sleep_unless_cliff
def sleep_and_probe(d, **kw):
    seen.append(tap_registered(r))          # a "tap" while the wheels are turning
    return orig_sleep(d, **kw)
r._sleep_unless_cliff = sleep_and_probe
r.drive(60, 60)
check("A: tap during a drive is ignored", seen == [False])
check("A: tap just after drive stops still ignored (grace)", not tap_registered(r))
time.sleep(_SELF_MOTION_TAP_GRACE_S + 0.05)
check("A: tap registers after drive + grace", tap_registered(r))

r, cli = make_robot()
seen.clear()
r._sleep_unless_cliff = sleep_and_probe
r.turn(20)
check("A: tap during a turn is ignored", seen == [False])

r, cli = make_robot()
r.lower_lift_fully(duration=0.1)
check("A: lift suppression still works (shrug fix kept)", not tap_registered(r))

r, cli = make_robot()
r._suppress_taps(2.0)
r._suppress_taps(0.0)
check("A: a shorter later move doesn't cut an earlier window short", r._tap_suppress_until - time.monotonic() > 1.5)

# ---------- B ----------
from cozmo_brain.config import Settings
from cozmo_brain.idle_fidget import IdleFidgeter
from cozmo_brain.robot.simulated import SimulatedRobot
class R(SimulatedRobot):
    def __init__(self): super().__init__(); self.gestures = 0
    def run_gesture(self, name, *, wheels=True): self.gestures += 1
class E:
    last_interaction_monotonic = 0.0
    turn_lock = __import__('threading').Lock()
    listening_window_open = False
S = dataclasses.replace(Settings(), idle_fidget_after_s=0)
robot, eng = R(), E()
f = IdleFidgeter(robot, eng, S)
eng.listening_window_open = True
f._check_once()
check("B: no fidget while a listening window is open", robot.gestures == 0)
eng.listening_window_open = False
f._check_once()
check("B: fidgets once the window is closed", robot.gestures == 1)

from cozmo_brain.conversation import Conversation
from cozmo_brain.engine import CozmoEngine
engine = CozmoEngine(Settings(), SimulatedRobot(), None, None, [], Conversation("sys"))
engine.last_interaction_monotonic = 0.0
engine.set_listening_window(True)
check("B: engine flag set on open", engine.listening_window_open)
engine.set_listening_window(False)
check("B: closing counts as activity (fidget waits a full interval after)",
      not engine.listening_window_open and time.monotonic() - engine.last_interaction_monotonic < 1.0)

# ---------- C ----------
fake = types.ModuleType("webrtcvad")
class Vad:
    def __init__(self, a): pass
    def is_speech(self, frame, rate): return any(frame)
fake.Vad = Vad
sys.modules["webrtcvad"] = fake
import cozmo_brain.audio.vad as vad
F = vad._FRAME_BYTES
def tone(n): return b"".join(struct.pack("<h", int(3000 * math.sin(i / 3))) for i in range(n * F // 2))
def silence(n): return b"\0" * F * n
class P:
    def __init__(self, d): self.stdout = io.BytesIO(d)
    def terminate(self): pass
    def wait(self, timeout=None): pass
    def poll(self): return 0
captured = {}
orig_open = vad.wave.open
def run(data, wait_s, utt_s=None):
    vad.subprocess.Popen = lambda *a, **k: P(data)
    ok = vad.record_until_silence(_harness.tmp("c.wav"), "dev", 2, 800, wait_s, 60, 200, max_utterance_s=utt_s)
    if ok:
        with orig_open(_harness.tmp("c.wav"), "rb") as w:
            captured["s"] = w.getnframes() / 16000
    return ok
frames_per_s = 1000 // vad._FRAME_MS  # ~33
# speech starts 1.9s into a 2s window and lasts 1.5s
ok = run(silence(int(1.9 * frames_per_s)) + tone(int(1.5 * frames_per_s)) + silence(40), wait_s=2, utt_s=15)
check(f"C: late-starting speech captured whole ({captured.get('s', 0):.1f}s, was ~0.1s before)", ok and captured["s"] > 1.4)
ok = run(silence(int(2.5 * frames_per_s)), wait_s=2, utt_s=15)
check("C: no speech -> still gives up after the wait timeout", not ok)
ok = run(tone(10 * frames_per_s), wait_s=2, utt_s=3)
check(f"C: utterance cap still enforced from onset ({captured.get('s', 0):.1f}s)", ok and 2.9 < captured["s"] < 3.2)
ok = run(silence(5) + tone(20) + silence(40), wait_s=2)
check("C: default (no max_utterance_s) still works", ok)
import os; os.remove(_harness.tmp("c.wav"))

print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
