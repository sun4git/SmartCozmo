"""The core agentic loop: user text in, LLM decides tool calls, tools execute
against the robot, results feed back to the LLM, repeat until it stops
calling tools (or a safety cap is hit). Mode-agnostic — every mode (voice,
VAD, text, calibration) funnels user input through `CozmoEngine.handle_turn`.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import requests

from cozmo_brain.config import Settings
from cozmo_brain.conversation import Conversation
from cozmo_brain.imaging import encode_image_b64
from cozmo_brain.llm.chat_client import ChatClient
from cozmo_brain.llm.speech_client import SpeechClient
from cozmo_brain.robot.base import RobotBackend
from cozmo_brain.tools.base import Tool, ToolResult

logger = logging.getLogger(__name__)

# Appended to the user's message when the robot can't move right now
# (RobotBackend.is_movement_blocked()). Confirmed on real hardware that
# telling the model only via the drive/turn tool result is too late: it
# usually calls `say` ("I'm coming!") *before* `drive` in the same turn, so
# the promise was already spoken before it ever learned the drive was
# refused. This gives it the fact before it decides what to say.
_CHARGER_BLOCKED_NOTE = (
    "[Status: I'm on my charger and my battery is still too low to come off - drive, turn, and "
    "the wheel parts of gestures won't work right now. If asked to move or come out, say I can't "
    "yet because I need to charge more; don't say I'm coming.]"
)

# Upper bound on waiting for background gestures at the end of a turn -
# longest real gesture (spin) is ~7s at the current turn calibration.
_GESTURE_WAIT_S = 15.0

# How long a background follow-up (FINAL_LLM_CALL=async) waits for an
# in-progress recording to actually stop before it acts - the recorder
# checks its stop signal every 30ms frame, so this is generous.
_MIC_RELEASE_WAIT_S = 1.0

# Spoken by the engine itself if a drive/turn was refused for that reason
# and the model never said anything after finding out (e.g. it ran out of
# MAX_TOOL_ITERATIONS, or ignored the tool result) - the human must never be
# left with a spoken promise and a Cozmo that silently didn't move.
_CHARGER_BLOCKED_FALLBACK = "Sorry, I can't come out yet - my battery's too low. I need to charge a bit more first."


@dataclass
class _TurnState:
    """What one user turn has produced so far - shared between the part of
    the turn run on the caller's thread and, with FINAL_LLM_CALL=async, the
    follow-up LLM call finished on a background thread."""

    lines: list[str] = field(default_factory=list)
    # True once a drive/turn got refused by the charger check, until a
    # successful `say` afterward - i.e. the model has told them why.
    unexplained_block: bool = False

    def summary(self) -> str:
        return "\n".join(self.lines) if self.lines else "(no response)"


class CozmoEngine:
    def __init__(
        self,
        settings: Settings,
        robot: RobotBackend,
        ollama: ChatClient,
        speech: SpeechClient,
        tools: list[Tool],
        conversation: Conversation,
        system_prompt_fn: Callable[[], str] | None = None,
    ):
        self._settings = settings
        self._robot = robot
        self._ollama = ollama
        self._speech = speech
        self._tools = tools
        self._tools_by_name = {t.name: t for t in tools}
        self.conversation = conversation
        # Rebuilds the system prompt at the start of every turn (main.py:
        # the persona plus the current memory file and date/time), so a
        # fact saved or hand-edited mid-run is seen on the next turn.
        self._system_prompt_fn = system_prompt_fn
        # Called with ("session_start", fields) / ("session_end", fields)
        # when --mode vad opens or closes a conversation (session_event()).
        # main.py: resets "asked about a suggestion" and starts the
        # after-conversation memory check (memory_suggestions.py).
        self.session_listeners: list[Callable[[str, dict], None]] = []
        # Tracks real conversation activity for idle_fidget.py - starts at
        # construction time (not 0/unset) so idle counting begins from
        # process start rather than looking infinitely idle before the
        # first turn ever happens.
        self.last_interaction_monotonic: float = time.monotonic()
        # Minutes Cozmo has been continuously docked, or None when he isn't
        # (main.py sets this to charger_break.py's DockTimer.minutes) - shown
        # in the per-turn battery line.
        self.dock_minutes_fn: Callable[[], float | None] | None = None
        # True while --mode vad has a listening window open (after a wake
        # word/tap, until "no follow-up heard"). idle_fidget.py doesn't
        # fidget then - confirmed on real hardware that a fidget's motor
        # noise got recorded and sent to STT as a blank clip mid-window.
        self.listening_window_open = False
        # Held for the whole of every handle_turn(). Background behavior that
        # speaks, drives, or writes to the conversation on its own
        # (charger_return.py) takes this too, so it never talks over a reply
        # in progress or mutates the message list mid-turn - and a turn that
        # starts meanwhile simply waits for it to finish.
        self.turn_lock = threading.Lock()
        # FINAL_LLM_CALL=async plumbing (see handle_turn()): set by the
        # background follow-up when the model *continues* its reply (more
        # actions or speech), so --mode vad stops the recording it already
        # started; mic_active is --mode vad saying a recording is running.
        self.followup_interrupt = threading.Event()
        self.mic_active = False
        # When True, each tool's result is logged the moment it finishes
        # (with how long it took), instead of only appearing in the
        # end-of-turn summary. --mode vad turns this on and stops printing
        # that summary: printed after the whole turn, the `[say] OK: ...`
        # recap used to land *after* "LLM step 2", reading as if Cozmo spoke
        # last. Text/push-to-talk modes leave it off - their printed summary
        # is the reply itself.
        self.log_steps_live = False
        self._followup_thread: threading.Thread | None = None
        # Things Cozmo should speak up about on his own, each delivered as a
        # short turn (deliver_announcements()) - today the answers to
        # background ask_assistant requests (assistant_relay.py).
        # announce_event is set while any are waiting: --mode vad stops a
        # recording for it only if no speech has started yet.
        self.announce_event = threading.Event()
        self._announcements: list[str] = []
        self._announce_lock = threading.Lock()
        # Set by request_listen() when Cozmo has just spoken up on his own
        # with something that wants an answer, outside a listening window:
        # --mode vad then opens a short window (SPEAK_UP_LISTEN_S) without
        # the wake word. Other modes never read it.
        self.listen_request = threading.Event()

    def session_event(self, event: str, **fields) -> None:
        """A conversation started or ended (--mode vad): marked in the
        archive, then passed to session_listeners."""
        self.conversation.mark(event, **fields)
        for listener in self.session_listeners:
            try:
                listener(event, fields)
            except Exception:  # noqa: BLE001 - a listener must never break the conversation loop
                logger.exception("Session listener failed on %s.", event)

    def set_listening_window(self, is_open: bool) -> None:
        """Called by --mode vad when a listening window opens/closes. Closing
        also counts as activity for idle_fidget.py, so a fidget waits a full
        IDLE_FIDGET_AFTER_S after the window, rather than firing the instant
        a long wordless window (e.g. a false activation) ends."""
        self.listening_window_open = is_open
        if not is_open:
            self.last_interaction_monotonic = time.monotonic()

    def request_listen(self) -> None:
        """Ask --mode vad to listen for a reply without the wake word, after
        Cozmo spoke up on his own. No-op inside a listening window (it's
        already listening) or with SPEAK_UP_LISTEN_S=0."""
        if self._settings.speak_up_listen_s > 0 and not self.listening_window_open:
            self.listen_request.set()

    def queue_announcement(self, note: str) -> None:
        """Queue a bracketed note for Cozmo to act on in a turn of his own
        (see deliver_announcements()). Safe from any thread."""
        with self._announce_lock:
            self._announcements.append(note)
            self.announce_event.set()

    def deliver_announcements(self) -> bool:
        """Run one turn per queued note (the note stands in for the user's
        words). Takes turn_lock through handle_turn(), so it waits for any
        turn in progress. Returns whether there was anything to deliver."""
        with self._announce_lock:
            notes, self._announcements = self._announcements, []
            self.announce_event.clear()
        for note in notes:
            logger.info("Speaking up on my own: %s", note)
            summary = self.handle_turn(note)
            if not self.log_steps_live:  # already logged live otherwise
                logger.info("%s", summary)
        if notes:
            # Delivered outside a window (by assistant_relay.py's thread):
            # let them reply without the wake word.
            self.request_listen()
        return bool(notes)

    def _turn_is_done(self, batch: list[tuple[str, ToolResult]], image_attached: bool) -> bool:
        """Whether to end the turn now instead of asking the model again.

        The generic tool-calling loop only stops when the model replies with
        no tool calls - but here Cozmo's actual answer is itself a tool call
        (`say`), so even a one-line reply cost a second LLM round trip just
        to hear "done", with the mic closed the whole time (estimated ~2-3s
        from real-hardware logs). So: stop once the batch's *last* call was a
        successful `say`, unless something in the batch needs the model to
        see it first - an error, a hazard/refused move/failed return
        (extra["needs_attention"], tools/registry.py), or a photo to look at.
        That last check matters because the model writes every call in a
        batch up front: `drive` then `say` means it spoke *before* knowing
        the drive hit a cliff. What happens next depends on FINAL_LLM_CALL
        (see config.py/handle_turn()): skip ends the turn, async finishes the
        LLM call in the background."""
        if not batch or image_attached:
            return False
        last_name, last_result = batch[-1]
        if last_name != "say" or not last_result.ok:
            return False
        # Every tool in the batch must be a pure action (Tool.safe_to_end_turn)
        # - an information tool like list_animations means the model hasn't
        # read that result yet, even if it spoke after calling it.
        for name, result in batch:
            tool = self._tools_by_name.get(name)
            if tool is None or not tool.safe_to_end_turn:
                return False
            if not result.ok or (result.extra and result.extra.get("needs_attention")):
                return False
        return True

    def _charger_status_note(self) -> str | None:
        """A one-line charger status note, added to every turn (~10 tokens).
        Confirmed on real hardware, twice: a gesture drove Cozmo off the
        charger mid-conversation and the model, never told, insisted "I'm
        already here!". A first fix only sent this when the state changed
        between turns - which missed a docked-then-off round trip that
        happened entirely *between* two turns (an autonomous return, then a
        fidget drive-off): same state at both turns, so no note, while the
        history said "docked". Every turn is the only version that can't
        go stale."""
        try:
            on_charger, charging = self._robot.is_on_charger(), self._robot.is_charging()
        except Exception as e:  # noqa: BLE001 - a status check must never break a turn
            logger.debug("Charger status check failed: %s", e)
            return None
        if not on_charger:
            return "[Status: I'm off my charger right now.]"
        if charging:
            return "[Status: I'm on my charger and charging.]"
        return "[Status: I'm on my charger, not currently charging (probably full).]"

    def _battery_note(self) -> str | None:
        """The real voltage (and time docked) for every turn - the model has
        no battery percentage, and without this it guessed ("fully charged!")
        from the one-line charger status alone."""
        try:
            voltage = self._robot.get_battery_voltage()
            if voltage is None:
                return None
            docked_for = ""
            if self.dock_minutes_fn is not None:
                minutes = self.dock_minutes_fn()
                if minutes is not None:
                    docked_for = f", docked for {minutes:.0f} min"
            return f"[Battery: {voltage:.2f}V{docked_for}.]"
        except Exception as e:  # noqa: BLE001 - a status check must never break a turn
            logger.debug("Battery note failed: %s", e)
            return None

    def _movement_blocked(self) -> bool:
        try:
            return self._robot.is_movement_blocked()
        except Exception as e:  # noqa: BLE001 - a status check must never break a turn
            logger.debug("Movement-blocked check failed: %s", e)
            return False

    def speak(self, text: str, mood: str = "neutral") -> bool:
        """Say something unprompted, through the same `say` tool the model
        uses (same TTS provider, AUDIO_OUTPUT routing, and lift handling).
        Caller must hold turn_lock. Returns whether it succeeded."""
        self.last_interaction_monotonic = time.monotonic()
        result = self._call_tool("say", {"text": text, "mood": mood})
        if not result.ok:
            logger.warning("Unprompted speech failed: %s", result.to_tool_message())
        return result.ok

    def add_note(self, text: str) -> None:
        """Record something Cozmo said/did on his own as an assistant message,
        so the model has context for the human's next reply (e.g. "yes" to a
        low-battery offer it never made itself). Caller must hold turn_lock."""
        self.last_interaction_monotonic = time.monotonic()
        self.conversation.add_assistant(text)

    def _call_tool(self, name: str, arguments: dict) -> ToolResult:
        tool = self._tools_by_name.get(name)
        if tool is None:
            known = ", ".join(sorted(self._tools_by_name))
            return ToolResult(False, f"Unknown tool '{name}'. Known tools: {known}")

        if not self._robot.is_healthy():
            logger.info("Robot connection looks stale before calling '%s' — reconnecting.", name)
            if not self._robot.reconnect():
                return ToolResult(False, "Cozmo seems to be disconnected, and reconnecting didn't work.")

        try:
            return tool.handler(arguments)
        except Exception as e:  # noqa: BLE001 - any tool failure becomes model-visible feedback
            logger.warning("Tool '%s' failed: %s", name, e)
            return ToolResult(False, str(e))

    def handle_turn(
        self, user_text: str, images: list[str] | None = None, *, allow_async_followup: bool = False
    ) -> str:
        """Runs one full user turn through the tool-calling loop. Returns a
        transcript-ish summary of what Cozmo said/did, for logging/display.

        `allow_async_followup` is passed only by --mode vad: with
        FINAL_LLM_CALL=async, once the reply ends in a clean `say`, this
        returns immediately (so the mic can reopen) and the follow-up LLM
        call finishes on a background thread, which keeps holding turn_lock
        until it's done - so the next turn, a charger return, or a fidget
        still can't start until it finishes. Other modes don't reopen a mic
        on their own, so they always wait for it (plain sync)."""
        self.turn_lock.acquire()
        handed_off = False
        try:
            summary, handed_off = self._handle_turn(user_text, images, allow_async_followup)
            return summary
        finally:
            if not handed_off:
                self.turn_lock.release()

    def settle_followup(self) -> bool:
        """If a background follow-up took the floor (the model continued its
        reply), wait for it to finish and return True; otherwise return False
        at once, even if a follow-up is still waiting on the LLM - that's the
        case async exists for. Called by --mode vad around each recording."""
        if not self.followup_interrupt.is_set():
            return False
        thread = self._followup_thread
        if thread is not None:
            thread.join(timeout=120)
        self.followup_interrupt.clear()
        return True

    def _handle_turn(
        self, user_text: str, images: list[str] | None, allow_async_followup: bool
    ) -> tuple[str, bool]:
        self.last_interaction_monotonic = time.monotonic()
        if self._system_prompt_fn is not None:
            try:
                self.conversation.set_system_prompt(self._system_prompt_fn())
            except Exception as e:  # noqa: BLE001 - keep the previous prompt rather than fail the turn
                logger.warning("Could not rebuild the system prompt (keeping the previous one): %s", e)
        charger_note = self._charger_status_note()
        if charger_note:
            user_text = f"{user_text}\n\n{charger_note}"
        battery_note = self._battery_note()
        if battery_note:
            user_text = f"{user_text}\n{battery_note}"
        logger.debug("Status notes sent: %s | %s", charger_note, battery_note)
        if self._movement_blocked():
            user_text = f"{user_text}\n\n{_CHARGER_BLOCKED_NOTE}"
        self.conversation.add_user(user_text, images=images)
        schema = [t.schema() for t in self._tools]
        turn = _TurnState()

        mode = self._settings.final_llm_call
        run_async = mode == "async" and allow_async_followup
        ended_early, next_step = self._run_steps(schema, turn, 0, can_end_early=mode == "skip" or run_async)

        if ended_early and run_async and next_step < self._settings.max_tool_iterations:
            self.followup_interrupt.clear()
            thread = threading.Thread(
                target=self._run_followup, args=(schema, turn, next_step), name="llm-followup", daemon=True
            )
            self._followup_thread = thread
            thread.start()
            # Still wait for gestures here: the mic must not reopen mid-spin,
            # whatever the follow-up turns out to be.
            self._wait_for_gestures()
            return turn.summary(), True

        self._finish_turn(turn)
        return turn.summary(), False

    def _run_steps(
        self,
        schema: list[dict],
        turn: _TurnState,
        first_step: int,
        can_end_early: bool,
        before_tools=None,
    ) -> tuple[bool, int]:
        """The tool-calling loop proper, from step `first_step`. Returns
        (ended_early, next_step): ended_early means it stopped after a clean
        final `say` (_turn_is_done()) rather than because the model replied
        with no tool calls, failed, or hit MAX_TOOL_ITERATIONS.
        `before_tools` runs once a response turns out to contain tool calls,
        before any of them execute (the async follow-up uses it to stop the
        mic first)."""
        where = " (background)" if threading.current_thread().name == "llm-followup" else ""
        for iteration in range(first_step, self._settings.max_tool_iterations):
            step_started = time.monotonic()
            try:
                response = self._ollama.chat(self.conversation.messages, tools=schema)
            except requests.RequestException as e:
                # Named by CHAT_PROVIDER, not a hardcoded Ollama URL - a
                # failed Groq/OpenAI call used to be reported as
                # "couldn't reach the LLM at <the Ollama URL>".
                logger.error("Chat request (%s) failed: %s", self._settings.chat_provider, e)
                turn.lines.append(f"(chat request to {self._settings.chat_provider} failed: {e})")
                return False, iteration
            tool_calls_raw = [
                {"id": tc.id, "function": {"name": tc.name, "arguments": tc.arguments}}
                for tc in response.tool_calls
            ]
            self.conversation.add_assistant(response.content, tool_calls=tool_calls_raw or None)

            # Only `say` makes Cozmo speak - text outside a tool call is
            # never heard, so label it as such rather than as "thinking".
            if response.content:
                turn.lines.append(f"(model text, not spoken) {response.content}")
                if self.log_steps_live:
                    logger.info("model text (not spoken)%s: %s", where, response.content)

            # Every step is logged, the final "done" one included (it used to
            # be silent, so a sync turn looked like it only ever had one
            # step), with how long the LLM call took - i.e. what that step
            # cost in waiting, mic closed unless it says "(background)".
            # Also shows whether the model batches actions with its `say`,
            # or speaks first and acts in a later step.
            step_s = time.monotonic() - step_started
            if not response.tool_calls:
                if iteration == 0:
                    logger.info("Model returned no tool calls this turn.")
                logger.info("LLM step %d%s (%.2fs): no tool calls - turn done", iteration + 1, where, step_s)
                return False, iteration + 1
            if before_tools is not None:
                before_tools()
            logger.info(
                "LLM step %d%s (%.2fs): %s",
                iteration + 1, where, step_s, ", ".join(tc.name for tc in response.tool_calls),
            )

            image_to_attach: str | None = None
            image_caption = "[Cozmo just looked around and captured a photo of what's in front of him.]"
            batch: list[tuple[str, ToolResult]] = []
            for tc in response.tool_calls:
                tool_started = time.monotonic()
                result = self._call_tool(tc.name, tc.arguments)
                if self.log_steps_live:
                    logger.info(
                        "tool %s (%.1fs): %s", tc.name, time.monotonic() - tool_started, result.to_tool_message()
                    )
                batch.append((tc.name, result))
                turn.lines.append(f"[{tc.name}] {result.to_tool_message()}")
                self.conversation.add_tool_result(tc.name, result.to_tool_message(), tool_call_id=tc.id)
                if result.extra and result.extra.get("blocked_by_charger"):
                    turn.unexplained_block = True
                elif tc.name == "say" and result.ok:
                    turn.unexplained_block = False
                if result.ok and result.extra and result.extra.get("image_path"):
                    image_to_attach = result.extra["image_path"]
                    image_caption = result.extra.get("image_caption", image_caption)

            if image_to_attach and self._settings.vision_enabled:
                try:
                    b64 = encode_image_b64(image_to_attach)
                    self.conversation.add_user(image_caption, images=[b64])
                except OSError as e:
                    logger.warning("Could not attach captured photo: %s", e)
            elif image_to_attach and self.conversation.archive is not None:
                # Not sent to the model, but still kept with the transcript.
                self.conversation.archive.record_photo(image_to_attach)

            if can_end_early and self._turn_is_done(batch, image_attached=bool(image_to_attach)):
                return True, iteration + 1

        logger.warning("Hit max_tool_iterations (%d) without a final reply.", self._settings.max_tool_iterations)
        return False, self._settings.max_tool_iterations

    def _run_followup(self, schema: list[dict], turn: _TurnState, first_step: int) -> None:
        """FINAL_LLM_CALL=async: the rest of a turn, on a background thread,
        while --mode vad is already listening. Usually the model just says
        "done" and nothing visible happens. If it continues (e.g. a model
        that said "sure!" alone and acts in its next step - confirmed live
        with Groq's openai/gpt-oss-120b), the recording is stopped first
        (_take_floor) so neither motor noise nor Cozmo's own voice lands in
        it. Releases the turn_lock handle_turn() handed over."""
        already_shown = len(turn.lines)
        try:
            self._run_steps(schema, turn, first_step, can_end_early=False, before_tools=self._take_floor)
            self._finish_turn(turn)
            if not self.log_steps_live:  # already logged live, as it happened
                for line in turn.lines[already_shown:]:
                    logger.info("Follow-up: %s", line)
        except Exception:  # noqa: BLE001 - a background thread must never die silently
            logger.exception("Background follow-up LLM call failed.")
        finally:
            self.turn_lock.release()

    def _take_floor(self) -> None:
        logger.info("Model continued its reply - pausing listening while it runs.")
        self.followup_interrupt.set()
        deadline = time.monotonic() + _MIC_RELEASE_WAIT_S
        while self.mic_active and time.monotonic() < deadline:
            time.sleep(0.02)

    def _finish_turn(self, turn: _TurnState) -> None:
        if turn.unexplained_block:
            logger.info("A move was refused (charging, low battery) and never explained - saying so directly.")
            if self.speak(_CHARGER_BLOCKED_FALLBACK, mood="sleepy"):
                self.conversation.add_assistant(f"[I said this out loud:] {_CHARGER_BLOCKED_FALLBACK}")
                turn.lines.append(f"[say] {_CHARGER_BLOCKED_FALLBACK}")
                if self.log_steps_live:
                    logger.info("tool say (fallback): %s", _CHARGER_BLOCKED_FALLBACK)
        self._wait_for_gestures()

    def _wait_for_gestures(self) -> None:
        # A turn isn't over while Cozmo is still moving: a `say`'s gesture
        # (GESTURE_ASYNC_ENABLED) keeps running in the background after the
        # speech finishes - `spin` takes ~7s, well past any sentence. The
        # caller (e.g. --mode vad) reopens the mic as soon as the turn
        # returns, and would otherwise record the motor noise.
        if not self._robot.wait_for_background_gestures(_GESTURE_WAIT_S):
            logger.warning("A background gesture was still running after %.0fs - not waiting longer.", _GESTURE_WAIT_S)
