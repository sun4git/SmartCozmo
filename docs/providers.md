# Switching speech providers (and, separately, chat)

[← Back to the README](../README.md)


Groq's free-tier rate limits are real — this happened during actual use,
not just as a theoretical risk. `STT_PROVIDER` and `TTS_PROVIDER` in `.env`
each independently pick `groq` (default) or `openai` — they don't have to
match, e.g. Groq STT + OpenAI TTS is valid:

```env
STT_PROVIDER=groq
TTS_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

All three providers implement the same `SpeechClient` interface
(`transcribe()`/`synthesize()`), so nothing else in the app — `tools/registry.py`,
every mode — needs to know which one is actually active. `create_speech_client()`
in `cozmo_brain/llm/__init__.py` builds one client per provider actually needed
(reusing a single instance if `STT_PROVIDER` and `TTS_PROVIDER` match) and
returns a small router that sends `transcribe()` to the STT one and
`synthesize()` to the TTS one.

A third option, `local`, runs fully offline — no API key, no rate limits,
no network after a one-time model download: `faster-whisper` (CTranslate2)
for STT, `Piper` for TTS (`cozmo_brain/llm/local_client.py`). Both engines
load lazily on first actual use, so picking `local` for only one of
STT/TTS never loads the other engine. Trade-off is latency, not
correctness: CPU-only Whisper inference on a Pi is slower than Groq's
cloud Whisper on dedicated hardware — **untested here how much slower**,
since there's no Pi available to benchmark on; `LOCAL_STT_MODEL` and
`LOCAL_TTS_VOICE_PATH` are both easy to drop to a smaller/faster
model/voice via `.env` alone if it's not responsive enough. Also worth
noting: the currently maintained `piper-tts` package is GPL-3.0-or-later
(the original MIT-licensed `rhasspy/piper` repo was archived in favor of
a GPL fork) — see the note in `requirements.txt`.

Piper needs a voice downloaded once. Save it into `models/`, from the
project folder:

```bash
python3 -m piper.download_voices en_US-lessac-medium --data-dir models
```

That creates `models/en_US-lessac-medium.onnx` (the voice) and
`models/en_US-lessac-medium.onnx.json` (its settings, which Piper reads from
next to the `.onnx`). `.env` then names it without the folder or extension:
`LOCAL_TTS_VOICE_PATH=en_US-lessac-medium`. Without `--data-dir`, the
downloader saves into whatever folder you run it from. Voice files are
large and re-downloadable, so `.gitignore` keeps them out of git. See
[Model files](wake-word.md#model-files-models).

`LOCAL_STT_MODEL`'s first use downloads a model from Hugging Face — nothing
in `local_client.py` overrides where to, so it lands in `faster-whisper`'s
own default cache (confirmed against its source, `faster_whisper/utils.py`:
`"base"` resolves to the repo `Systran/faster-whisper-base`, fetched via
`huggingface_hub.snapshot_download()`):

| OS | Default cache path |
|---|---|
| Linux (the Pi) | `~/.cache/huggingface/hub/models--Systran--faster-whisper-<size>/` |
| Windows | `%USERPROFILE%\.cache\huggingface\hub\models--Systran--faster-whisper-<size>\` |

Override the location with the `HF_HOME` env var if it needs to go
somewhere else (e.g. a different disk on the Pi). To pre-warm the model
before a real test, so the first STT call isn't slowed by the download:
`python3 -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')"`.

