"""Starts, stops and watches the cozmo_brain process.

The app is run exactly as ./run.sh runs it (python -m cozmo_brain <options>,
from the repo root, in cozmo-env when that exists). Its output is kept in a
ring buffer for the live log and also written to data/logs/<date>/<time>.log.
Stop sends SIGTERM, which the app treats like Ctrl+C (clean shutdown), and
only kills it if it doesn't exit in time.
"""

from __future__ import annotations

import collections
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MODES = ("voice", "vad", "text", "calibrate")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
_LEVEL_IN_LINE = re.compile(r"^\S+ \S+ (DEBUG|INFO|WARNING|ERROR|CRITICAL) ")
# How long a clean shutdown may take (the end-of-run memory check calls the
# chat model) before Stop gives up and kills the process.
STOP_GRACE_S = 30.0


def python_for(root: Path) -> str:
    """cozmo-env's interpreter if it exists (what run.sh activates), else ours."""
    for rel in ("cozmo-env/bin/python", "cozmo-env/Scripts/python.exe"):
        candidate = root / rel
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def build_command(root: Path, mode: str, simulate: bool, fresh: bool, log_level: str | None) -> list[str]:
    """Validated argv for the app. Raises ValueError for anything not offered."""
    if mode not in MODES:
        raise ValueError(f"Unknown mode {mode!r}")
    cmd = [python_for(root), "-m", "cozmo_brain", "--mode", mode]
    if simulate:
        cmd.append("--simulate")
    if fresh:
        cmd.append("--fresh")
    if log_level:
        if log_level.upper() not in LOG_LEVELS:
            raise ValueError(f"Unknown log level {log_level!r}")
        cmd += ["--log-level", log_level.upper()]
    return cmd


def find_external() -> list[int]:
    """PIDs of a cozmo_brain started outside the dashboard (Linux /proc only;
    elsewhere returns [])."""
    proc = Path("/proc")
    if not proc.is_dir():
        return []
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if b"-m" in argv and b"cozmo_brain" in argv:
            found.append(int(entry.name))
    return found


