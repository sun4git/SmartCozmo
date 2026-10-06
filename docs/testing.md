# Running the tests

[← Back to the README](../README.md)


```bash
python tests/run_all.py        # from the repo root, inside the project's venv
```

Runs every offline test in `tests/`, each in its own process, and prints one
line per file (`ok`/`FAIL`, checks passed, time); it exits non-zero on any
failure. These need **no robot, mic, network, or API keys**. They drive
the real `cozmo_brain` code against fakes: `tests/fake_pycozmo.py` stands in
for the robot (a fake `pycozmo.Client`) and `tests/fake_engine.py` for the
LLM (a scripted chat client). They also **ignore your `.env`**
(`tests/_harness.py`) and run against the code's built-in defaults, so
personal settings can't make them fail. A single test runs directly too,
e.g. `python tests/test_async_followup.py`.

What they cover: charger pose recording and invalidation, navigation and
docking (`test_return_real`, `test_dock_*`, `test_rotation_direction`), the
LOW/CRITICAL return policy (`test_return_policy`), fidget and charger
interplay (`test_fidget_charger`, `test_dock_dropoff`), self-caused-tap
suppression and VAD limits (`test_abc`), the charger-blocked reply
(`test_blocked_speech`), how turns end under every `FINAL_LLM_CALL`
mode (`test_final_say`, `test_async_*`), model files found by name in
`models/` and the real `hey_cozmo` wake word loading (`test_model_paths`),
and the conversation archive, resume, memory edits and search, including
the engine calling the memory tools (`test_history_memory`), and suggested
facts: the after-conversation check, the Suggested/Declined sections, and
keep/discard/later and clearing the lists (`test_memory_suggestions`),
the presence check — head tilt, silent for known/empty, one casual ask for a
stranger, how it slots into the idle fidget (`test_presence`), the charger
break, `leave_charger`, the per-turn battery line and who-moved-him labels
(`test_charger_break`), and the battery history records and analysis
(`test_battery_log`), SIGTERM stopping the app like Ctrl+C
(`test_sigterm`), the real wake-up/go-to-sleep clips, clip lengths and the clip-plus-speech player
(`test_wake_sleep`, `test_curated_clips`), and `ask_assistant` against a fake endpoint - the
session `user` field, failures, waiting for the answer, and background
answers delivered on their own (with the retry and word-for-word fallback
when the chat model fails), including only stopping a vad recording
nobody is talking in (`test_assistant`), and the short no-wake-word
window after Cozmo speaks up on his own, plus the battery offer/return
waiting for the gap between recordings instead of speaking into one
(`test_speak_up_listen`),
`dock()` stopping on contact, holding the charger heading, stopping when a
tread catches and retrying a miss (`test_dock_contact`,
`test_dock_heading_hold`), the background connection check
(`test_connection_monitor`), the backpack light: off means idle, and it
blinks while the mic records (`test_idle_light`, `test_listening_light`),
the Silero speech gate and the Whisper hallucination filters
(`test_speech_gate`, `test_stt_filter`), `standalone/sync_env.py`
(`test_sync_env`), and the dashboard: `.env` editing, transcript parsing,
the process runner and the HTTP layer (`test_dashboard`).

`tests/live/` holds **opt-in** tests that make real API calls with the keys
in your `.env`: which providers accept the conversation history shape, how
each model batches tool calls, and `async` end to end. `run_all.py` never
runs them. Run one yourself, e.g. `python tests/live/test_providers_history.py
groq`. They cost a few requests each, and Groq's free tier can answer 429.

All of these are logic checks. Anything involving the physical robot
(docking accuracy, real turn rates, real mic levels) still needs a
real-hardware run.
