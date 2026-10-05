# Keeping speech-to-text from hallucinating

[← Back to the README](../README.md)


Whisper doesn't return empty text for noise. It invents something from its
training data instead ("Thank you.", "you", even a Korean news sign-off).
The defenses are layered, cheapest first:

1. **Nothing is recorded until activation:** the wake word or a tap opens a
   listening window.
2. **Local gate, on the Pi, before anything is sent:** `webrtcvad` must call
   it speech-shaped **and** it must clear `VAD_MIN_RMS`, for
   `VAD_MIN_SPEECH_MS` in a row (`audio/vad.py`). Cozmo's own motors can't
   trigger it: taps are ignored while he moves, there's no fidgeting while
   listening, and a turn waits for gestures to finish.
3. **Neural speech check before sending** (`audio/speech_gate.py`): Silero
   VAD, the model bundled in faster-whisper (already on the Pi; no extra
   download). If it finds under `SPEECH_GATE_MIN_SPEECH_MS` (150ms) of real
   speech in the clip, the clip **isn't sent to any provider**. There's no
   STT call and nothing to hallucinate on. Measured with the real model:
   hiss and clatter 0.00s, motor hum 0.03–0.06s, versus one-word replies
   ("yes", "no", "okay", including a quiet "yes") 0.42–0.67s and a sentence
   ~3.5s. The check takes ~10–20ms. The model is loaded in the background
   when listening starts, so the first clip doesn't pay the ~1s load. If
   faster-whisper isn't installed, the check is skipped and clips are sent
   as before. Each clip logs `Speech gate (Silero): 0.48s of speech
   detected ... - sending to STT` (or `not sent`).
4. **Only 200ms of the end-of-speech silence is sent.** The 800ms used to
   *detect* that you stopped is trimmed, so Whisper gets no long silent tail
   to invent an ending over.
5. **Whisper is told the language** (`STT_LANGUAGE=en`), so it doesn't
   guess one on noise.
6. **Whisper's own confidence is used** (`verbose_json`, per segment). A
   segment is dropped if its no-speech estimate is above
   `STT_NO_SPEECH_PROB`, its decoding confidence is below
   `STT_MIN_AVG_LOGPROB`, or it's a repetitive loop
   (`STT_MAX_COMPRESSION_RATIO`). Local faster-whisper also gets its
   built-in Silero voice filter (`vad_filter=True`).
7. **After the fact:** text with no letters or digits (Groq returns a bare
   `"."` for clatter) and exact known phrases (`llm/stt_postprocess.py`) are
   ignored.

**Measured live (2026-09-28)**, same clips through each provider's client
(`tests/live/test_stt_hallucination.py`):

| Clip | Groq `whisper-large-v3-turbo` | OpenAI `whisper-1` | Wit.ai |
|---|---|---|---|
| Spoken sentence | correct (no_speech 0.00, logprob -0.12) | correct (0.00, -0.32) | correct |
| Room hiss | "Thank you." (0.00, -0.57) → phrase filter | "you" (**0.95**) → dropped | empty |
| Clatter | "." (0.00, -0.97) → no-letters rule | "You" (**0.93**) → dropped | empty |
| Motor hum | "." (0.00, -0.59) → no-letters rule | "you" (**0.97**) → dropped | empty |

**Groq's no-speech estimate is always 0.00**, so on Groq layer 6 only has
the logprob floor, and nothing new gets caught at the default -1.0. An
unfamiliar hallucinated phrase can still get through there. Every
transcription now logs each segment:

```
STT segment kept (no_speech=0.00 logprob=-0.21 compression=0.97): 'turn left please'
STT segment DROPPED (no_speech=0.95 logprob=-0.93 compression=0.27): 'you' - no_speech_prob 0.95 > 0.6
```

Once your real speech's `logprob` is known from those lines, tighten
`STT_MIN_AVG_LOGPROB` to just below it (e.g. -0.5). On the test clips that
drops Groq's "Thank you." (-0.57) and keeps speech (-0.12). Test-voice
speech is cleaner than a real mic, so don't set it tighter than the logs
support. Most noise never gets that far now: layer 3 stops it before it's
sent. Live checks: `tests/live/test_speech_gate_silero.py` (the real model,
on this machine or the Pi) and `tests/live/test_stt_hallucination.py`.
