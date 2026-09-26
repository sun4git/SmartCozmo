"""Entry point: wires config, robot backend, LLM/Groq clients, tools, and
conversation memory together, then hands off to the selected mode."""

from __future__ import annotations

import argparse
import logging
import sys

from cozmo_brain.config import settings
from cozmo_brain.conversation import Conversation
from cozmo_brain.engine import CozmoEngine
from cozmo_brain.idle_fidget import IdleFidgeter
from cozmo_brain.llm import create_speech_client
from cozmo_brain.llm.ollama_client import OllamaClient
from cozmo_brain.personality import SYSTEM_PROMPT
from cozmo_brain.robot import create_robot
from cozmo_brain.robot.battery_monitor import BatteryMonitor
from cozmo_brain.robot.pickup_reactor import PickupReactor
from cozmo_brain.tools import build_tools


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
        help="Ignore any saved conversation history and start clean.",
    )
    parser.add_argument("--log-level", default="INFO")
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

        battery_monitor = BatteryMonitor(robot, settings)
        battery_monitor.start()

        pickup_reactor = PickupReactor(robot)
        pickup_reactor.start()

        speech = create_speech_client(settings)
        ollama = OllamaClient(settings)
        tools = build_tools(robot, speech, ollama, settings)

        conversation = Conversation(
            SYSTEM_PROMPT,
            max_messages=settings.conversation_max_messages,
            history_path=settings.conversation_history_path,
        )
        if not args.fresh:
            conversation.load()

        engine = CozmoEngine(settings, robot, ollama, speech, tools, conversation)

        idle_fidgeter = IdleFidgeter(robot, engine, settings)
        idle_fidgeter.start()

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

    return 0


if __name__ == "__main__":
    sys.exit(main())
