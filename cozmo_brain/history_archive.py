"""The conversation archive: every run's full transcript, kept by date.

    data/history/2026-10-01/15-39-51.jsonl            one file per run (start to exit)
    data/history/2026-10-01/15-39-51-photo-01.png     photos taken in that run

Separate from what the model sees (conversation.py's Conversation, trimmed
to CONVERSATION_MAX_MESSAGES): nothing is ever trimmed here. Each line is
one JSON record with a local timestamp and a "type":

    run_start      mode and providers, written with the first real record
    session_start  --mode vad: a wake word or tap opened a listening window
    message        a chat message as the model saw it ("images" -> photo files)
    photo          a photo taken while vision was off (not sent to the model)
    session_end    --mode vad: the listening window closed
    run_end        the app exited normally

Written line by line as things happen, so a crash or power cut loses at
most the line being written. A run that never records anything leaves no
file. On startup, recent_messages() feeds the tail of earlier runs back
into the conversation, so Cozmo picks up where he left off.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _image_ext(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data.startswith(b"\xff\xd8"):
        return ".jpg"
    return ".bin"


def run_files(history_dir: Path) -> list[Path]:
    """Every run's transcript, oldest first (date folder, then start time)."""
    if not history_dir.is_dir():
        return []
    files = []
    for day in sorted(history_dir.iterdir()):
        if day.is_dir() and _DATE_DIR.match(day.name):
            files.extend(sorted(day.glob("*.jsonl")))
    return files


def read_records(path: Path) -> Iterator[dict[str, Any]]:
    """A transcript's records. A torn last line (power cut mid-write) is skipped."""
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("Skipping unreadable line in %s", path)
    except OSError as e:
        logger.warning("Could not read %s: %s", path, e)


class HistoryArchive:
    """Appends this run's records to data/history/<date>/<start time>.jsonl."""

    def __init__(self, history_dir: str | Path, run_info: dict[str, Any] | None = None):
        self.history_dir = Path(history_dir)
        started = datetime.now()
        day, stem = self.history_dir / started.strftime("%Y-%m-%d"), started.strftime("%H-%M-%S")
        self.path = day / f"{stem}.jsonl"
        n = 1
        while self.path.exists():  # two runs started within the same second
            n += 1
            self.path = day / f"{stem}-{n}.jsonl"
        self._run_info = run_info or {}
        self._started = False
        self._photo_count = 0
        self._lock = threading.Lock()
        # Set for an import: every record gets this time instead of now.
        self._fixed_ts: str | None = None

    def _ts(self) -> str:
        return self._fixed_ts or _now_iso()

    # --- writing ---------------------------------------------------------

    def _write(self, record: dict[str, Any]) -> None:
        """Caller holds _lock."""
        try:
            if not self._started:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._started = True
                self._append({"ts": self._ts(), "type": "run_start", **self._run_info})
            self._append(record)
        except OSError as e:
            logger.warning("Could not write to the conversation archive %s: %s", self.path, e)

    def _append(self, record: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _photo_path(self, ext: str) -> Path:
        self._photo_count += 1
        return self.path.with_name(f"{self.path.stem}-photo-{self._photo_count:02d}{ext}")

    def record_message(self, message: dict[str, Any]) -> None:
        """A chat message, as added to the conversation. Attached images
        (base64) are saved as files next to the transcript and referenced by
        name instead, so the transcript stays small and readable."""
        with self._lock:
            message = dict(message)
            record: dict[str, Any] = {"ts": self._ts(), "type": "message"}
            images = message.pop("images", None)
            if images:
                names = []
                for b64 in images:
                    try:
                        data = base64.b64decode(b64)
                        path = self._photo_path(_image_ext(data))
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(data)
                        names.append(path.name)
                    except (OSError, ValueError) as e:
                        logger.warning("Could not save a photo to the archive: %s", e)
                record["images"] = names
            record["message"] = message
            self._write(record)

    def record_photo(self, source: str) -> None:
        """A photo the model didn't see (vision off) - copied next to the transcript."""
        with self._lock:
            try:
                ext = Path(source).suffix or ".png"
                path = self._photo_path(ext)
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, path)
            except OSError as e:
                logger.warning("Could not save a photo to the archive: %s", e)
                return
            self._write({"ts": self._ts(), "type": "photo", "file": path.name})

    def record_event(self, event: str, **fields: Any) -> None:
        """A marker: session_start (with trigger), session_end. run_end is
        only written to a run that recorded something."""
        with self._lock:
            if event == "run_end" and not self._started:
                return
            self._write({"ts": self._ts(), "type": event, **fields})

    # --- reading ---------------------------------------------------------

    def recent_messages(self, limit: int) -> list[dict[str, Any]]:
        """The last `limit` chat messages from earlier runs, oldest first -
        what to resume the conversation with. Photos aren't re-attached (the
        model would pay for stale images every turn); a note saying one was
        taken stays."""
        collected: list[dict[str, Any]] = []
        for path in reversed(run_files(self.history_dir)):
            if path == self.path:
                continue
            messages = []
            for record in read_records(path):
                if record.get("type") == "message" and isinstance(record.get("message"), dict):
                    message = dict(record["message"])
                    if message.get("role") == "system":
                        continue
                    if record.get("images"):
                        message["content"] = f"{message.get('content', '')} [photo: {', '.join(record['images'])}]"
                    messages.append(message)
            collected = messages + collected
            if len(collected) >= limit:
                break
        return collected[-limit:] if limit > 0 else []

    def import_legacy(self, legacy_path: Path, data_dir: Path) -> bool:
        """One-time import of the old single-file history (conversation_history.json)
        as a run of its own, dated by the file's last change. The old file is
        then moved into data/ so it's imported only once."""
        if not legacy_path.is_file() or run_files(self.history_dir):
            return False
        try:
            messages = json.loads(legacy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Could not import the old conversation history %s: %s", legacy_path, e)
            return False
        changed = datetime.fromtimestamp(legacy_path.stat().st_mtime)
        imported = HistoryArchive(self.history_dir, {"imported_from": legacy_path.name})
        imported.path = self.history_dir / changed.strftime("%Y-%m-%d") / f"{changed.strftime('%H-%M-%S')}-imported.jsonl"
        imported._fixed_ts = changed.astimezone().isoformat(timespec="seconds")
        for message in messages if isinstance(messages, list) else []:
            if isinstance(message, dict) and message.get("role") != "system":
                imported.record_message(message)
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            legacy_path.replace(data_dir / "conversation_history.imported.json")
        except OSError as e:
            logger.warning("Imported %s but could not move it into %s: %s", legacy_path, data_dir, e)
        logger.info("Imported the old conversation history into %s", imported.path)
        return True


def prune_history(history_dir: Path, keep_days: int) -> int:
    """Delete date folders older than `keep_days` (0 = keep everything).
    Returns how many were deleted. Only touches YYYY-MM-DD folders."""
    if keep_days <= 0 or not history_dir.is_dir():
        return 0
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    deleted = 0
    for day in history_dir.iterdir():
        if day.is_dir() and _DATE_DIR.match(day.name) and day.name < cutoff:
            shutil.rmtree(day, ignore_errors=True)
            deleted += 1
    if deleted:
        logger.info("Deleted %d day(s) of conversation history older than %d days.", deleted, keep_days)
    return deleted
