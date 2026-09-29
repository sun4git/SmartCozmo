#!/usr/bin/env python3
"""
Compare your .env against .env.example and (optionally) add what's missing.

Why: .env is gitignored (it holds your API keys), so `git pull` never
updates it - new settings added to .env.example don't reach it on their own.
Every setting has a built-in default in cozmo_brain/config.py, so a missing
one isn't an error; but you can't see or tune a setting that isn't there,
and an OLD value that's still present overrides the new default (e.g. a
removed model name).

What it does:
  - lists settings in .env.example that are missing from .env, with their
    example values;
  - flags known-stale entries (see _KNOWN_STALE / _REPLACED below);
  - lists settings in .env that .env.example doesn't know about - by NAME
    only, never by value, since .env holds secrets.

It never changes or deletes an existing line. With --apply it APPENDS the
missing settings (with their explanatory comments copied from .env.example)
under a dated header, after saving a timestamped backup of .env.

Usage (from the repo root):
  python3 standalone/sync_env.py            # report only, changes nothing
  python3 standalone/sync_env.py --apply    # append missing settings (asks first)
  python3 standalone/sync_env.py --apply --yes
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SETTING = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")

# Values that were valid once but are known to be broken now. Shown with the
# fix; never rewritten automatically.
_KNOWN_STALE = {
    "GROQ_CHAT_MODEL": {
        "llama-3.3-70b-versatile": "removed from Groq (model_not_found) - use qwen/qwen3.8-27b",
    },
}
# Settings that were renamed/replaced and are now ignored by the code.
_REPLACED = {
    "END_TURN_AFTER_FINAL_SAY": "replaced by FINAL_LLM_CALL (sync|async|skip) - now ignored, safe to delete",
}


def parse_example(path: Path) -> dict[str, tuple[str, list[str]]]:
    """KEY -> (the setting's line as written, the comment block right above it)."""
    settings: dict[str, tuple[str, list[str]]] = {}
    comments: list[str] = []
    after_setting = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if line.startswith("#"):
            if after_setting:
                comments = []  # a new comment block, not the previous setting's
            comments.append(line)
            after_setting = False
            continue
        match = _SETTING.match(line)
        if match:
            # Settings in a run with no comment/blank line between them share
            # the comment block above the run (as in .env.example).
            settings.setdefault(match.group(1), (line, comments))
            after_setting = True
            continue
        if not line.strip():
            comments = []
            after_setting = False
    return settings


def parse_env(path: Path) -> dict[str, str]:
    """KEY -> value, for active (uncommented) settings only."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        match = _SETTING.match(raw.strip())
        if match:
            values.setdefault(match.group(1), match.group(2).split(" #")[0].strip().strip('"').strip("'"))
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare .env against .env.example; optionally append missing settings.")
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument("--example", type=Path, default=ROOT / ".env.example")
    parser.add_argument("--apply", action="store_true", help="append the missing settings to .env (backs it up first)")
    parser.add_argument("--yes", action="store_true", help="with --apply: don't ask for confirmation")
    args = parser.parse_args(argv)

    if not args.example.exists():
        print(f"{args.example} not found.", file=sys.stderr)
        return 2
    if not args.env.exists():
        print(f"{args.env} not found - create it first: cp .env.example .env", file=sys.stderr)
        return 2

    example = parse_example(args.example)
    env = parse_env(args.env)
    missing = [key for key in example if key not in env]
    unknown = sorted(key for key in env if key not in example)

    problems = False
    for key, stale in _KNOWN_STALE.items():
        if env.get(key) in stale:
            print(f"STALE  {key}={env[key]}\n       -> {stale[env[key]]}")
            problems = True
    for key, why in _REPLACED.items():
        if key in env:
            print(f"OLD    {key}\n       -> {why}")
            problems = True

    if missing:
        print(f"\n{len(missing)} setting(s) in .env.example are missing from {args.env.name} "
              "(the code uses these same defaults until you add them):")
        for key in missing:
            print(f"  {example[key][0]}")
    else:
        print(f"\n{args.env.name} has every setting in .env.example.")

    others = [key for key in unknown if key not in _REPLACED]
    if others:
        print(f"\nIn {args.env.name} but not in .env.example (names only - check for typos or old settings):")
        print("  " + ", ".join(others))

    if not args.apply:
        if missing:
            print(f"\nNothing changed. To append them: python3 {Path(__file__).relative_to(ROOT).as_posix()} --apply")
        return 1 if (missing or problems) else 0
    if not missing:
        print("\nNothing to add.")
        return 1 if problems else 0

    if not args.yes:
        answer = input(f"\nAppend {len(missing)} setting(s) to {args.env}? A backup is saved first. [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Cancelled - nothing changed.")
            return 1

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = args.env.with_name(f"{args.env.name}.bak-{stamp}")
    shutil.copy2(args.env, backup)

    text = args.env.read_text(encoding="utf-8")
    block = ["", f"# --- Added by standalone/sync_env.py on {stamp} (defaults from .env.example) ---"]
    written_comments: set[int] = set()
    for key in missing:
        line, comments = example[key]
        if comments and id(comments) not in written_comments:
            block.append("")
            block.extend(comments)
            written_comments.add(id(comments))
        block.append(line)
    with args.env.open("a", encoding="utf-8", newline="\n") as f:
        if text and not text.endswith("\n"):
            f.write("\n")
        f.write("\n".join(block) + "\n")

    print(f"\nAppended {len(missing)} setting(s) to {args.env}. Backup: {backup.name}")
    if problems:
        print("The STALE/OLD entries above were NOT changed - edit those yourself.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
