# Standalone scripts

[← Back to the README](../README.md)


One-off tools in `standalone/`, separate from the app. Run them from the
project folder, inside `cozmo-env`.

| Script | What it's for | Needs |
|---|---|---|
| `sync_env.py` | After a `git pull`: which `.env` settings are missing, stale, or unknown | Nothing |
| `show_history.py` | Read archived conversations as a transcript | Nothing |
| `battery_report.py` | How this robot's battery behaves on and off the charger (`data/battery.jsonl`) | Nothing |
| `orchestrator.py` | The original single-file proof of concept: mic → STT → LLM → Cozmo → TTS | Robot, mic, `GROQ_API_KEY`, Ollama |
| `pose_drift_test.py` | How far Cozmo's own position tracking drifts from reality | Robot, tape, a ruler |

## `sync_env.py` - check `.env` after a pull

`.env` is gitignored, so a pull never updates it. This compares it with
`.env.example`:

```bash
python3 standalone/sync_env.py                # report only, changes nothing
python3 standalone/sync_env.py --apply        # append missing settings (asks first, backs up .env)
python3 standalone/sync_env.py --apply --yes  # same, without asking
```

It lists settings missing from `.env` (with their example values), flags
known-stale values (e.g. a removed Groq model, or `look.png`/`known_people`
from before `data/`), and lists settings `.env.example` doesn't know - by
name only, never printing a value. It never changes or removes an existing
line; `--apply` only appends, with the comments from `.env.example`, after
saving a timestamped backup.

## `battery_report.py` - how the battery actually behaves

Reads `data/battery.jsonl` (see
[Coming off the charger](connection-and-battery.md#coming-off-the-charger-battery-line-and-charger-break))
and prints charge sessions (voltage on docking, minutes until `IS_CHARGING`
went off, total time docked), every stretch off the dock (how he left,
voltages, minutes off, minutes until low/critical, how it ended), and —
once at least 3 stretches actually reached low — a straight-line fit of
"minutes until low" against the voltage on leaving. That's the evidence for
deciding how a smarter "can I come out, and for how long?" should work; with
less data it says so instead of guessing.

```bash
python3 standalone/battery_report.py          # data/battery.jsonl
python3 standalone/battery_report.py --demo   # made-up numbers, just to see the format
```

## `show_history.py` - read past conversations

```bash
python3 standalone/show_history.py              # the latest run
python3 standalone/show_history.py 2026-10-01   # every run that day
python3 standalone/show_history.py --list       # which days/runs exist
python3 standalone/show_history.py data/history/2026-10-01/15-39-51.jsonl
```

Output looks like this (one run, `--mode vad`):

```
===== 2026-10-01 15:39:51  (mode: vad, simulated: False, chat_provider: groq, ...)

--- 15:39:51 woke up (wake word)
15:39:55 you: hey cozmo, remember that I like cricket
15:39:57   (remember_fact fact=Likes cricket.)
15:39:57 Cozmo [happy]: Cricket fan, got it! I'll remember that.
--- 15:40:20 back to idle
===== 15:52:03 stopped
```

It shows what was heard, what Cozmo said (with mood and gesture), the
other tools he used, failed tools, and the photo files saved next to the
transcript. The `.jsonl` itself has everything, including every tool
result. It reads `DATA_DIR` from `.env`. See
[Conversation history and memory](memory.md#conversation-history-and-memory).

## `orchestrator.py` - minimal end-to-end test

```bash
python3 standalone/orchestrator.py
```

Press Enter, speak for `RECORD_SECONDS` (default 5), and Cozmo answers;
type `quit` to exit. Groq Whisper for STT and Groq Orpheus for TTS, Ollama
(`OLLAMA_BASE_URL`/`OLLAMA_MODEL`) for chat, PyCozmo for the robot. It reads
the same `.env`, but only its own few settings (`GROQ_API_KEY` is
required). No memory, history, gestures, or provider switching - useful for
checking the mic → STT → LLM → robot → speaker chain on the Pi without the
full app. Its `turn()` is uncalibrated (see [Known gotchas](known-gotchas.md#known-gotchas)).

## `pose_drift_test.py` - position tracking drift

```bash
python3 standalone/pose_drift_test.py
```

An exploratory hardware test behind return-to-charger (roadmap item 6),
not a routine calibration. It drives a fixed path (`DRIVE_STEPS` at the top
of the script - edit it for your own test), prints where Cozmo *thinks* he
is, drives back to the start with `go_to_pose()` plus a heading correction,
and asks you to measure the real position by hand at each step. Mark the
start on the floor with tape first; it waits for Enter before moving. Uses
`TURN_SPEED_MMPS`/`TURN_SECONDS_PER_DEGREE` from `.env`; no LLM, STT, TTS or
API keys. The protocol and past results are in the script's docstring and
roadmap item 6.
