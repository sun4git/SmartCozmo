# Roadmap / open work

[← Back to the README](../README.md)


Done, via `cozmo_brain/`:

- ✅ **Conversation memory** — `Conversation` class, trimmed; every run
  archived by date under `data/history/` (with photos) and resumed on the
  next start; `data/memory.md` for facts about people, with
  `remember_fact`/`forget_fact`/`search_memory`. See
  [Conversation history and memory](memory.md#conversation-history-and-memory).
- ✅ **Real animation API** — `play_animation`/`list_animations` tools.
- ✅ **`turn()` calibration** — `--mode calibrate`, offering a choice of two
  checks. The original (spin the wheels for a fixed 2s, estimate the
  resulting angle, derive `TURN_SECONDS_PER_DEGREE` from scratch) is good
  for an initial calibration, but extrapolates a single linear rate from
  a short sample to any commanded turn — **confirmed on real hardware**
  that this can be noticeably off for a much longer commanded turn (e.g.
  `gestures.py`'s `spin`, a full 360°, ~4x longer than the 2s sample the
  rate was derived from) even when smaller turns feel fine: a small
  estimation error at the 2s scale gets linearly amplified by however much
  longer the real commanded turn is — a plausible root cause for an
  observed ~65°-out-of-360° spin overshoot. Added a second check instead
  of just recalibrating more carefully at the same scale: it turns a real
  360° through the actual `turn()` method (using whatever rate is
  currently configured) and asks how far short of or past the *starting
  mark* it landed, rather than estimating an abstract angle from scratch
  — comparing an end point to a visible start point is both easier and
  more precise for a person to judge, and it measures `turn()` at the same
  scale `spin` actually uses it at. Corrects the existing rate
  proportionally (assumes a linear duration-to-angle relationship near the
  current rate, same assumption the original check already makes, not a
  fixed-offset overshoot independent of commanded duration) rather than
  deriving one from zero, so it's meant to run after some baseline value
  already exists. **Verified as control-flow and correction-math logic
  only** (scripted tests against the simulated backend confirm both
  checks' math and that "short"/"past"/"exact" all produce the expected
  corrected value) — not yet confirmed this actually fixes the real
  overshoot on hardware.
- ✅ **Voice activity detection** — `--mode vad` (`webrtcvad`), gated by a
  zero-network on-device wake word (`openwakeword`, see [Wake word
  detection](wake-word.md#wake-word-detection-zero-network) below).
- ✅ **Camera-in-the-loop** — `look()` tool attaches the photo for vision.
- ✅ **TTS gain boost** — `TTS_GAIN` in config, now clipping-safe (peak-normalizing).
- ✅ **TTS voice/tone polish** — `TTS_PITCH_SHIFT` + `TTS_ROBOT_MOD_DEPTH`, picked by
  ear against sample WAVs (see [Making the voice sound less generic](audio-output.md#making-the-voice-sound-less-generic)).
- ✅ **Robustness** — Ollama request failures are caught gracefully
  mid-conversation; STT request failures (`speech.transcribe()`, in both
  `interactive.py` and `vad_mode.py`) are now caught the same way instead
  of crashing the session — **confirmed on real hardware**: an OpenAI 400
  used to take the whole process down mid-`--mode vad` session, with the
  model never told anything went wrong; Cozmo mid-session disconnects are
  now detected (via a `RobotState` telemetry heartbeat — see [Detecting
  and recovering from a dropped
  connection](connection-and-battery.md#detecting-and-recovering-from-a-dropped-connection)) and
  auto-reconnected, which also re-runs Wi-Fi auto-connect if
  `COZMO_WIFI_SSID` is set. **Untested against a real hardware drop** — this
  environment has no Cozmo to disconnect.
- ✅ **STT/TTS provider flexibility** — `STT_PROVIDER`/`TTS_PROVIDER` set
  independently to `groq` or `openai` in `.env`, for when Groq's free-tier
  rate limits get hit (which they did, during actual use), or to mix
  providers per component. See [Switching speech providers](providers.md#switching-speech-providers-and-separately-chat).
- ✅ **Chat provider flexibility, including vision** — `CHAT_PROVIDER=groq`/
  `openai` as an alternative to `ollama`, via `GroqChatClient`/
  `OpenAIChatClient` (`OpenAICompatibleChatClient` in
  `openai_compatible_chat.py`, shared since both are the same wire format),
  including `look`/`who_is_this`'s image attachments translated to OpenAI's
  content-array format, and a per-provider `*_VISION_MODEL` override for
  when the regular chat model isn't itself vision-capable. `VISION_PROVIDER`
  goes further — it can point image-bearing turns at an entirely different
  *provider* than `CHAT_PROVIDER` (e.g. Ollama for chat, Groq for vision),
  via a small `_ChatRouter` in `create_chat_client()`. **Not yet verified
  against a real Groq/OpenAI account** — implemented and unit-checked
  directly (message translation, image mime-sniffing, vision-model/
  provider selection, client construction) but no live turn has actually
  been run against either endpoint yet. See
  [Switching speech providers](providers.md#switching-speech-providers-and-separately-chat).
- ✅ **Battery indicator** — `BatteryMonitor` shows a face icon + red backpack
  light a few seconds before Cozmo auto-powers-off. See [Battery
  monitor](connection-and-battery.md#battery-monitor).
- ✅ **Physical sensor reactions** — all built on `RobotState` telemetry
  that was previously only ever used for the connection-health heartbeat
  (nothing in `cozmo_brain` used to *listen* to Cozmo at all): tap-to-talk
  (an alternate `--mode vad` activation trigger alongside the wake word,
  detected via an accelerometer-magnitude spike rather than a touch sensor
  Cozmo's body doesn't actually have); a cliff/fall safety reflex during
  every `drive()`/`turn()` (stops early, shows a scared face, and backs
  away specifically for a cliff); charger-safe wheels (`drive()`/`turn()`
  blocked only while actively charging, not merely resting on the dock —
  full-but-docked is allowed to move); a startle reaction the instant
  Cozmo is picked up; and idle fidgeting after a few quiet minutes with no
  real conversation turn. Auditing all of this for real also surfaced a
  genuine, separate bug: two gestures and 8 of 16 moods could leave
  Cozmo's lift arm raised (which physically covers the face screen) with
  nothing guaranteed to ever lower it again — fixed alongside the rest.
  See item 4 below for the full detail on each, including exactly what's
  confirmed on real hardware vs. still simulated-only.
- ✅ **Concurrent gesture + speech** — `gesture` and `say` used to be
  strictly sequential (a full gesture choreography blocking until it
  finished before speech could even start synthesizing), adding real dead
  air to every reply that used both. Nothing about the *hardware* requires
  that — Cozmo's real animation clips already combine audio + movement as
  one synchronized track — it was purely this program's own tool-dispatch
  loop and blocking calls. `RobotBackend.run_gesture_async()` (`robot/base.py`)
  now validates the gesture name synchronously (still fails fast on an
  unknown name) but runs its steps on a background thread and returns
  immediately, so a `say` call right after actually overlaps with the
  gesture instead of waiting on it. `GESTURE_ASYNC_ENABLED` (default
  `true`) falls back to the old strictly-sequential behavior. A reentrant
  wheel lock (`drive()`/`spin_wheels_for()` in `robot/real.py`) prevents a
  background gesture's own wheel steps from racing a separately-requested
  `drive`/`turn` tool call for control of the wheels — reentrant
  specifically because the charger-exit-before-turning logic above already
  calls `drive()` from inside `spin_wheels_for()` while holding it.
  **Confirmed on real hardware that this alone didn't produce actual
  overlap:** `gesture` being non-blocking only helps if it's called
  *before* `say` — the ordering between two separate tool calls is
  entirely up to the model, and when it called `say` first, `say()`
  fully blocks on its own audio regardless of `gesture`'s async-ness, so
  speech and movement never happened together at all (observed directly:
  audio played fully, *then* the gesture happened). Fixed by not relying
  on the model batching two separate tool calls in the right order at
  all: `say` now takes its own optional `gesture` argument
  (`tools/registry.py`'s `handle_say`), so one call deterministically
  starts the gesture (still via `run_gesture_async()`, gated by the same
  `GESTURE_ASYNC_ENABLED`) and then proceeds straight into TTS
  synthesis + playback while it continues in the background — genuine
  overlap guaranteed by construction instead of hoped for. The standalone
  `gesture` tool still exists for a silent physical reaction with no
  speech; the system prompt now explains the distinction (and that
  calling `gesture` alongside `say`, even in the same turn, never
  actually overlaps them). **Verified, including on real hardware:** the
  transport is genuinely thread-safe (`pycozmo`'s `Connection.send()` is
  a plain thread-safe `queue.Queue.put()`); a scripted test with a fake
  speech client (simulated TTS latency) confirms the gesture's own steps
  run concurrently with synthesis and audio playback begins while the
  gesture is still finishing; a mocked test confirms the reentrant wheel
  lock doesn't deadlock on the charger-exit self-call; and — the actual
  goal — directly observed on the robot itself: the model used `say`'s
  `gesture` argument on its own (no separate `gesture` tool call), and
  Cozmo genuinely spoke while performing the gesture (`peek`), not one
  after the other. The cosmetic race where a background gesture's own
  mood step and a concurrent `say()`'s mood-setting land around the same
  moment remains unverified either way (same class of accepted trade-off
  as `pickup_reactor`'s note above, not new).

  **Raised directly, a separate stuck-arm case — not a gesture failure this
  time, just the mood system working exactly as designed:** `happy`/
  `excited`/`proud`/`smug` are deliberately "arms up" (see `moods.py`) —
  `apply_mood()` raises the lift and leaves it there on purpose, since
  that's the whole visual point while the reply plays. Nothing brought it
  back down afterward, though: confirmed on real hardware that a plain
  "Hi" getting a `happy` reply left the arm raised indefinitely once the
  turn was over — not a bug, exactly the documented behavior, but read as
  a stuck arm rather than an intentional flourish once the moment had
  passed. `handle_say()` (`tools/registry.py`) now checks the same
  `Mood.lift_mm` field `apply_mood()` itself keys off — not a hardcoded
  mood-name list, so it stays correct automatically if a future mood adds
  this same "raised, not auto-lowered" shape — and calls
  `lower_lift_fully()` once the reply finishes if it's set. Deliberately
  scoped to `say()`'s own mood argument, not `apply_mood()` itself: a
  gesture's own internal mood steps still work exactly as before, since
  those sequences already choreograph their own lift movements
  deliberately. **Confirmed on real hardware:** the lift now visibly comes
  back down after a `happy`-style reply, matching the simulated-backend
  check (`set_lift_height_mm()` then `lower_lift_fully()` fire in that
  order, with neither an extra nor a missing call for moods that already
  set `lower_lift=True`).

  **Raised directly, a real gap in the "guaranteed by construction" claim
  above:** the guarantee is about *start order* only (gesture starts,
  *then* synthesis begins) — nothing ties the gesture's actual duration to
  how long synthesis takes. Most gestures run only ~1-3s total (see their
  step durations in `gestures.py`); if synthesis takes longer than that —
  plausible with a slower `TTS_PROVIDER` (e.g. `local` on constrained
  hardware, or just a slow network to a cloud provider) — the gesture can
  finish *before* audio even starts playing, silently degrading back to
  sequential despite the code's intent. `GESTURE_SPEECH_SYNC_ENABLED`
  (default `false`, preserves the behavior above unchanged) flips the
  order instead: synthesize first, then start the gesture right as
  playback begins, so the overlap no longer depends on synthesis speed at
  all — at the cost of Cozmo appearing to pause (mood/face still update
  immediately either way, independent of this) for however long synthesis
  takes, rather than reacting the instant `say` is called. **Verified as
  call-order logic only** (a scripted test with a fake speech client
  confirms `synthesize()` and the gesture start happen in the order this
  setting implies, in both positions). **Confirmed on real hardware:**
  toggling `GESTURE_SPEECH_SYNC_ENABLED` true vs. false produces a real,
  observable behavior difference (tested alongside `TTS_PROVIDER=edge`,
  whose higher latency — see below — makes the default's overlap loss
  easy to notice) — which setting actually feels better is a personal/
  use-case call, not settled here. With `true` specifically, confirmed the
  pause is exactly as designed: mood/face changes immediately (unaffected
  by this setting, applied at the very top of `handle_say()` regardless),
  while the gesture's physical movement is what actually waits for
  synthesis to finish.

  **A real bug found while auditing this, before it ever shipped to real
  use:** with `GESTURE_SPEECH_SYNC_ENABLED=true`, the gesture start was
  placed straight after the `speech.synthesize()` call - if synthesis
  raised (a real risk with any provider, `local` included, e.g. a missing
  Piper voice model or an unhandled Piper/faster-whisper error), the
  gesture was skipped entirely, not just delayed. For a gesture that
  raises the lift and only lowers it again as its own final step (see
  `gestures.py`), that meant the arm could get stuck up with nothing left
  to bring it back down - the exact failure mode the recent
  "recover the lift position if a background gesture step fails" fix
  (above) was built to catch, just via a different path it didn't cover.
  Fixed by moving the sync-mode gesture start into a `finally` block, so
  it still runs even when synthesis fails (the original exception still
  propagates afterward, so the tool call is correctly reported as failed
  to the model either way). **Verified as call-order logic only** (a
  scripted test confirms the gesture still starts when a fake speech
  client's `synthesize()` raises, in sync mode) - not yet confirmed this
  was the actual cause of any specific real-hardware report.

  **Raised directly, a real regression from going async:** the lift
  arm was reportedly not reliably ending up fully down anymore after a
  gesture. Root cause: `run_gesture_async()`'s background thread had *no*
  exception handling at all. Several gestures (`wake_up`, `cheer`,
  `shrug`, `fist_pump`, `alert`) raise the lift partway through and only
  lower it again as their own *final* step — in the old blocking
  `run_gesture()`, a mid-gesture failure on any earlier step was already
  visible as a failed tool call and (like any exception) would have
  equally abandoned the remaining steps; on a background thread it
  instead dies silently (Python's default: print a traceback, give up —
  invisible in our own logs), with the arm possibly left raised and
  nothing anywhere saying why. Exactly the one failure mode a background
  thread with physical side effects can't afford to have be silent.
  Fixed by wrapping the background thread's step loop in a try/except
  that logs the failure clearly and attempts `lower_lift_fully()` as a
  recovery action regardless of which step failed. **Verified the
  mechanism** with a scripted test that forces a mid-gesture exception
  (patching `set_head_angle_deg` to raise) and confirms the recovery
  lower-lift call fires right after the logged warning — **not
  confirmed this was the actual real-hardware trigger**, since nothing
  here can reproduce whatever specifically failed (a transient
  connection hiccup during `reconnect()` is a plausible candidate,
  since a background gesture step can now genuinely run concurrently
  with one). Either way, the recovery behavior is correct regardless of
  which step raised.

Still open, roughly in priority order:

1. **Smarter conversation trimming.** Current strategy is a hard message-count
   cutoff. Token-aware summarization of older turns would use the 262144
   token context window better on long sessions. Related, not started: an
   end-of-run summary ("last time we talked about...") to start a new run
   from instead of resuming raw messages - see
   [Conversation history and memory](memory.md#conversation-history-and-memory).
   (Suggested memory facts for the user to approve: done, same section.)
2. **Systemd service** for headless/boot-time operation, now that reconnect
   logic makes a long-running session more viable.
3. **More real animations, curated.** First batch built: ~25 hand-picked
   clips by friendly name in the `say`/`gesture` tools (see
   [Architecture](architecture.md)), played without wheels, with speech merged
   into the clip. Still open: hooking the `cue` clips (before a photo) and the
   `idle` clips (fidgets) and a recognised-face greeting up to their events;
   per-clip "wheels allowed" for the few that look better driving; and the
   clips' original sounds, which PyCozmo skips (`standalone/dump_clip_sounds.py`
   names them; the sound banks are extracted, but PyCozmo can't yet follow most
   Wwise events to a file or decode `.wem`).
4. **React to physical sensors — picked up, touched, shaken, cliff-detected,
   placed on the charger. Mostly done now** (see the ✅ sub-items below);
   The full detail (what is confirmed on real hardware vs. simulated only) is in
   [Roadmap item 4: physical sensors](roadmap-physical-sensors.md).
5. **A fully key-free STT+TTS provider**, on top of the existing Groq/OpenAI
   split — for running with literally no API account at all, not just as a
   Groq-rate-limit fallback. Two different properties are easy to conflate
   here, worth being precise about:
   - ✅ **No key AND no network — done.** `STT_PROVIDER`/`TTS_PROVIDER=local`
     (`cozmo_brain/llm/local_client.py`): `faster-whisper` (CTranslate2,
     real CPU speedups over plain Whisper) for STT, **Piper** for TTS —
     confirmed real and specifically built/validated for Raspberry
     Pi-class hardware (used throughout the Home Assistant/Rhasspy
     voice-assistant ecosystem), and already ONNX-based like
     `openwakeword`, so it fits the existing dependency pattern. See
     [Switching speech providers](providers.md#switching-speech-providers-and-separately-chat).
     **The real cost is latency, untested against real hardware:** CPU-only
     Whisper inference on a Pi 5 (no GPU) will be slower than Groq's cloud
     Whisper on dedicated hardware, and by how much is unmeasured — no Pi
     was available here to benchmark on. `LOCAL_STT_MODEL`/
     `LOCAL_TTS_VOICE_PATH` are both easy to drop to a smaller/faster
     model/voice from `.env` alone once real latency is known.
   - ✅ **No key, but still needs the internet — done, TTS side.**
     `TTS_PROVIDER=edge` (`cozmo_brain/llm/edge_client.py`) uses Microsoft
     Edge's own cloud TTS via the unofficial `edge-tts` library — no
     account, but not a published/supported Microsoft product, so it can
     break if Microsoft changes something internally (historically fixed
     upstream, not guaranteed). Good natural-sounding voices for free, at
     the cost of that reliability risk and still needing a network. See
     [Switching speech providers](providers.md#switching-speech-providers-and-separately-chat)
     for the real integration cost this surfaced (edge-tts only ever
     returns MP3, decoded via `PyAV` before reaching `tts_postprocess.py`)
     and what's actually been verified. **STT side still not built** — the
     Wit.ai option added separately (`STT_PROVIDER=witai`) covers a
     different trade-off (needs its own account, but no billing) rather
     than this exact "no key, needs internet" STT combination; nothing
     currently fills that specific gap.
6. **Autonomous return-to-charger on low battery** — built (see the
   "Implementation" sub-item at the end), **first run on real hardware on
   2026-09-28: navigation and the autonomous CRITICAL return worked**; later
   fixes are verified as logic only (see the dated sub-items).
   The full design, hardware iterations and open issues are in
   [Roadmap item 6: return to the charger](return-to-charger.md).
7. **Reminders - via your assistant: built; spoken by Cozmo: planned.**
   With `ASSISTANT_ENABLED=true`, "remind me at 5 to call mom" goes to your
   own assistant agent through `ask_assistant`, which sets the reminder and
   delivers it (OpenClaw: a message on your phone) - works while Cozmo is
   off. See [Asking your own assistant](assistant.md#asking-your-own-assistant-ask_assistant).
   Not run end to end on real hardware yet. What follows is the still-open
   part: Cozmo saying a reminder out loud himself.

   Today Cozmo knows the date and
   time (it's in his prompt every turn), but he only thinks when spoken
   to: "remember my dentist appointment is Monday at 5" is saved as a
   memory fact, and he may mention it if you talk to him that day, but he
   never speaks up on his own at 4:55. Real reminders need:
   - a reminder list (e.g. `data/reminders.json`: time, message, done);
   - tools to set ("remind me at 5 to call mom"), list, and cancel them;
   - a background check (every ~30s) that says a due reminder out loud,
     through the same `turn_lock` + `engine.speak()` path the low-battery
     offer uses (`charger_return.py`), so it never talks over a reply.

   Limits to design around: Cozmo has to be on, connected and running the
   app - a reminder due while he's off or disconnected can only be said
   late, when he's back; you have to be in the room to hear it (no phone
   notification without a separate service); repeating reminders ("every
   day at 9") are more work - start with one-off ones. To brainstorm
   before building: what happens to a missed reminder, whether he repeats
   one until it's acknowledged, and whether quiet hours are needed.
8. **"Can I come out, and for how long?" from real battery data - planned,
   data collection only so far.** Today leaving the charger (asked, idle
   peek, or the charger break) is allowed by one fixed rule: not docked at
   or below `BATTERY_LOW_VOLTAGE`. A guessed "high enough" voltage was
   deliberately not added, since the dock reads high and this robot's curve
   is unknown. Instead `data/battery.jsonl` (`battery_log.py`, see
   [Coming off the charger](connection-and-battery.md#coming-off-the-charger-battery-line-and-charger-break))
   records every charge session and every stretch off the dock. To build,
   once a few stretches have actually reached low:
   - check the numbers with `python3 standalone/battery_report.py`, then
     decide the rule (e.g. the minutes-to-low fit vs voltage on leaving);
   - use it when asked to come out ("sure, I've got about 10 minutes") and
     for the charger break, falling back to today's rule with too little data;
   - add a separate retention setting for `battery.jsonl` (it is kept
     forever until then - deliberately not tied to `HISTORY_RETENTION_DAYS`).
