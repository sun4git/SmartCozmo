"""Reads what the app saves under data/: conversation transcripts, the file
tree, battery events, plus a few host stats. Read-only, except memory.md
(see write_memory)."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

_DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_TEXT_EXT = {".md", ".txt", ".log", ".json", ".jsonl", ".csv", ".env", ".yaml", ".yml"}
EDITABLE = {"memory.md"}  # the only file the dashboard may write (hand-edited by design)
MAX_TEXT_BYTES = 2_000_000


# --- paths -------------------------------------------------------------------

def safe_path(base: Path, rel: str) -> Path:
    """`rel` inside `base`, or ValueError. Resolves symlinks and `..` first."""
    base = base.resolve()
    target = (base / rel.lstrip("/\\")).resolve()
    if target != base and base not in target.parents:
        raise ValueError("Path is outside the data folder")
    return target


# --- transcripts ---------------------------------------------------------------

def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        pass  # a torn last line (power cut mid-write)
    except OSError:
        return


def run_files(history_dir: Path) -> list[Path]:
    """Every run's transcript, oldest first."""
    if not history_dir.is_dir():
        return []
    out: list[Path] = []
    for day in sorted(history_dir.iterdir()):
        if day.is_dir() and _DATE_DIR.match(day.name):
            out.extend(sorted(day.glob("*.jsonl")))
    return out


def _args(raw: Any) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def normalize(record: dict[str, Any], rel_dir: str) -> list[dict[str, Any]]:
    """One archive record -> zero or more display events:
    {ts, kind, text, ...}. kind: you | cozmo | note | tool | error | thought |
    session | run | photo. Same reading as standalone/show_history.py."""
    ts, kind = record.get("ts", ""), record.get("type")
    base = {"ts": ts}

    def photo_urls(names: list[str]) -> list[str]:
        return [f"/api/file?path={rel_dir}/{n}" for n in names]

    if kind == "run_start":
        info = {k: v for k, v in record.items() if k not in ("ts", "type")}
        return [{**base, "kind": "run", "text": "Run started", "info": info}]
    if kind == "run_end":
        return [{**base, "kind": "run", "text": "Run ended"}]
    if kind == "session_start":
        return [{**base, "kind": "session", "text": f"Woke up ({record.get('trigger', '?')})"}]
    if kind == "session_end":
        return [{**base, "kind": "session", "text": "Back to idle"}]
    if kind == "photo":
        return [{**base, "kind": "photo", "text": "Photo", "images": photo_urls([record.get("file", "")])}]
    if kind != "message":
        return []

    message = record.get("message") or {}
    role = message.get("role")
    images = photo_urls(record.get("images") or [])
    if role == "user":
        content = str(message.get("content", ""))
        text, _, status = content.partition("\n\n[Status:")
        text = text.strip()
        event = {**base, "kind": "note" if text.startswith("[") else "you", "text": text}
        if status:
            event["status"] = "[Status:" + status
        if images:
            event["images"] = images
        return [event]
    if role == "assistant":
        out = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            name, args = fn.get("name"), _args(fn.get("arguments"))
            if name == "say":
                out.append({**base, "kind": "cozmo", "text": args.get("text", ""),
                            "mood": args.get("mood", ""), "gesture": args.get("gesture", "")})
            else:
                out.append({**base, "kind": "tool", "name": name or "?", "args": args})
        content = message.get("content")
        if content and not message.get("tool_calls"):
            out.append({**base, "kind": "thought", "text": str(content)})
        return out
    if role == "tool" and str(message.get("content", "")).startswith("ERROR"):
        return [{**base, "kind": "error", "name": message.get("name", "?"),
                 "text": str(message["content"])[len("ERROR: "):]}]
    return []


def load_run(history_dir: Path, run_id: str) -> dict[str, Any]:
    """run_id is '<date>/<time>' (the .jsonl name without its extension)."""
    path = safe_path(history_dir, run_id + ".jsonl")
    events: list[dict[str, Any]] = []
    rel_dir = f"history/{path.parent.name}"
    for record in read_jsonl(path):
        events.extend(normalize(record, rel_dir))
    return {"id": run_id, "events": events}


