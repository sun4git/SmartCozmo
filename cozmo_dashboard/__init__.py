"""Cozmo Control Room: a web dashboard that starts/stops the app and shows
what it is doing. Standard library only, and independent of cozmo_brain: it
runs the app as a subprocess and reads the files the app already writes
(data/history, data/battery.jsonl, .env)."""

DEFAULT_PORT = 30540
