#!/usr/bin/env bash
# Convenience launcher: activates cozmo-env and runs cozmo_brain in one step.
# Every option is passed straight through to the app - `./run.sh --help`
# lists them all.
#
# Modes (--mode, default voice):
#   ./run.sh --mode vad          # hands-free: wake word or a tap on Cozmo opens a
#                                #   listening window (needs the wake-word setup)
#   ./run.sh --mode voice        # push-to-talk: press Enter, speak
#   ./run.sh --mode text         # type instead of speaking
#   ./run.sh --mode calibrate    # measure/correct turn() accuracy
#
# Other options:
#   --simulate                   # no robot: console-logging simulated Cozmo
#   --fresh                      # ignore saved conversation history
#   --log-level LEVEL            # how much to log (case-insensitive):
#       INFO     (default) useful diagnostics: LLM steps + tool results, STT
#                results + segment scores, speech-gate/capture lines, docking
#       WARNING  quiet - only problems
#       ERROR    only failures
#       DEBUG    everything, incl. PyCozmo's own chatter
#     To change the default without typing the flag every time, set
#     LOG_LEVEL=WARNING (etc.) in .env. The flag overrides it.
#
# Examples:
#   ./run.sh --mode vad
#   ./run.sh --mode vad --log-level warning
#   ./run.sh --simulate --mode text --fresh
#
# Works regardless of your current directory (cds to this script's location
# first), so it's also safe to call from cron/systemd.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f "cozmo-env/bin/activate" ]; then
    echo "cozmo-env venv not found in $SCRIPT_DIR. Create it first:" >&2
    echo "  python3 -m venv cozmo-env && source cozmo-env/bin/activate && pip install -r requirements.txt" >&2
    exit 1
fi

# shellcheck disable=SC1091
source cozmo-env/bin/activate
exec python3 -m cozmo_brain "$@"
