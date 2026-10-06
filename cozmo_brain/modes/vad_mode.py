"""Hands-free voice mode: Cozmo listens continuously and reacts whenever you
say the wake word OR gently tap Cozmo's body, using on-device wake-word
detection (openWakeWord) gating voice activity detection instead of a
push-to-talk key. Requires `pip install webrtcvad-wheels` and openwakeword (see
cozmo_brain/audio/wakeword.py for the install command — it's not a plain
`pip install openwakeword`). The tap trigger needs no extra dependency —
it's RobotBackend.wait_for_tap(), backed by a real accelerometer spike on
the real backend (see robot/real.py).

After either trigger fires, the conversation stays open for follow-up turns
(VAD_FOLLOWUP_TIMEOUT_S, resets each turn) without repeating it — once
nothing is heard within that window, a wake word or tap is required again.

Also gives a physical "I heard you" cue on the robot itself (curious face +
light + head up on wake, back to neutral when the window closes) — this
mode is meant to be hands-free, so a terminal print alone isn't a real
indicator of anything."""

from __future__ import annotations

import logging
import threading
import time

import requests

from cozmo_brain.audio.speech_gate import clip_has_speech
from cozmo_brain.audio.speech_gate import warm_up as speech_gate_warm_up
from cozmo_brain.audio.vad import record_until_silence
from cozmo_brain.audio.wakeword import wait_for_wake_word
from cozmo_brain.config import Settings
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.llm.speech_client import SpeechClient
from cozmo_brain.llm.stt_postprocess import is_likely_hallucination
from cozmo_brain.robot.base import RobotBackend

logger = logging.getLogger(__name__)

# How long to wait before restarting the wake-word listener after it stops
# without hearing anything (see _wait_for_wake_word_or_tap). Long enough not
# to spin on a mic that's off, short enough to pick it up soon once it's on.
_MIC_RETRY_S = 5.0


def _set_listening_light_safely(robot: RobotBackend, listening: bool) -> None:
    try:
        robot.set_listening_indicator(listening)
    except Exception as e:  # noqa: BLE001 - a light hiccup shouldn't end the session
        logger.warning("Could not %s the listening light: %s", "start" if listening else "stop", e)


def _go_idle(robot: RobotBackend, apply_neutral: bool = True) -> None:
    """Idle = waiting for the wake word: neutral face, backpack light off.
    Off is reserved for this - every mood has a color (robot/moods.py) -
    so a dark backpack means "idle, not listening"."""
    if apply_neutral:
        _apply_mood_safely(robot, "neutral")
    try:
        robot.set_backpack_light("off")
    except Exception as e:  # noqa: BLE001 - a light hiccup shouldn't end the session
        logger.warning("Could not turn the backpack light off: %s", e)


def _apply_mood_safely(robot: RobotBackend, mood: str) -> None:
    try:
        robot.apply_mood(mood)
    except Exception as e:  # noqa: BLE001 - a listening-indicator hiccup shouldn't kill this loop
        logger.warning("Could not show '%s' mood: %s", mood, e)


