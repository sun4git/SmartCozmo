"""Test: SIGTERM (pkill/systemd) stops the app like Ctrl+C, so main()'s cleanup runs."""
import logging
import os
import signal
import sys

import _harness  # noqa: F401 - repo on sys.path, real .env ignored
from cozmo_brain.main import _stop_cleanly_on_sigterm

logging.basicConfig(level=logging.WARNING)
failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


previous = signal.getsignal(signal.SIGTERM)
_stop_cleanly_on_sigterm()
handler = signal.getsignal(signal.SIGTERM)
check("a Python handler is installed for SIGTERM", callable(handler) and handler is not previous)

try:
    handler(signal.SIGTERM, None)
    raised = False
except KeyboardInterrupt:
    raised = True
check("handler raises KeyboardInterrupt (same path as Ctrl+C)", raised)

# A cleanup in `finally` runs when it does, as main()'s does.
cleaned = []
try:
    try:
        handler(signal.SIGTERM, None)
    finally:
        cleaned.append("finally")
except KeyboardInterrupt:
    pass
check("finally-cleanup runs on SIGTERM", cleaned == ["finally"])

# Real delivery - POSIX only (the Pi). On Windows os.kill(SIGTERM) terminates
# the process outright, so there is nothing to catch.
if sys.platform != "win32":
    try:
        os.kill(os.getpid(), signal.SIGTERM)
        delivered = False
    except KeyboardInterrupt:
        delivered = True
    check("real SIGTERM to this process becomes KeyboardInterrupt", delivered)
else:
    print("     (real-signal check skipped on Windows - runs on the Pi)")

signal.signal(signal.SIGTERM, previous)
print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
