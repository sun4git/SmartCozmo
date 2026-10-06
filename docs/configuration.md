# Configuration reference

[← Back to the README](../README.md)


All runtime config lives in `.env` — see `.env.example` for the full,
annotated list (it's the source of truth). The essentials:

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | Required unless both `STT_PROVIDER` and `TTS_PROVIDER` are `openai`. Auth for Groq STT + TTS. |
| `OPENAI_API_KEY` | Required if `STT_PROVIDER` or `TTS_PROVIDER` is `openai`. |
| `WITAI_ACCESS_TOKEN` | Required if `STT_PROVIDER` is `witai`. Free, no billing — Wit.ai (Meta) has no TTS product, so this is STT-only. |
| `STT_PROVIDER` / `TTS_PROVIDER` | `groq` (default), `openai`, or `local`, set independently — which provider does STT vs. TTS; they don't have to match. `STT_PROVIDER` can also be `witai`; `TTS_PROVIDER` can also be `edge`. |
| `CHAT_PROVIDER` | Which provider does chat/tool-calling: `ollama` (default), `groq`, or `openai`. |
| `VISION_PROVIDER` | Which provider handles image-bearing turns (`look`/`who_is_this`) — independent of `CHAT_PROVIDER`, e.g. `ollama` for chat + `groq` for vision. Empty = same as `CHAT_PROVIDER`. |
| `CHAT_TIMEOUT_S` | Request timeout for `CHAT_PROVIDER`=`groq`/`openai` chat calls (Ollama has its own `OLLAMA_TIMEOUT_S`). |
| `GROQ_CHAT_MODEL` / `OPENAI_CHAT_MODEL` | Model name for `CHAT_PROVIDER`=`groq`/`openai` — must support tool-calling. |
| `OLLAMA_VISION_MODEL` / `GROQ_VISION_MODEL` / `OPENAI_VISION_MODEL` | Model used instead, per chat provider, on any turn with an image attached (`look`/`who_is_this`). Empty = fall back to that provider's regular chat model. |
| `OLLAMA_BASE_URL` | Ollama endpoint chat/tool-calling requests go to. |
| `OLLAMA_MODEL` | Model name; must support `tools`. Doesn't need `vision` too unless `OLLAMA_VISION_MODEL` is left empty. |
| `RAW_INPUT_WAV` / `TTS_OUTPUT_WAV` | Scratch file names for the recorded input and the synthesized reply (relative to the project; defaults `input.wav` / `cozmo_reply.wav`). |
| `RECORD_SECONDS` / `RECORD_DEVICE` | Push-to-talk recording length and ALSA/PipeWire device. |
| `AUDIO_OUTPUT` / `PLAYBACK_DEVICE` | Route speech to `cozmo` (default), `system` speaker, or `both` at once. |
| `GROQ_STT_MODEL` / `GROQ_TTS_MODEL` / `GROQ_TTS_VOICE` | Groq model/voice choices (used when `STT_PROVIDER`/`TTS_PROVIDER`=`groq`). |
| `GROQ_TTS_SPEED` | Groq's native speed control (0.5-5.0, default 1.0). |
| `OPENAI_STT_MODEL` / `OPENAI_TTS_MODEL` / `OPENAI_TTS_VOICE` | OpenAI model/voice choices (used when `STT_PROVIDER`/`TTS_PROVIDER`=`openai`). |
| `OPENAI_TTS_SPEED` | OpenAI's native speed control — try ~0.85-0.90 to roughly match Groq's pacing. |
| `LOCAL_STT_MODEL` / `LOCAL_STT_DEVICE` / `LOCAL_STT_COMPUTE_TYPE` | faster-whisper model size/device/compute type (used when `STT_PROVIDER`=`local`). No key, no network after the first model download. |
| `LOCAL_TTS_VOICE_PATH` | Piper voice for `TTS_PROVIDER`=`local`: its name in `models/` (`en_US-lessac-medium`) or a path to its `.onnx`. See [Model files](wake-word.md#model-files-models). |
| `LOCAL_TTS_SPEED` | Speed, same `>1.0`=faster direction as `GROQ_TTS_SPEED`/`OPENAI_TTS_SPEED` (Piper's own `length_scale` is the inverse — converted in `local_client.py`). |
| `EDGE_TTS_VOICE` | Stock `edge-tts` voice name (used when `TTS_PROVIDER`=`edge`). List them with `edge-tts --list-voices`. |
| `EDGE_TTS_SPEED` | Speed, same `>1.0`=faster direction as the other `*_TTS_SPEED` settings (converted to edge-tts's own signed-percentage `rate` string). |
| `TTS_GAIN` | Max volume boost for TTS output, RMS-targeted with a soft limiter (Cozmo's speaker is quiet). |
| `TTS_LEADIN_MS` | Silent lead-in before speech, works around the first word often being inaudible. |
| `TTS_PITCH_SHIFT` | Pitch+tempo shift for a smaller/more childlike/robotic voice. 1.0 = off. |
| `TTS_ROBOT_MOD_DEPTH` / `TTS_ROBOT_MOD_HZ` | Optional ring-modulation robotic timbre. Depth 0.0 = off. |
| `LOG_LEVEL` | Default log level (`DEBUG`/`INFO`/`WARNING`/`ERROR`, case-insensitive; default `INFO`). `--log-level` overrides it per run. |
| `ROBOT_BACKEND` | `real` or `simulated` (cozmo_brain/ only; `--simulate` overrides it). |
| `ROBOT_STALE_AFTER_S` | Seconds without robot telemetry before auto-reconnect kicks in. |
| `CONNECTION_CHECK_INTERVAL_S` | How often (default 120s) a background check looks for a dropped connection and reconnects, so a drop is caught while Cozmo is idle too, not only before the next tool call. While disconnected it retries every 30s at most. `0` = off. See [Connection and battery](connection-and-battery.md). |
| `BATTERY_LOW_VOLTAGE` / `BATTERY_CRITICAL_VOLTAGE` | Voltage thresholds for the face battery-warning icon; `BATTERY_LOW_VOLTAGE` also gates whether `drive`/`turn` refuse to move while charging (real backend only). |
| `BATTERY_CHECK_INTERVAL_S` | How often the battery monitor polls voltage. |
| `COZMO_WIFI_SSID` / `COZMO_WIFI_PASSWORD` | Optional Wi-Fi auto-connect (Linux/nmcli only). Password only needed for the first connect. |
| `TURN_SPEED_MMPS` / `TURN_SECONDS_PER_DEGREE` | `turn()` calibration — tune with `--mode calibrate`. |
| `MAX_DRIVE_SPEED_MMPS` / `MAX_DRIVE_DISTANCE_MM` | Safety clamps on the `drive` tool. |
| `CHARGER_EXIT_DISTANCE_MM` / `CHARGER_EXIT_SPEED_MMPS` | How far/fast to drive straight off the charger before performing a requested turn, if still docked (real backend only). |
| `CHARGER_DOCK_DISTANCE_MM` / `CHARGER_DOCK_SPEED_MMPS` | How far/fast the `dock` tool reverses onto the charger (real backend only). The distance also sets how far out from the recorded charger pose the return-to-charger staging point is. |
| `CHARGER_DOCK_OVERSHOOT_MM` | How much further than `CHARGER_DOCK_DISTANCE_MM` the dock reverse may continue; it stops the moment the charger contacts engage. |
| `CHARGER_DOCK_HEADING_GAIN` / `CHARGER_DOCK_JAM_DEG` / `CHARGER_DOCK_RETRIES` | How `dock()`'s reverse steers and recovers: wheel-speed difference per degree off the charger's heading (default 2.0; `0` = a plain blind reverse), how far he may twist off that heading before the reverse stops as "a tread caught the charger" (default 10°), and how many times a missed dock is retried from the staging point (default 1). |
| `CHARGER_CONTACT_WAIT_S` | How long `dock()` waits after reversing for the charger contacts to read as engaged before calling it a miss (the flag can lag behind actually being seated). |
| `CHARGER_BREAK_ENABLED` | Default `false`. `true` lets Cozmo step off the charger after `CHARGER_BREAK_AFTER_MIN` continuous docked minutes (30) while quiet and not too low to leave (`BATTERY_LOW_VOLTAGE`), then drive back after a random `CHARGER_BREAK_STRETCH_MIN`–`CHARGER_BREAK_STRETCH_MAX` minutes (5–10). See [Coming off the charger](connection-and-battery.md#coming-off-the-charger-battery-line-and-charger-break). |
| `AUTO_RETURN_TO_CHARGER_ENABLED` | Low-battery return-to-charger: offer at `BATTERY_LOW_VOLTAGE`, go on its own at `BATTERY_CRITICAL_VOLTAGE`, ask for help if the charger location isn't known (default `true`). |
| `RETURN_TO_POSE_TIMEOUT_S` | Upper bound on one `go_to_pose()` navigation leg before it's aborted (pycozmo's own has no timeout). |
| `MAX_TOOL_ITERATIONS` | Cap on LLM↔tool round-trips per user turn. |
| `FINAL_LLM_CALL` | The LLM call after a reply ending in a clean `say` (usually just "done"): `async` (default) reopens the mic right away and makes it in the background, pausing the recording if the model continues (`--mode vad` only; other modes behave as `sync`); `sync` waits for it before listening again (the fallback if `async` misbehaves); `skip` drops it (fastest, but a model that says "sure!" alone and acts in its *next* step, confirmed live with Groq's `openai/gpt-oss-120b`, loses the action). Early end only happens when every tool in the batch is a pure action (`Tool.safe_to_end_turn`, opt-in per tool, so new tools default to asking again) and nothing needs the model's attention (errors, hazards, refused moves, photos). A turn never returns until background gestures finish, so the mic doesn't reopen mid-`spin`. Worked examples: [How a turn ends](turn-flow.md#how-a-turn-ends-final_llm_call). |
| `CONVERSATION_MAX_MESSAGES` | How many messages the model sees (system prompt + recent history). |
| `DATA_DIR` | Where runtime data goes (default `data`, relative to the project): history, memory, photos. |
| `HISTORY_RETENTION_DAYS` | Delete archived days older than this at startup (default `0` = keep everything). |
| `MEMORY_MAX_FACTS` | Most facts `remember_fact` may save (default 40, read at startup); the memory is sent with every turn, so raising it makes every request longer. |
| `MEMORY_SUGGESTIONS_ENABLED` | After each conversation, one LLM call suggests facts worth keeping, for Cozmo to ask you about (default `true`). |
| `CONVERSATION_HISTORY_PATH` | The old single-file history - only read once, to import it into the archive. |
| `VAD_AGGRESSIVENESS` / `VAD_SILENCE_MS` / `VAD_MAX_UTTERANCE_S` | Hands-free listening tuning. |
| `VAD_FOLLOWUP_TIMEOUT_S` | How long a conversation stays open after a reply before the wake word is needed again. |
| `SPEAK_UP_LISTEN_S` | After Cozmo speaks up on his own with something you may answer (a background `ask_assistant` answer, the low-battery offer), `--mode vad` listens this many seconds without the wake word (default 10; `0` = off). |
| `STT_LANGUAGE` | Language hint for Whisper (Groq/OpenAI/local), default `en`; empty = auto-detect. Stops foreign-language hallucinations. |
| `STT_NO_SPEECH_PROB` / `STT_MIN_AVG_LOGPROB` / `STT_MAX_COMPRESSION_RATIO` | Per-segment Whisper confidence filters; see [Keeping speech-to-text from hallucinating](stt-hallucination.md#keeping-speech-to-text-from-hallucinating). Every segment's scores are logged for tuning. |
| `SPEECH_GATE_ENABLED` / `SPEECH_GATE_MIN_SPEECH_MS` / `SPEECH_GATE_THRESHOLD` | Silero speech check on every clip before any STT call (skipped if faster-whisper isn't installed). A clip with less than the minimum detected speech (default 150ms) isn't sent. |
| `VAD_MIN_SPEECH_MS` | Onset debounce (consecutive ms of speech needed to start recording, not a minimum utterance length) — filters out noise-triggered hallucinated transcriptions. |
| `VAD_MIN_RMS` | Loudness floor a frame must also clear (in addition to webrtcvad) to count as speech — the actual quota-saving filter, rejects noise before it ever reaches the STT API. |
| `WAKE_WORD_MODEL` / `WAKE_WORD_THRESHOLD` | Wake word gating `--mode vad` — a model's name in `models/` (default `hey_cozmo`), a stock name (`hey_jarvis`, …), or a path to an `.onnx`. |
| `TAP_THRESHOLD` / `TAP_DEBOUNCE_MS` | `--mode vad`'s tap-activation tuning — accelerometer-spike threshold and minimum time between accepted taps (real backend only; alternate trigger alongside the wake word). Cozmo's own lift movements (e.g. the `shrug` idle-fidget gesture) are suppressed separately, not via this threshold. |
| `CLIPS_ENABLED` | Master switch for all real Anki animation clips (default true): the start/end animations, the curated clip names the model can use, and `play_animation`/`list_animations`. false = built-in gestures only. |
| `CLIP_SPEECH_OVERLAP` | Play a curated clip while Cozmo speaks (default true; the speech audio is merged into the clip's frames). false = the clip plays right after the speech. |
| `CLIP_SOUNDS` / `CLIP_SOUND_VOLUME` / `CLIP_SOUND_SPEECH_VOLUME` / `CLIP_SOUNDS_DIR` | Cozmo's own sounds in the real clips (default `full`; volume 0.5 alone and 0.15 under speech, so the speech stays the main thing; folder `data/clip_sounds`). `off`, `effects` (only screen/servo blips under speech, never his voice) or `full`. Needs the sounds recovered with `standalone/extract_clip_sounds.py` and copied to the robot's machine (they are not in the repo); without them clips stay silent. |
| `WAKE_SLEEP_CLIPS` | Use the real wake-up clip at start and go-to-sleep clips at the end (default true; falls back to the `wake_up`/`sleep` gestures if off, or if clips are off or not downloaded). |
| `IDLE_FIDGET_ENABLED` / `IDLE_FIDGET_AFTER_S` | Whether Cozmo plays a small idle gesture after this many quiet seconds with no real conversation turn. Default 30s — kept short since Cozmo's own hardware-level inactivity disconnect/power-off happens well before the original 5min default ever fired. |
| `GESTURE_ASYNC_ENABLED` | Whether `gesture` runs in the background so `say` can overlap with it, instead of blocking until the gesture finishes. |
| `GESTURE_SPEECH_SYNC_ENABLED` | `false` (default) starts `say`'s bundled gesture before synthesizing speech (instant reaction, overlap not guaranteed if synthesis is slow). `true` synthesizes first and starts the gesture right as playback begins (overlap guaranteed regardless of synthesis speed, at the cost of a pause before Cozmo reacts). |
| `VISION_ENABLED` | Whether `look()`'s photo gets attached to the next LLM turn. |
| `KNOWN_PEOPLE_DIR` | Where `remember_person`'s reference photos are stored (default `data/known_people`; experimental). |
| `PRESENCE_CHECK_ENABLED` | Default `false`. `true` lets Cozmo occasionally look up when idle to see who's at the desk, and ask strangers who they are (see [Presence check](people-recognition.md#presence-check-knowing-whos-at-the-desk-presence_check_enabled)). Sends a photo to `VISION_PROVIDER` each look. |
| `PRESENCE_HEAD_ANGLE_DEG` | Head tilt for the look (default 35°; robot max ≈ 44°). |
| `DASHBOARD_PORT` / `DASHBOARD_HOST` / `DASHBOARD_TOKEN` | Read only by the [dashboard](dashboard.md), not the app. Port (default 30540); address to listen on (default `127.0.0.1` = this machine only); and the password asked once per browser (empty = none; set one whenever the host isn't `127.0.0.1`). |
| `ASSISTANT_ENABLED` | Default `false`. `true` adds the `ask_assistant` tool (needs `ASSISTANT_URL`). See [Asking your own assistant](assistant.md#asking-your-own-assistant-ask_assistant). |
| `ASSISTANT_NAME` | What Cozmo calls the assistant, out loud and in the tool description (default `Assistant`). |
| `ASSISTANT_URL` / `ASSISTANT_TOKEN` | Base URL of the OpenAI-compatible endpoint (no `/v1/...`) and its bearer token. |
| `ASSISTANT_MODEL` | The request's `model` field; for OpenClaw it picks the agent, `openclaw/<agentId>` (default `openclaw/default`). |
| `ASSISTANT_SESSION_USER` | Sent as `user`; OpenClaw keeps one session per value (default `cozmo`). |
| `ASSISTANT_BACKGROUND` | `true` (default): ask in the background and speak up with the answer when it arrives. `false`: wait silently for it. |
| `ASSISTANT_TIMEOUT_S` | Longest wait for an answer (default 180; lower it with `ASSISTANT_BACKGROUND=false`). |
| `PRESENCE_RECHECK_S` / `PRESENCE_EMPTY_RETRY_S` / `PRESENCE_ASK_COOLDOWN_S` | Seconds before the next look after a known person (900) / nobody (300) / a stranger (3600, also the minimum gap between "who are you?"s). |
