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

# Clip sounds come from a folder that is NOT in the repo (data/clip_sounds, see
# standalone/extract_clip_sounds.py). Default them off so no test depends on
# whether this machine happens to have it; the tests that exercise sounds turn
# them on explicitly (dataclasses.replace(Settings(), clip_sounds="full", clip_sounds_dir=...)).
os.environ.setdefault("CLIP_SOUNDS", "off")


def tmp(name: str) -> str:
    """A scratch file path outside the repo, for tests that write files."""
    return os.path.join(tempfile.gettempdir(), f"smartcozmo_test_{name}")
