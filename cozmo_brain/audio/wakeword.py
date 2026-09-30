"""Zero-network wake-word detection using openWakeWord's ONNX backend.

Why not a plain `pip install openwakeword`: on Linux it unconditionally
declares a dependency on `tflite-runtime`, which has no build for Python
3.12 anywhere (checked both PyPI and piwheels.org — every version is
"binary only" with zero files). Installing like this instead sidesteps it
entirely, since openWakeWord only ever imports tflite_runtime when you
explicitly ask for the tflite backend (verified against its source):

    pip install --no-deps openwakeword
    pip install onnxruntime scipy scikit-learn requests tqdm
    python3 -c "from openwakeword.utils import download_models; download_models(['hey_jarvis_v0.1'])"

That last line is a required one-time step, separate from the pip installs
— the package ships no model files at all (confirmed: loading a model
without it fails with a plain "file doesn't exist"). It needs internet once,
during setup; nothing at runtime after that.

`WAKE_WORD_MODEL` in .env can be either a stock model name (hey_jarvis,
alexa, hey_mycroft, hey_rhasspy, timer, weather — openWakeWord's built-in
free models) or a path to a custom-trained .onnx model (e.g. from
openWakeWord's training notebook, for an actual "hey cozmo"). Swapping
later is just changing this one value — openWakeWord's Model class already
resolves a bare name and a file path through the same code path (verified
against its source), no code changes needed here.

Verified for real (on a dev machine, not a Pi): installed this exact way,
downloaded the hey_jarvis model via `openwakeword.utils.download_models()`,
loaded it with `inference_framework="onnx"`, and confirmed predict() returns
scores keyed exactly as `_score_key()` derives them, for both a bare stock
name and a file path. NOT verified: the aarch64/Pi install itself (only the
wheel listings were checked, not a real install), or real microphone audio
— there's no Pi or mic in this environment.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import threading

import numpy as np

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_CHUNK_SAMPLES = 1280  # 80ms — openWakeWord's recommended frame size
_CHUNK_BYTES = _CHUNK_SAMPLES * 2  # 16-bit mono

# Whether the last wait_for_wake_word() call lost the mic stream - so the
# "mic stream ended" warning is logged once per outage, not on every retry.
_mic_down = False


def _score_key(model_spec: str) -> str:
    """The key openWakeWord's predict() uses for this model: the bare name
    if `model_spec` was one, or the file's stem if it was a path — mirrors
    Model.__init__'s own resolution logic exactly."""
    if os.path.exists(model_spec):
        return os.path.splitext(os.path.basename(model_spec))[0]
    return model_spec


def wait_for_wake_word(
    device: str,
    model_spec: str,
    threshold: float,
    stop_event: threading.Event | None = None,
) -> bool:
    """Blocks until `model_spec` is heard in the mic stream (returns True), or
    until `stop_event` is set by another thread (returns False early) - used
    to race this against a physical tap (see vad_mode.py) without leaving the
    mic/model running in the background once the other trigger wins.

    Also returns False if the mic stream ends on its own (arecord exited -
    e.g. the Bluetooth mic is off or disconnected). Callers must check
    `stop_event` to tell that apart from being cancelled: it is not a wake."""
    global _mic_down
    try:
        from openwakeword.model import Model
    except ImportError as e:
        raise RuntimeError(
            "Wake-word mode requires openwakeword, installed without its usual "
            "(broken, on Python 3.12) tflite-runtime dependency. See "
            "cozmo_brain/audio/wakeword.py or the README for the install command."
        ) from e

    oww_model = Model(wakeword_models=[model_spec], inference_framework="onnx")
    key = _score_key(model_spec)

    proc = subprocess.Popen(
        ["arecord", "-D", device, "-f", "S16_LE", "-r", str(_SAMPLE_RATE), "-c", "1", "-t", "raw"],
        stdout=subprocess.PIPE,
    )
    try:
        while stop_event is None or not stop_event.is_set():
            raw = proc.stdout.read(_CHUNK_BYTES)
            if len(raw) < _CHUNK_BYTES:
                with contextlib.suppress(Exception):
                    proc.wait(timeout=1)
                # Warn once per outage, not on every retry (vad_mode.py
                # restarts this every few seconds while the mic is off).
                logger.log(
                    logging.DEBUG if _mic_down else logging.WARNING,
                    "Mic stream ended (arecord exit code %s, device %r) - is the mic on/connected? "
                    "Retrying quietly until it's back.",
                    proc.returncode, device,
                )
                _mic_down = True
                break
            if _mic_down:
                _mic_down = False
                logger.info("Mic stream is back.")
            audio = np.frombuffer(raw, dtype=np.int16)
            scores = oww_model.predict(audio)
            score = scores.get(key, 0.0)
            if score >= threshold:
                logger.info("Wake word '%s' detected (score %.2f).", model_spec, score)
                return True
        return False
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=2)
