# Recognizing people (experimental)

[← Back to the README](../README.md)


The original Cozmo app could meet someone, learn their name, and greet them
by name later — that ran on Anki's own on-device face-detection/enrollment
system. PyCozmo hasn't reverse-engineered that: it only knows the NVRAM
storage slot addresses exist (`NVEntry_FaceEnrollData`, `NVEntry_FaceAlbumData`
in `protocol_encoder.py`), not the actual protocol to drive it. So
`remember_person`/`who_is_this` don't use that — they're a much simpler,
much less reliable substitute built entirely on the vision LLM already in
the loop, no new dependency:

- `remember_person(name)` just saves a reference photo under
  `KNOWN_PEOPLE_DIR/<name>.jpg` (default `data/known_people/`) — no
  processing at all. That's a face only; facts about the person go to
  `data/memory.md` via `remember_fact` (see
  [Conversation history and memory](memory.md#conversation-history-and-memory)).
- `who_is_this()` captures a new photo, then for each saved reference photo
  sends *both* images to Ollama in one message (`images: [reference, new]`)
  and asks it to judge whether they're the same person.

This is a real, working feature, but manage expectations: general-purpose
vision LLMs aren't built for fine-grained face re-identification the way a
dedicated recognizer is, and Cozmo's camera is low-resolution, often blurry,
and has a strong warm/red color cast — a combination that makes false
matches (or false "I don't know you"s) genuinely likely, not just a
theoretical caveat. Treat any match as Cozmo's fun guess, not a fact, and
say so — the persona prompt already leans into this ("his eyesight isn't
great") rather than hiding it.

## Presence check: knowing who's at the desk (`PRESENCE_CHECK_ENABLED`)

Cozmo sits on the desk with the person in the chair in front of him, so he
can occasionally look up and work out who he's talking to, without being
asked. **Off by default** (see privacy below). When on, `presence.py` takes
the place of an idle fidget (`idle_fidget.py`), so it only ever happens after
`IDLE_FIDGET_AFTER_S` of quiet — never mid-conversation, never while picked
up — and holds `turn_lock` like a fidget does:

1. He tilts his head up (`PRESENCE_HEAD_ANGLE_DEG`, default 35°), takes one
   photo, lowers his head, and **deletes the photo** straight away.
2. The vision model is asked whether a real face is visible, then whether it
   matches each `remember_person` reference photo (shared helpers in
   `people.py`, also used by `who_is_this`).
3. What happens next is deliberately low-key:

| Result | What Cozmo does | Next look |
|---|---|---|
| Nobody in view | Nothing (clears who he last saw) | after `PRESENCE_EMPTY_RETRY_S` (300s) |
| A known person | Nothing out loud. The name goes into the system prompt as "a guess from a blurry photo", so he can use it naturally | after `PRESENCE_RECHECK_S` (900s) |
| A stranger | One casual spoken line ("Hi there! I don't think I know you yet. What's your name?"), at most once per `PRESENCE_ASK_COOLDOWN_S` (3600s). A note tells the model to call `remember_person` if they give a name, to drop it if they brush it off, and — if someone is already enrolled — to casually ask once where that person is | after `PRESENCE_ASK_COOLDOWN_S` |

`remember_person` and `who_is_this` feed the same "who's here" state, and
while a stranger is the last thing he saw, the prompt tells him not to assume
it's the primary user.

Caveats:

- **Privacy:** every look sends a photo of whoever is there to
  `VISION_PROVIDER` (the chat provider if blank). With a local Ollama that
  stays on your machine; with Groq/OpenAI it is a cloud upload of whoever is
  at the desk. Enable it only if everyone in the room is fine with that.
- **Reliability:** same as `who_is_this` — a vision model comparing blurry
  photos, so expect misses and the odd needless "who are you?".
- **Answering him:** in `--mode vad` the person may need to say the wake word
  before replying to his question (no listening window opens after it).
- **Head angle is a guess** — tune `PRESENCE_HEAD_ANGLE_DEG` once you've seen
  what he sees. Not yet verified on real hardware.
- It works even with `IDLE_FIDGET_ENABLED=false` (the look still runs; the
  gestures don't).
