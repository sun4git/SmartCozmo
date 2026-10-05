"""python -m cozmo_dashboard [--host H] [--port P] - see dashboard.sh."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
from pathlib import Path

from cozmo_dashboard import DEFAULT_PORT, envfile
from cozmo_dashboard.runner import Runner
from cozmo_dashboard.server import Dashboard, make_server

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    # Dashboard settings live in .env next to the app's (DASHBOARD_PORT,
    # DASHBOARD_HOST, DASHBOARD_TOKEN); flags win. Read straight from the
    # file so this needs no packages beyond the standard library.
    env = envfile.parse_env(ROOT / ".env")

    def setting(name: str, default: str) -> str:
        return os.environ.get(name) or env.get(name) or default

    parser = argparse.ArgumentParser(description="Cozmo Control Room: start/stop SmartCozmo and watch it.")
    parser.add_argument("--host", default=setting("DASHBOARD_HOST", "127.0.0.1"),
                        help="Address to listen on (default 127.0.0.1 = this machine only; "
                        "0.0.0.0 = reachable from your network - set DASHBOARD_TOKEN too).")
    parser.add_argument("--port", type=int, default=int(setting("DASHBOARD_PORT", str(DEFAULT_PORT))))
    args = parser.parse_args(argv)

    data_dir = Path(setting("DATA_DIR", "data"))
    data_dir = data_dir if data_dir.is_absolute() else ROOT / data_dir
    token = setting("DASHBOARD_TOKEN", "")

    runner = Runner(ROOT, data_dir / "logs")
    dash = Dashboard(ROOT, data_dir, ROOT / ".env", ROOT / ".env.example", runner, args.host, token)
    try:
        server = make_server(dash, args.host, args.port)
    except OSError as e:
        print(f"Can't listen on {args.host}:{args.port}: {e}", file=sys.stderr)
        return 1

    # SIGTERM (systemd, pkill) should end things as cleanly as Ctrl+C does.
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())

    shown = "localhost" if args.host in ("127.0.0.1", "::1") else args.host
    print(f"Cozmo Control Room: http://{shown}:{args.port}", flush=True)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        if token:
            print("  Listening on the network; a dashboard token is required.")
        else:
            print("  WARNING: listening on the network with no DASHBOARD_TOKEN - anyone who can reach "
                  "this port can start/stop Cozmo and edit .env. Set DASHBOARD_TOKEN in .env.", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        print("Stopping the dashboard" + (" (and Cozmo)..." if runner.status()["state"] != "stopped" else "..."))
        runner.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
