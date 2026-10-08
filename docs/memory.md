# Conversation history and memory

[← Back to the README](../README.md)


Three separate things, all under `data/` (gitignored, `DATA_DIR`):

| | What | Lifetime |
|---|---|---|
| **Context** | What the model sees each turn: the system prompt, then the last `CONVERSATION_MAX_MESSAGES` messages (`conversation.py`) | Trimmed as it goes |
| **Archive** | `data/history/<date>/<time>.jsonl`: one file per run (start to exit), every message with a timestamp, photos saved next to it (`history_archive.py`) | Kept until deleted (`HISTORY_RETENTION_DAYS`) |
| **Memory** | `data/memory.md`: a few facts about people, sent to the model every turn (`memory.py`) | Until removed, by Cozmo or by hand |

**Archive.** Written line by line as things happen, so a crash loses at
most one line; a run that records nothing leaves no file. In `--mode vad`,
each wake word or tap starts a session inside the run's file
(`session_start` with the trigger, then `session_end` when the follow-up
window closes). Photos the model looked at (and `look` photos taken with
`VISION_ENABLED=false`) are saved as `<time>-photo-01.png` etc. next to the
transcript, not inside it. On startup, the context resumes from the last
messages of earlier runs - always behind the *current* system prompt
(the old single-file history also restored its old system prompt, so
prompt changes didn't take effect until `--fresh`). Resumed photos aren't
re-sent; a `[photo: ...]` note stays in their place.

Read it with:

```bash
python3 standalone/show_history.py              # the latest run
python3 standalone/show_history.py 2026-10-01   # every run that day
python3 standalone/show_history.py --list
```

An existing `conversation_history.json` (the old format) is imported once
as its own run, dated by the file, and moved to
`data/conversation_history.imported.json`.

**Moving an existing install** (after pulling): the history import is
automatic. Photos and reference faces move with two `.env` changes - an
explicit old value overrides the new `data/` defaults:

```bash
mkdir -p data
mv known_people data/ 2>/dev/null; rm -f look.png
# in .env: CAMERA_SNAPSHOT_PATH=data/look.png and KNOWN_PEOPLE_DIR=data/known_people
# (or delete both lines). standalone/sync_env.py flags the old values.
```

**Memory.** Plain markdown - open it and edit it; changes apply on the
next turn, no restart:

```markdown
## Alex (primary user)
- Likes cricket.

## Maya
- Alex's daughter. Loves the dance gesture.
```

One fact per `- ` line under a `## <name>` heading. Cozmo can't tell
voices apart, so the section with "primary user" in its heading is who he
assumes he's talking to - rename the template's `## Primary user` to your
name, keeping "(primary user)". Text above the first `##` heading is notes
for humans and is never sent. Cozmo edits it himself when asked
("remember that I like cricket", "forget that"), and also saves clearly
lasting things people tell him about themselves, saying so out loud.
`remember_fact` stops at `MEMORY_MAX_FACTS` (default 40) and asks to forget
something first; facts added by hand beyond that are still sent.

**Suggested facts.** Cozmo saves things live, while you talk. What he
misses gets a second chance: when a conversation ends (back to idle in
`--mode vad`, or when the app exits in the other modes), one LLM call
(`memory_suggestions.py`, no tools) reads what was said since the last
check and lists anything worth remembering for weeks or months. Most
conversations add nothing - the model is told that's the normal answer, and
a conversation of only a command or two isn't checked at all. Relative
dates are written as real ones ("the week of 2026-10-05"), so a stale plan
is recognizable later. Anything already known, waiting, or declined is
skipped, so it never duplicates what Cozmo saved live.

Suggestions wait in `memory.md`, below your facts:

```markdown
## Suggested (not yet approved)
- Has a dog named Bruno. [2026-10-01]
- Lives in Lisbon. [about Maya, 2026-10-01]

## Declined suggestions
- Is learning the guitar. [2026-09-30]
```

They're **never sent as things Cozmo knows**: he sees them in a separate,
clearly marked "noticed earlier but haven't confirmed" list (the newest 5),
and they don't count toward `MEMORY_MAX_FACTS` or show up in searches.
He's told to bring up **one**, in his own words, only when it fits - the
conversation touches on it, or a relaxed moment, never mid-request - and at
most once per conversation; most conversations shouldn't have one. Your
answer goes through `review_suggestion`: **keep** moves it into the
person's section (if memory is full, it stays waiting), **discard** moves it
to Declined so it's never suggested again, **later** leaves it. Ask "what
did you want to remember?" to go through them all. Or edit the file: move a
line up into a section (drop the `[...]` tag) to keep it, or delete it.

To empty a list without editing the file, ask: "clear all the waiting
suggestions", "clear the declined ones", or both (`clear_suggestions`).
Cozmo is told how many are declined (not what they are), so he knows that
list exists; he says how many will go and checks before clearing, since it
can't be undone. Cleared declined facts may be suggested again.
Suggestions don't expire. `MEMORY_SUGGESTIONS_ENABLED=false` turns the
check off.

When you quit (Ctrl+C, `quit` in text mode, or `pkill -f 'cozmo_brain --mode vad'`
from another shell - SIGTERM is handled exactly like Ctrl+C, so systemd stops
it cleanly too; only `kill -9` skips this; it ends with `Stopped.` and exit
code 130, no traceback - real errors still print theirs), the app checks whatever
wasn't checked yet - it prints "Checking this conversation for anything
worth remembering..." and exiting can take a few seconds longer. In
`--mode vad` that's usually nothing (each conversation was already checked
when it ended), so there's no extra call.

**Search.** `search_memory` matches keywords (a trailing "s" ignored, so
"like" finds "likes") against saved facts and everything said in the
archive - the human's words and Cozmo's `say` text, with dates. The system
prompt includes the current date and time, so "what did we talk about
yesterday?" can be placed. It's keyword search, not meaning search: "my
trip" won't find "my vacation".

**Privacy.** Everything in `data/` is plain text on the Pi, never
committed. What goes to the chat provider (Groq/OpenAI), on top of the
conversation itself:
- with *every* request: your confirmed facts and the newest 5 waiting
  suggestions, as part of the system prompt;
- with each after-conversation check: that conversation again, plus your
  facts and the waiting and declined suggestions.

Only `CHAT_PROVIDER=ollama` keeps all of it local. Cozmo is told never to
save passwords, health, money or address details, even if asked, and the
check is told never to suggest them - keep those out of hand edits too.

**What's verified:** offline tests (`tests/test_history_memory.py`:
archive, resume, import, retention, memory edits, search, and the engine
calling the tools; `tests/test_memory_suggestions.py`: the suggestion check
and review) and a simulated text-mode run. Saving facts live was seen
working on the Pi (2026-10-01). **Not verified:** how well each chat model
judges what's worth suggesting, and when it brings one up - that needs real
conversations, on each provider.
