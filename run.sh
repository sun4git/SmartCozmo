#!/usr/bin/env bash
# Convenience launcher: activates cozmo-env and runs cozmo_brain in one step.
#
# Usage: ./run.sh --mode voice
#        ./run.sh --simulate --mode text
#        ./run.sh --mode calibrate
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
