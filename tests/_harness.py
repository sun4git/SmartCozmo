"""Shared setup for the offline tests in tests/.

Importing this (before anything from cozmo_brain) puts the repo root on
sys.path and makes cozmo_brain IGNORE the real .env, so every offline test
runs against the code's built-in defaults: your own .env settings (e.g.
FINAL_LLM_CALL, AUTO_RETURN_TO_CHARGER_ENABLED) can't make a test fail, and
no API key is ever read. Tests that override a setting do it explicitly with
dataclasses.replace(Settings(), ...). The live tests in tests/live/ don't use
this - they need the real .env.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *args, **kwargs: False  # cozmo_brain.config calls this at import


def tmp(name: str) -> str:
    """A scratch file path outside the repo, for tests that write files."""
    return os.path.join(tempfile.gettempdir(), f"smartcozmo_test_{name}")
