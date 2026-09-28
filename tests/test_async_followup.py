"""Test: FINAL_LLM_CALL=async (and sync/skip unchanged)."""
import logging
import sys
import threading
import time

import _harness  # noqa: F401 - repo on sys.path, real .env ignored
from fake_engine import DONE, NS, SAY, MoveResult, call, make, wait_idle

logging.disable(logging.CRITICAL)
failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: failures.append(name)

# 1. async, model then says "done" (common case): returns before the slow final call finishes
engine, robot, chat = make("async", [SAY(), DONE], delays={1: 1.0})
t0 = time.monotonic(); engine.handle_turn("hi", allow_async_followup=True); took = time.monotonic() - t0
check(f"async: returns right after the say, not after the 1s final call (took {took:.2f}s)", took < 0.5)
check("async: turn_lock still held while the follow-up runs", engine.turn_lock.locked())
check("async: follow-up finishes and releases the lock", wait_idle(engine))
check("async: final call's reply recorded in history", [m["role"] for m in engine.conversation.messages][-1] == "assistant")
check("async: no interrupt when the model just said done", not engine.followup_interrupt.is_set())

# 2. sync (default) and skip unchanged
engine, robot, chat = make("sync", [SAY(), DONE], delays={1: 0.5})
t0 = time.monotonic(); engine.handle_turn("hi", allow_async_followup=True); took = time.monotonic() - t0
check(f"sync: waits for the final call before returning (took {took:.2f}s, 2 calls={chat.n == 2})", took >= 0.5 and chat.n == 2)
engine, robot, chat = make("skip", [SAY(), DONE])
engine.handle_turn("hi", allow_async_followup=True)
check(f"skip: 1 call only (got {chat.n})", chat.n == 1)
engine, robot, chat = make("async", [SAY(), DONE], delays={1: 0.5})
t0 = time.monotonic(); engine.handle_turn("hi"); took = time.monotonic() - t0
check(f"async without allow_async_followup (text/voice modes) behaves as sync (took {took:.2f}s)", took >= 0.5 and not engine.turn_lock.locked())

# 3. gpt-oss case: say alone, then act next step - async still performs the action
engine, robot, chat = make("async", [SAY("Sure, turning!"), NS(content="", tool_calls=[call("turn", angle_degrees=90)]), SAY("Done!"), DONE], delays={1: 0.3})
engine.handle_turn("turn left", allow_async_followup=True)
wait_idle(engine)
check(f"async: 'say alone, act next step' model still turns ({[e for e, _ in robot.events]})", any(e == "turn" for e, _ in robot.events))
check("async: interrupt raised because the model continued", engine.followup_interrupt.is_set())
check("async: settle_followup() reports it and clears", engine.settle_followup() and not engine.followup_interrupt.is_set())

# 4. a new turn issued immediately waits for the follow-up; history order stays correct
engine, robot, chat = make("async", [SAY("one"), DONE, SAY("two"), DONE], delays={1: 0.6})
engine.handle_turn("first", allow_async_followup=True)
engine.handle_turn("second", allow_async_followup=True)
wait_idle(engine)
roles = [m["role"] for m in engine.conversation.messages]
check(f"async: next turn waits; history in order ({roles})",
      roles == ["system", "user", "assistant", "tool", "assistant", "user", "assistant", "tool", "assistant"])

# 5. needs_attention batch -> no handoff, sync continue even in async mode
r_cliff = NS(content="", tool_calls=[call("drive", distance_mm=50, speed_mmps=60), call("say", text="Here I come!")])
engine, robot, chat = make("async", [r_cliff, DONE], delays={1: 0.4})
robot.drive = lambda d, s: MoveResult(moved=True, hazard="cliff")
t0 = time.monotonic(); engine.handle_turn("go", allow_async_followup=True); took = time.monotonic() - t0
check(f"async: hazard in batch -> model consulted before returning (took {took:.2f}s)", took >= 0.35 and not engine.turn_lock.locked())

# 6. follow-up LLM failure -> lock still released, nothing hangs
engine, robot, chat = make("async", [SAY()], fail_at=1)
engine.handle_turn("hi", allow_async_followup=True)
check("async: follow-up request failure still releases the lock", wait_idle(engine))

# 7. a fidget / charger return can't start while the follow-up holds the lock
engine, robot, chat = make("async", [SAY(), DONE], delays={1: 0.5})
engine.handle_turn("hi", allow_async_followup=True)
check("async: fidget-style non-blocking acquire fails during follow-up", not engine.turn_lock.acquire(blocking=False))
wait_idle(engine)

# 8. Full --mode vad window with a fake mic: Cozmo continues -> recording stopped, action runs, listening resumes
import cozmo_brain.modes.vad_mode as vm
engine, robot, chat = make("async", [SAY("Sure!"), NS(content="", tool_calls=[call("turn", angle_degrees=90)]), DONE], delays={1: 0.3})
log = []
records = iter(["speech", "wait_for_stop", "silence"])
def fake_record(*a, stop_event=None, **k):
    kind = next(records)
    log.append((f"record:{kind}:start", time.monotonic())); robot.events.append((f"record:{kind}:start", 0))
    if kind == "speech":
        return True
    if kind == "wait_for_stop":
        stopped = stop_event.wait(3.0)
        log.append((f"record:stopped={stopped}", time.monotonic()))
        return False
    return False
vm.record_until_silence = fake_record
speech_client = NS(transcribe=lambda p: "turn left please")
s = engine._settings
vm._run_listening_window(engine, robot, speech_client, s)
wait_idle(engine)
names = [n for n, _ in log]
turn_t = [t for e, t in robot.events if e == "turn"]
resume_t = [t for n, t in log if n == "record:silence:start"]
check(f"vad: the recording in progress was stopped when Cozmo continued ({names})", "record:stopped=True" in names)
order = [e for e, _ in robot.events]
check(f"vad: the continued action ran before listening resumed ({order})", "turn" in order and order.index("turn") < order.index("record:silence:start"))
check("vad: listening resumed after the follow-up (window not closed early)", "record:silence:start" in names)

print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
