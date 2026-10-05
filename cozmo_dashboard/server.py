"""The dashboard's HTTP server: static page, JSON API, and two live streams
(Server-Sent Events) for the app's log and the conversation."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import mimetypes
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from cozmo_dashboard import data, envfile
from cozmo_dashboard.runner import LOG_LEVELS, MODES, Runner

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
COOKIE = "cozmo_dash"
MAX_BODY = 2_000_000
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}

# Product photos shown in the header. Fetched once through the dashboard and
# cached under data/dashboard/img, so the page works offline afterwards and
# no third party sees the browser. Only these exact URLs are ever fetched.
IMAGE_URLS = {
    "1": "https://m.media-amazon.com/images/I/51fvoEpEUhL._AC_SX679_.jpg",
    "2": "https://m.media-amazon.com/images/I/61Rr0XsbPNL._AC_SX679_.jpg",
    "3": "https://m.media-amazon.com/images/I/51INNNLyxVL._AC_.jpg",
    "4": "https://m.media-amazon.com/images/I/51XcAx9qdDL._AC_SX679_.jpg",
    "5": "https://m.media-amazon.com/images/I/61GN+LgjdJL._AC_SX679_.jpg",
    "6": "https://m.media-amazon.com/images/I/61Vnp5X50oL._AC_SX679_.jpg",
}

LOGIN_PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cozmo Control Room</title>
<style>body{font:16px system-ui,sans-serif;background:#0b1220;color:#e8f4ff;display:grid;place-items:center;height:100vh;margin:0}
form{background:#101a2e;padding:32px;border-radius:20px;box-shadow:0 0 40px #00b4ff33;display:grid;gap:14px;width:min(320px,86vw)}
h1{margin:0;font-size:20px}input,button{font:inherit;padding:12px;border-radius:12px;border:1px solid #2b3d63;background:#0b1220;color:inherit}
button{background:#12a7f5;border:0;color:#02131f;font-weight:700;cursor:pointer}.err{color:#ff7b7b;font-size:14px}</style>
<form method="post" action="/login"><h1>Cozmo Control Room</h1>
<input type="password" name="token" placeholder="Dashboard token" autofocus autocomplete="current-password">
{error}<button>Unlock</button></form>"""


def token_cookie(token: str) -> str:
    """What the cookie holds - a hash, so the token itself never sits in the browser."""
    return hashlib.sha256(("cozmo-dashboard:" + token).encode()).hexdigest()


class Dashboard:
    """Everything a request handler needs."""

    def __init__(self, root: Path, data_dir: Path, env_path: Path, example_path: Path,
                 runner: Runner, host: str, token: str = ""):
        self.root, self.data_dir = Path(root), Path(data_dir)
        self.history_dir = self.data_dir / "history"
        self.env_path, self.example_path = Path(env_path), Path(example_path)
        self.runner, self.host, self.token = runner, host, token
        self.image_dir = self.data_dir / "dashboard" / "img"

    def fetch_image(self, key: str) -> Path | None:
        url = IMAGE_URLS.get(key)
        if url is None:
            return None
        cached = self.image_dir / f"{key}.jpg"
        if cached.is_file():
            return cached
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (cozmo-dashboard)"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                body = resp.read(3_000_000)
            if not body.startswith(b"\xff\xd8"):
                return None
            self.image_dir.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(body)
            return cached
        except (OSError, urllib.error.URLError):
            return None