class _SummaryCache:
    """Run summaries, recomputed only when a file's size or mtime changes."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[tuple[int, float], dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def get(self, path: Path, run_id: str) -> dict[str, Any]:
        try:
            st = path.stat()
        except OSError:
            return {}
        sig = (st.st_size, st.st_mtime)
        with self._lock:
            hit = self._cache.get(run_id)
            if hit and hit[0] == sig:
                return hit[1]
        info: dict[str, Any] = {}
        turns = replies = sessions = 0
        first = ""
        for record in read_jsonl(path):
            kind = record.get("type")
            if kind == "run_start":
                info = {k: v for k, v in record.items() if k not in ("ts", "type")}
            elif kind == "session_start":
                sessions += 1
            elif kind == "message":
                m = record.get("message") or {}
                if m.get("role") == "user":
                    text = str(m.get("content", "")).split("\n\n[Status:")[0].strip()
                    if text and not text.startswith("["):
                        turns += 1
                        first = first or text
                elif m.get("role") == "assistant":
                    replies += sum(1 for c in m.get("tool_calls") or []
                                   if (c.get("function") or {}).get("name") == "say")
        photos = len(list(path.parent.glob(f"{path.stem}-photo-*")))
        summary = {"id": run_id, "date": path.parent.name, "time": path.stem.replace("-", ":", 2)[:8],
                   "size": st.st_size, "turns": turns, "replies": replies, "sessions": sessions,
                   "photos": photos, "first": first[:140], "info": info}
        with self._lock:
            self._cache[run_id] = (sig, summary)
        return summary


_summaries = _SummaryCache()


def list_runs(history_dir: Path) -> list[dict[str, Any]]:
    """Newest first."""
    out = []
    for path in reversed(run_files(history_dir)):
        run_id = f"{path.parent.name}/{path.stem}"
        out.append(_summaries.get(path, run_id))
    return [r for r in out if r]


def search_runs(history_dir: Path, query: str, limit: int = 80) -> list[dict[str, Any]]:
    """Case-insensitive text search over everything said, newest runs first."""
    q = query.strip().lower()
    hits: list[dict[str, Any]] = []
    if not q:
        return hits
    for path in reversed(run_files(history_dir)):
        run_id = f"{path.parent.name}/{path.stem}"
        for record in read_jsonl(path):
            for ev in normalize(record, f"history/{path.parent.name}"):
                text = ev.get("text") or ""
                if ev["kind"] in ("you", "cozmo", "note", "thought", "error") and q in text.lower():
                    hits.append({"run": run_id, **ev})
                    if len(hits) >= limit:
                        return hits
    return hits


class ConversationTail:
    """Follows the newest transcript: the run happening now (or the last one).
    poll() returns (reset, run_id, new_events); reset means the file changed
    (a new run started) so the client should start over."""

    def __init__(self, history_dir: Path):
        self.history_dir = history_dir
        self._path: Path | None = None
        self._offset = 0
        self._partial = ""

    def poll(self) -> tuple[bool, str | None, list[dict[str, Any]]]:
        files = run_files(self.history_dir)
        latest = files[-1] if files else None
        reset = latest != self._path
        if reset:
            self._path, self._offset, self._partial = latest, 0, ""
        if latest is None:
            return reset, None, []
        events: list[dict[str, Any]] = []
        try:
            with latest.open("rb") as f:
                f.seek(self._offset)
                chunk = f.read()
        except OSError:
            return reset, f"{latest.parent.name}/{latest.stem}", []
        self._offset += len(chunk)
        data = self._partial + chunk.decode("utf-8", errors="replace")
        *lines, self._partial = data.split("\n")  # the last piece may be a half-written line
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                events.extend(normalize(json.loads(line), f"history/{latest.parent.name}"))
            except json.JSONDecodeError:
                pass
        return reset, f"{latest.parent.name}/{latest.stem}", events


# --- files -------------------------------------------------------------------

def file_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _IMAGE_EXT:
        return "image"
    if ext in (".wav", ".mp3", ".ogg"):
        return "audio"
    if ext in _TEXT_EXT or path.name == "memory.md":
        return "text"
    return "other"


def list_dir(data_dir: Path, rel: str) -> dict[str, Any]:
    target = safe_path(data_dir, rel)
    if not target.is_dir():
        raise FileNotFoundError(rel)
    entries = []
    for child in target.iterdir():
        try:
            st = child.stat()
        except OSError:
            continue
        is_dir = child.is_dir()
        entries.append({
            "name": child.name, "dir": is_dir, "size": None if is_dir else st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds"),
            "kind": "dir" if is_dir else file_kind(child),
            "editable": not is_dir and child.relative_to(data_dir.resolve()).as_posix() in EDITABLE,
        })
    entries.sort(key=lambda e: (not e["dir"], e["name"].lower() if not e["dir"] else e["name"]), reverse=False)
    # Date folders and run files read best newest first.
    if entries and all(e["dir"] and _DATE_DIR.match(e["name"]) for e in entries):
        entries.reverse()
    elif target.name != "data" and entries and all(re.match(r"^\d\d-\d\d-\d\d", e["name"]) for e in entries):
        entries.reverse()
    base = data_dir.resolve()
    rel_clean = "" if target == base else target.relative_to(base).as_posix()
    return {"path": rel_clean, "entries": entries}


def guess_type(path: Path) -> str:
    if path.suffix.lower() in (".jsonl", ".log", ".md", ".txt", ".csv"):
        return "text/plain; charset=utf-8"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def read_text(data_dir: Path, rel: str) -> dict[str, Any]:
    path = safe_path(data_dir, rel)
    if not path.is_file():
        raise FileNotFoundError(rel)
    size = path.stat().st_size
    with path.open("rb") as f:
        raw = f.read(MAX_TEXT_BYTES)
    return {"path": rel, "text": raw.decode("utf-8", errors="replace"), "truncated": size > MAX_TEXT_BYTES,
            "editable": path.relative_to(data_dir.resolve()).as_posix() in EDITABLE}


def write_memory(data_dir: Path, rel: str, text: str) -> dict[str, Any]:
    """Save an edit of memory.md (the app re-reads it every turn). The
    previous version is kept as memory.md.bak."""
    path = safe_path(data_dir, rel)
    if path.relative_to(data_dir.resolve()).as_posix() not in EDITABLE:
        raise PermissionError("The dashboard only edits memory.md")
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError("That file is too large")
    backup = None
    if path.is_file():
        backup = path.with_name(path.name + ".bak")
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return {"saved": True, "backup": backup.name if backup else None}


def memory_view(data_dir: Path) -> dict[str, Any]:
    """memory.md as sections of facts, for display."""
    path = data_dir / "memory.md"
    if not path.is_file():
        return {"exists": False, "sections": []}
    sections: list[dict[str, Any]] = []
    current = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("## "):
            current = {"name": line[3:].strip(), "facts": []}
            sections.append(current)
        elif current is not None and line.startswith("- "):
            current["facts"].append(line[2:].strip())
    return {"exists": True, "sections": sections}


# --- battery -------------------------------------------------------------------

def battery_now(data_dir: Path) -> dict[str, Any] | None:
    """The app's latest reading (battery_now.json, rewritten every
    BATTERY_CHECK_INTERVAL_S), with its age. None if there isn't one yet."""
    path = data_dir / "battery_now.json"
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(rec["ts"])
        age = (datetime.now(ts.tzinfo) - ts).total_seconds()
        interval = float(rec.get("interval_s") or 30)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    rec["age_s"] = max(0, round(age))
    # Written every `interval` seconds while the app runs; well past that = it stopped (or lost the robot).
    rec["stale"] = age > max(3 * interval, 45)
    return rec


