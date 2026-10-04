"""Client for an external personal assistant agent that Cozmo can hand
requests to (the `ask_assistant` tool, tools/assistant_tools.py) - things
Cozmo can't do himself: reminders, looking things up on the web, running
tasks.

Speaks the OpenAI-compatible /v1/chat/completions shape, so it isn't tied
to one product. Tested against OpenClaw's gateway, whose documented session
behavior this relies on (docs.openclaw.ai/gateway/openai-http-api): each
request is a new session UNLESS it carries an OpenAI `user` string, from
which the gateway derives a stable session key - so every call with the
same ASSISTANT_SESSION_USER lands in one ongoing conversation on the
assistant's side. That's why only the single request is sent, never
Cozmo's own history: the assistant keeps its own context.

The token is only ever put in the Authorization header, never logged.
"""

from __future__ import annotations

import logging
import time

import requests

from cozmo_brain.config import Settings

logger = logging.getLogger(__name__)

# Long replies are cut before going back to Cozmo's model - it only needs
# enough to answer in a sentence or two, and the whole result is kept in
# the conversation history.
_MAX_REPLY_CHARS = 2000


# Prepended to every request so the assistant knows the reply will be
# spoken by a robot (short, plain text), who "me" is, and that the words
# came through speech-to-text.
_CONTEXT = (
    "[Relayed by Cozmo, the desk robot, from a spoken request. Unless it says otherwise, it's "
    "from your owner, the same person as usual. Your reply is read out loud by the robot, so "
    "answer in one to three short plain sentences - no markdown, lists, links or emoji. "
    "Speech-to-text can mishear, so if the request looks garbled or doesn't make sense, say "
    "so instead of acting on it.]"
)


class AssistantClient:
    def __init__(self, settings: Settings):
        self.name = settings.assistant_name.strip() or "the assistant"
        self._url = settings.assistant_url.rstrip("/") + "/v1/chat/completions"
        self._model = settings.assistant_model
        self._user = settings.assistant_session_user
        self._timeout_s = settings.assistant_timeout_s
        self._headers = {"Content-Type": "application/json"}
        if settings.assistant_token:
            self._headers["Authorization"] = f"Bearer {settings.assistant_token}"
        self._session = requests.Session()

    def ask(self, request: str) -> str:
        """Send one request, return the assistant's reply text. Raises
        RuntimeError with a short, model-readable reason on any failure
        (the engine turns that into an ERROR tool result)."""
        request = request.strip()
        if not request:
            raise ValueError("Empty request - say what to ask.")
        payload = {
            "model": self._model,
            "user": self._user,
            "messages": [{"role": "user", "content": f"{_CONTEXT}\n\n{request}"}],
        }
        started = time.monotonic()
        logger.info("Asking %s: %s", self.name, request)
        try:
            resp = self._session.post(self._url, headers=self._headers, json=payload, timeout=self._timeout_s)
        except requests.Timeout:
            raise RuntimeError(f"{self.name} didn't answer within {self._timeout_s}s.") from None
        except requests.RequestException as e:
            logger.warning("Could not reach %s at %s: %s", self.name, self._url, e)
            raise RuntimeError(f"Couldn't reach {self.name} (network error).") from None
        took = time.monotonic() - started
        if not resp.ok:
            # The body says what was rejected; capped, and never includes our headers.
            logger.warning("%s returned HTTP %s after %.1fs: %s", self.name, resp.status_code, took, resp.text[:500])
            if resp.status_code in (401, 403):
                raise RuntimeError(f"{self.name} refused the request (check ASSISTANT_TOKEN).")
            raise RuntimeError(f"{self.name} returned an error (HTTP {resp.status_code}).")
        try:
            reply = resp.json()["choices"][0]["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError):
            logger.warning("Unexpected reply shape from %s: %s", self.name, resp.text[:500])
            raise RuntimeError(f"{self.name} sent back something I couldn't read.") from None
        reply = reply.strip()
        logger.info("%s replied (%.1fs): %s", self.name, took, reply)
        if not reply:
            return f"{self.name} replied with nothing."
        if len(reply) > _MAX_REPLY_CHARS:
            reply = reply[:_MAX_REPLY_CHARS].rstrip() + " ..."
        return reply
