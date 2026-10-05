# Repo layout

[← Back to the README](../README.md)


```
.
├── standalone/              # one-off scripts, not part of cozmo_brain (run from the repo root)
│   ├── orchestrator.py      # lightweight single-file test script (no memory/tools/gestures)
│   ├── battery_report.py    # summarise data/battery.jsonl: charge sessions, time off the dock, minutes-to-low
│   ├── pose_drift_test.py   # pose/dead-reckoning drift test behind return-to-charger (roadmap item 6)
│   ├── show_history.py      # print archived conversations as a readable transcript
│   └── sync_env.py          # after a git pull: shows/appends .env settings missing vs .env.example
├── tests/                   # offline tests (fakes - no robot/mic/keys): python tests/run_all.py
│   └── live/                # opt-in tests that call real LLM providers with your .env keys
├── models/                  # model files, named in .env without folder/extension (see "Model files")
│   ├── hey_cozmo.onnx       # custom "hey Cozmo" wake word (committed)
│   └── en_US-lessac-medium.onnx(.json)  # Piper voice for TTS_PROVIDER=local (downloaded, gitignored)
├── training/
│   └── wake_word_training.ipynb  # Colab notebook that trained hey_cozmo.onnx (see "Training a custom wake word")
├── data/                    # everything Cozmo saves at runtime (gitignored) - see "Conversation history and memory"
│   ├── history/<date>/<time>.jsonl   # one transcript per run, photos next to it
│   ├── memory.md            # facts about people, sent to the model every turn - edit freely
│   ├── battery.jsonl        # battery events: charge curve + every time off the dock (battery_log.py)
│   ├── known_people/        # remember_person reference photos
│   └── look.png             # last camera snapshot
├── run.sh                   # activates cozmo-env + runs cozmo_brain in one step (./run.sh --help)
├── dashboard.sh             # starts the Control Room web dashboard (start/stop Cozmo, live log, history, .env editor)
├── cozmo_dashboard/         # the dashboard: stdlib-only Python server + static page (see "The dashboard")
├── requirements.txt        # pip install -r requirements.txt (core deps only — see setup step 1 for extras)
├── .env.example             # template for every config variable, annotated — copy to .env
├── .env                      # your real config (gitignored, not in repo)
├── README.md              # overview, quick start and the index of docs/
├── docs/                    # the detailed documentation, one page per topic (linked from the README)
└── cozmo_brain/              # the full application
    ├── main.py               # CLI entry point (--mode voice|vad|text|calibrate, --simulate)
    ├── config.py             # typed Settings, loaded from .env
    ├── conversation.py       # what the model sees: system prompt + recent messages, trimmed; resumes from the archive
    ├── history_archive.py    # data/history/: every run's full transcript + photos, by date
    ├── memory.py             # data/memory.md: facts about people, plus search over facts and transcripts
    ├── memory_suggestions.py # after a conversation: suggests facts worth keeping, for you to approve
    ├── engine.py             # the agentic tool-calling loop (CozmoEngine)
    ├── charger_return.py     # low-battery policy: offer at LOW, return on its own at CRITICAL
    ├── charger_break.py      # opt-in: steps off the dock after a while and drives back (CHARGER_BREAK_ENABLED)
    ├── battery_log.py        # data/battery.jsonl: charge sessions and every stretch off the dock
    ├── connection_monitor.py # background link check: reconnects a dropped connection while idle (CONNECTION_CHECK_INTERVAL_S)
    ├── idle_fidget.py        # small idle gestures after a quiet spell (IDLE_FIDGET_AFTER_S)
    ├── presence.py           # opt-in presence check: looks up when idle to see who's at the desk (PRESENCE_CHECK_ENABLED)
    ├── people.py             # shared face-matching helpers (who_is_this, presence check)
    ├── assistant_relay.py    # background ask_assistant: answers spoken when they arrive (ASSISTANT_BACKGROUND)
    ├── personality.py        # Cozmo's system prompt / persona
    ├── imaging.py             # shared base64 image helper (vision attach, who_is_this)
    ├── model_files.py         # finds models/<name>.onnx for WAKE_WORD_MODEL / LOCAL_TTS_VOICE_PATH
    ├── llm/
    │   ├── ollama_client.py   # /api/chat wrapper with tool-calling
    │   ├── chat_client.py      # ChatClient interface (CHAT_PROVIDER=ollama/groq/openai)
    │   ├── openai_compatible_chat.py  # Shared chat-completions impl for GroqChatClient/OpenAIChatClient (tool-calling + vision wire translation)
    │   ├── speech_client.py    # SpeechClient interface shared by every STT/TTS provider
    │   ├── groq_client.py       # Groq Whisper STT + Orpheus TTS + GroqChatClient
    │   ├── openai_client.py     # OpenAI Whisper STT + TTS + OpenAIChatClient (STT_PROVIDER/TTS_PROVIDER/CHAT_PROVIDER=openai)
    │   ├── local_client.py      # Offline faster-whisper STT + Piper TTS (STT_PROVIDER/TTS_PROVIDER=local)
    │   ├── witai_client.py      # Wit.ai (Meta) STT only, free/no billing (STT_PROVIDER=witai)
    │   ├── edge_client.py       # Microsoft Edge online TTS only, free/no key (TTS_PROVIDER=edge)
    │   ├── assistant_client.py  # external assistant agent (ask_assistant), OpenAI-compatible, e.g. OpenClaw
    │   ├── tts_postprocess.py    # shared voice character + gain, used by every TTS provider
    │   └── stt_postprocess.py    # filters known STT hallucination/fallback phrases (Whisper + Wit.ai)
    ├── audio/
    │   ├── recorder.py        # fixed-length arecord capture (push-to-talk)
    │   ├── vad.py              # webrtcvad-based hands-free capture
    │   ├── speech_gate.py      # Silero speech check on every clip before any STT call
    │   ├── wakeword.py         # openWakeWord-based wake word, gates --mode vad
    │   └── player.py           # optional system-speaker audio output (AUDIO_OUTPUT)
    ├── robot/
    │   ├── __init__.py         # create_robot() factory (real vs. simulated)
    │   ├── base.py             # RobotBackend interface + shared mood/gesture playback
    │   ├── real.py             # PyCozmo-backed implementation (+ health/reconnect)
    │   ├── simulated.py        # console-logging implementation, no hardware needed
    │   ├── wifi.py             # optional nmcli-based Wi-Fi auto-connect
    │   ├── pickup_reactor.py   # startle reaction when Cozmo is picked up
    │   ├── moods.py            # curated expression+light+pose presets
    │   ├── gestures.py         # curated multi-step gesture choreography
    │   ├── battery_face.py     # renders the low-battery face icon
    │   └── battery_monitor.py  # background thread: checks voltage, shows the icon
    ├── tools/
    │   ├── base.py             # Tool/ToolResult contract
    │   ├── registry.py         # the tools exposed to the LLM
    │   ├── memory_tools.py     # remember_fact / forget_fact / search_memory / review_suggestion / clear_suggestions
    │   └── assistant_tools.py  # ask_assistant + its system-prompt section (ASSISTANT_ENABLED)
    └── modes/
        ├── interactive.py       # --mode voice  (push-to-talk)
        ├── vad_mode.py           # --mode vad    (hands-free, wake word + follow-up)
        ├── text_mode.py          # --mode text   (typed input, dev-friendly)
        └── calibrate_mode.py     # --mode calibrate (measure turn() accuracy)
```
