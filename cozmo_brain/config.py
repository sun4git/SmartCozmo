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

    # Which provider actually does STT+TTS. "groq" (default) or "openai" —
    # e.g. switch to "openai" if Groq's free-tier rate limits get hit.
    # See cozmo_brain/llm/__init__.py's create_speech_client().
    audio_provider: str = field(default_factory=lambda: _env_str("AUDIO_PROVIDER", "groq"))

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

    # --- Groq STT/TTS (used when AUDIO_PROVIDER=groq) ---
    stt_model: str = field(default_factory=lambda: _env_str("STT_MODEL", "whisper-large-v3-turbo"))
    tts_model: str = field(default_factory=lambda: _env_str("TTS_MODEL", "canopylabs/orpheus-v1-english"))
    tts_voice: str = field(default_factory=lambda: _env_str("TTS_VOICE", "austin"))

    # --- OpenAI STT/TTS (used when AUDIO_PROVIDER=openai) ---
    # Model/voice names are OpenAI's own, not interchangeable with Groq's.
    openai_stt_model: str = field(default_factory=lambda: _env_str("OPENAI_STT_MODEL", "whisper-1"))
    openai_tts_model: str = field(default_factory=lambda: _env_str("OPENAI_TTS_MODEL", "tts-1"))
    openai_tts_voice: str = field(default_factory=lambda: _env_str("OPENAI_TTS_VOICE", "alloy"))

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

    # Minimum total voiced (webrtcvad-positive) audio required before a
    # capture is sent to Whisper at all. A single false-positive frame from
    # background noise used to be enough to trigger a whole recording —
    # Whisper doesn't reliably return "empty" on a noise-only clip, it
    # hallucinates a plausible-sounding sentence instead (a well-documented
    # Whisper failure mode). Discarding tiny voiced-frame counts as noise,
    # the same as "no speech heard", stops those clips before they're
    # transcribed at all.
    vad_min_speech_ms: int = field(default_factory=lambda: _env_int("VAD_MIN_SPEECH_MS", 300))

    # How long to keep listening for a follow-up utterance after Cozmo
    # replies, without needing the wake word again. Resets on every turn;
    # once nothing is heard within this window, the wake word is required
    # again. See modes/vad_mode.py.
    vad_followup_timeout_s: int = field(default_factory=lambda: _env_int("VAD_FOLLOWUP_TIMEOUT_S", 15))

    # --- Wake word (--mode vad) ---
    # A stock openWakeWord model name (hey_jarvis, alexa, hey_mycroft,
    # hey_rhasspy, timer, weather) or a path to a custom-trained .onnx model.
    # See cozmo_brain/audio/wakeword.py — untested against real hardware.
    wake_word_model: str = field(default_factory=lambda: _env_str("WAKE_WORD_MODEL", "hey_jarvis"))
    wake_word_threshold: float = field(default_factory=lambda: _env_float("WAKE_WORD_THRESHOLD", 0.5))

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
                "OPENAI_API_KEY is not set (needed because AUDIO_PROVIDER=openai). "
                "Add it to .env."
            )
        return self.openai_api_key


settings = Settings()
