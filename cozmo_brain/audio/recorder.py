"""Fixed-length microphone recording via `arecord` (Linux/PipeWire — the Pi setup)."""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)


def record_fixed(path: str, seconds: int, device: str) -> None:
    logger.info("Recording %ds from '%s'...", seconds, device)
    subprocess.run(
        ["arecord", "-D", device, "-f", "S16_LE", "-r", "16000", "-c", "1", "-d", str(seconds), path],
        check=True,
    )
