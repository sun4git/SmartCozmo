"""Test: model files resolve from models/ by bare name (WAKE_WORD_MODEL=hey_cozmo,
LOCAL_TTS_VOICE_PATH=en_US-lessac-medium), paths still work, stock wake word
names pass through, and the committed hey_cozmo model loads and scores under
the key the listener reads (only if openwakeword is installed)."""
import logging
import os
import sys
import tempfile
from pathlib import Path

import _harness  # noqa: F401 - repo on sys.path, real .env ignored

from cozmo_brain import model_files
from cozmo_brain.audio import wakeword

logging.disable(logging.CRITICAL)
failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: failures.append(name)

real_models_dir = model_files.MODELS_DIR
check("models/ is the repo's models folder", real_models_dir == Path(__file__).resolve().parent.parent / "models")

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    models = tmp / "models"
    models.mkdir()
    (models / "hey_test.onnx").write_bytes(b"x")
    (models / "en_US-test-medium.onnx").write_bytes(b"x")
    elsewhere = tmp / "elsewhere.onnx"
    elsewhere.write_bytes(b"x")
    model_files.MODELS_DIR = models
    try:
        find = model_files.find_model_file
        check("bare name -> models/<name>.onnx", find("hey_test") == str(models / "hey_test.onnx"))
        check("name with extension -> models/<name>", find("hey_test.onnx") == str(models / "hey_test.onnx"))
        check("voice name -> models/<voice>.onnx", find("en_US-test-medium") == str(models / "en_US-test-medium.onnx"))
        check("absolute path elsewhere still works", find(str(elsewhere)) == str(elsewhere))
        check("missing name -> None", find("hey_missing") is None)
        check("missing absolute path -> None (no models/ fallback)", find(str(tmp / "nope.onnx")) is None)
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            check("relative path from the current folder still works (old root layout)",
                  find("elsewhere.onnx") == "elsewhere.onnx")
        finally:
            os.chdir(cwd)

        check("wake word name -> its file, keyed by bare name",
              wakeword._resolve_model("hey_test") == (str(models / "hey_test.onnx"), "hey_test"))
        check("wake word with extension -> same key",
              wakeword._resolve_model("hey_test.onnx")[1] == "hey_test")
        check("stock name passes through unchanged", wakeword._resolve_model("hey_jarvis") == ("hey_jarvis", "hey_jarvis"))
    finally:
        model_files.MODELS_DIR = real_models_dir

check("committed models/hey_cozmo.onnx is found by name",
      model_files.find_model_file("hey_cozmo") == str(real_models_dir / "hey_cozmo.onnx"))

try:
    import numpy as np
    from openwakeword.model import Model
except ImportError:
    print("SKIP hey_cozmo load check - openwakeword not installed")
else:
    path, key = wakeword._resolve_model("hey_cozmo")
    oww = Model(wakeword_models=[path], inference_framework="onnx")
    scores = oww.predict(np.zeros(1280, dtype=np.int16))
    check(f"hey_cozmo loads and scores under key {key!r}", key in scores)
    check("silence scores below the default threshold", scores.get(key, 1.0) < 0.5)

sys.exit(1 if failures else 0)
