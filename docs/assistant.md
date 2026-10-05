# Asking your own assistant (`ask_assistant`)

[← Back to the README](../README.md)


If you already run a personal assistant agent, Cozmo can hand it the things
he can't do himself: reminders, anything that needs the internet (news,
weather, facts he isn't sure of), or tasks outside his desk. It's written
against [OpenClaw](https://docs.openclaw.ai)'s gateway, but any endpoint
that speaks OpenAI's `/v1/chat/completions` works. Off by default:

```
ASSISTANT_ENABLED=true
ASSISTANT_NAME=Sunny                 # what you call it - Cozmo uses the same name
ASSISTANT_URL=http://<host>:18789    # base URL, no /v1/...
ASSISTANT_TOKEN=<gateway token>
ASSISTANT_MODEL=openclaw/main        # OpenClaw: openclaw/<agentId>
```

On OpenClaw, the chat-completions endpoint has to be enabled in the gateway
config first.

**How a request goes.** "Hey Cozmo, remind me at 5 to call mom" -> Cozmo
calls `ask_assistant` with one complete sentence. Agent runs are slow -
measured 27-28s against a real OpenClaw for a web lookup and for setting a
reminder, more when its model is rate-limited - so by default
(`ASSISTANT_BACKGROUND=true`) the request runs in the background: Cozmo
says "I've asked Sunny, I'll let you know" and carries on. When the answer
arrives (`assistant_relay.py`) it's queued, and Cozmo gets a turn of his own
to pass it on, in his own words:

- outside a `--mode vad` listening window (or in text/push-to-talk mode)
  he speaks up straight away, waiting only for any reply in progress
  (`turn_lock`);
- inside one, a recording nobody has started talking in yet is stopped
  early for it, and the window stays open afterwards so you can answer
  without the wake word.
- outside one, a short listening window opens once he's said it
  (`SPEAK_UP_LISTEN_S`, default 10s; `0` = off), so you can answer or
  follow up without the wake word too. A recording that's already capturing someone
  talking is never cut off - the answer comes right after that turn.

Passing an answer on takes one chat-model call. If that turn gets no
`say` out - usually the chat provider failing right then (429 rate limits
are common on free tiers) - it's retried once after 5s, and if that fails
too, Cozmo reads the answer out word for word ("Sunny says: ...", cut to
about 400 characters at a sentence end), or "Sorry, I couldn't get an
answer from Sunny just now." for a failed request. So while the app is
running, an answer that arrives is always heard. This covers the
assistant's immediate replies (an answer, or "I've set your reminder") -
the reminder itself, when it fires later, is sent by the assistant on its
own (WhatsApp) and never reaches Cozmo (spoken reminders: roadmap item 7).
A pending request is lost if the app restarts or stops before it's answered.

`ASSISTANT_BACKGROUND=false` is the simple version: he says "let me ask
Sunny", waits silently with the mic closed (up to `ASSISTANT_TIMEOUT_S`,
default 180s - lower it for this), then answers. At most 3 background
requests can be waiting at once. If the assistant can't be reached, times
out, or rejects the token, Cozmo is told so and says it - he isn't allowed
to make up an answer.

**Sessions.** OpenClaw's endpoint starts a new session for every request
unless the request carries an OpenAI `user` string; then all requests with
that string share one session ([docs](https://docs.openclaw.ai/gateway/openai-http-api)).
Every call sends `user` = `ASSISTANT_SESSION_USER` (default `cozmo`), so the
assistant remembers earlier requests from Cozmo - change it to start a fresh
session. Only the one request is sent, never Cozmo's own conversation;
it's prefixed with a short note that it was relayed by a robot from speech
(so the answer should be one to three plain spoken sentences, and a
garbled-looking request should be questioned, not acted on).

**Reminders** are set and delivered by the assistant (on OpenClaw, as a
message on your phone), so they work even while Cozmo is off. Cozmo
speaking them out loud himself is still roadmap item 7.

**Safety.** Anything Cozmo hears can reach the assistant, including
misheard speech and other people in the room - and an assistant like
OpenClaw can message people and run tools. So the prompt tells Cozmo to read
back, and wait for a yes, before anything that reaches or affects someone
else (messaging a person or group, buying, deleting, changing settings);
reminders for you and plain questions go straight through. That's a prompt
rule, not a hard block: the real limits are whatever the assistant's own
agent is allowed to do, so point `ASSISTANT_MODEL` at an agent with only the
permissions you're happy for anyone near Cozmo to use. The token lives only
in `.env` and is never logged. The connection is plain HTTP unless your
`ASSISTANT_URL` is `https://` - fine on a home network, not across the
internet.

Verified offline against a fake endpoint (`tests/test_assistant.py`,
background delivery and the vad stop rule included). Against the real
OpenClaw gateway (2026-10-04): the session behavior (two curl calls with
the same `user` remembered a word), and through `AssistantClient` a web
lookup (27.0s, a clean one-sentence answer) and a WhatsApp reminder (set in
28.3s, and received on WhatsApp 3 minutes later as asked). **Not yet run end to end through Cozmo on real hardware.**
