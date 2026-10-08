# Cozmo Control Room (the dashboard)

A web page for running Cozmo without a terminal: start and stop him in any
mode, watch the log and conversation live, read past conversations, browse
what he saves, and edit `.env`. It's separate from `cozmo_brain/` (it runs
the app as a subprocess, exactly as `run.sh` does, and reads the files the app
already writes), so nothing about the app changes, and it needs nothing
beyond Python's standard library.

![The Control Room's Control page](../docs/images/dashboard.jpg)

```bash
./dashboard.sh                    # then open http://localhost:30540
./dashboard.sh --port 8080
./dashboard.sh --host 0.0.0.0     # reachable from your PC while it runs on the Pi
```

| Page | What it does |
|---|---|
| **Control** | Pick a mode (voice / hands-free / text / calibrate), tick *Simulated robot* or *Fresh start*, choose a log level, press **Start**. **Stop** sends the same clean shutdown as Ctrl+C (he saves the conversation first); if he hasn't exited after 30 s it kills him. While running there's a box to send a line to his terminal: your message in text mode, an empty Send = pressing Enter in voice mode (or a simulated tap with *Simulated robot*). Also shows his battery, the host's temperature/memory/disk, and the latest conversation and log. His face on this page sleeps when stopped and reacts to the mood of his last reply |
| **Live** | The conversation as chat bubbles (with mood, gesture, tool calls, errors, photos) beside the live log, filterable by level and text, with Save log |
| **History** | Every archived run (`data/history/`) with search across everything ever said. Each run downloads as HTML (one self-contained page, photos included, opens offline), plain text, or the raw `.jsonl` |
| **Files** | Browse `data/`: transcripts, photos (click to enlarge), logs, `battery.jsonl`. `memory.md` can be edited here (the app re-reads it every turn; the old version is kept as `memory.md.bak`). Nothing else is editable or deletable from the page |
| **Settings** | `.env` as a form, grouped like `.env.example` with its comments one click away, plus a search box. Only the lines you change are touched, comments and layout are kept, and a timestamped `.env.bak-...` is saved first (the same convention as `sync_env.py`). Secrets (anything with KEY/TOKEN/SECRET/PASSWORD in the name) are never sent to the browser: you can replace or remove them, not read them. Cozmo reads `.env` only at startup, so after saving while he's running the page offers a **Restart** |
| **Cozmo** | Battery voltage over time from `data/battery.jsonl` (with your low/critical thresholds) and recent trips off the charger; what he remembers (`memory.md`) |

Notes:

- **Logs.** Everything the app prints is also saved to `data/logs/<date>/<time>.log`, one file per start from the dashboard (browse them under Files). Runs started from a terminal with `run.sh` are not captured there, but their *conversation* still shows up live (the dashboard follows the newest transcript in `data/history/`).
- **One copy at a time.** On Linux the dashboard notices a `cozmo_brain` started from a terminal and refuses to start a second one (two would fight over the robot and the mic). It can't stop one it didn't start.
- **Stopping the dashboard** (Ctrl+C, systemd stop) also stops the Cozmo it started, because the app's output pipe closes with it.
- **Network access and security.** Default is `127.0.0.1` (this machine only). To open it from another computer set `DASHBOARD_HOST=0.0.0.0` **and** `DASHBOARD_TOKEN=<something>` in `.env` (or use an SSH tunnel: `ssh -L 30540:localhost:30540 pi@<pi-address>`). Without a token, anyone on the network who can reach the port can start/stop Cozmo and change `.env`. The page is plain HTTP, so the token travels unencrypted on your LAN: fine at home, not across the internet. Writes require a JSON request from the same origin, and when listening on localhost only, requests whose `Host` isn't a localhost name are refused (guards against a malicious web page poking at it).
- **Photos.** The pictures in the header (rotating) and on the History page's empty state are the project author's own photos of their Cozmo, in `cozmo_dashboard/static/img/`, covered by the repo's license. The dashboard never downloads anything.
- **Settings it reads from `.env`:** `DASHBOARD_PORT` (default 30540), `DASHBOARD_HOST`, `DASHBOARD_TOKEN`. Flags override them. `DATA_DIR` is honoured too.
- **On Windows** the dashboard runs, but Stop ends the app abruptly (no graceful SIGTERM there), so the end-of-run memory check doesn't happen. It's meant for the Pi.
- Tests: `tests/test_dashboard.py` (part of `run_all.py`) covers `.env` editing, transcript parsing, the runner against a fake app, and the HTTP layer including the security checks. The page itself has only been looked at in Edge on Windows so far, not on the Pi.

## Setting a token

There is no default token: `DASHBOARD_TOKEN` is empty (no password). You only
need one when `DASHBOARD_HOST` is not `127.0.0.1`. Generate one and put it in
`.env`; don't reuse a password from elsewhere:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```

```
DASHBOARD_HOST=0.0.0.0
DASHBOARD_TOKEN=<the generated value>
```

Restart `./dashboard.sh`; the browser asks once and remembers it in a cookie.

## Files

- `dashboard.sh` - launcher (uses `cozmo-env` if present)
- `cozmo_dashboard/server.py` - HTTP server, API, live streams
- `cozmo_dashboard/runner.py` - starts/stops the app, keeps its log
- `cozmo_dashboard/envfile.py` - comment-preserving `.env` editing
- `cozmo_dashboard/data.py` - transcripts, files, battery, host stats
- `cozmo_dashboard/static/` - the page (HTML, CSS, JS)
