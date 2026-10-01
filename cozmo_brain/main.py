"""Entry point: wires config, robot backend, LLM/Groq clients, tools, and
conversation memory together, then hands off to the selected mode."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from cozmo_brain.charger_return import ChargerReturner
from cozmo_brain.config import PROJECT_ROOT, settings
from cozmo_brain.conversation import Conversation
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.history_archive import HistoryArchive, prune_history
from cozmo_brain.connection_monitor import ConnectionMonitor
from cozmo_brain.idle_fidget import IdleFidgeter
from cozmo_brain.llm import create_chat_client, create_speech_client
from cozmo_brain.memory import Memory
from cozmo_brain.personality import SYSTEM_PROMPT, build_system_prompt
from cozmo_brain.robot import create_robot
from cozmo_brain.robot.battery_monitor import BatteryMonitor
from cozmo_brain.robot.pickup_reactor import PickupReactor
from cozmo_brain.tools import build_tools


_LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]


def _default_log_level() -> str:
    # argparse doesn't check a default against `choices`, so an invalid
    # LOG_LEVEL in .env would otherwise crash logging.basicConfig().
    level = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    if level not in _LOG_LEVELS:
        print(f"Ignoring invalid LOG_LEVEL={level!r} in .env (use one of {', '.join(_LOG_LEVELS)}) - using INFO.",
              file=sys.stderr)
        return "INFO"
    return level


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SmartCozmo: an LLM-driven Cozmo assistant.")
    parser.add_argument(
        "--mode",
        choices=["voice", "vad", "text", "calibrate"],
        default="voice",
        help="voice = push-to-talk mic, vad = hands-free continuous listening "
        "(wake word or a tap on Cozmo's body), text = type instead of speak, "
        "calibrate = measure turn() accuracy.",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Force the simulated (console-logging) robot backend, regardless of ROBOT_BACKEND in .env.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Don't resume from earlier conversations - start with an empty context. "
        "This run is still archived, and the memory file is still used.",
    )
    parser.add_argument(
        "--log-level",
        # Case-insensitive (logging itself only accepts uppercase names, so
        # "--log-level debug" used to crash at startup). Default comes from
        # LOG_LEVEL in .env, else INFO.
        type=str.upper,
        choices=_LOG_LEVELS,
        default=_default_log_level(),
        help="How much to log. INFO (default): the useful diagnostics - LLM steps and tool results, "
        "STT results and segment scores, speech-gate and capture lines, docking/charger events. "
        "WARNING: only problems (quiet). ERROR: only failures. DEBUG: everything, incl. PyCozmo's "
        "own chatter. Default can be set with LOG_LEVEL in .env.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.log_level.upper() != "DEBUG":
        # PyCozmo logs periodic byte/packet counters on this logger every
        # few seconds (Connection.log_stats()) - distracting noise in normal
        # use, especially in --mode text. --log-level DEBUG opts back in.
        logging.getLogger("pycozmo.protocol").setLevel(logging.WARNING)
        # Robot-firmware debug messages (decoded in pycozmo/robot_debug.py),
        # most commonly "AnimationController.IsReadyToPlay.BufferStarved" -
        # PyCozmo's own reference CLI (run.py) defaults this same logger to
        # WARNING specifically because that message is routine chatter (the
        # animation engine reporting "nothing queued to play right now",
        # true whenever Cozmo isn't actively mid-playback) that fires too
        # often to be useful at INFO. Matching that default here - we
        # weren't applying it before, which is why it was visible at all.
        logging.getLogger("pycozmo.robot").setLevel(logging.WARNING)

    robot = create_robot(settings, force_simulated=args.simulate)

    with robot:
        if args.mode == "calibrate":
            from cozmo_brain.modes import calibrate_mode

            calibrate_mode.run(robot, settings)
            return 0

        pickup_reactor = PickupReactor(robot)
        pickup_reactor.start()

        speech = create_speech_client(settings)
        ollama = create_chat_client(settings)

        data_dir = Path(settings.data_dir)
        history_dir = data_dir / "history"
        archive = HistoryArchive(history_dir, run_info={
            "mode": args.mode, "simulated": args.simulate, "chat_provider": settings.chat_provider,
            "stt_provider": settings.stt_provider, "tts_provider": settings.tts_provider,
        })
        legacy = Path(settings.conversation_history_path)
        archive.import_legacy(legacy if legacy.is_absolute() else PROJECT_ROOT / legacy, data_dir)
        prune_history(history_dir, settings.history_retention_days)
        memory = Memory(data_dir / "memory.md", history_dir, max_facts=settings.memory_max_facts)
        memory.ensure_file()
        tools = build_tools(robot, speech, ollama, settings, memory=memory)

        conversation = Conversation(
            SYSTEM_PROMPT,
            max_messages=settings.conversation_max_messages,
            archive=archive,
        )
        if not args.fresh:
            conversation.load()

        engine = CozmoEngine(
            settings, robot, ollama, speech, tools, conversation,
            system_prompt_fn=lambda: build_system_prompt(memory.prompt_section()),
        )

        # Started after the engine exists (not alongside pickup_reactor
        # above) because the low-battery return-to-charger policy speaks and
        # writes to the conversation through it.
        charger_returner = ChargerReturner(robot, engine, settings)
        battery_monitor = BatteryMonitor(robot, settings, on_reading=charger_returner.on_battery_reading)
        battery_monitor.start()

        idle_fidgeter = IdleFidgeter(robot, engine, settings)
        idle_fidgeter.start()

        connection_monitor = ConnectionMonitor(robot, engine, settings)
        connection_monitor.start()

        try:
            if args.mode == "text":
                from cozmo_brain.modes import text_mode

                text_mode.run(engine)
            elif args.mode == "vad":
                from cozmo_brain.modes import vad_mode

                vad_mode.run(engine, robot, speech, settings)
            else:
                from cozmo_brain.modes import interactive

                interactive.run(engine, speech, settings)
        finally:
            battery_monitor.stop()
            pickup_reactor.stop()
            idle_fidgeter.stop()
            connection_monitor.stop()
            archive.record_event("run_end")

    return 0


if __name__ == "__main__":
    sys.exit(main())
