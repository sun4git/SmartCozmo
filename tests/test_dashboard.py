"""Test: the Cozmo Control Room (cozmo_dashboard/) - .env editing, transcript
parsing, the process runner (with a fake child instead of cozmo_brain), and the
HTTP layer end to end on a temp folder. Standard library only; no robot,
network or .env involved."""
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import _harness  # noqa: F401 - repo on sys.path
from cozmo_dashboard import data, envfile, runner as runner_mod
from cozmo_dashboard.runner import Runner, build_command
from cozmo_dashboard.server import Dashboard, make_server

failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


tmp = Path(tempfile.mkdtemp(prefix="smartcozmo_dash_"))

# --- .env parsing and editing -------------------------------------------------
example = tmp / ".env.example"
example.write_text(
    "# header\n\n# --- Secrets ---\n\n# Groq key.\nGROQ_API_KEY=your_groq_api_key_here\n\n"
    "# --- Battery ---\n\n# Low voltage.\n# Second line.\nBATTERY_LOW_VOLTAGE=3.7\nFLAG=true\n", encoding="utf-8")
env = tmp / ".env"
env.write_text("# my notes\nGROQ_API_KEY=gsk_realsecret\nBATTERY_LOW_VOLTAGE=3.6 # tuned\nCUSTOM_THING=\"a b\"\n", encoding="utf-8")

ex = envfile.parse_example(example)
check("example: keys in order", [s["key"] for s in ex] == ["GROQ_API_KEY", "BATTERY_LOW_VOLTAGE", "FLAG"])
check("example: section and comment", ex[1]["section"] == "Battery" and ex[1]["comment"] == "Low voltage.\nSecond line.")
view = envfile.view(env, example)
secret = next(s for s in view["settings"] if s["key"] == "GROQ_API_KEY")
check("view: secret value never returned", secret["value"] == "" and secret["is_set"] is True and "gsk_" not in json.dumps(view))
check("view: inline comment stripped from value", next(s for s in view["settings"] if s["key"] == "BATTERY_LOW_VOLTAGE")["value"] == "3.6")
check("view: missing setting shows the default", next(s for s in view["settings"] if s["key"] == "FLAG")["value"] == "true")
check("view: unknown keys listed as extras", [e["key"] for e in view["extras"]] == ["CUSTOM_THING"])
placeholder = tmp / "ph.env"
placeholder.write_text("GROQ_API_KEY=your_groq_api_key_here\n", encoding="utf-8")
check("view: placeholder secret counts as not set", envfile.view(placeholder, example)["settings"][0]["is_set"] is False)

res = envfile.apply_changes(env, {"BATTERY_LOW_VOLTAGE": "3.8", "FLAG": "false", "CUSTOM_THING": None}, now=datetime(2026, 10, 5, 12, 0, 0))
text = env.read_text(encoding="utf-8")
check("apply: edits in place, keeps comments", "# my notes" in text and "BATTERY_LOW_VOLTAGE=3.8\n" in text)
check("apply: appends new settings under a heading", "# --- Set from the dashboard on 20261005-120000 ---\nFLAG=false" in text)
check("apply: None removes the line", "CUSTOM_THING" not in text)
check("apply: backup saved with the old content", (tmp / ".env.bak-20261005-120000").read_text(encoding="utf-8").count("CUSTOM_THING") == 1 and res["backup"])
check("apply: untouched secret survives", "GROQ_API_KEY=gsk_realsecret" in text)
envfile.apply_changes(env, {"NOTE": 'has "quotes" # and hash'})
check("apply: awkward value round-trips", envfile.parse_env(env)["NOTE"] == 'has "quotes" # and hash')
for bad in ({"lower": "x"}, {"OK": "a\nb"}):
    try:
        envfile.apply_changes(env, bad)
        check(f"apply: rejects {bad}", False)
    except ValueError:
        check(f"apply: rejects {bad}", True)

