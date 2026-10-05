# How a turn ends (`FINAL_LLM_CALL`)

[← Back to the README](../README.md)


A turn is a loop: the LLM returns tool calls, the engine runs them and sends
the results back, and so on until the LLM replies with **no** tool calls.
Cozmo's actual answer is itself a tool call (`say`). So after almost every
reply the loop makes one more LLM call, just to hear "done". In `--mode vad`
the mic stays closed during that call (~1–2s). `FINAL_LLM_CALL` decides what
happens to it:

| Setting | That last call | Mic reopens | Catches a model that acts *after* its `say`? |
|---|---|---|---|
| `async` (default) | made in the background | right after the `say` | ✅ (pauses the mic while it acts) |
| `sync` | made, and waited for | after it returns | ✅ |
| `skip` | not made | right after the `say` | ❌ the action is lost |

**When a turn is allowed to stop early at all.** This is the same rule for
`async` and `skip` (`CozmoEngine._turn_is_done()`). A step qualifies only if
**all** of these hold:
- its **last** call is a successful `say`;
- every tool in that step is a pure action (`Tool.safe_to_end_turn`: `say`,
  `gesture`, `play_animation`, `drive`, `turn`, `dock`, `remember_person`);
- nothing needs the model's attention: no error, no cliff/fall, no move
  refused on the charger, no failed charger return, no photo.

Until a step qualifies, everything runs synchronously, with the mic closed,
exactly like `sync`. **New tools default to `safe_to_end_turn=False`**, so a
turn that uses one never ends before the model has seen that tool's result.
Only opt a tool in if its success result tells the model nothing new.

**Examples** (verified in scripted tests with a fake model). "Before mic
reopens" is what you wait through; "background" happens while Cozmo is
already listening:

| You say | Model's steps | `sync`: before mic reopens | `async`: before mic reopens | `async`: background |
|---|---|---|---|---|
| "Hi!" | `say` → *done* | `say`, *done* | `say` | *done* |
| "Turn left" (batching model) | `say`+`turn` → *done* | both | both (last call isn't `say`) | — |
| "What do you see?" | `say`+`look` → `say` → *done* | all 3 | `say`+`look`, `say` | *done* |
| "What moves can you do?" | `list_animations`+`say` → `say` → *done* | all 3 | first 2 (must read the list) | *done* |
| "Drive forward" (hits a cliff) | `say`+`drive` → `say` → *done* | all 3 | first 2 (must react to the cliff) | *done* |
| "Turn left" (**one-tool-per-step model**, e.g. Groq `openai/gpt-oss-120b`) | `say` → `turn` → *done* | all 3 | `say` | `turn` (mic paused), *done* |
| "Look around" (same kind of model) | `say` → `look` → `say` → *done* | all 4 | `say` | `look`, `say`, *done* (mic paused from `look`) |

With `skip`, the last two rows would stop right after the first `say`: he'd
announce the turn and never make it. That's exactly what happened with
gpt-oss-120b in a live test, and why `skip` isn't the default.

**What `async` looks like when the model continues** (the "one-tool-per-step" rows):

```
you: "turn left"
  ├─ LLM step 1: say("Sure, turning!")   ← Cozmo speaks
  ├─ handle_turn() returns → mic reopens, listening...
  │     (background) LLM step 2 → turn  ← model continued
  │        ├─ followup_interrupt set → recording stops within ~30ms
  │        │   (a capture you'd already started is discarded)
  │        ├─ turn(90) runs, mic closed
  │        └─ LLM step 3 → done, turn_lock released
  └─ "(Cozmo continued his reply - listening again)" → same listening window resumes
```

**Rules that hold in every mode:**
- A turn never returns while a gesture is still running (`spin` is ~7s), so
  the mic never reopens into motor noise.
- The background call keeps holding `turn_lock`. Your next turn, an
  autonomous charger return, or an idle fidget waits for it, so the
  conversation history always stays in order.
- `async` only applies in `--mode vad`. Text and push-to-talk modes don't
  reopen a mic on their own, so they always behave like `sync`.
- `async` still makes the extra LLM call every turn. It saves waiting, not
  API usage (a live test hit Groq's free-tier `429` limit). Only `skip`
  saves the call.

The history this leaves behind (a turn ending on a tool result, then the
next user message) was live-tested and accepted by every provider: Ollama,
OpenAI, and Groq.

**Reading the log.** Every LLM step is logged, including the final "done"
one, with how long the call took. Unless marked `(background)`, that's time
the mic was closed:

```
# async (default) - a plain reply
LLM step 1 (1.10s): say
tool say (11.2s): OK: Said (mood=proud) while performing 'nod_yes' ...: I feel POWERFUL!
LLM step 2 (background) (0.90s): no tool calls - turn done   <- listening meanwhile

# sync - same reply
LLM step 1 (1.10s): say
tool say (11.2s): OK: Said ...
LLM step 2 (0.90s): no tool calls - turn done        <- mic closed for this

# async - a model that says first, then acts
LLM step 1 (1.10s): say
tool say (2.4s): OK: Said ...: Sure, turning!
Model continued its reply - pausing listening while it runs.
LLM step 2 (background) (0.95s): turn
tool turn (1.8s): OK: Turned 90 degrees.
LLM step 3 (background) (0.80s): no tool calls - turn done
```

(Timings are illustrative.) In `--mode vad` each tool's result is logged
the moment it finishes (`CozmoEngine.log_steps_live`), and the old
end-of-turn `[say] OK: ...` recap isn't printed. Printed after the whole
turn, that recap used to appear *after* `LLM step 2`, which read as if
Cozmo spoke last. Text and push-to-talk modes still print the recap, since
there it's the reply itself. If step 1 shows only `say` right after you asked
for a movement, and step 2 shows the movement, that model is one of the
"one-tool-per-step" kind above. `sync` and `async` handle it; `skip` would
not.