def battery_view(data_dir: Path, limit: int = 400) -> dict[str, Any]:
    path = data_dir / "battery.jsonl"
    records = list(read_jsonl(path))
    points = [{"ts": r.get("ts"), "event": r.get("event"), "v": r.get("v")}
              for r in records if isinstance(r.get("v"), (int, float))][-limit:]
    latest = points[-1] if points else None
    last_docked = next((r for r in reversed(records) if r.get("event") in ("docked", "undocked", "run_start")), None)
    stretches = [r for r in records if r.get("event") == "stretch"][-10:]
    now = battery_now(data_dir)
    if now and (not latest or str(now["ts"]) > str(latest["ts"])):
        points.append({"ts": now["ts"], "event": "now", "v": now["v"]})  # the chart ends at the live reading
    return {"points": points, "latest": latest, "state": last_docked, "stretches": stretches,
            "events": len(records), "now": now}


# --- host --------------------------------------------------------------------

_git_cache: tuple[float, str] = (0.0, "")


def _git_commit(root: Path) -> str:
    global _git_cache
    if time.time() - _git_cache[0] < 60:
        return _git_cache[1]
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True,
                             text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    _git_cache = (time.time(), out)
    return out


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent
            return s.getsockname()[0]
    except OSError:
        return ""


def host_view(root: Path, data_dir: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"hostname": socket.gethostname(), "ip": _lan_ip(), "commit": _git_commit(root)}
    try:
        out["load"] = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        pass
    try:
        out["cpu_temp_c"] = round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
    except (OSError, ValueError):
        pass
    try:
        mem = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, _, v = line.partition(":")
            mem[k] = int(v.split()[0])
        out["mem_used_pct"] = round(100 * (1 - mem["MemAvailable"] / mem["MemTotal"]))
    except (OSError, KeyError, ValueError, IndexError):
        pass
    try:
        out["uptime_s"] = int(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError):
        pass
    try:
        usage = shutil.disk_usage(data_dir if data_dir.exists() else root)
        out["disk_free_gb"] = round(usage.free / 1e9, 1)
        out["disk_used_pct"] = round(100 * usage.used / usage.total)
    except OSError:
        pass
    return out