# --- transcripts --------------------------------------------------------------
hist = tmp / "data" / "history"
day = hist / "2026-10-05"
day.mkdir(parents=True)
records = [
    {"ts": "2026-10-05T10:00:00+00:00", "type": "run_start", "mode": "vad", "simulated": False},
    {"ts": "2026-10-05T10:00:01+00:00", "type": "session_start", "trigger": "wake_word"},
    {"ts": "2026-10-05T10:00:02+00:00", "type": "message", "message": {"role": "user", "content": "hello cozmo\n\n[Status: battery ok]"}},
    {"ts": "2026-10-05T10:00:03+00:00", "type": "message", "message": {"role": "assistant", "tool_calls": [
        {"function": {"name": "say", "arguments": json.dumps({"text": "Hi there!", "mood": "happy", "gesture": "cheer"})}},
        {"function": {"name": "look", "arguments": "{}"}}]}},
    {"ts": "2026-10-05T10:00:04+00:00", "type": "message", "message": {"role": "tool", "name": "look", "content": "ERROR: camera busy"}},
    {"ts": "2026-10-05T10:00:05+00:00", "type": "message", "message": {"role": "user", "content": "[Cozmo was picked up]"}},
    {"ts": "2026-10-05T10:00:06+00:00", "type": "message", "images": ["10-00-00-photo-01.png"], "message": {"role": "user", "content": "what is this"}},
    {"ts": "2026-10-05T10:00:07+00:00", "type": "session_end"},
]
run_path = day / "10-00-00.jsonl"
run_path.write_text("\n".join(json.dumps(r) for r in records) + '\n{"ts": "torn', encoding="utf-8")
(day / "10-00-00-photo-01.png").write_bytes(b"\x89PNG\r\n")

loaded = data.load_run(hist, "2026-10-05/10-00-00")["events"]
kinds = [e["kind"] for e in loaded]
check("transcript: kinds in order, torn line skipped",
      kinds == ["run", "session", "you", "cozmo", "tool", "error", "note", "you", "session"])
check("transcript: say carries mood and gesture", loaded[3]["text"] == "Hi there!" and loaded[3]["mood"] == "happy" and loaded[3]["gesture"] == "cheer")
check("transcript: status suffix split off the user's words", loaded[2]["text"] == "hello cozmo" and loaded[2]["status"].startswith("[Status:"))
check("transcript: photo url points into the data folder", loaded[7]["images"] == ["/api/file?path=history/2026-10-05/10-00-00-photo-01.png"])
runs = data.list_runs(hist)
check("runs: summary counts", runs[0]["turns"] == 2 and runs[0]["replies"] == 1 and runs[0]["sessions"] == 1 and runs[0]["photos"] == 1 and runs[0]["info"]["mode"] == "vad")
check("search finds text, newest first", [h["text"] for h in data.search_runs(hist, "HELLO")] == ["hello cozmo"])
try:
    data.load_run(hist, "../../.env")
    check("load_run: path traversal refused", False)
except (ValueError, FileNotFoundError):
    check("load_run: path traversal refused", True)

tail = data.ConversationTail(hist)
reset, rid, events = tail.poll()
check("tail: first poll sends the whole latest run", reset and rid == "2026-10-05/10-00-00" and len(events) == 9)
with run_path.open("a", encoding="utf-8") as f:  # finish the torn line, then add one more
    f.write('": "x"}\n' + json.dumps({"ts": "t", "type": "message", "message": {"role": "user", "content": "later"}}) + "\n")
reset, rid, events = tail.poll()
check("tail: only the new complete lines, no reset", not reset and [e["text"] for e in events] == ["later"])
(hist / "2026-10-06").mkdir()
(hist / "2026-10-06" / "09-00-00.jsonl").write_text(json.dumps({"ts": "t", "type": "run_start"}) + "\n", encoding="utf-8")
reset, rid, events = tail.poll()
check("tail: a newer run resets", reset and rid == "2026-10-06/09-00-00")

