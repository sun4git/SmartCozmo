"""Read and edit .env without losing its comments or layout.

.env.example supplies the structure (section headings, the comment above each
setting, the default value); .env supplies the current values. Editing
changes only the lines that were touched, saves a timestamped backup first,
and appends settings that aren't in .env yet under a dated heading - the same
conventions as standalone/sync_env.py.

Secrets are never sent to the browser: they are reported as set/not set, and
a new value can be written but not read back.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

_SETTING = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
_SECTION = re.compile(r"^#\s*---\s*(.+?)\s*(?:---)?\s*$")
_SECRET_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD")
_PLACEHOLDER = re.compile(r"^(your_|<)", re.IGNORECASE)


def is_secret(key: str) -> bool:
    return any(w in key for w in _SECRET_WORDS)


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        inner = raw[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\") if raw[0] == '"' else inner
    return raw.split(" #")[0].strip()


def parse_env(path: Path) -> dict[str, str]:
    """KEY -> value for active (uncommented) settings; the first one wins,
    like sync_env.py."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        m = _SETTING.match(raw.strip())
        if m:
            values.setdefault(m.group(1), _unquote(m.group(2)))
    return values


def parse_example(path: Path) -> list[dict[str, Any]]:
    """The settings in .env.example, in order: key, default, section, comment."""
    out: list[dict[str, Any]] = []
    section, comments, after_setting = "General", [], False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if line.startswith("#"):
            m = _SECTION.match(line)
            if m and line.lstrip("#").strip().startswith("---"):
                section, comments, after_setting = m.group(1).strip("- "), [], False
                continue
            if after_setting:
                comments = []
            comments.append(line.lstrip("#").strip())
            after_setting = False
            continue
        m = _SETTING.match(line)
        if m:
            out.append({
                "key": m.group(1), "default": _unquote(m.group(2)), "section": section,
                "comment": "\n".join(comments).strip(),
            })
            after_setting = True
        elif not line.strip():
            comments, after_setting = [], False
    return out


def view(env_path: Path, example_path: Path) -> dict[str, Any]:
    """Everything the editor shows. Secret values are left out."""
    current = parse_env(env_path)
    known = parse_example(example_path) if example_path.is_file() else []
    settings = []
    for item in known:
        key = item["key"]
        secret = is_secret(key)
        value = current.get(key)
        row: dict[str, Any] = {
            "key": key, "section": item["section"], "comment": item["comment"],
            "default": "" if secret else item["default"], "secret": secret,
            "in_env": key in current,
        }
        if secret:
            row["is_set"] = bool(value) and not _PLACEHOLDER.match(value)
            row["value"] = ""
        else:
            row["value"] = value if value is not None else item["default"]
        settings.append(row)
    known_keys = {i["key"] for i in known}
    extras = [{"key": k, "secret": is_secret(k), "value": "" if is_secret(k) else v,
               "is_set": bool(v)} for k, v in current.items() if k not in known_keys]
    return {"settings": settings, "extras": extras, "env_exists": env_path.is_file()}


def _format_value(value: str) -> str:
    """Quote only when a bare value would be read back differently."""
    if value == "" or (value == value.strip() and "#" not in value
                       and '"' not in value and "'" not in value and "\\" not in value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def apply_changes(env_path: Path, changes: dict[str, str | None], *, now: datetime | None = None) -> dict[str, Any]:
    """Set (str) or remove (None) settings. Existing lines are edited in
    place; new ones are appended. Returns what changed and the backup's name.
    Raises ValueError on a bad key or value."""
    for key, value in changes.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"Invalid setting name: {key!r}")
        if value is not None and ("\n" in value or "\r" in value or "\0" in value):
            raise ValueError(f"{key}: a value can't contain a line break")
    if not changes:
        return {"changed": [], "backup": None}

    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    backup = None
    lines: list[str] = []
    if env_path.is_file():
        backup = env_path.with_name(f"{env_path.name}.bak-{stamp}")
        shutil.copy2(env_path, backup)
        lines = env_path.read_text(encoding="utf-8").splitlines()

    done: set[str] = set()
    out: list[str] = []
    for line in lines:
        m = _SETTING.match(line.strip())
        key = m.group(1) if m else None
        if key in changes and key not in done:
            done.add(key)
            if changes[key] is not None:
                out.append(f"{key}={_format_value(changes[key])}")
            continue  # None: drop the line (the app falls back to its default)
        out.append(line)
    added = [k for k, v in changes.items() if k not in done and v is not None]
    if added:
        out += ["", f"# --- Set from the dashboard on {stamp} ---"]
        out += [f"{k}={_format_value(changes[k])}" for k in added]
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    return {"changed": sorted(k for k in changes if k in done or k in added),
            "backup": backup.name if backup else None}
