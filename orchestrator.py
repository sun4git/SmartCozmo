#!/usr/bin/env python3
"""
Cozmo LLM orchestrator - v1

Flow per turn:
    Press Enter -> record N seconds from the Bose mic
    -> Groq Whisper STT -> text
    -> Ollama (gemma4:31b-cloud) with tool definitions
    -> execute whichever tool_calls come back via PyCozmo
    -> for say(): Groq TTS (Orpheus, 22050Hz direct) -> played through Cozmo's speaker
    -> loop until you type 'quit' instead of pressing Enter

This is intentionally simple: blocking, single-threaded, fixed-length
recording, no voice activity detection yet. Get this working first,
then add streaming / VAD / camera-in-the-loop as later passes.

Prereqs already confirmed working in this project:
    - Pi connected to Cozmo's Wi-Fi (wlan0), Ethernet as default route
    - Bluetooth mic (Bose) on PipeWire, headset-head-unit-msbc profile
    - GROQ_API_KEY env var set (STT + TTS)
    - Ollama reachable at OLLAMA_BASE_URL with tool-calling model
"""

import os
import json
import subprocess
import requests
import pycozmo
from dotenv import load_dotenv

# Load variables from the .env file into the system environment
load_dotenv() 

# ---- Config (all overridable via .env — see .env.example) ----
GROQ_API_KEY = os.environ["GROQ_API_KEY"]  # required, no default
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://192.168.1.200:41438")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:31b-cloud")

RECORD_SECONDS = int(os.environ.get("RECORD_SECONDS", "5"))
RECORD_DEVICE = os.environ.get("RECORD_DEVICE", "pipewire")
STT_MODEL = os.environ.get("GROQ_STT_MODEL", "whisper-large-v3-turbo")
TTS_MODEL = os.environ.get("GROQ_TTS_MODEL", "canopylabs/orpheus-v1-english")
TTS_VOICE = os.environ.get("GROQ_TTS_VOICE", "austin")  # troy, austin, daniel, autumn, diana, hannah

RAW_INPUT_WAV = os.environ.get("RAW_INPUT_WAV", "input.wav")
TTS_OUTPUT_WAV = os.environ.get("TTS_OUTPUT_WAV", "cozmo_reply.wav")
# -----------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "say",
            "description": "Speak a short sentence out loud through Cozmo's speaker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "What Cozmo should say."}
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drive",
            "description": "Drive forward or backward in a straight line.",
            "parameters": {
                "type": "object",
                "properties": {
                    "distance_mm": {
                        "type": "number",
                        "description": "Distance to drive in millimeters. Positive = forward, negative = backward.",
                    },
                    "speed_mmps": {
                        "type": "number",
                        "description": "Speed in mm/s. Keep this modest, e.g. 50-100.",
                    },
                },
                "required": ["distance_mm", "speed_mmps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "turn",
            "description": "Turn in place, left or right.",
            "parameters": {
                "type": "object",
                "properties": {
                    "angle_degrees": {
                        "type": "number",
                        "description": "Positive = turn left (counterclockwise), negative = turn right.",
                    }
                },
                "required": ["angle_degrees"],
            },
        },
    },
]

SYSTEM_PROMPT = (
    "You are Cozmo, a small expressive robot. You are curious, a little "
    "mischievous, and talk in short, upbeat sentences. When the user talks "
    "to you, decide whether to just speak (use the say tool), move "
    "(drive/turn), or both. Always use at least one tool per turn - if you "
    "just want to reply verbally, call say(). Keep spoken text short, one "
    "or two sentences."
)


def record_audio(path: str, seconds: int) -> None:
    print(f"Recording {seconds}s from {RECORD_DEVICE}... speak now.")
    subprocess.run(
        [
            "arecord",
            "-D", RECORD_DEVICE,
            "-f", "S16_LE",
            "-r", "16000",
            "-c", "1",
            "-d", str(seconds),
            path,
        ],
        check=True,
    )


def transcribe(path: str) -> str:
    with open(path, "rb") as f:
        r = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            files={"file": f},
            data={"model": STT_MODEL},
            timeout=30,
        )
    r.raise_for_status()
    return r.json()["text"].strip()


def ask_llm(user_text: str) -> list:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]
    r = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "messages": messages,
            "tools": TOOLS,
            "stream": False,
        },
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    return data.get("message", {}).get("tool_calls", []) or []


def speak(cli: "pycozmo.Client", text: str) -> None:
    print(f"Cozmo says: {text}")
    r = requests.post(
        "https://api.groq.com/openai/v1/audio/speech",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": TTS_MODEL,
            "voice": TTS_VOICE,
            "input": text,
            "response_format": "wav",
            "sample_rate": 22050,
        },
        timeout=30,
    )
    r.raise_for_status()
    with open(TTS_OUTPUT_WAV, "wb") as f:
        f.write(r.content)

    cli.set_volume(65535)
    cli.play_audio(TTS_OUTPUT_WAV)
    cli.wait_for(pycozmo.event.EvtAudioCompleted)


def drive(cli: "pycozmo.Client", distance_mm: float, speed_mmps: float) -> None:
    speed = abs(speed_mmps) if distance_mm >= 0 else -abs(speed_mmps)
    duration = abs(distance_mm) / abs(speed_mmps) if speed_mmps else 0
    print(f"Driving {distance_mm}mm at {speed_mmps}mm/s ({duration:.1f}s)")
    cli.drive_wheels(lwheel_speed=speed, rwheel_speed=speed, duration=duration)


def turn(cli: "pycozmo.Client", angle_degrees: float) -> None:
    # Rough approximation: differential wheel speeds for a fixed duration.
    # Tune TURN_SPEED / calibration once you see real behavior on the robot.
    turn_speed = 40.0  # mm/s on each wheel, opposite directions
    seconds_per_degree = 0.011  # placeholder - calibrate empirically
    duration = abs(angle_degrees) * seconds_per_degree
    direction = 1 if angle_degrees > 0 else -1
    print(f"Turning {angle_degrees} degrees (~{duration:.1f}s)")
    cli.drive_wheels(
        lwheel_speed=-turn_speed * direction,
        rwheel_speed=turn_speed * direction,
        duration=duration,
    )


def execute_tool_calls(cli: "pycozmo.Client", tool_calls: list) -> None:
    if not tool_calls:
        print("(model returned no tool calls)")
        return
    for tc in tool_calls:
        fn = tc.get("function", {})
        name = fn.get("name")
        args = fn.get("arguments", {})
        if isinstance(args, str):  # some backends stringify this
            args = json.loads(args)

        try:
            if name == "say":
                speak(cli, args["text"])
            elif name == "drive":
                drive(cli, args["distance_mm"], args["speed_mmps"])
            elif name == "turn":
                turn(cli, args["angle_degrees"])
            else:
                print(f"(unknown tool: {name})")
        except Exception as e:
            print(f"Error executing {name}: {e}")


def main():
    print("Connecting to Cozmo...")
    with pycozmo.connect() as cli:
        print("Connected. Press Enter to talk, or type 'quit' to exit.")
        while True:
            cmd = input("\n[Enter=talk, quit=exit] > ").strip().lower()
            if cmd == "quit":
                break

            record_audio(RAW_INPUT_WAV, RECORD_SECONDS)
            text = transcribe(RAW_INPUT_WAV)
            print(f"You said: {text}")

            if not text:
                print("(heard nothing, try again)")
                continue

            tool_calls = ask_llm(text)
            execute_tool_calls(cli, tool_calls)


if __name__ == "__main__":
    main()
