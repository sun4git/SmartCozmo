# Troubleshooting cheat sheet

[← Back to the README](../README.md)


```bash
# Stop the app cleanly from another shell (same cleanup as Ctrl+C; avoid kill -9)
pkill -f 'cozmo_brain --mode vad'

# Network health
ip -4 route show table all
nmcli device status
nmcli connection show --active

# Reconnect Bluetooth mic if it dropped
bluetoothctl connect <MAC_ADDRESS>
pactl set-card-profile bluez_card.<MAC_WITH_UNDERSCORES> headset-head-unit-msbc
pactl set-default-source bluez_input.<MAC_WITH_UNDERSCORES>.0

# Test mic only
arecord -D pipewire -f S16_LE -r 16000 -c 1 -d 5 test.wav && aplay -D pipewire test.wav

# Test Groq STT only
curl https://api.groq.com/openai/v1/audio/transcriptions \
  -H "Authorization: Bearer $GROQ_API_KEY" \
  -F file=@test.wav -F model=whisper-large-v3-turbo

# Test Groq TTS only
curl https://api.groq.com/openai/v1/audio/speech \
  -H "Authorization: Bearer $GROQ_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"canopylabs/orpheus-v1-english","voice":"austin","input":"test","response_format":"wav","sample_rate":22050}' \
  --output test_tts.wav

# Test Ollama reachability
curl "$OLLAMA_BASE_URL/api/tags"

# Test the assistant endpoint (ask_assistant) - same request shape Cozmo sends;
# repeat with the same "user" to check the session remembers
curl -X POST "$ASSISTANT_URL/v1/chat/completions"   -H "Content-Type: application/json" -H "Authorization: Bearer <ASSISTANT_TOKEN>"   -d '{"model": "openclaw/main", "user": "cozmo", "messages": [{"role": "user", "content": "Hi"}]}'

# Test PyCozmo connection only
python3 -c "import pycozmo; cli = pycozmo.Client(); cli.start(); cli.connect(); cli.wait_for_robot(); print('Connected!'); cli.disconnect(); cli.stop()"

# Exercise the full tool-calling loop with no robot, mic, or Ollama reachability needed for the robot side
python3 -m cozmo_brain --simulate --mode text
```
