"""Central configuration, loaded from environment variables (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _project_path(value: str) -> str:
    """A relative path is taken from the project folder, not the current
    directory, so data lands in the same place however the app is started."""
    path = Path(value)
    return str(path if path.is_absolute() else PROJECT_ROOT / path)


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

    # Wit.ai (Meta) Server Access Token - free, no billing, STT only (no
    # TTS product exists). See cozmo_brain/llm/witai_client.py.
    witai_access_token: str = field(default_factory=lambda: os.environ.get("WITAI_ACCESS_TOKEN", ""))

    # Which provider does STT, TTS, and chat/tool-calling — each is
    # independently configurable, so e.g. STT_PROVIDER=groq + TTS_PROVIDER=openai
    # is valid. "groq" (default), "openai", "local" (fully offline, no API
    # key — see the Local STT/TTS section below), or "witai" (STT only, see
    # above) for STT/TTS; "ollama" (default), "groq", or "openai" for chat.
    # See cozmo_brain/llm/__init__.py's create_speech_client() / create_chat_client().
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
    # or gets deprecated. The Groq default was llama-3.3-70b-versatile until
    # it disappeared from Groq (model_not_found, confirmed 2026-09-28).
    # qwen/qwen3.8-27b replaced it: in a live test it batched `say` with
    # actions in one step, while openai/gpt-oss-120b (the other tool-capable
    # option) did one tool per step and said "sure!" alone before acting.
    groq_chat_model: str = field(default_factory=lambda: _env_str("GROQ_CHAT_MODEL", "qwen/qwen3.8-27b"))
    openai_chat_model: str = field(default_factory=lambda: _env_str("OPENAI_CHAT_MODEL", "gpt-4o-mini"))

    # Model used instead, for whichever CHAT_PROVIDER is active, on any turn
    # that has an image attached (the `look`/`who_is_this` tools) — only
    # matters when the regular chat model above isn't itself vision-capable.
    # Left empty, each falls back to its own chat model above.
    # GROQ_VISION_MODEL keeps its own explicit default (the same model as
    # GROQ_CHAT_MODEL's today, which is vision-capable) so that switching
    # GROQ_CHAT_MODEL to a text-only model doesn't silently break `look`;
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

    # Groq's own native speed control (confirmed in its API reference:
    # 0.5-5.0, default 1.0), same idea as OPENAI_TTS_SPEED below.
    groq_tts_speed: float = field(default_factory=lambda: _env_float("GROQ_TTS_SPEED", 1.0))

    # --- OpenAI STT/TTS (used when STT_PROVIDER/TTS_PROVIDER=openai) ---
    # Model/voice names are OpenAI's own, not interchangeable with Groq's.
    openai_stt_model: str = field(default_factory=lambda: _env_str("OPENAI_STT_MODEL", "whisper-1"))

    # --- Whisper hallucination defenses (Groq, OpenAI, local) ---
    # Language to transcribe in (ISO-639-1, e.g. "en"). Telling Whisper the
    # language stops it guessing one on noise - that guess is where the
    # foreign-language hallucinations (e.g. the Korean news sign-off seen
    # on real hardware) come from. Empty = let Whisper auto-detect.
    stt_language: str = field(default_factory=lambda: _env_str("STT_LANGUAGE", "en"))
    # Per-segment confidence filters for Whisper models, which return
    # no_speech_prob / avg_logprob / compression_ratio (see
    # stt_postprocess.filter_whisper_segments). Measured live 2026-09-28:
    # OpenAI whisper-1 gives noise no_speech_prob 0.93-0.97 vs 0.00 for
    # speech - a clean split. Groq whisper-large-v3-turbo always reports
    # 0.00, so for Groq only avg_logprob helps (speech ~-0.15, noise
    # -0.57..-0.97 on clean test audio - real mic speech will score lower,
    # so the default is Whisper's own conservative -1.0 until real logs
    # show where to tighten it). Every segment's values are logged at INFO.
    stt_no_speech_prob: float = field(default_factory=lambda: _env_float("STT_NO_SPEECH_PROB", 0.6))
    stt_min_avg_logprob: float = field(default_factory=lambda: _env_float("STT_MIN_AVG_LOGPROB", -1.0))
    stt_max_compression_ratio: float = field(
        default_factory=lambda: _env_float("STT_MAX_COMPRESSION_RATIO", 2.4)
    )
    # Neural speech check on every captured clip before it's sent to any STT
    # provider (audio/speech_gate.py - Silero VAD, the model bundled in
    # faster-whisper; skipped if faster-whisper isn't installed). A clip
    # with less than SPEECH_GATE_MIN_SPEECH_MS of detected speech isn't
    # sent. Measured with the real model (two runs): noise 0.00-0.06s, one-word replies
    # 0.42-0.67s - 150ms leaves wide margins both ways. SPEECH_GATE_THRESHOLD
    # is Silero's own per-frame speech probability cutoff (its default 0.5).
    speech_gate_enabled: bool = field(default_factory=lambda: _env_bool("SPEECH_GATE_ENABLED", True))
    speech_gate_min_speech_ms: int = field(default_factory=lambda: _env_int("SPEECH_GATE_MIN_SPEECH_MS", 150))
    speech_gate_threshold: float = field(default_factory=lambda: _env_float("SPEECH_GATE_THRESHOLD", 0.5))
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

    # A downloaded Piper voice: its name in models/ (en_US-lessac-medium ->
    # models/en_US-lessac-medium.onnx) or a path to its .onnx file. Its
    # sibling .onnx.json must sit alongside it. See model_files.py.
    # "medium" quality voices are Piper's own recommended tier for Raspberry
    # Pi-class hardware; drop to a "low" quality voice instead if it's not
    # fast enough on the real Pi.
    local_tts_voice_path: str = field(
        default_factory=lambda: _env_str("LOCAL_TTS_VOICE_PATH", "en_US-lessac-medium")
    )

    # Speed, same direction/semantics as GROQ_TTS_SPEED/OPENAI_TTS_SPEED
    # (>1.0 = faster, 1.0 = default). Piper's own SynthesisConfig takes the
    # inverse as "length_scale" (>1.0 = slower) - local_client.py converts.
    local_tts_speed: float = field(default_factory=lambda: _env_float("LOCAL_TTS_SPEED", 1.0))

    # --- Edge TTS (used when TTS_PROVIDER=edge) ---
    # Microsoft Edge's online TTS via the unofficial `edge-tts` library -
    # free, no API key, needs internet. TTS only, no STT counterpart. See
    # cozmo_brain/llm/edge_client.py.
    edge_tts_voice: str = field(default_factory=lambda: _env_str("EDGE_TTS_VOICE", "en-US-EmmaMultilingualNeural"))

    # Same >1.0=faster direction as GROQ_TTS_SPEED/OPENAI_TTS_SPEED/
    # LOCAL_TTS_SPEED - converted to edge-tts's own signed-percentage
    # "rate" string (e.g. "+20%") in edge_client.py.
    edge_tts_speed: float = field(default_factory=lambda: _env_float("EDGE_TTS_SPEED", 1.0))

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
    camera_snapshot_path: str = field(
        default_factory=lambda: _project_path(_env_str("CAMERA_SNAPSHOT_PATH", "data/look.png"))
    )

    # Directory storing reference photos for remember_person/who_is_this.
    # Experimental — see cozmo_brain/tools/registry.py for how matching works.
    known_people_dir: str = field(
        default_factory=lambda: _project_path(_env_str("KNOWN_PEOPLE_DIR", "data/known_people"))
    )

    # --- Robot backend ---
    # "real" talks to an actual Cozmo over PyCozmo. "simulated" logs actions to
    # the console instead — useful for developing/testing away from the robot.
    robot_backend: str = field(default_factory=lambda: _env_str("ROBOT_BACKEND", "real"))

    # Seconds without RobotState telemetry before is_healthy() treats the
    # connection as dropped and reconnect() gets tried (see robot/real.py).
    # Inferred from the protocol, not tuned against a real disconnect.
    robot_stale_after_s: float = field(default_factory=lambda: _env_float("ROBOT_STALE_AFTER_S", 5.0))
    # How often connection_monitor.py checks the connection in the
    # background (and reconnects if it's stale), so a drop is caught while
    # idle too, not only before the next tool call. 0 = off.
    connection_check_interval_s: float = field(
        default_factory=lambda: _env_float("CONNECTION_CHECK_INTERVAL_S", 120.0)
    )

    # --- Battery monitor (real backend only) ---
    # Voltage thresholds sourced from real-world Cozmo community usage: 3.7V
    # is used as a "seek charger" threshold in a working autonomy script,
    # 3.5V is reported as the official SDK's own low-battery warning level.
    # Below CRITICAL, a face icon + warning light are shown periodically
    # until the robot is on the charger or disconnects.
    battery_low_voltage: float = field(default_factory=lambda: _env_float("BATTERY_LOW_VOLTAGE", 3.7))
    battery_critical_voltage: float = field(default_factory=lambda: _env_float("BATTERY_CRITICAL_VOLTAGE", 3.5))
    battery_check_interval_s: float = field(default_factory=lambda: _env_float("BATTERY_CHECK_INTERVAL_S", 30.0))

    # Low-battery return-to-charger (cozmo_brain/charger_return.py). At
    # BATTERY_LOW_VOLTAGE Cozmo *offers* out loud to head back; at
    # BATTERY_CRITICAL_VOLTAGE he goes on his own - both only once the
    # reading has held for 2 consecutive checks, so motor-load voltage sag
    # during a drive can't trigger it. If no valid charger location is
    # known (never left the charger this session, picked up, or reconnected
    # since), he asks to be put on it instead. false disables all of that;
    # the `dock` tool's navigate-first behavior on an explicit request stays
    # either way.
    auto_return_to_charger_enabled: bool = field(
        default_factory=lambda: _env_bool("AUTO_RETURN_TO_CHARGER_ENABLED", True)
    )
    # Upper bound on one go_to_pose() navigation before it's aborted -
    # pycozmo's own go_to_pose() has no timeout and would otherwise block
    # forever if its completion event never arrives. A generous guess for
    # desk-scale distances (go_to_pose() drives at <=100mm/s with slow
    # 20mm/s^2 ramps), not tuned against real hardware.
    return_to_pose_timeout_s: float = field(default_factory=lambda: _env_float("RETURN_TO_POSE_TIMEOUT_S", 60.0))

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
    # anyway (see robot/real.py). 150mm confirmed on real hardware as
    # actually enough to clear the dock - 100mm (the original guess) was
    # observed not reliably clearing it.
    charger_exit_distance_mm: float = field(default_factory=lambda: _env_float("CHARGER_EXIT_DISTANCE_MM", 150.0))
    charger_exit_speed_mmps: float = field(default_factory=lambda: _env_float("CHARGER_EXIT_SPEED_MMPS", 60.0))

    # How far/fast to reverse for dock() (RobotBackend.dock(), see
    # robot/base.py) - the same false-CLIFF_DETECTED platform/edge geometry
    # as above, just approached from the opposite direction. Same magnitude
    # as the confirmed exit distance, since it's the same physical
    # platform/edge - not itself independently confirmed on real hardware yet.
    charger_dock_distance_mm: float = field(default_factory=lambda: _env_float("CHARGER_DOCK_DISTANCE_MM", 150.0))
    charger_dock_speed_mmps: float = field(default_factory=lambda: _env_float("CHARGER_DOCK_SPEED_MMPS", 60.0))
    # How much further than CHARGER_DOCK_DISTANCE_MM dock() may keep
    # reversing - it stops the moment the charger contacts engage, so this
    # is only an upper bound. Confirmed on real hardware that an exact
    # CHARGER_DOCK_DISTANCE_MM reverse fell short (open-loop timing +
    # acceleration ramp); an extra ~40mm seated it. 50 is a first guess
    # just above that, not tuned.
    charger_dock_overshoot_mm: float = field(default_factory=lambda: _env_float("CHARGER_DOCK_OVERSHOOT_MM", 50.0))
    # How long dock() waits after reversing for IS_ON_CHARGER before calling
    # it a miss - confirmed on real hardware that the flag can come on well
    # after Cozmo is physically seated (not set at 0.3s, set within ~40s).
    # Returns as soon as it appears; 10 is a guess, tune from the logged delay.
    charger_contact_wait_s: float = field(default_factory=lambda: _env_float("CHARGER_CONTACT_WAIT_S", 10.0))
    # dock()'s reverse steers to hold the charger's heading: mm/s of tread
    # speed difference per degree of heading error (0 = off, the old blind
    # reverse). Confirmed on real hardware that a tread caught the charger
    # entrance and twisted him -17deg; 2.0 is a first guess, not tuned.
    charger_dock_heading_gain: float = field(default_factory=lambda: _env_float("CHARGER_DOCK_HEADING_GAIN", 2.0))
    # Twisted further than this off the charger's heading during the dock
    # reverse (despite steering) = treated as caught on the charger: stop.
    charger_dock_jam_deg: float = field(default_factory=lambda: _env_float("CHARGER_DOCK_JAM_DEG", 10.0))
    # After a missed dock, return_to_charger() drives back out to the staging
    # point and tries again this many times.
    charger_dock_retries: int = field(default_factory=lambda: _env_int("CHARGER_DOCK_RETRIES", 1))

    # --- Agentic loop / conversation ---
    max_tool_iterations: int = field(default_factory=lambda: _env_int("MAX_TOOL_ITERATIONS", 4))
    # What to do with the LLM call that follows a reply ending in a clean
    # `say` (the model usually answers "done" - see engine.py's
    # _turn_is_done()):
    #   async - (default, chosen by the user 2026-09-29) reopen the mic
    #           right away and make the call in the background; if the model
    #           continues (more actions/speech), the recording is paused
    #           while that runs (--mode vad only; other modes treat it as
    #           sync)
    #   sync  - wait for it before listening again: the conservative
    #           fallback if async ever misbehaves on hardware - costs one
    #           round trip of mic-closed time per reply
    #   skip  - don't make it at all. Fastest, but confirmed live: a model
    #           that says "sure!" alone and acts in its *next* step
    #           (openai/gpt-oss-120b on Groq) loses the action entirely.
    final_llm_call: str = field(default_factory=lambda: _env_str("FINAL_LLM_CALL", "async").lower())
    conversation_max_messages: int = field(default_factory=lambda: _env_int("CONVERSATION_MAX_MESSAGES", 40))
    # The old single-file history. Only read once, to import it into the
    # archive under data/history/ (history_archive.py), then moved into
    # data/. Kept as a setting so an existing .env pointing elsewhere still
    # gets imported.
    conversation_history_path: str = field(
        default_factory=lambda: _env_str("CONVERSATION_HISTORY_PATH", "conversation_history.json")
    )

    # --- Saved data: conversation archive and memory ---
    # Everything Cozmo saves at runtime: history/<date>/<run>.jsonl (every
    # conversation, with its photos), memory.md (facts about people),
    # known_people/ and look.png. Relative to the project folder.
    data_dir: str = field(default_factory=lambda: _project_path(_env_str("DATA_DIR", "data")))
    # Delete archived days older than this many days at startup. 0 = keep
    # everything forever.
    history_retention_days: int = field(default_factory=lambda: _env_int("HISTORY_RETENTION_DAYS", 0))
    # Most facts remember_fact may save (memory.md is sent with every turn,
    # so this caps its cost). Hand-added facts beyond it are still sent.
    memory_max_facts: int = field(default_factory=lambda: _env_int("MEMORY_MAX_FACTS", 40))
    # After each conversation, one LLM call looks for things worth
    # remembering that Cozmo didn't save; they wait in memory.md's
    # "Suggested" section until he asks and you say yes. See
    # memory_suggestions.py.
    memory_suggestions_enabled: bool = field(
        default_factory=lambda: _env_bool("MEMORY_SUGGESTIONS_ENABLED", True)
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
    # distinguish a tap from a pickup. A third self-caused source is
    # rejected separately too: Cozmo's own lift motor starting/stopping
    # (e.g. the "shrug" idle-fidget gesture, confirmed on real hardware to
    # register as a false tap) — set_lift_height_mm()/lower_lift_fully()
    # suppress tap detection for their own duration, not via this threshold
    # either, since we always know exactly when that's happening.
    # Default raised 150 -> 600 (2026-09-30): at 150, an idle Cozmo
    # nobody touched logged false taps with jumps of 159-187 and woke
    # himself up; 500 stopped them on the Pi while a real tap still
    # measured 1567. 600 leaves a bit more margin over the false spikes.
    tap_threshold: float = field(default_factory=lambda: _env_float("TAP_THRESHOLD", 600.0))

    # Minimum time between two accepted taps - without this, a single tap's
    # multi-sample "ringing" (confirmed on real hardware: one tap can cross
    # the threshold on 2-3 consecutive RobotState packets) would register as
    # several taps.
    tap_debounce_ms: int = field(default_factory=lambda: _env_int("TAP_DEBOUNCE_MS", 450))

    # --- Wake word (--mode vad) ---
    # A custom model's name in models/ (hey_cozmo -> models/hey_cozmo.onnx),
    # a stock openWakeWord model name (hey_jarvis, alexa, hey_mycroft,
    # hey_rhasspy, timer, weather) or a path to an .onnx file.
    # See cozmo_brain/audio/wakeword.py and model_files.py.
    wake_word_model: str = field(default_factory=lambda: _env_str("WAKE_WORD_MODEL", "hey_cozmo"))
    wake_word_threshold: float = field(default_factory=lambda: _env_float("WAKE_WORD_THRESHOLD", 0.5))

    # --- Idle fidgeting ---
    # After this many seconds with no real conversation turn (see
    # engine.py's last_interaction_monotonic), a small idle-appropriate
    # gesture plays every interval this long while the quiet continues -
    # purely cosmetic personality, not sensor-driven, so no calibration
    # concern like the tap/cliff thresholds above. See idle_fidget.py.
    #
    # Raised directly on real hardware: the original 300s (5 min) default
    # never actually got a chance to fire - Cozmo disconnects/powers off
    # (its own hardware-level inactivity behavior, not anything in this
    # codebase) well before 5 minutes of quiet elapses, so idle fidgeting
    # was effectively dead in practice. 30s is a deliberately short
    # replacement - roughly VAD_FOLLOWUP_TIMEOUT_S's 15s wake-word window
    # plus another 15s - specifically so it gets a real chance to run
    # before whatever cuts the session short does.
    idle_fidget_enabled: bool = field(default_factory=lambda: _env_bool("IDLE_FIDGET_ENABLED", True))
    idle_fidget_after_s: int = field(default_factory=lambda: _env_int("IDLE_FIDGET_AFTER_S", 30))

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

    # `say`'s bundled gesture (see tools/registry.py's handle_say()) starts
    # before speech is synthesized by default (False) - Cozmo reacts the
    # instant `say` is called, but the overlap with speech isn't guaranteed
    # if synthesis (TTS_PROVIDER/network/CPU-dependent) takes longer than
    # the gesture itself, which can happen with a slower TTS backend (e.g.
    # local). Set true to synthesize first and only start the gesture once
    # speech is actually ready to play - overlap is then guaranteed
    # regardless of synthesis speed, at the cost of Cozmo appearing to
    # pause (mood/face still update immediately either way, just no
    # movement) for however long synthesis takes. Only matters when
    # GESTURE_ASYNC_ENABLED is also true - gesture is fully sequential
    # either way when it's false.
    gesture_speech_sync_enabled: bool = field(
        default_factory=lambda: _env_bool("GESTURE_SPEECH_SYNC_ENABLED", False)
    )

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

    def require_witai_key(self) -> str:
        if not self.witai_access_token:
            raise RuntimeError(
                "WITAI_ACCESS_TOKEN is not set (needed because STT_PROVIDER is "
                "'witai'). Get one free at wit.ai (Settings -> API Details -> "
                "Server Access Token) and add it to .env."
            )
        return self.witai_access_token


settings = Settings()
