"""Test: connection_monitor.py - background health check, reconnect when stale,
never underneath a turn, logs only on state changes."""
import dataclasses
import logging
import sys
import threading
import time
from types import SimpleNamespace as NS

import _harness  # noqa: F401 - repo on sys.path, real .env ignored

from cozmo_brain.config import Settings
from cozmo_brain.connection_monitor import ConnectionMonitor

records = []
logging.getLogger("cozmo_brain.connection_monitor").addHandler(
    type("H", (logging.Handler,), {"emit": lambda s, r: records.append((r.levelname, r.getMessage()))})()
)
logging.getLogger("cozmo_brain.connection_monitor").setLevel(logging.DEBUG)
failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


class FakeRobot:
    def __init__(self, healthy=True, reconnect_ok=True):
        self.healthy = healthy
        self.reconnect_ok = reconnect_ok
        self.reconnects = 0

    def is_healthy(self):
        return self.healthy

    def reconnect(self):
        self.reconnects += 1
        if self.reconnect_ok:
            self.healthy = True
        return self.reconnect_ok


def make(**kw):
    robot = FakeRobot(**kw)
    engine = NS(turn_lock=threading.Lock())
    return robot, engine, ConnectionMonitor(robot, engine, Settings())


# 1. Healthy -> no reconnect, only a DEBUG line (nothing at INFO+).
robot, engine, mon = make()
records.clear()
mon._check_once()
check("healthy: no reconnect", robot.reconnects == 0)
check(f"healthy: nothing logged above DEBUG ({records})", all(lvl == "DEBUG" for lvl, _ in records))

# 2. Stale -> warns once, reconnects, logs success.
robot, engine, mon = make(healthy=False)
records.clear()
mon._check_once()
check("stale: reconnect attempted", robot.reconnects == 1)
check("stale: warned + reconnected logged",
      any("looks disconnected" in m for _, m in records) and any("reconnected after" in m for _, m in records))
check("lock released afterwards", engine.turn_lock.acquire(blocking=False))
engine.turn_lock.release()

# 3. A turn in progress (turn_lock held) -> never reconnects underneath it.
robot, engine, mon = make(healthy=False)
engine.turn_lock.acquire()
mon._check_once()
check("turn in progress: no reconnect", robot.reconnects == 0)
engine.turn_lock.release()

# 4. Reconnect keeps failing -> one "looks disconnected" warning per outage,
#    "still disconnected" on each try; recovery logged once.
robot, engine, mon = make(healthy=False, reconnect_ok=False)
records.clear()
mon._check_once(); mon._check_once()
check("failing: tried each check", robot.reconnects == 2)
check("failing: outage warned once", sum("looks disconnected" in m for _, m in records) == 1)
check("failing: still-disconnected each try", sum("still disconnected" in m for _, m in records) == 2)
robot.healthy = True  # e.g. telemetry resumed on its own / engine reconnected
records.clear()
mon._check_once()
check(f"recovery logged once ({records})", len(records) == 1 and "back" in records[0][1])

# 5. Reconnect raising -> logged, lock released, thread-safe to continue.
robot, engine, mon = make(healthy=False)
robot.reconnect = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
records.clear()
mon._check_once()
check("exception logged, not raised", any("Connection check failed: boom" in m for _, m in records))
check("lock released after exception", engine.turn_lock.acquire(blocking=False))
engine.turn_lock.release()

# 6. Interval 0 -> never starts; thread starts and stops cleanly otherwise.
robot = FakeRobot()
mon = ConnectionMonitor(robot, NS(turn_lock=threading.Lock()), dataclasses.replace(Settings(), connection_check_interval_s=0))
mon.start()
check("interval 0: no thread", mon._thread is None)
mon = ConnectionMonitor(robot, NS(turn_lock=threading.Lock()), dataclasses.replace(Settings(), connection_check_interval_s=0.05))
mon.start(); time.sleep(0.2); t0 = time.monotonic(); mon.stop()
check(f"stops promptly ({time.monotonic() - t0:.2f}s)", not mon._thread.is_alive() and time.monotonic() - t0 < 1)

print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
