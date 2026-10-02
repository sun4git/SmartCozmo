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

# --- python -m cozmo_brain: quiet on interrupt, full traceback on a real error ---
import subprocess  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_entry_point(body):
    """Run the real `python -m cozmo_brain` entry point with main() replaced."""
    code = (
        "import cozmo_brain.main as m\n"
        f"def fake(argv=None):\n    {body}\n"
        "m.main = fake\n"
        "import runpy\n"
        "runpy.run_module('cozmo_brain', run_name='__main__')\n"
    )
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)


p = run_entry_point("raise KeyboardInterrupt")
check("interrupt: exit code 130, 'Stopped.', no traceback",
      p.returncode == 130 and "Stopped." in p.stderr and "Traceback" not in p.stderr)
p = run_entry_point("raise RuntimeError('boom')")
check("real error: traceback still shown", p.returncode != 0 and "Traceback" in p.stderr and "boom" in p.stderr)
p = run_entry_point("return 0")
check("normal exit: code 0, quiet", p.returncode == 0 and p.stderr.strip() == "")

# --- stdout line-buffered, so print()s stay in order in a redirected log ---
p = subprocess.run(
    [sys.executable, "-c", "import sys, cozmo_brain.main as m; m._line_buffered_stdout(); "
     "print(sys.stdout.line_buffering)"],
    cwd=ROOT, capture_output=True, text=True, timeout=60,
)
check("stdout becomes line-buffered even when redirected", p.stdout.strip() == "True")

print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
