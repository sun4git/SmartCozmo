"""Test: multi-step turns under FINAL_LLM_CALL=async."""
import sys, time, logging
import _harness  # noqa: F401 - repo on sys.path, real .env ignored
import contextlib, io
with contextlib.redirect_stdout(io.StringIO()):
    from fake_engine import make, call, NS, DONE, SAY, wait_idle, MoveResult
logging.disable(logging.CRITICAL)
failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: failures.append(name)

def run(script, robot_patch=None):
    engine, robot, chat = make("async", script)
    if robot_patch: robot_patch(robot)
    calls_at = []
    orig = chat.chat
    def spy(m, tools=None):
        bg = threading.current_thread().name == "llm-followup"
        if bg: time.sleep(0.2)
        calls_at.append("bg" if bg else "fg"); return orig(m, tools=tools)
    chat.chat = spy
    engine.handle_turn("go", allow_async_followup=True)
    returned_after = list(calls_at)
    wait_idle(engine)
    return returned_after, calls_at, robot
import threading

# look -> say about it: info tool forces the model to see the photo before the mic reopens
fg, all_, _ = run([NS(content="", tool_calls=[call("say", text="Let me look"), call("look")]), SAY("I see a cat!"), DONE])
check(f"say+look -> say: photo step runs synchronously, only the final 'done' call is async (before return={fg}, all={all_})",
      fg == ["fg", "fg"] and all_ == ["fg", "fg", "bg"])

# list_animations then say in one batch: model hasn't read the list yet -> sync
fg, all_, _ = run([NS(content="", tool_calls=[call("list_animations"), call("say", text="Let me check")]), SAY("I can do these!"), DONE])
check(f"list_animations+say -> say: stays sync until the model has read the list (before return={fg}, all={all_})",
      fg == ["fg", "fg"] and all_ == ["fg", "fg", "bg"])

# hazard: drive hit a cliff -> model must react synchronously
def cliff(r): r.drive = lambda d, s: MoveResult(moved=True, hazard="cliff")
fg, all_, _ = run([NS(content="", tool_calls=[call("say", text="On it!"), call("drive", distance_mm=300, speed_mmps=80)]), SAY("Whoa, a cliff!"), DONE], cliff)
check(f"say+drive(cliff) -> say: model reacts to the hazard before the mic reopens (before return={fg}, all={all_})",
      fg == ["fg", "fg"] and all_ == ["fg", "fg", "bg"])

# last call isn't say: say, turn, drive -> model sees move results synchronously
fg, all_, _ = run([NS(content="", tool_calls=[call("say", text="On it!"), call("turn", angle_degrees=90), call("drive", distance_mm=100, speed_mmps=80)]), DONE])
check(f"say->turn->drive: sync, ends when the model says done (before return={fg}, all={all_})",
      fg == ["fg", "fg"] and all_ == ["fg", "fg"])

# multi-step continuation in the background: say alone -> [look] -> [say] -> done, all in bg with mic paused
fg, all_, robot = run([SAY("Sure, let me see"), NS(content="", tool_calls=[call("look")]), SAY("It's a mug!"), DONE])
check(f"continuation with its own multi-step (look, then say) completes in the background (before return={fg}, all={all_})",
      fg == ["fg"] and all_ == ["fg", "bg", "bg", "bg"])
print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
