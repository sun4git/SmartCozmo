"""Finds model files - the wake word and the Piper voice - in the project's
models/ folder, so .env can name them without a folder or extension
(WAKE_WORD_MODEL=hey_cozmo, LOCAL_TTS_VOICE_PATH=en_US-lessac-medium).

models/ is found relative to the project, not the current directory, so it
works however the app is started. A full or relative path in .env still
works as before; it is tried first.
"""

from __future__ import annotations

from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


def find_model_file(value: str, suffix: str = ".onnx") -> str | None:
    """The path of an existing file for `value`, or None if there is none.

    Tries, in order: `value` as a path (absolute, or relative to the current
    directory), models/<value>, then models/<value><suffix>."""
    given = Path(value)
    candidates = [given]
    if not given.is_absolute():
        candidates.append(MODELS_DIR / value)
        if not value.endswith(suffix):
            candidates.append(MODELS_DIR / (value + suffix))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None
