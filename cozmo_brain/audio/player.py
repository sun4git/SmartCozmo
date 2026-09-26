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

# Found on real hardware: aplay hung indefinitely mid-playback (a Bluetooth
# speaker/PipeWire glitch, not confirmed which) with no timeout on the
# subprocess call, freezing the whole program - had to be restarted. This
# is the same class of issue as robot.say_wav()'s own cli.wait_for(...,
# timeout=30) already guards against for Cozmo's speaker; the system-audio
# path had no equivalent. Matches that same 30s value for consistency, not
# independently tuned.
_TIMEOUT_S = 30


def play_wav(path: str, device: str) -> None:
    logger.info("Playing '%s' through system audio device '%s'.", path, device)
    try:
        subprocess.run(["aplay", "-D", device, path], check=True, timeout=_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        # subprocess.run() already killed the hung aplay process by the
        # time this fires. Logged and swallowed, not re-raised - a stuck
        # playback device shouldn't end the conversation turn (or, worse,
        # freeze the whole hands-free session) over one bad output.
        logger.warning(
            "aplay on device '%s' didn't finish within %ds - killed it. Playback device may be stuck.",
            device,
            _TIMEOUT_S,
        )
    except subprocess.CalledProcessError as e:
        logger.warning("aplay on device '%s' exited with an error: %s", device, e)