class Runner:
    def __init__(self, root: Path, log_dir: Path, ring_size: int = 5000):
        self.root = Path(root)
        self.log_dir = Path(log_dir)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._ring: collections.deque[dict[str, Any]] = collections.deque(maxlen=ring_size)
        self._seq = 0
        self._proc: subprocess.Popen | None = None
        self._state = "stopped"  # stopped | running | stopping
        self._info: dict[str, Any] = {}
        self._log_file = None
        self._log_path: Path | None = None

    # --- status ----------------------------------------------------------

    def status(self) -> dict[str, Any]:
        with self._lock:
            proc = self._proc
            state, info, seq = self._state, dict(self._info), self._seq
            log_name = self._log_path.name if self._log_path else None
        external = [] if proc else find_external()
        return {
            "state": state,
            "pid": proc.pid if proc else None,
            "external_pids": external,
            "started_at": info.get("started_at"),
            "uptime_s": (time.time() - info["started_ts"]) if proc and "started_ts" in info else None,
            "mode": info.get("mode"),
            "simulate": info.get("simulate"),
            "fresh": info.get("fresh"),
            "log_level": info.get("log_level"),
            "command": info.get("command"),
            "exit_code": info.get("exit_code"),
            "stopped_at": info.get("stopped_at"),
            "log_file": log_name,
            "log_seq": seq,
        }

    # --- start / stop ----------------------------------------------------

    def start(self, mode: str, simulate: bool = False, fresh: bool = False, log_level: str | None = None) -> dict[str, Any]:
        cmd = build_command(self.root, mode, simulate, fresh, log_level)
        with self._lock:
            if self._proc is not None:
                raise RuntimeError("Cozmo is already running - stop it first.")
        external = find_external()
        if external:
            raise RuntimeError(
                f"A cozmo_brain is already running outside the dashboard (pid {', '.join(map(str, external))}). "
                "Two copies would fight over the robot and the microphone - stop that one first.")
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        try:
            proc = subprocess.Popen(
                cmd, cwd=self.root, env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
        except OSError as e:
            raise RuntimeError(f"Could not start the app: {e}") from e
        started = datetime.now()
        day = self.log_dir / started.strftime("%Y-%m-%d")
        log_path, log_file = None, None
        try:
            day.mkdir(parents=True, exist_ok=True)
            log_path = day / f"{started.strftime('%H-%M-%S')}.log"
            log_file = log_path.open("a", encoding="utf-8")
        except OSError as e:
            logger.warning("Can't write the log file in %s: %s", day, e)
        with self._cond:
            self._proc, self._state = proc, "running"
            self._log_path, self._log_file = log_path, log_file
            self._info = {
                "started_at": started.astimezone().isoformat(timespec="seconds"), "started_ts": time.time(),
                "mode": mode, "simulate": simulate, "fresh": fresh, "log_level": log_level,
                "command": " ".join(["python", "-m", "cozmo_brain"] + cmd[3:]),
            }
            self._add("sys", f"--- started: {self._info['command']} (pid {proc.pid}) ---")
        threading.Thread(target=self._pump, args=(proc,), daemon=True, name="cozmo-log-pump").start()
        return self.status()

    def stop(self, force: bool = False) -> dict[str, Any]:
        with self._lock:
            proc = self._proc
            if proc is None:
                raise RuntimeError("Cozmo isn't running.")
            self._state = "stopping"
            self._add("sys", "--- killing ---" if force
                      else "--- stopping (a clean shutdown can take a few seconds) ---")
        if force:
            proc.kill()
        else:
            self._terminate(proc)
            threading.Thread(target=self._kill_if_stuck, args=(proc,), daemon=True).start()
        return self.status()

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        try:
            if os.name == "nt":
                proc.terminate()  # no graceful SIGTERM on Windows
            else:
                proc.send_signal(signal.SIGTERM)  # main.py treats it like Ctrl+C
        except OSError:
            pass

    def _kill_if_stuck(self, proc: subprocess.Popen) -> None:
        try:
            proc.wait(STOP_GRACE_S)
        except subprocess.TimeoutExpired:
            with self._lock:
                self._add("sys", f"--- didn't exit within {STOP_GRACE_S:.0f}s - killing ---")
            proc.kill()

    def shutdown(self) -> None:
        """Dashboard exiting: take the app down with it (its pipes die with us)."""
        with self._lock:
            proc = self._proc
        if proc is not None:
            self._terminate(proc)
            try:
                proc.wait(STOP_GRACE_S)
            except subprocess.TimeoutExpired:
                proc.kill()

    def send_input(self, text: str) -> None:
        """A line on the app's stdin: a typed message in --mode text, Enter
        for push-to-talk, a simulated tap in --simulate."""
        with self._lock:
            proc = self._proc
            if proc is None or proc.stdin is None or self._state != "running":
                raise RuntimeError("Cozmo isn't running.")
            self._add("in", text if text else "(Enter)")
        try:
            proc.stdin.write(text.replace("\r", " ").replace("\n", " ") + "\n")
            proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise RuntimeError(f"Couldn't send that to the app: {e}") from e

    # --- log -------------------------------------------------------------

    def _add(self, stream: str, text: str) -> None:
        """Caller holds the lock."""
        self._seq += 1
        m = _LEVEL_IN_LINE.match(text)
        entry = {"seq": self._seq, "ts": datetime.now().strftime("%H:%M:%S"), "stream": stream,
                 "level": m.group(1) if m else "", "text": text}
        self._ring.append(entry)
        if self._log_file is not None and stream != "in":
            try:
                self._log_file.write(text + "\n")
                self._log_file.flush()
            except OSError:
                self._log_file = None
        self._cond.notify_all()

    def _pump(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            with self._lock:
                self._add("out", line.rstrip("\r\n"))
        code = proc.wait()
        with self._lock:
            self._add("sys", f"--- exited with code {code} ---")
            self._proc, self._state = None, "stopped"
            self._info.update(exit_code=code, stopped_at=datetime.now().astimezone().isoformat(timespec="seconds"))
            if self._log_file is not None:
                try:
                    self._log_file.close()
                except OSError:
                    pass
                self._log_file = None
            self._cond.notify_all()

    def read_log(self, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._ring)[-limit:]

    def wait_log(self, after: int, timeout: float) -> list[dict[str, Any]]:
        """Blocks until there's a line newer than `after` (or the timeout)."""
        with self._cond:
            if self._seq <= after:
                self._cond.wait(timeout)
            return [e for e in self._ring if e["seq"] > after]
