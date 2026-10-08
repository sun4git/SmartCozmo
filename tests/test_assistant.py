"""Test: the ask_assistant tool and its client (llm/assistant_client.py),
against a fake OpenAI-compatible endpoint on localhost - request shape (the
`user` session field, model, auth, only the one request), reply handling,
failures, tool/prompt wiring, and an engine turn that waits for the answer
even after a `say` (FINAL_LLM_CALL=skip)."""
import dataclasses
import json
import logging
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import fake_engine as fe  # noqa: F401 - also sets up _harness (repo on sys.path, real .env ignored)

from cozmo_brain.config import Settings
from cozmo_brain.conversation import Conversation
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.llm.assistant_client import AssistantClient
from cozmo_brain.personality import build_system_prompt
from cozmo_brain.tools import build_tools
from cozmo_brain.tools.assistant_tools import assistant_prompt_section

logging.disable(logging.CRITICAL)
failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: failures.append(name)


class Fake(BaseHTTPRequestHandler):
    # What the next request gets back: (status, body) or "sleep".
    reply = (200, {"choices": [{"message": {"role": "assistant", "content": "It's 24 degrees and sunny."}}]})
    seen: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Fake.seen.append((self.path, dict(self.headers), body))
        if Fake.reply == "sleep":
            time.sleep(1.5)
            Fake.reply = (200, {"choices": [{"message": {"content": "late"}}]})
        status, data = Fake.reply
        raw = data.encode() if isinstance(data, str) else json.dumps(data).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except ConnectionError:
            pass  # the timeout check's client already gave up

    def log_message(self, *a): pass


server = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=server.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{server.server_address[1]}/"

S = dataclasses.replace(
    Settings(), audio_output="cozmo", final_llm_call="skip",
    assistant_enabled=True, assistant_name="Sunny", assistant_url=URL, assistant_token="t0ken",
    assistant_model="openclaw/main", assistant_session_user="cozmo", assistant_timeout_s=5,
)
client = AssistantClient(S)

# --- request shape -------------------------------------------------------------
reply = client.ask("  What's the weather in Lisbon?  ")
path, headers, body = Fake.seen[-1]
check("posts to <url>/v1/chat/completions (trailing slash handled)", path == "/v1/chat/completions")
check("bearer token sent", headers.get("Authorization") == "Bearer t0ken")
check("model field = ASSISTANT_MODEL", body["model"] == "openclaw/main")
check("user field = ASSISTANT_SESSION_USER (stable session)", body["user"] == "cozmo")
check("only one message sent (not Cozmo's history)", len(body["messages"]) == 1 and body["messages"][0]["role"] == "user")
content = body["messages"][0]["content"]
check("request text sent, trimmed", content.endswith("\n\nWhat's the weather in Lisbon?"))
check("context line asks for short spoken plain text", "read out loud" in content and "no markdown" in content)
check("reply text returned", reply == "It's 24 degrees and sunny.")

no_token = AssistantClient(dataclasses.replace(S, assistant_token=""))
no_token.ask("hi")
check("no token -> no Authorization header", "Authorization" not in Fake.seen[-1][1])

# --- failures --------------------------------------------------------------------
def error_of(fn):
    try:
        fn(); return None
    except Exception as e:  # noqa: BLE001
        return e

Fake.reply = (401, {"error": "unauthorized"})
e = error_of(lambda: client.ask("hi"))
check("401 -> RuntimeError naming ASSISTANT_TOKEN", isinstance(e, RuntimeError) and "ASSISTANT_TOKEN" in str(e))
check("token never in the error text", "t0ken" not in str(e))
Fake.reply = (500, "oops")
e = error_of(lambda: client.ask("hi"))
check("500 -> RuntimeError with status", isinstance(e, RuntimeError) and "500" in str(e))
Fake.reply = (200, {"unexpected": True})
e = error_of(lambda: client.ask("hi"))
check("unreadable body -> RuntimeError", isinstance(e, RuntimeError) and "couldn't read" in str(e))
Fake.reply = (200, {"choices": [{"message": {"content": None}}]})
check("empty reply -> says it replied with nothing", client.ask("hi") == "Sunny replied with nothing.")
Fake.reply = (200, {"choices": [{"message": {"content": "x" * 5000}}]})
check("long reply capped", len(client.ask("hi")) < 2100)
Fake.reply = "sleep"
slow = AssistantClient(dataclasses.replace(S, assistant_timeout_s=0.5))
e = error_of(lambda: slow.ask("hi"))
check("timeout -> RuntimeError saying it didn't answer", isinstance(e, RuntimeError) and "didn't answer" in str(e))
time.sleep(1.2)  # let the slow handler finish before the next request
e = error_of(lambda: client.ask("   "))
check("empty request rejected without a call", isinstance(e, ValueError))
dead = AssistantClient(dataclasses.replace(S, assistant_url="http://127.0.0.1:9"))
e = error_of(lambda: dead.ask("hi"))
check("unreachable -> RuntimeError (network error)", isinstance(e, RuntimeError) and "reach" in str(e))

