"""Best-effort automatic Wi-Fi join to Cozmo's own access point, via nmcli.

Fully opt-in: does nothing unless `COZMO_WIFI_SSID` is set in `.env`. Linux/
nmcli only — this is a Raspberry Pi deployment concern, not something a
Windows dev machine (or the simulated robot backend) needs.

Cozmo's SSID/password can regenerate on power cycle (raise/lower his lift and
check his face for the current ones). If a saved NetworkManager profile no
longer matches what he's currently broadcasting, this falls back to logging
a clear instruction to reconnect manually, rather than guessing.

Note: on some systems `nmcli` Wi-Fi actions require root (see
docs/known-gotchas.md, "nmcli commands may need sudo"). This module never adds `sudo`
itself — silently blocking on an interactive password prompt from a
non-interactive process (e.g. under systemd) is worse than just failing
loudly. If you hit permission errors, set up passwordless sudo/polkit for
nmcli, or run cozmo_brain as a user in the right group for your distro.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time

logger = logging.getLogger(__name__)


def ensure_connected(ssid: str, password: str = "", timeout: float = 15.0) -> bool:
    """Make a best-effort attempt to join Cozmo's Wi-Fi AP before connecting.

    Returns True if we believe Wi-Fi is fine to proceed on (already
    connected, freshly connected, or this feature doesn't apply here) —
    False only when auto-connect was configured and genuinely failed, so the
    caller can still try PyCozmo's own connect and get its normal error.
    """
    if not ssid:
        return True

    if sys.platform != "linux" or shutil.which("nmcli") is None:
        logger.debug("Wi-Fi auto-connect skipped (not Linux, or nmcli not found).")
        return True

    if _nmcli(["connection", "up", ssid], timeout=timeout) == 0:
        logger.info("Connected to Cozmo's Wi-Fi ('%s') using a saved profile.", ssid)
        return True

    if not password:
        logger.warning(
            "Could not join Cozmo's Wi-Fi ('%s') using a saved profile, and no "
            "COZMO_WIFI_PASSWORD is set for first-time setup. Connect manually once: "
            "nmcli dev wifi connect \"%s\" password <password-from-his-face>",
            ssid, ssid,
        )
        return False

    logger.info("No saved profile for '%s' yet — attempting first-time connect.", ssid)
    _nmcli(["dev", "wifi", "rescan"], timeout=timeout, log_failure=False)
    if _nmcli(["dev", "wifi", "connect", ssid, "password", password], timeout=timeout) != 0:
        logger.warning(
            "First-time Wi-Fi connect to '%s' failed. His SSID/password regenerate on "
            "power cycle sometimes — raise/lower his lift, check his face for the current "
            "ones, update COZMO_WIFI_SSID/COZMO_WIFI_PASSWORD in .env, and try again.",
            ssid,
        )
        return False

    # CRITICAL: keep the wired connection as the default route (see docs/setup.md) —
    # otherwise this machine loses its route to the internet/Ollama/Groq.
    _nmcli(["connection", "modify", ssid, "ipv4.never-default", "yes"], timeout=timeout, log_failure=False)
    logger.info("Connected to Cozmo's Wi-Fi ('%s') for the first time and saved the profile.", ssid)
    time.sleep(2)  # give DHCP/link-local negotiation a moment before PyCozmo dials in
    return True


def _nmcli(args: list[str], timeout: float, log_failure: bool = True) -> int:
    try:
        result = subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        if log_failure:
            logger.warning("nmcli %s failed to run: %s", " ".join(args), e)
        return 1
    if log_failure and result.returncode != 0:
        logger.debug("nmcli %s -> exit %d: %s", " ".join(args), result.returncode, result.stderr.strip())
    return result.returncode
