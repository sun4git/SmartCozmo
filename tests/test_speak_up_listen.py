"""Test: after Cozmo speaks up on his own with something that wants an
answer (a background ask_assistant answer; the low-battery offer is checked
in test_return_policy), --mode vad opens a short listening window without
the wake word (SPEAK_UP_LISTEN_S) - and doesn't when it's 0, when a window
is already open, or for nothing at all."""
import dataclasses
import logging
import sys
import threading
import time

import fake_engine as fe  # also sets up _harness (repo on sys.path, real .env ignored)

from cozmo_brain.modes import vad_mode

logging.disable(logging.CRITICAL)
failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: failures.append(name)

# --- engine.request_listen() ------------------------------------------------------
engine, robot, chat = fe.make("sync", [])
check("default SPEAK_UP_LISTEN_S is 10", engine._settings.speak_up_listen_s == 10)
engine.request_listen()
check("outside a window: listen requested", engine.listen_request.is_set())
engine.listen_request.clear()
engine.listening_window_open = True
engine.request_listen()
check("inside a window: not requested (already listening)", not engine.listen_request.is_set())
engine.listening_window_open = False
engine._settings = dataclasses.replace(engine._settings, speak_up_listen_s=0)
engine.request_listen()
check("SPEAK_UP_LISTEN_S=0: never requested", not engine.listen_request.is_set())

# --- deliver_announcements() --------------------------------------------------------
engine, robot, chat = fe.make("sync", [fe.SAY("Sunny says hi!")])
check("nothing to deliver: no listen request", engine.deliver_announcements() is False
      and not engine.listen_request.is_set())
engine.queue_announcement("[Sunny just answered: hi.]")
engine.deliver_announcements()
check("delivered outside a window: listen requested", engine.listen_request.is_set())
engine, robot, chat = fe.make("sync", [fe.SAY("Sunny says hi!")])
engine.listening_window_open = True  # the vad loop delivering between recordings
engine.queue_announcement("[Sunny just answered: hi.]")
engine.deliver_announcements()
check("delivered inside a window: no request (window continues)", not engine.listen_request.is_set())

# --- _wait_for_wake_word_or_tap() returns "spoke up" -----------------------------------
stopped = []
def fake_wake_word(device, model, threshold, stop_event):
    stop_event.wait(5)
    stopped.append(stop_event.is_set())
    return False
vad_mode.wait_for_wake_word = fake_wake_word
class TapRobot:
    def wait_for_tap(self, timeout): time.sleep(timeout); return False
req = threading.Event()
threading.Timer(0.3, req.set).start()
started = time.monotonic()
trigger = vad_mode._wait_for_wake_word_or_tap(TapRobot(), engine._settings, req)
check(f"listen request -> 'spoke up' ({trigger}, {time.monotonic() - started:.2f}s)",
      trigger == "spoke up" and time.monotonic() - started < 2)
time.sleep(0.1)
check("wake-word listener was stopped (mic freed for the recording)", stopped == [True])
# Without a request object (older callers), behavior is unchanged: a tap still wins.
class TappedRobot:
    def wait_for_tap(self, timeout): return True
check("no request object: tap still returns 'tap'",
      vad_mode._wait_for_wake_word_or_tap(TappedRobot(), engine._settings) == "tap")

# --- the window's first wait uses SPEAK_UP_LISTEN_S ----------------------------------------
waits = []
def fake_record(path, device, aggr, silence_ms, max_seconds, *a, **k):
    waits.append(max_seconds)
    return False  # nobody answered
vad_mode.record_until_silence = fake_record
engine, robot, chat = fe.make("sync", [])
S = engine._settings
vad_mode._run_listening_window(engine, robot, None, S, first_timeout_s=S.speak_up_listen_s)
check(f"spoke-up window waits SPEAK_UP_LISTEN_S for a reply ({waits})", waits == [10])
waits.clear()
vad_mode._run_listening_window(engine, robot, None, S)
check(f"wake-word window unchanged: VAD_MAX_UTTERANCE_S ({waits})", waits == [S.vad_max_utterance_s])

print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
sys.exit(1 if failures else 0)