A fourth option, `witai` (`STT_PROVIDER` only — see
`cozmo_brain/llm/witai_client.py`), uses Wit.ai (Meta): free, no billing,
but it needs its own account and `WITAI_ACCESS_TOKEN` (get one at
[wit.ai](https://wit.ai) — log in with a Facebook/Meta account, create an
app, then Settings → API Details → Server Access Token). Wit.ai has no
text-to-speech product at all, so it can only ever be `STT_PROVIDER`;
setting `TTS_PROVIDER=witai` raises `NotImplementedError` from
`synthesize()` instead of silently misbehaving.

**Verified directly against a real account/token** (a synthesized test
WAV, transcribed accurately): `Content-Type: audio/wav` for a plain WAV
file is correct, and the response really is multiple JSON objects
concatenated in one body — but NOT one-object-per-line NDJSON as first
guessed; each object is itself pretty-printed across several lines
(partial transcriptions building up word-by-word, ending in one marked
`"is_final": true`). `_parse_transcript()` decodes each top-level object
in sequence regardless of internal newlines, rather than naively
splitting on lines (which fails on every single line, since none are
valid JSON alone). **An anomaly seen twice during verification, worth
taking seriously rather than dismissing as noise:** 2 of ~9 test calls
(different audio each time, surrounding calls all correct) returned the
exact same unrelated transcript, verbatim — "Hey Facebook." — not two
different garbage strings, the identical phrase both times. That
specificity points away from random corruption and toward something
systematic, e.g. a low-confidence fallback baked into a fresh, completely
untrained Wit.ai app (possibly example/demo content from Wit.ai's own
default app template). **Confirmed recurring on real testing** — "hey
facebook" is now in `stt_postprocess.py`'s known-phrase filter, which
turned out fine to fold directly into the same list as Whisper's own
hallucination phrases: every call site there just wants "is this text
probably not real speech" regardless of which provider produced it, and
each entry's own comment records which provider/mechanism it's actually
from, so the list stays honest about provenance without needing a
parallel function. Latency,
measured from a dev machine (not the Pi — real Pi numbers will differ):
~1-2s per short utterance. Worth weighing before committing to it either
way: it's Meta's servers
processing the audio.

A fifth option, `edge` (`TTS_PROVIDER` only — see
`cozmo_brain/llm/edge_client.py`), uses Microsoft Edge's online TTS via the
unofficial `edge-tts` library: free, no API key, needs internet (an
unofficial, reverse-engineered endpoint, not a published/supported
Microsoft product — can break if Microsoft changes something internally).
Edge has no speech-to-text counterpart, so it can only ever be
`TTS_PROVIDER`; setting `STT_PROVIDER=edge` raises `NotImplementedError`
from `transcribe()` instead of silently misbehaving. `EDGE_TTS_VOICE`
picks a stock voice (list them all with `edge-tts --list-voices`);
`EDGE_TTS_SPEED` uses the same `>1.0`=faster direction as the other
providers, converted to edge-tts's own signed-percentage `rate` string.

**Real integration cost, not just a drop-in:** edge-tts's own service only
ever returns MP3 — confirmed against its source (`communicate.py`): the
output format is hardcoded to `audio-24khz-48kbitrate-mono-mp3`, not
configurable at all. `tts_postprocess.py`'s pitch/gain/ring-mod pipeline is
built on Python's stdlib `wave` module, which can't read MP3, so
`edge_client.py` decodes MP3 → raw PCM → a WAV container via `PyAV` (the
`av` package) before handing it off — same shape every other provider
already produces. PyAV ships FFmpeg bundled in its own wheel (no system
`ffmpeg` install needed), and is already a dependency of `faster-whisper`
(`STT_PROVIDER=local`), so it's often already installed for free. **Verified
directly, not assumed:** measured the decode step in isolation — ~8ms for
a ~4.6s clip (after one-time codec init), negligible next to the ~1.1s
network+synthesis round-trip it followed. The full pipeline (real network
call → MP3 → PyAV decode → WAV → `tts_postprocess.py`'s resample/pitch/
gain → file) was run end-to-end through the actual `create_speech_client()`
factory and produced a correct, correctly-resampled WAV — not just unit
logic. **Confirmed working on real hardware**, including noticeably higher
latency than the other providers on real testing (consistent with the
~1.1s network+synthesis round-trip measured from a dev machine above,
likely compounded by the Pi's own network path and slower MP3 decode) —
worth factoring in if responsiveness matters more than voice quality/cost.

`LOCAL_STT_MODEL` also accepts a full Hugging Face repo id directly (any
string containing a `/`), not just the short size names above — confirmed
against `download_model()`'s source: a `/` only changes how the repo id is
resolved (skips the short-name lookup table, treats the value as a literal
repo id instead), it doesn't change *where* the download goes. Same cache,
same `HF_HOME` override, just a different subfolder name for whichever
repo you pointed at. This makes **distil-whisper** models a drop-in,
zero-code-change option worth trying for exactly the `tiny`/`base`-speed
vs. `small`-accuracy trade-off: they're real CTranslate2 conversions
published under `Systran/faster-distil-whisper-{small,medium,large-v3}.en`
(English-only), claimed ~6x faster than the equivalent full-size Whisper
model at roughly 1% worse WER — i.e. built specifically to close that gap:

```env
LOCAL_STT_MODEL=Systran/faster-distil-whisper-small.en
```

Chat/tool-calling (the "brain" — see [Architecture](architecture.md#architecture)) has its
own independent `CHAT_PROVIDER` setting and a matching `create_chat_client()`
factory + `ChatClient` interface (`cozmo_brain/llm/chat_client.py`), mirroring
the STT/TTS setup above. `ollama` (default), `groq`, and `openai` are all
implemented — `GroqChatClient`/`OpenAIChatClient` (in `groq_client.py`/
`openai_client.py`) both wrap `OpenAICompatibleChatClient`
(`cozmo_brain/llm/openai_compatible_chat.py`), since Groq's and OpenAI's
chat-completions endpoints share an identical request/response shape —
only the base URL, key, and model name (`GROQ_CHAT_MODEL`/`OPENAI_CHAT_MODEL`)
differ.

This isn't a trivial swap, though: Ollama's own `/api/chat` message
conventions — which `Conversation`/`CozmoEngine` build internally, since
Ollama was the only backend until now — aren't wire-compatible with
OpenAI-compatible endpoints. Concretely, Ollama accepts a plain-dict
`arguments` object and doesn't require pairing a `tool` message to a
specific prior tool call; OpenAI/Groq **require** `function.arguments` as
a JSON-encoded *string* and every `tool` message to carry a `tool_call_id`
matching the originating `tool_calls[].id` — get either wrong and the API
rejects the request outright (400) on the very first tool-calling turn, not
a silent misbehavior. `openai_compatible_chat.py`'s `_to_wire_messages()`
translates at the boundary, and `ToolCall` now carries an `id` (generated
by `OllamaClient` too, if Ollama's own response didn't include one) so
`engine.py`/`conversation.py` can thread it through without needing to know
which provider is actually active.

**Vision is translated too:** `_to_wire_messages()` converts Ollama's own
`"images"` message field (a plain list of base64 strings, used by both
`look`'s vision-in-the-loop attachment and `who_is_this`'s direct
`ollama.chat(...)` face-comparison call) into OpenAI's content-array
`image_url` format, sniffing the real image format from its magic bytes
rather than trusting a file extension — `who_is_this` compares a
`look`-captured PNG against `remember_person`-saved `.jpg` reference
photos in the same call, so a single hardcoded mime type would be wrong
for one side of that comparison.

Each chat provider also gets its own vision-model override —
`OLLAMA_VISION_MODEL` / `GROQ_VISION_MODEL` / `OPENAI_VISION_MODEL` —
used instead of the regular chat model on any turn that actually carries
an image. This matters because a provider's default chat model isn't
necessarily vision-capable. `GROQ_CHAT_MODEL`'s old default
(`llama-3.3-70b-versatile`) wasn't, and has since been removed from Groq
entirely (`model_not_found`, confirmed 2026-09-28). Its default is now
`qwen/qwen3.8-27b`, which does both. `GROQ_VISION_MODEL` still has its own
explicit default (the same model), so that pointing `GROQ_CHAT_MODEL` at a
text-only model doesn't silently break `look`; `OPENAI_CHAT_MODEL`'s default (`gpt-4o-mini`) already handles
vision itself, so `OPENAI_VISION_MODEL` is left empty (falls back) by
default. Same idea for Ollama — pick a vision-capable `OLLAMA_MODEL`
directly, or set `OLLAMA_VISION_MODEL` instead if the chat model you want
for tool-calling isn't vision-capable.

Beyond just a different *model*, `VISION_PROVIDER` can also point at a
completely different *provider* than `CHAT_PROVIDER` — e.g.
`CHAT_PROVIDER=ollama` with `VISION_PROVIDER=groq` keeps regular chat/
tool-calling on Ollama but routes only image-bearing turns to Groq
instead, in any combination of the three. Left empty (default), it falls
back to `CHAT_PROVIDER` (same provider, just possibly a different model,
as above). `create_chat_client()` builds one client per provider actually
needed (same as `create_speech_client()` does for STT/TTS) and, only when
the two providers actually differ, wraps them in a small `_ChatRouter`
that dispatches `chat()` to whichever one the current turn needs — when
they're the same provider, the plain client is returned directly, so
nothing changes from before this existed.

Either way — same-provider model switch or cross-provider routing — the
decision of which to use checks the whole conversation history, not just
the latest turn, so once an image has been attached the vision model/
provider stays selected until that message ages out of
`CONVERSATION_MAX_MESSAGES` — harmless since a vision-capable model still
handles plain text/tool-calling turns fine, just not the cheapest choice
for those turns.

**Not yet verified against a real Groq/OpenAI account** — both the
tool-calling and vision message translation were unit-checked directly
(round-tripped sample exchanges through `_to_wire_messages()`, confirmed
image mime-sniffing against real PNG/JPEG magic bytes, and confirmed the
vision-model switch picks correctly), and client construction was checked
against the real `.env` keys, but no live turn has actually been run
against either endpoint yet.

Model and voice names are **not interchangeable between providers** —
Groq's Orpheus voices (`austin`, `troy`, ...) don't exist on OpenAI, and
vice versa (`alloy`, `nova`, ...); see `.env.example` for both lists.

One real difference this surfaced: **Groq's Orpheus endpoint accepts a
`sample_rate` parameter directly in the request; OpenAI's TTS endpoint has
no such parameter at all** (confirmed against OpenAI's own Python SDK
source, not guessed). Since Cozmo's `play_audio()` only accepts 22050 or
48000 Hz, `tts_postprocess.py` (shared by both providers, extracted from
what used to be Groq-only code) now reads whatever rate a provider's
response actually declares and resamples to `TTS_SAMPLE_RATE` — the same
`audioop.ratecv()` trick already used for the pitch-shift effect, just
used here for a plain format conversion. This makes the rate correct
regardless of provider, without needing to know OpenAI's exact native
output rate in advance.

**OpenAI's speech sounds noticeably faster than Groq's** — measured
directly, not assumed: same sentence, both providers, raw output before
any of our own processing. Groq's Orpheus ("austin") took 5.14s; OpenAI's
default voice ("alloy") took 4.41s for identical text — **OpenAI's voice
genuinely speaks about 14% faster on its own**, which is an inherent
difference between the two voices/engines, not a bug. There was also a
smaller, real one: resampling OpenAI's 24000Hz output down to 22050Hz
introduced an *extra* ~4-5% speedup beyond the intended `TTS_PITCH_SHIFT`
effect — likely `audioop.ratecv()`'s known imprecision as a simple
resampler, especially on a non-round rate ratio (24000:22050). Small
enough not to chase further for now. For the dominant (voice-pace) effect,
`OPENAI_TTS_SPEED` uses OpenAI's own native `speed` parameter to
compensate — confirmed it actually changes output duration (tested
`0.87` vs `1.0` directly) — rather than fighting a whole-engine pacing
difference via our own pitch-shift math.
