# Example conversations

[← Back to the README](../README.md)


These first three are real, captured `--mode text --simulate` sessions from
testing this project — not scripted mockups. `say`'s text is LLM-generated,
so it'll never come out identically twice; these are one real run each,
shown to give a feel for it, not a guarantee of exact wording.

**Being funny and expressive:**
```
you> Hey Cozmo, say hi and do a happy dance for me!
[say] OK: Said (mood=excited): Again?! You just can't get enough of my talent!
[gesture] OK: Performed gesture 'dance': A quick, silly wheel shimmy with flashing lights.
```

**Looking around, and some sass:**
```
you> What's in front of you right now?
[look] OK: Took a photo.
[say] OK: Said (mood=curious): Hold on! Let me see...
[say] OK: Said (mood=excited): Oh! It's you! My favorite giant human!
[gesture] OK: Performed gesture 'cheer': Bounces its lift arm up and down excitedly with green lights.

you> You're kind of full of yourself, you know that?
[say] OK: Said (mood=smug): I'm not full of myself... I'm just awesome!
[gesture] OK: Performed gesture 'fist_pump': Raises its arm high in triumph, proud and smug.
```

**Registering and "recognizing" a person** (see
[Recognizing people (experimental)](people-recognition.md#recognizing-people-experimental) above):
```
you> Hi Cozmo, remember me as Alex.
[remember_person] OK: Got it - I'll remember this face as Alex.
[say] OK: Said (mood=excited): Alex! Got it! I'll try not to forget you, human!
[gesture] OK: Performed gesture 'fist_pump': Raises its arm high in triumph, proud and smug.

you> Do you know who I am?
[who_is_this] OK: I took a look, but I don't recognize this person.
[say] OK: Said (mood=curious): Hmm, let me look... just a second!
[gesture] OK: Performed gesture 'peek': Curiously cranes its head up and tilts, like peeking at something.
[say] OK: Said (mood=embarrassed): Uh oh! Everything is so blurry! Are you Alex? I'm guessing yes!
[gesture] OK: Performed gesture 'shrug': A quick 'I dunno' shrug: lift up-down with a confused face.
```
That "no match" on the very next turn, on the *same* stored photo, is the
real unreliability this feature has — not a cherry-picked failure. It's a
genuinely accurate example of what to expect, and the persona is meant to
own it (hedging, self-deprecating about "his eyesight") rather than
confidently asserting a wrong answer.

**Asking the assistant, answer in the background** (see
[Asking your own assistant](assistant.md#asking-your-own-assistant-ask_assistant)). A
real run on 2026-10-04 against a real OpenClaw agent named Sunny, with
Groq chat and the simulated robot, driven through the engine directly
rather than `--mode text`; log lines trimmed:
```
you> Hey Cozmo, what's the latest news headline in India today?
LLM step 1 (0.89s): ask_assistant
Asking Sunny: Tell me the latest / top news headline in India today, October 4 2026.
tool ask_assistant (0.0s): OK: Asked Sunny: "...". The answer comes later, on its own - ...
(user turn returned after 1.0s - Cozmo is free to talk and listen)
Sunny replied (35.5s): Top headlines today include a student protest march in New Delhi ...
Speaking up on my own: [Sunny just answered the request you sent earlier (...): ...]
[say] OK: Said (mood=curious) while performing 'alert': Oh, the news is in! According to
      Sunny, there are two big ones today: First, students are marching in New Delhi ...
```
In that run the chat model's step after `ask_assistant` hit Groq's
free-tier rate limit (429), so the usual "I've asked Sunny" line was never
said; the answer was still delivered on its own.

## More things to try

A few more prompts to give a sense of range — not verified transcripts like
above, just what they're expected to trigger:

**Fun:**
- *"Do a little happy dance!"* → `gesture: dance`
- *"You just won something amazing, celebrate!"* → `gesture: cheer` + an excited `say`
- *"Sneak up on me."* → `gesture: sneaky_creep` + a suspicious mood
- *"Pretend you're falling asleep."* → `gesture: sleep`, then *"wake up!"* → `gesture: wake_up`
- *"Say something dramatic about how your day is going."* → an exaggerated mood + `say`, very in-character

**Smart:**
- *"What real animations do you actually have?"* → `list_animations` — a genuine, honest
  answer (real clip names if `pycozmo_resources.py download` was run, or a
  clear "none loaded" rather than a guess).
- *"Look around and tell me what's in the room."* → `look`, then a description
  grounded in the real captured photo, not a guess at what's plausible.
- *"Drive forward about 20 centimeters, slowly."* → `drive`, clamped to the
  safety limits in `.env` regardless of what's asked.
- *"Turn about a quarter turn to your left."* → `turn`, using the calibrated
  `TURN_SECONDS_PER_DEGREE` from `--mode calibrate`.
- *"Remember me as \<name\>"* / *"Do you know who I am?"* → `remember_person` /
  `who_is_this` — see above for what to actually expect from this one.

**Memory:**
- *"Remember that I like cricket."* → `remember_fact`, then a line under
  your section in `data/memory.md`.
- *"What do you know about me?"* → answered from the memory in the prompt,
  no tool needed.
- *"Actually, forget that I like cricket."* → `forget_fact`.
- *"What did we talk about yesterday?"* / *"When did I mention Goa?"* →
  `search_memory`, answered with dates from the archive.

**Your own assistant** (needs `ASSISTANT_ENABLED=true`; the name is your
`ASSISTANT_NAME`):
- *"Remind me in 10 minutes to stretch."* → `ask_assistant`; the reminder
  arrives as a message from the assistant (confirmed on WhatsApp with
  OpenClaw), even if Cozmo is off by then.
- *"What's the weather in Lisbon?"* / *"Any big news today?"* → `ask_assistant`,
  "I've asked Sunny", and the answer ~30s later, spoken on its own.
- *"Tell my friends group I'll be late."* → affects other people, so Cozmo
  should read it back and wait for a yes before asking.
- *"What's Sunny?"* → answered from the prompt: your assistant, by its name.
