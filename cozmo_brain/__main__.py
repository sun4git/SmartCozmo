import sys

from cozmo_brain.main import main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        # Ctrl+C, or SIGTERM from pkill/systemd (main._stop_cleanly_on_sigterm).
        # main()'s cleanup has already run by now; the traceback would only show
        # where it happened to be waiting. Any real error - including one during
        # that cleanup - is a different exception and still prints in full.
        print("Stopped.", file=sys.stderr)
        raise SystemExit(130)  # the usual exit code for "interrupted"
