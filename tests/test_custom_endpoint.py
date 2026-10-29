"""Test: CHAT_PROVIDER/VISION_PROVIDER = custom1..3 (any OpenAI-compatible
endpoint). A local fake server stands in for the service, so the real HTTP
path is exercised: URL, Authorization header, model per turn (chat vs.
vision), tool-call parsing, /models reachability, chat-vs-vision routing
across two slots, and the config errors for an incomplete slot."""
import base64
import dataclasses
import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import _harness  # noqa: F401 - repo on sys.path, real .env ignored

from cozmo_brain.config import CustomEndpoint, Settings
from cozmo_brain.llm import _ChatRouter, create_chat_client
from cozmo_brain.llm.custom_client import CustomChatClient, normalize_base_url
from cozmo_brain.llm.ollama_client import OllamaClient

failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: failures.append(name)


class FakeService:
    """Records every request; answers /chat/completions with a tool call when
    the last user message says 'tool', else plain text. /models needs the key."""

    def __init__(self, key):
        self.key = key
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                authed = self.headers.get("Authorization") == f"Bearer {outer.key}"
                outer.requests.append(("GET", self.path, authed, None))
                self._send(200 if authed else 401, {"data": []})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                authed = self.headers.get("Authorization") == f"Bearer {outer.key}"
                outer.requests.append(("POST", self.path, authed, body))
                if not authed:
                    return self._send(401, {"error": "bad key"})
                wants_tool = "tool" in str(body["messages"][-1].get("content"))
                message = {"role": "assistant", "content": "" if wants_tool else f"hi from {body['model']}"}
                if wants_tool:
                    message["tool_calls"] = [{"id": "call_1", "type": "function",
                                              "function": {"name": "say", "arguments": json.dumps({"text": "yo"})}}]
                self._send(200, {"choices": [{"message": message}]})

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()


PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 16).decode()
TEXT = [{"role": "user", "content": "hello"}]
TOOL = [{"role": "user", "content": "use a tool"}]
IMAGE = [{"role": "user", "content": "what is this?", "images": [PNG_B64]}]

# --- normalize_base_url ---
check("trailing slash dropped", normalize_base_url("https://openrouter.ai/api/v1/") == "https://openrouter.ai/api/v1")
check("full /chat/completions URL trimmed to the base",
      normalize_base_url("https://integrate.api.nvidia.com/v1/chat/completions") == "https://integrate.api.nvidia.com/v1")
check("full URL with trailing slash trimmed too",
      normalize_base_url(" https://x.example/v1/chat/completions/ ") == "https://x.example/v1")
for bad in ("openrouter.ai/api/v1", "ftp://x.example/v1", "https://", ""):
    try:
        normalize_base_url(bad)
        check(f"bad base URL {bad!r} rejected", False)
    except ValueError:
        check(f"bad base URL {bad!r} rejected", True)

# --- settings: slots read from the environment, incomplete slots named ---
SECRET = "sk-test-NOT-A-REAL-KEY"
saved = {k: v for k, v in os.environ.items() if k.startswith("CUSTOM")}
for k in saved:
    del os.environ[k]
try:
    blank = Settings()
    check("empty slot has the default label", blank.custom1 == CustomEndpoint(name="custom1"))
    for provider in ("custom1", "custom2", "custom3"):
        try:
            blank.custom_endpoint(provider)
            check(f"{provider} with nothing set is refused", False)
        except RuntimeError as e:
            n = provider[-1]
            check(f"{provider} error names its three required settings",
                  all(f"CUSTOM{n}_{k}" in str(e) for k in ("BASE_URL", "API_KEY", "MODEL")))
    for bad in ("custom0", "custom4", "custom", "customx", "groq"):
        try:
            blank.custom_endpoint(bad)
            check(f"{bad!r} is not a slot", False)
        except ValueError:
            check(f"{bad!r} is not a slot", True)

    os.environ.update({"CUSTOM2_NAME": "OpenRouter", "CUSTOM2_BASE_URL": " https://openrouter.ai/api/v1 ",
                       "CUSTOM2_API_KEY": SECRET, "CUSTOM2_MODEL": "some/model", "CUSTOM2_VISION_MODEL": "some/vision"})
    s = Settings()
    ep = s.custom_endpoint("CUSTOM2")
    check("slot 2 read from CUSTOM2_* (provider name case-insensitive, values stripped)",
          ep == CustomEndpoint("OpenRouter", "https://openrouter.ai/api/v1", SECRET, "some/model", "some/vision"))
    check("other slots stay empty", s.custom1.base_url == "" and s.custom3.model == "")

    os.environ["CUSTOM2_API_KEY"] = ""
    try:
        Settings().custom_endpoint("custom2")
        check("missing key is refused", False)
    except RuntimeError as e:
        check("missing key: error names only CUSTOM2_API_KEY", "CUSTOM2_API_KEY" in str(e) and "CUSTOM2_MODEL" not in str(e))
finally:
    for k in [k for k in os.environ if k.startswith("CUSTOM")]:
        del os.environ[k]
    os.environ.update(saved)

