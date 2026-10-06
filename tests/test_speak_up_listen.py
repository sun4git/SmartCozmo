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

# --- run_unprompted(): never into an open recording ------------------------------------
engine, robot, chat = fe.make("sync", [])
ran = []
check("outside a window: runs at once", engine.run_unprompted(lambda: ran.append("now"), 1) and ran == ["now"])
engine.set_listening_window(True)
check("inside a window: queued, returns True", engine.run_unprompted(lambda: ran.append("later"), 1) and ran == ["now"])
check("inside a window: announce_event set (stops an idle recording)", engine.announce_event.is_set())
held = []
engine.run_unprompted(lambda: held.append(not engine.turn_lock.acquire(blocking=False)), 1)
check("vad loop runs queued actions in order", engine.deliver_announcements() and ran == ["now", "later"])
check("queued action holds turn_lock while it runs", held == [True])
check("queue empty afterwards", not engine.announce_event.is_set() and engine.deliver_announcements() is False)
engine.set_listening_window(False)
engine.turn_lock.acquire()
check("outside a window, turn busy past wait_s -> False (caller retries)", engine.run_unprompted(lambda: None, 0.1) is False)
engine.turn_lock.release()
engine.set_listening_window(True)
def boom(): raise RuntimeError("x")
engine.run_unprompted(boom, 1)
engine.run_unprompted(lambda: ran.append("after boom"), 1)
engine.deliver_announcements()
check("a failing queued action doesn't drop the next one", ran[-1] == "after boom")
engine.set_listening_window(False)

# --- the real battery offer inside a listening window ----------------------------------
from cozmo_brain.charger_return import ChargerReturner  # noqa: E402

class BatRobot(fe.Robot):
    on_charger = False
    def is_on_charger(self): return self.on_charger
    def has_charger_pose(self): return True
engine, _, chat = fe.make("sync", [])
bat = BatRobot()
engine._robot = bat
engine._tools_by_name["say"].handler = lambda a: (bat.events.append(("speak", a["text"])), fe.NS(ok=True, to_tool_message=lambda: "OK"))[1]
cr = ChargerReturner(bat, engine, engine._settings)
engine.set_listening_window(True)
cr.on_battery_reading(3.65); cr.on_battery_reading(3.65)
check("LOW offer inside a window: not spoken yet (would land in the recording)", not bat.events)
check("LOW offer queued for between recordings", engine.announce_event.is_set())
cr.on_battery_reading(3.65); cr.on_battery_reading(3.65)
engine.deliver_announcements()
offers = [e for e in bat.events if e[0] == "speak"]
check(f"spoken once when the vad loop runs it, not queued twice ({len(offers)})", len(offers) == 1 and "head back" in offers[0][1])
check("offer still written to the conversation", any("call the dock tool" in (m.get("content") or "")
      for m in engine.conversation.messages))

engine, _, chat = fe.make("sync", [])
bat = BatRobot(); engine._robot = bat
engine._tools_by_name["say"].handler = lambda a: (bat.events.append(("speak", a["text"])), fe.NS(ok=True, to_tool_message=lambda: "OK"))[1]
cr = ChargerReturner(bat, engine, engine._settings)
engine.set_listening_window(True)
cr.on_battery_reading(3.65); cr.on_battery_reading(3.65)
bat.on_charger = True  # put back on the charger before the gap came
engine.deliver_announcements()
check("put back on the charger meanwhile -> queued offer skipped", not bat.events)

# --- vad: anything queued as the window closes runs before the wake word listens again ---
engine, robot, chat = fe.make("sync", [])
order = []
def fake_wait(*a, **k):
    order.append("wait")
    if order.count("wait") > 1:
        raise KeyboardInterrupt  # stop the endless loop on the second wait
    return "tap"
def window_then_queue(engine_, *a, **k):
    engine_.run_unprompted(lambda: order.append("offer"), 1)  # arrives just as the window ends
vad_mode._wait_for_wake_word_or_tap = fake_wait
vad_mode._run_listening_window = window_then_queue
vad_mode.speech_gate_warm_up = lambda s: None
try:
    vad_mode.run(engine, robot, None, engine._settings)
except KeyboardInterrupt:
    pass
check(f"queued at window close runs before listening again ({order})", order == ["wait", "offer", "wait"])

print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
sys.exit(1 if failures else 0)
