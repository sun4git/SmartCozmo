# Real bugs this uncovered

[← Back to the README](../README.md)


While wiring up `drive()`/`turn()`, we found that this version of PyCozmo's
`Client.drive_wheels(..., duration=...)` accepts a `duration` keyword but
**never actually uses it** — the robot keeps driving until told otherwise.
`orchestrator.py`'s original `drive()`/`turn()` pass `duration` expecting it
to auto-stop the robot, which does not happen. `cozmo_brain/robot/real.py`
works around this properly: it sleeps for the computed duration itself, then
calls `cli.stop_all_motors()` explicitly. Worth keeping in mind if you write
any other timed movement — always stop the robot explicitly.

**Found on real hardware** (this one only showed up once actually running
against a physical Cozmo, not in any offline testing): `say`/`gesture`/every
mood failed with `Invalid image dimensions. Only 128x32 images are
supported. 128x64 given.` PyCozmo's own `procedural_face.DEFAULT_WIDTH` /
`DEFAULT_HEIGHT` are `128x64`, but `image_encoder.ImageEncoder` — what
actually talks to Cozmo's real screen — hard-requires exactly `128x32`
(Cozmo's real face display is a short, wide strip, not square). PyCozmo's
own default doesn't match its own hardware constraint. Fixed in
`show_expression()` by constructing the expression class with
`width=128, height=32` explicitly instead of the library's default — the
face geometry scales by width/height, so this renders correctly proportioned
for the real screen rather than a squashed crop. Verified against all 25
expression classes.

**Also only found on real hardware:** photos from `look()`/`remember_person()`/
`who_is_this()` came back visibly torn/glitchy (interleaved garbage across
part of the frame) — bad enough that face comparison couldn't work at all.
`capture_photo()` was registering the capture handler and grabbing
whichever frame arrived first, immediately after `enable_camera()`.
PyCozmo's own `camera.py` example does the same `enable_camera()` call but
then explicitly **sleeps 2 seconds "to let the image stabilize"** before
ever registering a handler — the first frame(s) off a just-started stream
come back mid-transition, torn. Added the same 2-second wait. This adds a
real, noticeable delay to every photo now, which is the deliberate
trade-off for a usable image.

**Likely the same class of issue, this time on audio output:** the first
word of `say()`'s speech was often inaudible while the rest of the
sentence played fine. Traced through `anim_controller.py`'s actual
playback loop (a background thread polling a queue at a fixed frame rate,
sending either real audio or silence) — nothing there structurally drops
the first packets, which points at Cozmo's own audio hardware needing a
brief moment to actually start outputting sound after a run of silence,
not a software queueing bug. `TTS_LEADIN_MS` (default 200ms) prepends
silence before the real speech so the warm-up eats that instead of the
first word — verified precisely that the prepended block is genuinely
silent and the real audio starts exactly at the configured mark, and that
a real STT round-trip still transcribes the first word correctly with the
lead-in present. **Not verified: whether 200ms is actually the right
amount on real hardware** — there's no Cozmo here to confirm the audible
result, only that the mechanism itself works exactly as intended.

**A latency bug, not a hardware quirk:** `cozmo_brain` felt noticeably
slower per response than `orchestrator.py`, even though both do the same
STT → LLM → TTS round trip. Traced to `show_expression()`'s default
`duration=2.0`, passed straight through to PyCozmo's own
`cli.display_image(im, duration=2.0)` — which does `time.sleep(duration)`
whenever `duration is not None`. Since `apply_mood()` (now called on every
`say()`, including the default "neutral" mood — see above) never
overrode that default, **every single response was blocking for 2 full
seconds just to show the face**, on top of ~0.4s each for any head/lift
movement the mood also sets — before the TTS audio even played.
`orchestrator.py` has no mood/face system at all, so none of this existed
there. Fixed by defaulting `show_expression()`'s duration to `None`
instead: the face is set and persists until the next mood change, rather
than being held for a fixed 2s and auto-cleared — which was never actually
the intended behavior for routine mood-setting, only for a deliberately
timed gesture beat (no gesture currently uses this path directly).

**Found in `--mode vad` use:** Cozmo would occasionally "hear" and act on
entire sentences that were never said — reported symptoms included fully
formed sentences in other languages (e.g. Korean) out of what should have
been silence. Root cause: `record_until_silence()` (`cozmo_brain/audio/vad.py`)
started a full recording the moment **a single** 30ms frame was classified
as speech by `webrtcvad` — cheap Bluetooth mics + background noise trip this
easily. That mostly-silent clip still got sent to Whisper, and Whisper
doesn't reliably return empty text on a noise-only clip — it hallucinates a
plausible-sounding sentence from its training data instead (a well-known,
widely-reported Whisper failure mode; confirmed neither Groq's nor OpenAI's
hosted Whisper endpoint expose a documented way to suppress this — Groq's
`verbose_json` does expose `no_speech_prob`/`avg_logprob` for post-hoc
filtering, but OpenAI's `whisper-1` API doesn't reliably surface those
fields despite them existing in the underlying model). Fixed at the capture
layer instead, so it works regardless of provider — but it took two tries:

The **first attempt** required `VAD_MIN_SPEECH_MS` (300ms) of total
*accumulated* voiced audio — summed across the whole capture — before
accepting it. **Found on real hardware to be the wrong metric**: a real,
short reply logged only 90ms of accumulated speech frames out of a 900ms
capture and got silently discarded exactly like the noise case it was meant
to catch, breaking real hands-free conversation (a pause before speaking
made this much more likely to hit, since `webrtcvad`'s per-frame
classification is genuinely conservative/noisy on a real Bluetooth mic, not
just on noise).

The **corrected version** treats `VAD_MIN_SPEECH_MS` as an onset debounce
instead: it requires that many *consecutive* milliseconds of speech before
committing to a recording at all (default now 60ms, i.e. 2 frames) — still
enough to reject a single isolated 30ms false-positive frame, but with no
further minimum once that debounce is satisfied, since real words can be
legitimately brief. Verified with a scripted fake-mic test covering all four
shapes: a single noise frame (rejected), the exact 90ms real-utterance shape
that was wrongly rejected before (now accepted), a normal longer utterance
(accepted), and total silence (rejected, unchanged).

**Confirmed on real hardware that the VAD-side fix alone isn't sufficient**
in a genuinely noisy environment: the corrected onset debounce fixed the
false-rejection of real short replies, but background noise in a real room
can still reliably clear even a 60ms/2-frame bar, and the resulting
noise-only clips kept hallucinating — with the *same exact* phrases
recurring verbatim across many separate capture windows in a quiet room
with nothing actually said or playing (`"Thank you for watching"` and a
real MBC news anchor's sign-off, both among the most widely-reported
Whisper hallucination phrases). That repetition is itself informative:
these hosted Whisper endpoints decode close to greedily, so a similar
noise/silence profile tends to reproduce the same memorized caption each
time rather than a random one.

Added a second, independent layer as a backstop:
`cozmo_brain/llm/stt_postprocess.py`'s `is_likely_hallucination()` checks
transcribed text against a known-phrase list (both modes call it right
after `speech.transcribe()`, treating a match the same as "heard nothing").
Matching is exact after normalizing case/punctuation/whitespace, not fuzzy
or substring-based — deliberately, so it can never misfire on real speech
that happens to share words with a listed phrase. Verified against the
exact strings reported on real hardware (including the trailing-punctuation
variants actually seen) plus real sentences from the same session,
confirming zero false positives on the latter. **This list is necessarily
incomplete** — it's a pragmatic backstop for known repeat offenders, not a
general hallucination detector; new recurring phrases will need adding as
they show up. **Important limitation, raised directly on real hardware:**
this filter runs *after* the (rate-limited) STT API call already happened —
it hides the wrong reply, but doesn't save any quota. Confirmed on real
hardware that in a genuinely noisy room, noise cleared the onset debounce
often enough to burn through a free-tier STT quota on nothing but ambient
noise.

The actual quota-saving fix has to happen *before* the STT call, at the
capture gate itself: `webrtcvad` only looks at spectral shape, not
loudness, so it can't distinguish quiet background noise that happens to
look speech-shaped from someone actually talking into the mic. `VAD_MIN_RMS`
adds an independent loudness floor a frame must *also* clear (16-bit PCM
RMS scale, 0–32767) before counting as speech — quiet ambient noise
essentially never reaches typical near-mic speech volume. **Unverified
default (150)** — there was no real audio available here to calibrate it;
`cozmo_brain/audio/vad.py` now logs the actual peak RMS seen on both
accepted and rejected captures specifically so this can be tuned from real
data instead of guessed again (compare a rejected-noise log line's peak RMS
against an accepted-speech one to pick a number that sits between them).
Verified the gating logic itself with a scripted test using synthetic PCM
frames at controlled amplitudes (quiet-but-webrtcvad-flagged noise →
rejected; loud real speech → accepted; loud audio webrtcvad doesn't
classify as speech → still rejected). If noise still gets through after
tuning `VAD_MIN_RMS`, also worth trying `VAD_AGGRESSIVENESS=3` (stricter
webrtcvad noise rejection) — untested against this specific environment.

**Found on real hardware, crashed the whole process:** an STT request to
OpenAI (`STT_PROVIDER=openai`) returned `400 Bad Request` mid-`--mode vad`
session, and nothing caught it — `resp.raise_for_status()` in
`llm/openai_client.py`/`llm/groq_client.py` raises on any non-2xx response,
but neither `vad_mode.py` nor `interactive.py` wrapped their
`speech.transcribe()` call in anything, so the exception propagated all the
way up and killed the process (contrast `engine.py`, which already catches
`requests.RequestException` around its Ollama calls the same way). Bad for
push-to-talk, worse for hands-free — nobody's watching an unattended
session to restart it. Both call sites now catch `requests.RequestException`
and treat it the same as "heard something, nothing transcribable" (logs a
warning, keeps the follow-up window/loop open, moves on) instead of
crashing. Also surfaced a second issue while fixing the first:
`raise_for_status()` discards the response body, so the original crash's
traceback showed only a generic `400 Client Error: Bad Request` with no clue
why — the catch now logs `e.response.text`, which for OpenAI/Groq's STT
endpoints carries the actual `error.message` explaining the rejection.
**Root cause of that specific 400 not yet identified** — the fix makes it
survivable and diagnosable next time, not preventable; if it recurs, the
logged response body is the next thing to look at.

**Found on real hardware:** `Tool 'say' failed: Unknown mood 'offended'.`
— the model called `say(mood="offended")`, a word that was never a real
mood (`_MOOD_NAMES`, from `robot/moods.py`, has no such entry). The `say`
tool's JSON schema already restricts `mood` to a proper `enum` of the real
names (`tools/registry.py`), but that alone clearly isn't enough — the
model doesn't reliably respect it. Root cause: `personality.py`'s own
persona description said Cozmo is "easily delighted or offended by the
smallest things" — the model read that, decided "offended" sounded like a
plausible mood, and called it verbatim. Same issue, one line down:
"comically devastated by small setbacks" ("devastated" isn't a mood
either). Fixed both wording spots (swapped for real mood words —
`excited`/`annoyed`, `sad`), and added a second line of defense: the
prompt now also spells out the exact closed vocabulary of valid moods and
gestures at the end, built from `MOODS`/`GESTURES` directly (not
hardcoded, so it can't drift out of sync if either dict changes) rather
than relying on the schema `enum` alone. **Not yet confirmed whether this
fully prevents a recurrence** — it removes the specific prompt wording
that caused this one and adds a stronger hint, but nothing stops the model
from inventing a different plausible-sounding word next time; if it
happens again, that's the next thing to strengthen.

**Found on real hardware, froze the whole process, had to be restarted:**
`aplay` (system-speaker output, `audio/player.py`'s `play_wav()`) hung
indefinitely mid-playback — the log showed it start, then nothing for
several minutes except the unrelated battery monitor still ticking on its
own thread, proving the main thread itself was stuck, not the process as
a whole. Root cause: `subprocess.run(["aplay", ...], check=True)` had no
`timeout` at all, unlike `robot.say_wav()`'s own `cli.wait_for(...,
timeout=30)` for Cozmo's speaker — the system-audio path had no equivalent
escape hatch. A stuck Bluetooth speaker/PipeWire sink (not confirmed which)
could block that call forever, and with `AUDIO_OUTPUT=both`,
`system_thread.join()` (also no timeout) would then block the main thread
right along with it. Fixed by adding `timeout=30` (matching `say_wav()`'s
value, not independently tuned) to the `subprocess.run()` call, catching
`subprocess.TimeoutExpired` (`subprocess.run()` already kills the hung
process by the time this fires) and `CalledProcessError`, and logging a
warning instead of letting either propagate — a stuck playback device
now can't freeze a turn, or the whole hands-free session, over one bad
output. **Root cause of *why* aplay hung not identified** — same caveat
as the STT-crash fix above: this makes it survivable and diagnosable
(the warning log), not preventable.

**Follow-up, ruling out a red herring:** repeated
`AnimationController.IsReadyToPlay.BufferStarved` messages (from
`pycozmo.robot`, decoded firmware debug messages) showed up in the log
around the same incident, raising the question of whether *that* was the
actual cause instead. Traced it: PyCozmo's own reference CLI (`run.py`)
defaults that exact logger to `WARNING` specifically because this message
is routine chatter — the animation engine reporting "nothing queued to
play right now," true any time Cozmo isn't actively mid-playback, logged
too often to be useful at INFO. `main.py` was never applying that same
suppression (only `pycozmo.protocol`'s was), which is why it was visible
at all. Now applied the same way, so this stops being clutter — and,
more importantly, stops being a plausible-looking but wrong lead the next
time something actually hangs.