# --- tool + prompt wiring ----------------------------------------------------------
Fake.reply = (200, {"choices": [{"message": {"content": "Reminder set for 5pm."}}]})
robot = fe.Robot()
plain = {t.name for t in build_tools(robot, fe.Speech(), None, S)}
check("no assistant -> no ask_assistant tool", "ask_assistant" not in plain)
tools = {t.name: t for t in build_tools(robot, fe.Speech(), None, S, assistant=client)}
tool = tools.get("ask_assistant")
check("assistant given -> ask_assistant tool", tool is not None)
check("tool description uses the configured name", "Ask Sunny" in tool.description and "let me ask Sunny" in tool.description)
check("ask_assistant must be read by the model (not safe_to_end_turn)", tool.safe_to_end_turn is False)
result = tool.handler({"request": "Remind me at 5pm to call mom"})
check("tool result carries the reply under the name", result.ok and result.message == "Sunny says: Reminder set for 5pm.")
section = assistant_prompt_section("Sunny")
check("prompt section names the assistant", "Sunny" in section and "ask_assistant" in section)
check("prompt section asks to confirm actions affecting others", "only call it once they say yes" in section)
check("section included in the system prompt when given", section in build_system_prompt("x", assistant_section=section))
check("no section when not given", "ask_assistant" not in build_system_prompt("x"))

# --- engine: say + ask_assistant in one response goes back to the model --------------
script = [
    fe.NS(content="", tool_calls=[fe.call("say", text="Let me ask Sunny!"),
                                  fe.call("ask_assistant", request="What's the weather in Lisbon?")]),
    fe.NS(content="", tool_calls=[fe.call("say", text="Sunny says it's sunny!")]),
]
chat = fe.Chat(script)
engine = CozmoEngine(S, robot, chat, fe.Speech(),
                     build_tools(robot, fe.Speech(), chat, S, assistant=client), Conversation("sys"))
engine.handle_turn("what's the weather?")
spoke = [e for e in robot.events if e[0] == "speak"]
check(f"turn didn't end after 'let me ask' - answer spoken too (2 says, got {len(spoke)})", len(spoke) == 2)
tool_msgs = [m for m in engine.conversation.messages if m["role"] == "tool"]
check("assistant's reply is in the conversation for the model", any("Sunny says:" in m["content"] for m in tool_msgs))

Fake.reply = (500, "down")
chat = fe.Chat([fe.NS(content="", tool_calls=[fe.call("ask_assistant", request="hi")])])
engine = CozmoEngine(S, robot, chat, fe.Speech(),
                     build_tools(robot, fe.Speech(), chat, S, assistant=client), Conversation("sys"))
engine.handle_turn("ask Sunny hi")
tool_msgs = [m for m in engine.conversation.messages if m["role"] == "tool"]
check("assistant failure becomes an ERROR result, not a crash", tool_msgs and tool_msgs[-1]["content"].startswith("ERROR:"))

# --- background mode (ASSISTANT_BACKGROUND, assistant_relay.py) -----------------------
from cozmo_brain.assistant_relay import AssistantRelay  # noqa: E402