def _wait_for_wake_word_or_tap(
    robot: RobotBackend, settings: Settings, listen_request: threading.Event | None = None
) -> str:
    """Blocks until either the wake word is heard or Cozmo is tapped,
    whichever comes first, and returns which one ("wake word"/"tap") - or
    "spoke up" when `listen_request` (CozmoEngine.request_listen()) is set:
    Cozmo just said something on his own that wants an answer.
    Races the two on separate threads (wake-word listening owns a mic
    subprocess + model inference, so it's given a cancel signal rather than
    just abandoned once a tap wins).

    If the wake-word listener stops without hearing anything (mic stream
    ended - e.g. the Bluetooth mic is off - or the listener crashed), that
    is not a wake: taps keep working, and the listener is restarted after
    _MIC_RETRY_S. Raised directly: with the mic off, this used to count
    as a wake and loop, looking like fake wakes."""
    while True:
        stop_wake_word = threading.Event()
        wake_word_done = threading.Event()
        heard: list[bool] = []

        def _listen_for_wake_word() -> None:
            try:
                heard.append(wait_for_wake_word(
                    settings.record_device, settings.wake_word_model, settings.wake_word_threshold, stop_wake_word
                ))
            except Exception:  # noqa: BLE001 - logged; taps keep working and it's retried below
                logger.exception("Wake-word listener crashed.")
            finally:
                wake_word_done.set()

        thread = threading.Thread(target=_listen_for_wake_word, daemon=True)
        thread.start()

        while not wake_word_done.is_set():
            if robot.wait_for_tap(timeout=0.2):
                stop_wake_word.set()
                thread.join(timeout=2)
                return "tap"
            if listen_request is not None and listen_request.is_set():
                stop_wake_word.set()  # frees the mic for the recording
                thread.join(timeout=2)
                return "spoke up"

        thread.join()
        if heard and heard[0]:
            return "wake word"

        # Debug only: wakeword.py already warns once per mic outage, and
        # this repeats every _MIC_RETRY_S while the mic stays off.
        logger.debug("Wake-word listener stopped without a wake word - retrying in %.0fs (taps still work).",
                     _MIC_RETRY_S)
        deadline = time.monotonic() + _MIC_RETRY_S
        while time.monotonic() < deadline:
            if robot.wait_for_tap(timeout=0.2):
                return "tap"
            if listen_request is not None and listen_request.is_set():
                return "spoke up"


def run(engine: CozmoEngine, robot: RobotBackend, speech: SpeechClient, settings: Settings) -> None:
    print(
        f"Listening for the wake word ('{settings.wake_word_model}') or a tap. "
        "Press Ctrl+C to exit.\n"
    )
    engine.log_steps_live = True
    speech_gate_warm_up(settings)
    _go_idle(robot, apply_neutral=False)
    while True:
        trigger = _wait_for_wake_word_or_tap(robot, settings, engine.listen_request)
        # Whatever opened it, this window covers any pending request.
        engine.listen_request.clear()
        if trigger == "spoke up":
            logger.info("Spoke up on my own - listening %ds for a reply (no wake word needed).",
                        settings.speak_up_listen_s)
            print(f"Listening {settings.speak_up_listen_s}s for a reply (no wake word needed)...")
        else:
            logger.info("Woke up: %s.", trigger)
            print(f"Woke up ({trigger}) - listening...")
        engine.set_listening_window(True)
        # Each wake-to-idle stretch is one session in this run's transcript.
        engine.session_event("session_start", trigger=trigger)
        try:
            _run_listening_window(
                engine, robot, speech, settings,
                first_timeout_s=settings.speak_up_listen_s if trigger == "spoke up" else None,
            )
        finally:
            engine.set_listening_window(False)
            engine.session_event("session_end")
        # Anything queued for "between recordings" just as the window closed
        # (a Sunny answer, the battery offer) runs now, before the wake word
        # listens again - otherwise it would wait for the next window.
        engine.deliver_announcements()


