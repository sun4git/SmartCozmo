# The cozmo_brain/ application

[← Back to the README](../README.md)


## Architecture

```
                    ┌────────────────────────────────────────────┐
                    │                CozmoEngine                  │
mic/typed text ───► │  Conversation ◄──► Ollama (tool-calling)    │
                    │        ▲                  │                │
                    │        │            tool_calls              │
                    │        │                  ▼                │
                    │   tool results ◄──── Tools (say/gesture/... )│
                    └────────────────────────────│─────────────────┘
                                                  ▼
                                     RobotBackend (real | simulated)
                                        │                    │
                              PyCozmo (Wi-Fi)        console logging
                                        │
                                    Cozmo 🤖
```

Every user turn runs through `CozmoEngine.handle_turn()`: the message is
added to conversation history, sent to Ollama with the full tool schema, and
if the model returns tool calls, each one is executed against the active
`RobotBackend` and its result is fed back to the model — repeating (up to
`MAX_TOOL_ITERATIONS`) until the model stops calling tools. This lets Cozmo
chain actions in one turn (e.g. `gesture` then `say` then `look`) instead of
doing exactly one thing per user utterance.

## Modes

```bash
python3 -m cozmo_brain --mode voice       # push-to-talk (default) — press Enter, speak
python3 -m cozmo_brain --mode vad         # hands-free — say the wake word OR tap Cozmo, then talk (needs webrtcvad + openwakeword)
python3 -m cozmo_brain --mode text        # type instead of speak — great for dev/testing
python3 -m cozmo_brain --mode calibrate   # measure real turn() degrees-per-second
python3 -m cozmo_brain --simulate         # force the console-logging robot backend (any mode)
```

`python3 -m cozmo_brain` requires `cozmo-env` to be activated first (or call
`cozmo-env/bin/python -m cozmo_brain` directly, which is equivalent without
activating). `./run.sh` does both in one step — activates `cozmo-env` and
forwards all arguments, e.g. `./run.sh --mode voice`. `./run.sh --help` lists
every option.

Every run is archived under `data/history/`, and the next run resumes where
the last one left off; pass `--fresh` to start with an empty context instead
(the run is still archived, and memory still applies). See
[Conversation history and memory](memory.md#conversation-history-and-memory).

`--mode vad` accepts either the wake word or a gentle tap on Cozmo's body as
the activation trigger — whichever comes first (see
`modes/vad_mode.py`/`_wait_for_wake_word_or_tap`). A tap is detected as a
brief accelerometer-magnitude spike above a rolling baseline
(`TAP_THRESHOLD`/`TAP_DEBOUNCE_MS`, see `.env.example` and `robot/real.py`),
gated on Cozmo's own `IS_PICKED_UP` status flag so being picked up/carried
isn't mistaken for a tap — real backend only (`--simulate` falls back to a
keypress in place of a tap; wake-word detection still needs a real mic
either way). Once either trigger fires, everything else — VAD-gated
listening, the follow-up window, hallucination filtering — is the exact
same code path as before; tapping is just a second way in. See [item 4 in
the roadmap](roadmap.md#roadmap--open-work) for how the threshold was picked, and
why this ended up folded into `--mode vad` instead of a standalone mode.

## Tools available to the LLM

| Tool | Does |
|---|---|
| `say(text, mood, gesture)` | Speaks via Groq TTS (routed per `AUDIO_OUTPUT`), striking a matching facial expression + backpack light color first. The optional `gesture` plays a choreography concurrently with the speech (guaranteed overlap — see below), rather than before or after it. A mood that raises the lift (`happy`/`excited`/`proud`/`smug`) is lowered again automatically once the reply finishes. |
| `gesture(name)` | Plays a curated multi-step choreography (face + lights + head/arm + wheels) **on its own, with no speech** — pairs with `say` only sequentially, never simultaneously (use `say`'s own `gesture` argument for that). |
| `play_animation(name)` | Plays a **real** Anki animation clip or group by exact name. |
| `list_animations()` | Lists the real animation/group names actually loaded on this robot. |
| `drive(distance_mm, speed_mmps)` | Drives straight, clamped to safe limits. |
| `turn(angle_degrees)` | Turns in place (calibrated via `TURN_SECONDS_PER_DEGREE`). |
| `dock()` | Goes back onto the charger. If Cozmo remembers where it is (he drove off it earlier and hasn't been picked up or reconnected since), navigates to a staging point in front of it, then reverses on until the contacts engage. Otherwise only reverses in a straight line, so it only works already close and facing away from it. See roadmap item 6. |
| `leave_charger()` | Drives straight off the dock (the `CHARGER_EXIT_*` drive any movement does first when docked). Refuses honestly, with the voltage, if the battery is too low to leave. No timed return afterwards — the low/critical battery rules bring him back. |
| `look()` | Captures a camera frame and attaches it to the next LLM turn for vision. |
| `remember_person(name)` | Captures a reference photo, stored under that name. |
| `who_is_this()` | Captures a photo and asks the vision LLM if it matches anyone remembered. **Experimental.** |
| `remember_fact(fact, person)` | Saves a fact to `data/memory.md`, under the primary user (default) or a named person. Refused when `MEMORY_MAX_FACTS` is reached. |
| `forget_fact(fact)` | Removes the one saved fact that matches; if several match, removes nothing and lists them. |
| `search_memory(query)` | Keyword search over saved facts and every archived conversation, with dates and times. |
| `review_suggestion(suggestion, decision)` | Records your answer after Cozmo asks about a suggested fact: `keep` (saved), `discard` (never suggested again) or `later`. |
| `clear_suggestions(which)` | Empties the `waiting` suggestions, the `declined` ones, or `both`, when you ask. Cozmo says how many and checks first; confirmed facts aren't touched. |
| `ask_assistant(request)` | Only when `ASSISTANT_ENABLED=true`. Hands a request Cozmo can't do himself (a reminder, a web lookup, a task) to your own assistant agent, called by `ASSISTANT_NAME`, and returns its answer. See [Asking your own assistant](assistant.md#asking-your-own-assistant-ask_assistant). |

**Moods** (used by `say` and inside gestures): `neutral`, `happy`, `excited`,
`curious`, `proud`, `sad`, `sleepy`, `bored`, `scared`, `surprised`,
`confused`, `annoyed`, `angry`, `suspicious`, `embarrassed`, `smug` — each is
a real `pycozmo.expressions` procedural face paired with a backpack light
color and an optional head/lift pose (`cozmo_brain/robot/moods.py`).
`neutral` (the default `say` mood) deliberately resets head/lift to level
— several moods and gestures raise the lift arm, which physically covers
the face screen, and without a real reset the arm was staying up almost
permanently on real hardware.

**Gestures** (multi-step choreography, `cozmo_brain/robot/gestures.py`):
`nod_yes`, `shake_no`, `wake_up`, `sleep`, `cheer`, `dance`, `spin`,
`flinch`, `peek`, `shrug`, `fist_pump`, `sneaky_creep`, `sad_shuffle`,
`alert`.

**Real animation clips** (`play_animation`/`list_animations`) use PyCozmo's
actual `play_anim`/`play_anim_group` API against Anki's original animation
assets — this only works once you've run `pycozmo_resources.py download`
(step 1 above). If those assets aren't present, `list_animations()` just
returns empty and the model is expected to fall back to `gesture`/`say`
moods instead — nothing crashes.