# --- files --------------------------------------------------------------------
dd = tmp / "data"
(dd / "memory.md").write_text("## Suneel (primary user)\n- Likes cricket.\n", encoding="utf-8")
(dd / "battery.jsonl").write_text(
    json.dumps({"ts": "2026-10-05T10:00:00+00:00", "event": "run_start", "v": 3.9, "docked": True}) + "\n"
    + json.dumps({"ts": "2026-10-05T10:05:00+00:00", "event": "undocked", "v": 3.88}) + "\n", encoding="utf-8")
check("memory view parses people and facts", data.memory_view(dd)["sections"] == [{"name": "Suneel (primary user)", "facts": ["Likes cricket."]}])
check("battery view: latest and state", data.battery_view(dd)["latest"]["v"] == 3.88 and data.battery_view(dd)["state"]["event"] == "undocked")
for evil in ("../.env", "history/../../.env", "/etc/passwd"):
    try:
        p = data.safe_path(dd, evil)
        check(f"safe_path {evil!r} stays inside", dd.resolve() in p.parents or p == dd.resolve())
    except ValueError:
        check(f"safe_path {evil!r} refused", True)
check("only memory.md is editable", data.list_dir(dd, "")["entries"] and
      [e["name"] for e in data.list_dir(dd, "")["entries"] if e["editable"]] == ["memory.md"])
try:
    data.write_memory(dd, "battery.jsonl", "x")
    check("write refused for other files", False)
except PermissionError:
    check("write refused for other files", True)
data.write_memory(dd, "memory.md", "## A\n- new\n")
check("memory write keeps a .bak", (dd / "memory.md.bak").read_text(encoding="utf-8").startswith("## Suneel"))

# --- runner (fake child) ------------------------------------------------------
fake = tmp / "fake_app.py"
fake.write_text(
    "import sys, time\n"
    "print('2026-10-05 10:00:00,000 INFO fake: hello', flush=True)\n"
    "for line in sys.stdin:\n"
    "    if line.strip() == 'quit': break\n"
    "    print('got ' + line.strip(), flush=True)\n", encoding="utf-8")
real_build = runner_mod.build_command
runner_mod.build_command = lambda root, mode, simulate, fresh, level: [sys.executable, str(fake), mode]
r = Runner(tmp, tmp / "logs")
st = r.start("text")
check("runner: running after start", st["state"] == "running" and st["mode"] == "text")
try:
    r.start("text")
    check("runner: second start refused", False)
except RuntimeError:
    check("runner: second start refused", True)
r.send_input("hi cozmo")


def wait_for(cond, secs=8):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


check("runner: output captured with level", wait_for(lambda: any(l["level"] == "INFO" for l in r.read_log())))
check("runner: stdin reaches the app", wait_for(lambda: any(l["text"] == "got hi cozmo" for l in r.read_log())))
r.send_input("quit")
check("runner: stops when the app exits", wait_for(lambda: r.status()["state"] == "stopped") and r.status()["exit_code"] == 0)
check("runner: log file written", any(p.read_text(encoding="utf-8").count("got hi cozmo") for p in (tmp / "logs").rglob("*.log")))
r.start("text")
r.stop()
check("runner: stop ends a running app", wait_for(lambda: r.status()["state"] == "stopped"))
runner_mod.build_command = real_build

cmd = build_command(Path(tmp), "vad", True, True, "debug")
check("command: flags as run.sh passes them", cmd[1:] == ["-m", "cozmo_brain", "--mode", "vad", "--simulate", "--fresh", "--log-level", "DEBUG"])
for bad in (("rm -rf", False, False, None), ("vad", False, False, "loud")):
    try:
        build_command(Path(tmp), *bad)
        check(f"command: rejects {bad[0]!r}/{bad[3]!r}", False)
    except ValueError:
        check(f"command: rejects {bad[0]!r}/{bad[3]!r}", True)