# --- the client against a fake service ---
KEY_A, KEY_B = "key-for-a", "key-for-b"
a, b = FakeService(KEY_A), FakeService(KEY_B)
try:
    base = Settings()
    s = dataclasses.replace(
        base, chat_provider="custom1", vision_provider="",
        custom1=CustomEndpoint("A", a.url + "/chat/completions", KEY_A, "chat-a", "vision-a"),
        custom2=CustomEndpoint("B", b.url + "/", KEY_B, "chat-b", ""),
    )

    client = create_chat_client(s)
    check("custom1 builds a CustomChatClient (no router when vision follows chat)", isinstance(client, CustomChatClient))
    check("is_reachable: /models with the right key", client.is_reachable())
    check("reachability hit <base>/models", a.requests[-1][:3] == ("GET", "/v1/models", True))

    r = client.chat(TEXT)
    check("plain chat returns the text", r.content == "hi from chat-a" and not r.tool_calls)
    method, path, authed, body = a.requests[-1]
    check("chat POSTs to <base>/chat/completions even when the full URL was pasted", path == "/v1/chat/completions")
    check("chat sends the Bearer key", authed)
    check("chat turn uses the chat model", body["model"] == "chat-a")

    r = client.chat(IMAGE)
    check("image turn uses the vision model", a.requests[-1][3]["model"] == "vision-a")
    content = a.requests[-1][3]["messages"][-1]["content"]
    check("image sent as an OpenAI image_url part",
          isinstance(content, list) and any(p.get("type") == "image_url" for p in content))

    r = client.chat(TOOL, tools=[{"type": "function", "function": {"name": "say", "parameters": {}}}])
    check("tool call parsed (name, JSON arguments, id)",
          len(r.tool_calls) == 1 and r.tool_calls[0].name == "say"
          and r.tool_calls[0].arguments == {"text": "yo"} and r.tool_calls[0].id == "call_1")
    check("tools passed through to the service", "tools" in a.requests[-1][3])

    wrong = dataclasses.replace(s, custom1=dataclasses.replace(s.custom1, api_key="wrong"))
    check("is_reachable is False with a rejected key", not create_chat_client(wrong).is_reachable())

    # empty vision model falls back to the chat model
    s_b = dataclasses.replace(s, chat_provider="custom2")
    create_chat_client(s_b).chat(IMAGE)
    check("empty CUSTOM2_VISION_MODEL falls back to its chat model", b.requests[-1][3]["model"] == "chat-b")
    check("trailing-slash base URL works", b.requests[-1][1] == "/v1/chat/completions")

    # chat on slot 1, vision on slot 2: different services, same app
    s_router = dataclasses.replace(s, chat_provider="custom1", vision_provider="custom2")
    router = create_chat_client(s_router)
    check("different chat and vision slots build a router", isinstance(router, _ChatRouter))
    a_before, b_before = len(a.requests), len(b.requests)
    router.chat(TEXT)
    check("text turn goes to slot 1 only", len(a.requests) == a_before + 1 and len(b.requests) == b_before)
    router.chat(IMAGE)
    check("image turn goes to slot 2 only, with slot 2's key",
          len(a.requests) == a_before + 1 and len(b.requests) == b_before + 1 and b.requests[-1][2])

    # a custom slot alongside a built-in provider
    mixed = create_chat_client(dataclasses.replace(s, chat_provider="ollama", vision_provider="custom1"))
    check("ollama chat + custom1 vision builds a router", isinstance(mixed, _ChatRouter))
finally:
    a.stop()
    b.stop()

# --- factory errors, built-ins untouched ---
try:
    create_chat_client(dataclasses.replace(Settings(), chat_provider="custom9"))
    check("unknown provider refused", False)
except ValueError as e:
    check("unknown provider error lists the custom slots", "custom1" in str(e) and "custom3" in str(e))
try:
    create_chat_client(dataclasses.replace(Settings(), chat_provider="custom1"))
    check("unfilled slot refused at startup", False)
except RuntimeError as e:
    check("unfilled slot error points at CUSTOM1_*", "CUSTOM1_BASE_URL" in str(e))
try:
    create_chat_client(dataclasses.replace(
        Settings(), chat_provider="custom1", custom1=CustomEndpoint("A", "not-a-url", "k", "m", "")))
    check("malformed base URL refused at startup", False)
except ValueError:
    check("malformed base URL refused at startup", True)
check("ollama still builds as before", isinstance(create_chat_client(dataclasses.replace(Settings(), chat_provider="ollama")), OllamaClient))

# --- log hygiene: warn on plain http to a remote host, never log the key ---
class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []
    def emit(self, record):
        self.lines.append(record.getMessage())

logging.disable(logging.NOTSET)
cap = Capture()
logging.getLogger("cozmo_brain.llm.custom_client").addHandler(cap)
logging.getLogger("cozmo_brain.llm.custom_client").setLevel(logging.INFO)
try:
    def build(url):
        cap.lines.clear()
        CustomChatClient(dataclasses.replace(Settings(), custom1=CustomEndpoint("N", url, SECRET, "m", "")), "custom1")
        return list(cap.lines)

    remote = build("http://api.example.invalid/v1")
    check("plain http to a remote host warns", any("unencrypted" in l for l in remote))
    check("the API key is never logged", not any(SECRET in l for l in remote))
    check("https to a remote host: no warning", not any("unencrypted" in l for l in build("https://api.example.invalid/v1")))
    check("http to localhost: no warning", not any("unencrypted" in l for l in build("http://localhost:1234/v1")))
    check("http to a LAN address: no warning", not any("unencrypted" in l for l in build("http://192.168.1.50:8000/v1")))
finally:
    logging.getLogger("cozmo_brain.llm.custom_client").removeHandler(cap)
    logging.disable(logging.CRITICAL)

sys.exit(1 if failures else 0)