class Handler(BaseHTTPRequestHandler):
    server_version = "CozmoDashboard"
    dash: Dashboard  # set on the subclass made by make_server()

    def log_message(self, fmt: str, *args: Any) -> None:  # keep the terminal quiet
        logger.debug("%s - %s", self.address_string(), fmt % args)

    # --- plumbing ---------------------------------------------------------

    def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, status: int = 200) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _body_json(self) -> dict[str, Any]:
        if "application/json" not in self.headers.get("Content-Type", ""):
            raise ValueError("Expected a JSON body")
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("Body too large")
        raw = self.rfile.read(length) if length else b"{}"
        obj = json.loads(raw or b"{}")
        if not isinstance(obj, dict):
            raise ValueError("Expected a JSON object")
        return obj

    def _host_ok(self) -> bool:
        """Blocks DNS-rebinding: when bound to loopback only, the Host header must be a loopback name."""
        if self.dash.host not in _LOOPBACK:
            return True
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in _LOOPBACK

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urlparse(origin).netloc == self.headers.get("Host")

    def _authed(self) -> bool:
        if not self.dash.token:
            return True
        cookies = {}
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            cookies[k] = v
        return hmac.compare_digest(cookies.get(COOKIE, ""), token_cookie(self.dash.token))

    # --- routing ----------------------------------------------------------

    def do_GET(self) -> None:
        self._route("GET")

    def do_POST(self) -> None:
        self._route("POST")

    def do_PUT(self) -> None:
        self._route("PUT")

    def _route(self, method: str) -> None:
        url = urlparse(self.path)
        path, query = url.path, {k: v[-1] for k, v in parse_qs(url.query).items()}
        if not self._host_ok():
            return self._error(403, "Unexpected Host header")
        if method == "POST" and path == "/login":
            return self._login()
        if not self._authed():
            if path.startswith("/api/"):
                return self._error(401, "Not signed in")
            return self._send(401, LOGIN_PAGE.replace("{error}", "").encode(), "text/html; charset=utf-8")
        if method != "GET" and not self._origin_ok():
            return self._error(403, "Cross-origin request refused")
        try:
            if method == "GET":
                return self._get(path, query)
            return self._write(method, path)
        except (ValueError, json.JSONDecodeError) as e:
            self._error(400, str(e))
        except PermissionError as e:
            self._error(403, str(e))
        except FileNotFoundError:
            self._error(404, "Not found")
        except RuntimeError as e:
            self._error(409, str(e))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:  # noqa: BLE001 - a bug in one request must not kill the server
            logger.exception("Error handling %s %s", method, path)
            self._error(500, "Internal error - see the dashboard's terminal")

    def _login(self) -> None:
        length = min(int(self.headers.get("Content-Length") or 0), 4096)
        form = parse_qs(self.rfile.read(length).decode("utf-8", errors="replace"))
        supplied = (form.get("token") or [""])[0]
        if self.dash.token and hmac.compare_digest(supplied.encode(), self.dash.token.encode()):
            self._send(303, b"", "text/plain", {
                "Location": "/", "Set-Cookie": f"{COOKIE}={token_cookie(self.dash.token)}; Path=/; HttpOnly; SameSite=Strict"})
        else:
            err = '<div class="err">That token is not right.</div>'
            self._send(401, LOGIN_PAGE.replace("{error}", err).encode(), "text/html; charset=utf-8")

    # --- GET --------------------------------------------------------------

    def _get(self, path: str, q: dict[str, str]) -> None:
        d = self.dash
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path.startswith("/img/"):
            img = d.fetch_image(path[len("/img/"):])
            if img is None:
                return self._error(404, "Image unavailable")
            return self._send(200, img.read_bytes(), "image/jpeg", {"Cache-Control": "max-age=604800"})
        if path == "/api/status":
            return self._json({"runner": d.runner.status(), "host": data.host_view(d.root, d.data_dir)})
        if path == "/api/config":
            return self._json({"modes": MODES, "log_levels": LOG_LEVELS, "auth": bool(d.token),
                               "exposed": d.host not in _LOOPBACK})
        if path == "/api/log":
            return self._json({"lines": d.runner.read_log(int(q.get("limit", 1000)))})
        if path == "/api/log/stream":
            return self._stream_log(int(q.get("after", 0)))
        if path == "/api/conversation/stream":
            return self._stream_conversation()
        if path == "/api/history":
            return self._json({"runs": data.list_runs(d.history_dir)})
        if path == "/api/history/run":
            return self._json(data.load_run(d.history_dir, q.get("id", "")))
        if path == "/api/history/search":
            return self._json({"hits": data.search_runs(d.history_dir, q.get("q", ""))})
        if path == "/api/files":
            return self._json(data.list_dir(d.data_dir, q.get("path", "")))
        if path == "/api/file":
            return self._serve_file(q.get("path", ""), download=q.get("download") == "1")
        if path == "/api/file/text":
            return self._json(data.read_text(d.data_dir, q.get("path", "")))
        if path == "/api/env":
            return self._json(envfile.view(d.env_path, d.example_path))
        if path == "/api/battery":
            return self._json(data.battery_view(d.data_dir))
        if path == "/api/memory":
            return self._json(data.memory_view(d.data_dir))
        self._error(404, "Not found")

    def _static(self, name: str) -> None:
        target = (STATIC_DIR / name).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            return self._error(404, "Not found")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    def _serve_file(self, rel: str, download: bool) -> None:
        path = data.safe_path(self.dash.data_dir, rel)
        if not path.is_file():
            raise FileNotFoundError(rel)
        extra = {"Content-Disposition": f'{"attachment" if download else "inline"}; filename="{path.name}"'}
        self._send(200, path.read_bytes(), data.guess_type(path), extra)

    # --- writes -----------------------------------------------------------

    def _write(self, method: str, path: str) -> None:
        d, body = self.dash, self._body_json()
        if method == "POST" and path == "/api/start":
            return self._json(d.runner.start(
                str(body.get("mode", "voice")), bool(body.get("simulate")), bool(body.get("fresh")),
                body.get("log_level") or None))
        if method == "POST" and path == "/api/stop":
            return self._json(d.runner.stop(force=bool(body.get("force"))))
        if method == "POST" and path == "/api/input":
            d.runner.send_input(str(body.get("text", ""))[:2000])
            return self._json({"ok": True})
        if method == "POST" and path == "/api/env":
            changes = body.get("changes")
            if not isinstance(changes, dict) or not all(v is None or isinstance(v, str) for v in changes.values()):
                raise ValueError("changes must map setting names to text, or null to remove one")
            result = envfile.apply_changes(d.env_path, changes)
            running = d.runner.status()["state"] != "stopped"
            return self._json({**result, "restart_needed": running and bool(result["changed"])})
        if method == "PUT" and path == "/api/file/text":
            return self._json(data.write_memory(d.data_dir, str(body.get("path", "")), str(body.get("text", ""))))
        self._error(404, "Not found")

    # --- live streams -----------------------------------------------------

    def _sse_start(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _sse(self, payload: Any, event: str = "message") -> None:
        self.wfile.write(f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode())
        self.wfile.flush()

    def _stream_log(self, after: int) -> None:
        self._sse_start()
        runner = self.dash.runner
        try:
            # First connect: the buffered backlog. A reconnect passes the last seq it saw.
            backlog = runner.read_log(1500) if after == 0 else runner.wait_log(after, 0)
            if backlog:
                self._sse({"lines": backlog})
                after = backlog[-1]["seq"]
            while True:
                lines = runner.wait_log(after, 15)
                if lines:
                    self._sse({"lines": lines})
                    after = lines[-1]["seq"]
                else:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _stream_conversation(self) -> None:
        import time

        self._sse_start()
        tail = data.ConversationTail(self.dash.history_dir)
        idle = 0
        try:
            while True:
                reset, run_id, events = tail.poll()
                if reset or events:
                    self._sse({"reset": reset, "run": run_id, "events": events})
                    idle = 0
                else:
                    idle += 1
                    if idle >= 30:  # ~15s
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        idle = 0
                time.sleep(0.5)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


def make_server(dash: Dashboard, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"dash": dash})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server