# --- HTTP, end to end ---------------------------------------------------------
runner2 = Runner(tmp, tmp / "logs2")
dash = Dashboard(tmp, dd, env, example, runner2, "127.0.0.1", token="")
server = make_server(dash, "127.0.0.1", 0)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"


def call(path, method="GET", body=None, headers=None, raw=False):
    h = {"Content-Type": "application/json", **(headers or {})} if body is not None else dict(headers or {})
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = resp.read()
            return resp.status, (payload if raw else json.loads(payload))
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload)
        except ValueError:
            return e.code, payload


code, body = call("/api/status")
check("http: status", code == 200 and body["runner"]["state"] == "stopped")
code, page = call("/", raw=True)
check("http: index page served", code == 200 and b"Cozmo Control Room" in page)
code, body = call("/api/env")
check("http: env view has no secret values", code == 200 and "gsk_realsecret" not in json.dumps(body))
code, body = call("/api/history")
check("http: history list", code == 200 and len(body["runs"]) >= 1)
code, body = call("/api/file?path=../.env")
check("http: path traversal blocked", code in (400, 403, 404))
code, img = call("/api/file?path=history/2026-10-05/10-00-00-photo-01.png", raw=True)
check("http: photo served from data", code == 200 and img.startswith(b"\x89PNG"))
code, body = call("/api/env", "POST", {"changes": {"FLAG": "true"}})
check("http: env save", code == 200 and body["changed"] == ["FLAG"] and body["restart_needed"] is False)
code, _ = call("/api/env", "POST", {"changes": {"bad name": "x"}})
check("http: bad setting name -> 400", code == 400)
code, _ = call("/api/start", "POST", {"mode": "nope"})
check("http: bad mode -> 400", code == 400)
code, _ = call("/api/stop", "POST", {})
check("http: stop when not running -> 409", code == 409)
code, _ = call("/api/env", "POST", {"changes": {}}, headers={"Origin": "http://evil.example"})
check("http: cross-origin write refused", code == 403)
code, _ = call("/api/env", "POST", {"changes": {}}, headers={"Host": "evil.example"})
check("http: unexpected Host (DNS rebinding) refused", code == 403)
req = urllib.request.Request(base + "/api/stop", data=b"{}", method="POST", headers={"Content-Type": "text/plain"})
try:
    urllib.request.urlopen(req, timeout=5)
    check("http: non-JSON write refused", False)
except urllib.error.HTTPError as e:
    check("http: non-JSON write refused", e.code == 400)

# live conversation stream: first event is the latest run
with urllib.request.urlopen(base + "/api/conversation/stream", timeout=5) as resp:
    first = b""
    while b"\n\n" not in first:
        first += resp.read1(4096)
payload = json.loads(first.decode().split("data: ", 1)[1].split("\n")[0])
check("stream: conversation starts with a reset of the latest run", payload["reset"] is True and payload["run"] == "2026-10-06/09-00-00")
server.shutdown()

# --- token auth ---------------------------------------------------------------
dash_t = Dashboard(tmp, dd, env, example, runner2, "127.0.0.1", token="s3cret")
server_t = make_server(dash_t, "127.0.0.1", 0)
threading.Thread(target=server_t.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server_t.server_address[1]}"
code, _ = call("/api/status")
check("auth: API refused without the cookie", code == 401)
code, page = call("/", raw=True)
check("auth: page shows the login form", code == 401 and b"Unlock" in page)
from cozmo_dashboard.server import token_cookie  # noqa: E402
code, body = call("/api/status", headers={"Cookie": f"cozmo_dash={token_cookie('s3cret')}"})
check("auth: cookie unlocks the API", code == 200)
code, _ = call("/api/status", headers={"Cookie": "cozmo_dash=wrong"})
check("auth: wrong cookie refused", code == 401)
server_t.shutdown()

sys.exit(1 if failures else 0)
