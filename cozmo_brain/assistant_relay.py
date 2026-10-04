"""ASSISTANT_BACKGROUND=true: ask_assistant requests run on their own
thread, so Cozmo isn't stuck silent (mic closed) for the 20-30s+ an agent
run with a web search takes - measured 27-28s against a real OpenClaw on
2026-10-04, and more when its model is rate-limited.

The tool returns at once ("asked, the answer comes later"); when the
answer (or a failure) arrives it's queued on the engine
(CozmoEngine.queue_announcement) and Cozmo gets a short turn to pass it on
in his own words. Who delivers it:
- outside a --mode vad listening window: this thread, straight away (it
  waits for turn_lock, so never talks over a reply in progress);
- inside one: the vad loop, between recordings - a recording that hasn't
  heard any speech yet is stopped early for it (engine.announce_event),
  one already capturing someone talking is not. See modes/vad_mode.py.

Lives alongside engine.py (like charger_return.py) because it needs the
engine, which is built after the tools - main.py calls attach().
"""

from __future__ import annotations

import logging
import threading

from cozmo_brain.llm.assistant_client import AssistantClient

logger = logging.getLogger(__name__)

# More than this many unanswered requests at once is refused - a runaway
# model (or a mishearing loop) shouldn't be able to flood the assistant.
_MAX_IN_FLIGHT = 3


class AssistantRelay:
    def __init__(self, assistant: AssistantClient):
        self._assistant = assistant
        self._engine = None
        self._lock = threading.Lock()
        self._in_flight = 0

    def attach(self, engine) -> None:
        self._engine = engine

    def submit(self, request: str) -> tuple[bool, str]:
        """Start asking in the background. Returns (started, message for
        the model)."""
        name = self._assistant.name
        request = request.strip()
        if not request:
            return False, "Empty request - say what to ask."
        with self._lock:
            if self._in_flight >= _MAX_IN_FLIGHT:
                return False, f"Already waiting on {self._in_flight} answers from {name} - wait for those first."
            self._in_flight += 1
        threading.Thread(target=self._run, args=(request,), name="assistant-request", daemon=True).start()
        return True, (
            f"Asked {name}: \"{request}\". The answer comes later, on its own - tell them you've "
            f"asked {name} and will pass the answer on when it arrives. Don't guess it now."
        )

    def _run(self, request: str) -> None:
        name = self._assistant.name
        try:
            reply = self._assistant.ask(request)
            note = (
                f"[{name} just answered the request you sent earlier (\"{request}\"): {reply} "
                f"Tell them now with `say`, in your own words - it's been a little while, so "
                f"say briefly what it's about. Nobody said anything new; this is you speaking up.]"
            )
        except Exception as e:  # noqa: BLE001 - every failure must still reach the person
            logger.warning("Background request to %s failed: %s", name, e)
            note = (
                f"[The request you sent {name} earlier (\"{request}\") failed: {e} "
                f"Tell them now with `say` - honestly, don't make up an answer. Nobody said "
                f"anything new; this is you speaking up.]"
            )
        finally:
            with self._lock:
                self._in_flight -= 1
        engine = self._engine
        if engine is None:
            logger.warning("Answer from %s arrived but no engine is attached - dropped: %s", name, note)
            return
        engine.queue_announcement(note)
        # Inside a vad listening window the vad loop delivers it between
        # recordings, so it can't land in the middle of someone talking.
        if not engine.listening_window_open:
            try:
                engine.deliver_announcements()
            except Exception:  # noqa: BLE001 - a background thread must never die silently
                logger.exception("Delivering %s's answer failed.", name)
