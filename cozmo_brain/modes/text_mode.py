"""Type what you'd say instead of speaking it.

No microphone, Bluetooth, or robot connection required for the LLM/tool
side of things — combine with --simulate to develop the whole tool-calling
loop and gesture library on a plain dev machine.
"""

from __future__ import annotations

from cozmo_brain.engine import CozmoEngine


def run(engine: CozmoEngine) -> None:
    print("Text mode. Type what you'd say to Cozmo, or 'quit' to exit.\n")
    while True:
        text = input("you> ").strip()
        if text.lower() == "quit":
            break
        if not text:
            continue
        summary = engine.handle_turn(text)
        print(summary, "\n")
