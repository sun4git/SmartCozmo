#!/usr/bin/env bash
# Starts the Cozmo Control Room (web dashboard): start/stop Cozmo in any mode,
# live log and conversation, history, files, .env editor.
#
#   ./dashboard.sh                 # http://localhost:30540
#   ./dashboard.sh --port 8080
#   ./dashboard.sh --host 0.0.0.0  # reachable from other machines on your
#                                  #   network - set DASHBOARD_TOKEN in .env first
#
# Defaults can live in .env: DASHBOARD_PORT, DASHBOARD_HOST, DASHBOARD_TOKEN.
# The dashboard itself needs nothing beyond Python's standard library; the
# app it starts runs in cozmo-env, the same as ./run.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f "cozmo-env/bin/activate" ]; then
    # shellcheck disable=SC1091
    source cozmo-env/bin/activate
fi
exec python3 -m cozmo_dashboard "$@"
