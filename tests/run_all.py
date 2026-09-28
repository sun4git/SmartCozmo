"""Run every offline test in tests/ (not tests/live/), each in its own process.

    python tests/run_all.py          # from the repo root, in the project's venv

Each test prints PASS/FAIL per check and exits non-zero on any failure. No
robot, mic, network, or API keys needed - they use fakes, and ignore .env
(see _harness.py). Needs the same Python deps as the app (incl. pycozmo).
"""

import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    tests = sorted(HERE.glob("test_*.py"))
    failed = []
    for path in tests:
        started = time.monotonic()
        proc = subprocess.run([sys.executable, str(path)], cwd=HERE.parent, capture_output=True, text=True)
        took = time.monotonic() - started
        checks = [l for l in proc.stdout.splitlines() if l.startswith(("PASS ", "FAIL "))]
        n_fail = sum(1 for l in checks if l.startswith("FAIL "))
        ok = proc.returncode == 0
        print(f"{'ok  ' if ok else 'FAIL'} {path.name:32s} {len(checks) - n_fail}/{len(checks)} checks  ({took:.1f}s)")
        if not ok:
            failed.append(path.name)
            for line in checks:
                if line.startswith("FAIL "):
                    print(f"       {line}")
            if proc.returncode != 0 and not n_fail:
                print("       " + (proc.stderr.strip().splitlines() or ["(no output)"])[-1])
    print(f"\n{len(tests) - len(failed)}/{len(tests)} test files passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
