"""System audio playback — an alternative (or supplement) to routing speech
through Cozmo's own (small, quiet even at max volume) speaker. Useful in a
noisy room where a real Bluetooth speaker will simply be heard better.
AUDIO_OUTPUT in .env is "cozmo" (default), "system", or "both" — see
handle_say() in tools/registry.py for how "both" plays simultaneously
rather than one after the other."""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)


def play_wav(path: str, device: str) -> None:
    logger.info("Playing '%s' through system audio device '%s'.", path, device)
    subprocess.run(["aplay", "-D", device, path], check=True)