def _run_listening_window(
    engine: CozmoEngine, robot: RobotBackend, speech: SpeechClient, settings: Settings,
    first_timeout_s: int | None = None,
) -> None:
    """One activation's worth of conversation: listen, reply, keep listening
    for follow-ups, until nothing is heard within the window.
    `first_timeout_s` replaces the first wait for speech (SPEAK_UP_LISTEN_S
    after Cozmo spoke up on his own; after that, follow-ups as usual)."""
    _apply_mood_safely(robot, "curious")

    # How long to wait for speech to *start*: VAD_MAX_UTTERANCE_S right
    # after the wake word, then VAD_FOLLOWUP_TIMEOUT_S for every follow-up,
    # so the conversation can continue without repeating the wake word until
    # the human actually goes quiet. Separately, once speech starts, one
    # utterance may run up to VAD_MAX_UTTERANCE_S from that point
    # (max_utterance_s below) - it used to share this same budget, so
    # speech starting late in the window got cut off at the window's end
    # (confirmed on real hardware: 0.4s captured, "hit the 15s cap").
    listen_timeout = settings.vad_max_utterance_s if first_timeout_s is None else first_timeout_s

    while True:
        # FINAL_LLM_CALL=async: the previous reply's follow-up LLM call may
        # still be running in the background - that's fine, we listen
        # meanwhile. If it turned out Cozmo continues his reply (more actions
        # or speech), it stops this recording via followup_interrupt; wait
        # for it to finish, then simply listen again with the same window.
        engine.settle_followup()
        # A background answer (ask_assistant, ASSISTANT_BACKGROUND) that
        # arrived during the last turn is spoken now, before listening.
        engine.deliver_announcements()
        engine.mic_active = True
        # Blinking backpack = recording right now; steady/off = not.
        _set_listening_light_safely(robot, True)
        try:
            got_speech = record_until_silence(
                settings.raw_input_wav,
                settings.record_device,
                settings.vad_aggressiveness,
                settings.vad_silence_ms,
                listen_timeout,
                settings.vad_min_speech_ms,
                settings.vad_min_rms,
                max_utterance_s=settings.vad_max_utterance_s,
                stop_event=engine.followup_interrupt,
                idle_stop_event=engine.announce_event,
            )
        finally:
            engine.mic_active = False
            _set_listening_light_safely(robot, False)
        if not got_speech and engine.settle_followup():
            print("(Cozmo continued his reply - listening again)")
            continue
        # Stopped for (or ended with) an answer waiting: say it, and keep the
        # window open so they can reply without the wake word.
        if not got_speech and engine.deliver_announcements():
            print("(Cozmo passed on an answer - listening again)")
            continue
        if not got_speech:
            print("(no follow-up heard - wake word needed again)\n")
            _go_idle(robot)
            return

        # Neural speech check before spending an STT call on the clip - loud,
        # speech-shaped noise can pass the loudness/webrtcvad gate above, and
        # Whisper would invent text for it (audio/speech_gate.py).
        if not clip_has_speech(settings.raw_input_wav, settings):
            print("(heard a sound, but no speech in it - not sent to speech-to-text, listening again)")
            continue

        stt_started = time.monotonic()
        try:
            text = speech.transcribe(settings.raw_input_wav)
        except requests.RequestException as e:
            # An STT request failure used to crash the whole hands-free
            # session (confirmed on real hardware: an OpenAI 400 here
            # took the process down mid-conversation, with nothing above
            # this catching it) - treated the same as "heard something,
            # nothing transcribable" instead, since the mic is otherwise
            # unattended and a transient/edge-case API error shouldn't
            # end the session. e.response.text carries the provider's
            # actual error detail (raise_for_status() alone discards
            # it), logged here since a bare "400 Bad Request" gives no
            # way to root-cause a repeat.
            detail = e.response.text.strip() if e.response is not None else str(e)
            logger.warning("Speech-to-text request failed: %s", detail)
            print(f"(speech-to-text request failed, try again: {detail})\n")
            continue
        # Logged every time, empty results included - the mic is closed
        # for this whole round trip, and an empty result used to loop
        # straight back to recording with nothing logged at all
        # (confirmed on real hardware: 13 silent captures in a row).
        logger.info(
            "STT (%s) took %.2fs -> %r", settings.stt_provider, time.monotonic() - stt_started, text
        )
        if not text:
            print("(heard something, but speech-to-text returned nothing - listening again)")
            continue  # heard something, but nothing transcribable - keep the conversation open
        print(f"You said: {text}")
        if is_likely_hallucination(text):
            print("(that's a known Whisper artifact from background noise, not real speech - ignoring)\n")
            continue

        # Each step and tool result is logged live, in order, by the engine
        # (log_steps_live, set in run()), so the end-of-turn summary isn't
        # printed again here - it would repeat those lines out of order.
        engine.handle_turn(text, allow_async_followup=True)
        print()

        listen_timeout = settings.vad_followup_timeout_s
