"""Central configuration, loaded from environment variables (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, str(default)))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # --- Secrets ---
    groq_api_key: str = field(default_factory=lambda: os.environ.get("GROQ_API_KEY", ""))
    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))

    # Which provider does STT, TTS, and chat/tool-calling — each is
    # independently configurable, so e.g. STT_PROVIDER=groq + TTS_PROVIDER=openai
    # is valid. "groq" (default), "openai", or "local" (fully offline, no API
    # key — see the Local STT/TTS section below) for STT/TTS; "ollama"
    # (default), "groq", or "openai" for chat. See
    # cozmo_brain/llm/__init__.py's create_speech_client() / create_chat_client().
    stt_provider: str = field(default_factory=lambda: _env_str("STT_PROVIDER", "groq"))
    tts_provider: str = field(default_factory=lambda: _env_str("TTS_PROVIDER", "groq"))
    chat_provider: str = field(default_factory=lambda: _env_str("CHAT_PROVIDER", "ollama"))

    # Which provider handles a turn that has an image attached (`look`/
    # `who_is_this`) — independent of CHAT_PROVIDER, so e.g.
    # CHAT_PROVIDER=ollama + VISION_PROVIDER=groq is valid: regular
    # chat/tool-calling stays on Ollama, only vision turns go to Groq
    # instead. Left empty (default), it falls back to CHAT_PROVIDER — same
    # provider for both, just possibly a different *model* within it (see
    # GROQ_VISION_MODEL etc. below). See create_chat_client()'s _ChatRouter.
    vision_provider: str = field(default_factory=lambda: _env_str("VISION_PROVIDER", ""))

    # Request timeout in seconds for GroqChatClient/OpenAIChatClient (Ollama
    # has its own OLLAMA_TIMEOUT_S below).
    chat_timeout_s: int = field(default_factory=lambda: _env_int("CHAT_TIMEOUT_S", 60))

    # Model name for CHAT_PROVIDER=groq/openai — must support tool-calling.
    # Defaults are current, tool-capable, cost-efficient models as of when
    # this was written; check each provider's own model list if either 404s
    # or gets deprecated.
    groq_chat_model: str = field(default_factory=lambda: _env_str("GROQ_CHAT_MODEL", "llama-3.3-70b-versatile"))
    openai_chat_model: str = field(default_factory=lambda: _env_str("OPENAI_CHAT_MODEL", "gpt-4o-mini"))

    # Model used instead, for whichever CHAT_PROVIDER is active, on any turn
    # that has an image attached (the `look`/`who_is_this` tools) — only
    # matters when the regular chat model above isn't itself vision-capable.
    # Left empty, each falls back to its own chat model above.
    # GROQ_CHAT_MODEL's default (llama-3.3-70b-versatile) is NOT
    # vision-capable, so GROQ_VISION_MODEL needs its own real default;
    # OPENAI_CHAT_MODEL's default (gpt-4o-mini) already handles vision
    # itself, so OPENAI_VISION_MODEL is left empty (falls back) by default.
    # Same for OLLAMA_MODEL — pick a vision-capable one there directly if
    # it doesn't already support vision, or set OLLAMA_VISION_MODEL instead.
    ollama_vision_model: str = field(default_factory=lambda: _env_str("OLLAMA_VISION_MODEL", ""))
    groq_vision_model: str = field(default_factory=lambda: _env_str("GROQ_VISION_MODEL", "qwen/qwen3.8-27b"))
    openai_vision_model: str = field(default_factory=lambda: _env_str("OPENAI_VISION_MODEL", ""))

    # --- Ollama (tool-calling LLM) ---
    ollama_base_url: str = field(default_factory=lambda: _env_str("OLLAMA_BASE_URL", "http://192.168.1.200:41438"))
    ollama_model: str = field(default_factory=lambda: _env_str("OLLAMA_MODEL", "gemma4:31b-cloud"))
    ollama_timeout_s: int = field(default_factory=lambda: _env_int("OLLAMA_TIMEOUT_S", 60))

    # --- Audio input (mic) ---
    record_seconds: int = field(default_factory=lambda: _env_int("RECORD_SECONDS", 5))
    record_device: str = field(default_factory=lambda: _env_str("RECORD_DEVICE", "pipewire"))

    # --- Audio output routing ---
    # "cozmo" (default) plays through Cozmo's own speaker only. "system"
    # plays through this machine's own audio output instead (e.g. the same
    # Bluetooth speaker used for the mic) - useful in a noisy room where
    # Cozmo's tiny speaker isn't loud/clear enough even with TTS_GAIN.
    # "both" plays through both at once, so Cozmo still "performs" the line
    # while it's also actually intelligible. See cozmo_brain/audio/player.py.
    audio_output: str = field(default_factory=lambda: _env_str("AUDIO_OUTPUT", "cozmo"))
    playback_device: str = field(default_factory=lambda: _env_str("PLAYBACK_DEVICE", "pipewire"))

    # --- Groq STT/TTS (used when STT_PROVIDER/TTS_PROVIDER=groq) ---
    groq_stt_model: str = field(default_factory=lambda: _env_str("GROQ_STT_MODEL", "whisper-large-v3-turbo"))
    groq_tts_model: str = field(default_factory=lambda: _env_str("GROQ_TTS_MODEL", "canopylabs/orpheus-v1-english"))
    groq_tts_voice: str = field(default_factory=lambda: _env_str("GROQ_TTS_VOICE", "austin"))

    # --- OpenAI STT/TTS (used when STT_PROVIDER/TTS_PROVIDER=openai) ---
    # Model/voice names are OpenAI's own, not interchangeable with Groq's.
    openai_stt_model: str = field(default_factory=lambda: _env_str("OPENAI_STT_MODEL", "whisper-1"))
    openai_tts_model: str = field(default_factory=lambda: _env_str("OPENAI_TTS_MODEL", "tts-1"))
    openai_tts_voice: str = field(default_factory=lambda: _env_str("OPENAI_TTS_VOICE", "alloy"))

    # --- Local STT/TTS (used when STT_PROVIDER/TTS_PROVIDER=local) ---
    # Fully offline, no API key, no rate limits — faster-whisper (CTranslate2)
    # for STT, Piper for TTS. Requires `pip install faster-whisper piper-tts`
    # (commented out in requirements.txt by default) and, for TTS, a
    # downloaded Piper voice (`python3 -m piper.download_voices <name>`).
    # See cozmo_brain/llm/local_client.py.

    # Whisper model size: "tiny"/"base"/"small"/"medium"/"large-v3". Bigger =
    # more accurate but slower on the Pi's CPU-only hardware — "base" is a
    # starting guess to tune from real latency on real hardware, not a
    # measured choice (no Pi available here to benchmark on). First run
    # downloads the model from Hugging Face into a local cache — that
    # one-time step needs internet, every run after that is fully offline.
    local_stt_model: str = field(default_factory=lambda: _env_str("LOCAL_STT_MODEL", "base"))

    # "cpu" — the Pi has no GPU. "int8" quantization is CTranslate2's
    # fastest CPU compute type, at some accuracy cost vs "float32"/"int8_float16".
    local_stt_device: str = field(default_factory=lambda: _env_str("LOCAL_STT_DEVICE", "cpu"))
    local_stt_compute_type: str = field(default_factory=lambda: _env_str("LOCAL_STT_COMPUTE_TYPE", "int8"))

    # Path to a downloaded Piper voice's .onnx file (its sibling .onnx.json
    # must sit alongside it). "medium" quality voices are Piper's own
    # recommended tier for Raspberry Pi-class hardware; drop to a "low"
    # quality voice instead if it's not fast enough on the real Pi.
    local_tts_voice_path: str = field(
        default_factory=lambda: _env_str("LOCAL_TTS_VOICE_PATH", "en_US-lessac-medium.onnx")
    )

    # OpenAI's own speed control (0.25-4.0, native to their TTS API).
    # Measured directly: the default "alloy" voice speaks ~14% faster than
    # Groq's Orpheus "austin" for identical text — try ~0.85-0.90 here to
    # roughly match Groq's pacing if that difference is noticeable. 1.0 = OpenAI's own default.
    openai_tts_speed: float = field(default_factory=lambda: _env_float("OPENAI_TTS_SPEED", 1.0))

    # --- TTS output shape (applies regardless of provider) ---
    # TTS_SAMPLE_RATE: Cozmo's play_audio() only accepts 22050/48000Hz. Groq's
    # Orpheus can be asked for this rate directly; OpenAI's TTS cannot (no
    # such parameter exists) - tts_postprocess.py resamples to this rate from
    # whatever the provider's response actually declares, either way.
    tts_sample_rate: int = field(default_factory=lambda: _env_int("TTS_SAMPLE_RATE", 22050))

    # Max gain toward a target RMS (average loudness), not a peak target -
    # matching peaks alone left "peakier" voices (confirmed: OpenAI's
    # default) sounding quieter overall than Groq's despite an identical
    # peak. A tanh soft-limiter (not a hard peak ceiling) protects against
    # overflow, so this can safely go higher than the old peak-based
    # default could - verified directly: even at gain=4.0, zero samples
    # come within 90% of full scale. See tts_postprocess.py.
    tts_gain: float = field(default_factory=lambda: _env_float("TTS_GAIN", 4.0))

    # Silent lead-in prepended before the actual speech (milliseconds).
    # Reported symptom: Cozmo's speaker often misses the first word, the
    # rest plays fine — likely a hardware audio warm-up after a run of
    # silence, not a queueing bug (see tts_postprocess.py). Untested
    # against real hardware how much is actually needed; 0 disables it.
    tts_leadin_ms: float = field(default_factory=lambda: _env_float("TTS_LEADIN_MS", 200.0))

    # Voice character (Orpheus is a generic human voice, not Cozmo's real one).
    # These defaults were picked by ear against sample WAVs — the ring-mod
    # effect alone didn't actually read as "Cozmo," but this combo was the
    # best available approximation. See cozmo_brain/llm/groq_client.py.
    tts_pitch_shift: float = field(default_factory=lambda: _env_float("TTS_PITCH_SHIFT", 1.10))
    tts_robot_mod_hz: float = field(default_factory=lambda: _env_float("TTS_ROBOT_MOD_HZ", 35.0))
    tts_robot_mod_depth: float = field(default_factory=lambda: _env_float("TTS_ROBOT_MOD_DEPTH", 0.25))

    # --- Scratch audio file paths ---
    raw_input_wav: str = field(default_factory=lambda: _env_str("RAW_INPUT_WAV", "input.wav"))
    tts_output_wav: str = field(default_factory=lambda: _env_str("TTS_OUTPUT_WAV", "cozmo_reply.wav"))
    camera_snapshot_path: str = field(default_factory=lambda: _env_str("CAMERA_SNAPSHOT_PATH", "look.png"))

    # Directory storing reference photos for remember_person/who_is_this.
    # Experimental — see cozmo_brain/tools/registry.py for how matching works.
    known_people_dir: str = field(default_factory=lambda: _env_str("KNOWN_PEOPLE_DIR", "known_people"))

    # --- Robot backend ---
    # "real" talks to an actual Cozmo over PyCozmo. "simulated" logs actions to
    # the console instead — useful for developing/testing away from the robot.
    robot_backend: str = field(default_factory=lambda: _env_str("ROBOT_BACKEND", "real"))

    # Seconds without RobotState telemetry before is_healthy() treats the
    # connection as dropped and reconnect() gets tried (see robot/real.py).
    # Inferred from the protocol, not tuned against a real disconnect.
    robot_stale_after_s: float = field(default_factory=lambda: _env_float("ROBOT_STALE_AFTER_S", 5.0))

    # --- Battery monitor (real backend only) ---
    # Voltage thresholds sourced from real-world Cozmo community usage: 3.7V
    # is used as a "seek charger" threshold in a working autonomy script,
    # 3.5V is reported as the official SDK's own low-battery warning level.
    # Below CRITICAL, a face icon + warning light are shown periodically
    # until the robot is on the charger or disconnects.
    battery_low_voltage: float = field(default_factory=lambda: _env_float("BATTERY_LOW_VOLTAGE", 3.7))
    battery_critical_voltage: float = field(default_factory=lambda: _env_float("BATTERY_CRITICAL_VOLTAGE", 3.5))
    battery_check_interval_s: float = field(default_factory=lambda: _env_float("BATTERY_CHECK_INTERVAL_S", 30.0))

    # --- Wi-Fi auto-connect (optional, Linux/nmcli only — see robot/wifi.py) ---
    # Leave COZMO_WIFI_SSID empty to disable and keep connecting manually (nmcli
    # dev wifi connect ...) as before. Cozmo's SSID/password can regenerate on
    # power cycle, so this is best-effort, not guaranteed.
    cozmo_wifi_ssid: str = field(default_factory=lambda: _env_str("COZMO_WIFI_SSID", ""))
    cozmo_wifi_password: str = field(default_factory=lambda: os.environ.get("COZMO_WIFI_PASSWORD", ""))

    # --- Movement calibration ---
    turn_speed_mmps: float = field(default_factory=lambda: _env_float("TURN_SPEED_MMPS", 40.0))
    turn_seconds_per_degree: float = field(default_factory=lambda: _env_float("TURN_SECONDS_PER_DEGREE", 0.011))
    max_drive_speed_mmps: float = field(default_factory=lambda: _env_float("MAX_DRIVE_SPEED_MMPS", 150.0))
    max_drive_distance_mm: float = field(default_factory=lambda: _env_float("MAX_DRIVE_DISTANCE_MM", 1000.0))

    # How far/fast to drive straight when a turn is requested while still
    # resting (full) on the charger, before performing that turn - confirmed
    # on real hardware that turning in place on/near the dock risks
    # catching on it, and its platform/edge falsely trips CLIFF_DETECTED
    # anyway (see robot/real.py). Guessed distance/speed, not yet tuned
    # against the real dock's actual geometry.
    charger_exit_distance_mm: float = field(default_factory=lambda: _env_float("CHARGER_EXIT_DISTANCE_MM", 100.0))
    charger_exit_speed_mmps: float = field(default_factory=lambda: _env_float("CHARGER_EXIT_SPEED_MMPS", 60.0))

    # --- Agentic loop / conversation ---
    max_tool_iterations: int = field(default_factory=lambda: _env_int("MAX_TOOL_ITERATIONS", 4))
    conversation_max_messages: int = field(default_factory=lambda: _env_int("CONVERSATION_MAX_MESSAGES", 40))
    conversation_history_path: str = field(
        default_factory=lambda: _env_str("CONVERSATION_HISTORY_PATH", "conversation_history.json")
    )

    # --- Voice activity detection (--mode vad) ---
    vad_aggressiveness: int = field(default_factory=lambda: _env_int("VAD_AGGRESSIVENESS", 2))
    vad_silence_ms: int = field(default_factory=lambda: _env_int("VAD_SILENCE_MS", 800))
    vad_max_utterance_s: int = field(default_factory=lambda: _env_int("VAD_MAX_UTTERANCE_S", 15))

    # Onset debounce (not a minimum utterance length): how many consecutive
    # milliseconds of webrtcvad-positive audio are required before a capture
    # is committed to recording at all, vs. a single false-positive frame
    # from background noise flipping straight into "recording" and getting
    # sent to Whisper — which doesn't reliably return "empty" on a
    # noise-only clip, it hallucinates a plausible-sounding sentence instead
    # (a well-documented Whisper failure mode). Once this debounce is
    # satisfied there's no further minimum on total utterance length — real
    # words can be legitimately short (confirmed on real hardware: an
    # earlier version of this setting required a minimum *total* speech-frame
    # count across the whole capture instead of just at onset, and that
    # discarded genuinely short real replies as readily as actual noise).
    vad_min_speech_ms: int = field(default_factory=lambda: _env_int("VAD_MIN_SPEECH_MS", 60))

    # Loudness (RMS, 16-bit PCM scale 0-32767) floor a frame must clear,
    # IN ADDITION to webrtcvad classifying it as speech, to count as real
    # speech at all. webrtcvad only looks at spectral shape, not loudness,
    # so it can't tell quiet background noise that happens to look
    # speech-shaped from someone actually talking into the mic — this is an
    # independent second signal for exactly that gap. Confirmed on real
    # hardware to matter: the onset debounce above wasn't sufficient on its
    # own in a genuinely noisy room, reliably clearing a 2-frame debounce
    # and burning through free-tier STT quota on noise. UNVERIFIED default —
    # no real audio was available to calibrate this number; cozmo_brain/
    # audio/vad.py logs the actual peak RMS seen on both accepted and
    # rejected captures specifically so this can be tuned from real data
    # instead of guessed again.
    vad_min_rms: int = field(default_factory=lambda: _env_int("VAD_MIN_RMS", 150))

    # How long to keep listening for a follow-up utterance after Cozmo
    # replies, without needing the wake word again. Resets on every turn;
    # once nothing is heard within this window, the wake word is required
    # again. See modes/vad_mode.py.
    vad_followup_timeout_s: int = field(default_factory=lambda: _env_int("VAD_FOLLOWUP_TIMEOUT_S", 15))

    # --- Tap-to-talk (an alternate --mode vad activation trigger, alongside
    # the wake word — see modes/vad_mode.py) ---
    # A tap is detected as a brief spike in accelerometer magnitude above a
    # slow-moving rolling baseline (see robot/real.py). Threshold picked from
    # real-hardware logs: resting jitter stayed within ~50 of baseline, a
    # genuine tap spiked several hundred to 1000+, and a sustained multi-
    # sample event (picked up off the charger, carried, set back down) was
    # separately and more reliably rejected via the IS_PICKED_UP status flag
    # below, not this threshold — so this only needs to clear noise, not
    # distinguish a tap from a pickup.
    tap_threshold: float = field(default_factory=lambda: _env_float("TAP_THRESHOLD", 150.0))

    # Minimum time between two accepted taps - without this, a single tap's
    # multi-sample "ringing" (confirmed on real hardware: one tap can cross
    # the threshold on 2-3 consecutive RobotState packets) would register as
    # several taps.
    tap_debounce_ms: int = field(default_factory=lambda: _env_int("TAP_DEBOUNCE_MS", 450))

    # --- Wake word (--mode vad) ---
    # A stock openWakeWord model name (hey_jarvis, alexa, hey_mycroft,
    # hey_rhasspy, timer, weather) or a path to a custom-trained .onnx model.
    # See cozmo_brain/audio/wakeword.py — untested against real hardware.
    wake_word_model: str = field(default_factory=lambda: _env_str("WAKE_WORD_MODEL", "hey_jarvis"))
    wake_word_threshold: float = field(default_factory=lambda: _env_float("WAKE_WORD_THRESHOLD", 0.5))

    # --- Idle fidgeting ---
    # After this many seconds with no real conversation turn (see
    # engine.py's last_interaction_monotonic), a small idle-appropriate
    # gesture plays every interval this long while the quiet continues -
    # purely cosmetic personality, not sensor-driven, so no calibration
    # concern like the tap/cliff thresholds above. See idle_fidget.py.
    idle_fidget_enabled: bool = field(default_factory=lambda: _env_bool("IDLE_FIDGET_ENABLED", True))
    idle_fidget_after_s: int = field(default_factory=lambda: _env_int("IDLE_FIDGET_AFTER_S", 300))

    # --- Gesture/speech concurrency ---
    # Whether the `gesture` tool runs asynchronously (starts the
    # choreography on a background thread and returns immediately) instead
    # of blocking until it finishes. Confirmed thread-safe at the protocol
    # level (pycozmo.Connection.send() is just a thread-safe queue.put()),
    # but this is genuinely new end-to-end on real hardware, and it changes
    # what the gesture tool's result means to the model (started, not
    # necessarily finished) - set false to fall back to the old strictly
    # sequential behavior if it misbehaves.
    gesture_async_enabled: bool = field(default_factory=lambda: _env_bool("GESTURE_ASYNC_ENABLED", True))

    # --- Vision ---
    vision_enabled: bool = field(default_factory=lambda: _env_bool("VISION_ENABLED", True))

    def require_groq_key(self) -> str:
        if not self.groq_api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Copy .env.example to .env and fill it in."
            )
        return self.groq_api_key

    def require_openai_key(self) -> str:
        if not self.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set (needed because STT_PROVIDER or "
                "TTS_PROVIDER is 'openai'). Add it to .env."
            )
        return self.openai_api_key


settings = Settings()