def wait_for(cond, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond(): return True
        time.sleep(0.02)
    return False

def bg_engine(script):
    relay = AssistantRelay(client)
    chat = fe.Chat(script)
    eng = CozmoEngine(S, robot, chat, fe.Speech(),
                      build_tools(robot, fe.Speech(), chat, S, assistant=client, assistant_relay=relay),
                      Conversation("sys"))
    relay.attach(eng)
    return eng, relay, chat

tools = {t.name: t for t in build_tools(robot, fe.Speech(), None, S, assistant=client, assistant_relay=AssistantRelay(client))}
bg_tool = tools["ask_assistant"]
check("background tool may end a turn (result is only 'sent')", bg_tool.safe_to_end_turn is True)
check("background description says the answer comes later", "comes back later" in bg_tool.description)
check("background prompt section explains later answers", "come back later" in assistant_prompt_section("Sunny", background=True))
check("waiting prompt section doesn't", "come back later" not in assistant_prompt_section("Sunny"))

# Outside a listening window: the relay thread delivers the answer itself, as a turn.
Fake.reply = "sleep"  # answers "late" after 1.5s
robot.events.clear()
eng, relay, chat = bg_engine([
    fe.NS(content="", tool_calls=[fe.call("ask_assistant", request="Remind me at 5pm to call mom"),
                                  fe.call("say", text="I've asked Sunny!")]),  # skip mode: turn ends here
    fe.NS(content="", tool_calls=[fe.call("say", text="Sunny set your 5pm reminder!")]),
])
started = time.monotonic()
eng.handle_turn("remind me at 5 to call mom")
took = time.monotonic() - started
check(f"turn returns without waiting for the 1.5s answer ({took:.2f}s)", took < 1.0)
first = [m for m in eng.conversation.messages if m["role"] == "tool" and "Asked Sunny" in m["content"]]
check("tool result says asked, answer later", bool(first) and "comes later" in first[0]["content"])
check("answer delivered on its own as a second spoken turn",
      wait_for(lambda: len([e for e in robot.events if e[0] == "speak"]) == 2))
notes = [m for m in eng.conversation.messages if m["role"] == "user" and "just answered" in m["content"]]
check("delivery note carries the request and the reply",
      bool(notes) and "Remind me at 5pm to call mom" in notes[0]["content"] and ": late " in notes[0]["content"])
check("queue empty and event cleared after delivery", not eng.announce_event.is_set())

# Inside a listening window: queued for the vad loop, not spoken by the relay thread.
Fake.reply = (200, {"choices": [{"message": {"content": "Sunny."}}]})
robot.events.clear()
eng, relay, chat = bg_engine([fe.NS(content="", tool_calls=[fe.call("say", text="Here's Sunny's answer!")])])
eng.listening_window_open = True
relay.submit("What's the weather?")
check("in a window: answer queued and announce_event set", wait_for(eng.announce_event.is_set))
time.sleep(0.2)
check("in a window: relay thread didn't speak", not [e for e in robot.events if e[0] == "speak"])
check("vad loop's deliver_announcements() speaks it", eng.deliver_announcements() and len(robot.events) == 1)
check("nothing left -> deliver_announcements() False", eng.deliver_announcements() is False)

# Failure in the background still reaches the person, honestly.
Fake.reply = (500, "down")
eng, relay, chat = bg_engine([fe.NS(content="", tool_calls=[fe.call("say", text="Sunny's not answering.")])])
eng.listening_window_open = True
relay.submit("hi")
check("failure queued as a note", wait_for(eng.announce_event.is_set))
eng.deliver_announcements()
fail_notes = [m for m in eng.conversation.messages if m["role"] == "user" and "failed" in m["content"]]
check("failure note says don't make up an answer", bool(fail_notes) and "make up" in fail_notes[0]["content"])

# In-flight cap.
Fake.reply = "sleep"
relay = AssistantRelay(AssistantClient(dataclasses.replace(S, assistant_timeout_s=0.3)))
results = [relay.submit(f"q{i}")[0] for i in range(4)]
check(f"at most 3 requests in flight ({results})", results == [True, True, True, False])
check("empty request refused", relay.submit("  ")[0] is False)
time.sleep(1.6)

# --- delivery fallback: chat failing when the answer arrives ----------------------------
import requests  # noqa: E402
from cozmo_brain import engine as engine_mod  # noqa: E402
from cozmo_brain import assistant_relay as relay_mod  # noqa: E402
engine_mod._ANNOUNCE_RETRY_S = 0.05

class SaidSpeech:
    def __init__(self): self.said = []
    def synthesize(self, t, p): self.said.append(t); return p

class FlakyChat:
    """Raises a 429-style error for the first `fails` calls, then scripted."""
    def __init__(self, fails, script=()):
        self.fails, self.script, self.n = fails, list(script), 0
    def chat(self, messages, tools=None):
        self.n += 1
        if self.n <= self.fails: raise requests.HTTPError("429 Too Many Requests")
        return self.script.pop(0) if self.script else fe.DONE

def fallback_engine(chat):
    speech = SaidSpeech()
    eng = CozmoEngine(S, robot, chat, speech, build_tools(robot, speech, chat, S), Conversation("sys"))
    eng.listening_window_open = True  # deliver from the test, not a thread
    return eng, speech

eng, speech = fallback_engine(FlakyChat(fails=99))
eng.queue_announcement("[Sunny just answered: It's 24 degrees.]", "Sunny says: It's 24 degrees.")
eng.deliver_announcements()
check(f"chat down twice -> fallback said word for word ({speech.said})", speech.said == ["Sunny says: It's 24 degrees."])
check("fallback recorded in the conversation", any("[I said this out loud:] Sunny says" in (m.get("content") or "")
      for m in eng.conversation.messages if m["role"] == "assistant"))
check("retried once before falling back (2 LLM attempts)", eng._ollama.n == 2)

eng, speech = fallback_engine(FlakyChat(fails=1, script=[fe.NS(content="", tool_calls=[fe.call("say", text="It's 24 and sunny!")])]))
eng.queue_announcement("[Sunny just answered: It's 24 degrees.]", "Sunny says: It's 24 degrees.")
eng.deliver_announcements()
check(f"chat fails once -> retry speaks in Cozmo's words, no fallback ({speech.said})", speech.said == ["It's 24 and sunny!"])

eng, speech = fallback_engine(fe.Chat([fe.NS(content="", tool_calls=[fe.call("say", text="Got it!")])]))
eng.queue_announcement("[note]", "FALLBACK")
eng.deliver_announcements()
check("normal delivery: one turn, no fallback", speech.said == ["Got it!"] and eng._ollama.n == 1)

eng, speech = fallback_engine(FlakyChat(fails=99))
eng.queue_announcement("[note with no fallback]")
eng.deliver_announcements()
check("no fallback given -> nothing said, no crash", speech.said == [])

# The relay supplies the fallback, for answers and for failures.
queued = []
class QEngine:
    listening_window_open = True
    def queue_announcement(self, note, fallback=None): queued.append(fallback)
Fake.reply = (200, {"choices": [{"message": {"content": "Reminder set for 5pm."}}]})
r = AssistantRelay(client); r.attach(QEngine()); r.submit("remind me at 5")
check("relay fallback for an answer: '<name> says: <reply>'", wait_for(lambda: queued) and queued[0] == "Sunny says: Reminder set for 5pm.")
queued.clear(); Fake.reply = (500, "down")
r.submit("hi")
check("relay fallback for a failure: honest sorry", wait_for(lambda: queued) and "couldn't get an answer from Sunny" in queued[0])
long = "This is a sentence. " * 40
check("long answers cut at a sentence end for the fallback",
      len(relay_mod._spoken(long)) <= 400 and relay_mod._spoken(long).endswith("."))
check("short answers kept whole", relay_mod._spoken("Hi there.") == "Hi there.")

# --- vad: idle_stop_event only stops a recording nobody is talking in -----------------
import io, math, struct, types  # noqa: E401,E402
fake_vad = types.ModuleType("webrtcvad")
class _Vad:
    def __init__(self, a): pass
    def is_speech(self, frame, rate): return any(frame)
fake_vad.Vad = _Vad
sys.modules["webrtcvad"] = fake_vad
from cozmo_brain.audio import vad  # noqa: E402
F = 960  # bytes per 30ms frame
tone = lambda n: b"".join(struct.pack("<h", int(3000 * math.sin(i / 3))) for i in range(n * F // 2))
silence = lambda n: b"\0" * F * n
class _P:
    def __init__(self, d): self.stdout = io.BytesIO(d)
    def terminate(self): pass
    def wait(self, timeout=None): pass
    def poll(self): return 0
def rec(data, event):
    vad.subprocess.Popen = lambda *a, **k: _P(data)
    return vad.record_until_silence(fe._harness.tmp("a.wav"), "dev", 2, 300, 5, 60, 200, idle_stop_event=event)
ev = threading.Event(); ev.set()
check("idle_stop_event set, silence -> stops, nothing captured", rec(silence(100), ev) is False)
ev2 = threading.Event()
check("idle_stop_event clear -> normal capture", rec(silence(5) + tone(20) + silence(20), ev2) is True)
# Speech starts, then the event is set mid-utterance: the utterance must survive.
class _SetMid(io.BytesIO):
    def __init__(self, d, at, e): super().__init__(d); self.n, self.at, self.e = 0, at, e
    def read(self, k):
        self.n += 1
        if self.n == self.at: self.e.set()
        return super().read(k)
ev3 = threading.Event()
vad.subprocess.Popen = lambda *a, **k: types.SimpleNamespace(
    stdout=_SetMid(silence(3) + tone(20) + silence(20), 10, ev3), terminate=lambda: None, wait=lambda timeout=None: None, poll=lambda: 0)
check("event set mid-speech -> utterance still captured",
      vad.record_until_silence(fe._harness.tmp("b.wav"), "dev", 2, 300, 5, 60, 200, idle_stop_event=ev3) is True)

server.shutdown()
print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
sys.exit(1 if failures else 0)
