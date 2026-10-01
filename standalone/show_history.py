#!/usr/bin/env python3
"""
Print archived conversations (data/history/) as a readable transcript.

Each run of the app is one file, data/history/<date>/<start time>.jsonl;
in --mode vad, each wake word/tap starts a session inside it. This shows
what was heard, what Cozmo said (with his mood/gesture), the other tools
he used, errors, and the photos saved next to the transcript. The full
detail, tool results included, is in the .jsonl itself.

Usage (from the repo root):
  python3 standalone/show_history.py              # the latest run
  python3 standalone/show_history.py 2026-10-01   # every run that day
  python3 standalone/show_history.py --list       # which days/runs exist
  python3 standalone/show_history.py data/history/2026-10-01/15-39-51.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cozmo_brain.config import settings  # noqa: E402
from cozmo_brain.history_archive import read_records, run_files  # noqa: E402


def _time(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return "--:--:--"


def _args(raw) -> dict:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def show(path: Path) -> None:
    for record in read_records(path):
        kind, t = record.get("type"), _time(record.get("ts", ""))
        if kind == "run_start":
            info = ", ".join(f"{k}: {v}" for k, v in record.items() if k not in ("ts", "type"))
            print(f"\n===== {path.parent.name} {t}  ({info})")
        elif kind == "session_start":
            print(f"\n--- {t} woke up ({record.get('trigger', '?')})")
        elif kind == "session_end":
            print(f"--- {t} back to idle")
        elif kind == "run_end":
            print(f"===== {t} stopped")
        elif kind == "photo":
            print(f"{t}   [photo: {record.get('file')}]")
        elif kind == "message":
            _show_message(t, record.get("message") or {}, record.get("images") or [])


def _show_message(t: str, message: dict, images: list[str]) -> None:
    role = message.get("role")
    if role == "user":
        text = str(message.get("content", "")).split("\n\n[Status:")[0].strip()
        photos = f"  [photo: {', '.join(images)}]" if images else ""
        print(f"{t} {'(note)' if text.startswith('[') else 'you'}: {text}{photos}")
    elif role == "assistant":
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            name, args = fn.get("name"), _args(fn.get("arguments"))
            if name == "say":
                extras = ", ".join(str(args[k]) for k in ("mood", "gesture") if args.get(k))
                print(f"{t} Cozmo{f' [{extras}]' if extras else ''}: {args.get('text', '')}")
            else:
                shown = ", ".join(f"{k}={v}" for k, v in args.items())
                print(f"{t}   ({name}{f' {shown}' if shown else ''})")
        if message.get("content") and not message.get("tool_calls"):
            # Plain text is never spoken - an unprompted note, or the model
            # writing outside `say`.
            print(f"{t}   (not spoken) {message['content']}")
    elif role == "tool" and str(message.get("content", "")).startswith("ERROR"):
        print(f"{t}   ({message.get('name')} failed: {message['content'][len('ERROR: '):]})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Show archived Cozmo conversations.")
    parser.add_argument("what", nargs="?", help="a date (YYYY-MM-DD) or a .jsonl file; default: the latest run")
    parser.add_argument("--list", action="store_true", help="list the archived runs")
    args = parser.parse_args()

    history_dir = Path(settings.data_dir) / "history"
    files = run_files(history_dir)
    if args.list:
        for f in files:
            print(f"{f.parent.name}  {f.stem}")
        print(f"{len(files)} run(s) in {history_dir}")
        return 0
    if args.what and args.what.endswith(".jsonl"):
        chosen = [Path(args.what)]
    elif args.what:
        chosen = [f for f in files if f.parent.name == args.what]
    else:
        chosen = files[-1:]
    if not chosen:
        print(f"No archived conversations found for {args.what or 'any day'} in {history_dir}.")
        return 1
    for path in chosen:
        show(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
