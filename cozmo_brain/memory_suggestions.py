"""After a conversation: did anyone say something worth remembering that
Cozmo didn't already save? If so, it goes to the "Suggested (not yet
approved)" section of data/memory.md (memory.py) - never straight into what
Cozmo knows. He asks about one later, when it fits (review_suggestion).

Runs when a --mode vad conversation ends (back to idle), in the
background, and once more when the app exits, for whatever wasn't checked
yet (the only check in voice/text mode). Each check reads only what was
said since the last one, from this run's archive file. One LLM call per
check (CHAT_PROVIDER's client, no tools) - and none at all when nobody said
more than a couple of words. Most conversations should add nothing; the
model is told that's the normal answer. MEMORY_SUGGESTIONS_ENABLED=false
turns it off.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Any

from cozmo_brain.history_archive import HistoryArchive, read_records
from cozmo_brain.llm.chat_client import ChatClient
from cozmo_brain.memory import Memory, spoken_lines

logger = logging.getLogger(__name__)

# Below this, a conversation is only a command or two - nothing to learn.
_MIN_HUMAN_WORDS = 4
# Most suggestions one check may add.
_MAX_PER_CHECK = 3

_PROMPT = """\
You help a small home robot, Cozmo, decide what to remember about the people \
he talks to. Below is a conversation he just had, and what he already knows.

Today is {today}.

List facts from the conversation that are worth remembering for weeks or \
months: lasting preferences, important people or pets in their life, \
birthdays, plans with a date, hobbies, what they do. Only things the HUMAN \
said about themselves or people they know - never what Cozmo said, never \
requests or commands ("turn left", "dance"), never passing moods or the \
moment ("I'm tired", "it's raining").

Rules:
- Most conversations have nothing worth remembering. Returning no facts is \
the normal, correct answer - don't stretch.
- At most {max_facts} facts, each short and in the third person ("Has a dog \
named Bruno.").
- Write relative dates as real dates: "next week" said on 2026-10-01 \
becomes "the week of 2026-10-05".
- Skip anything already known, already waiting, or declined (listed below), \
even in other words.
- Never anything sensitive: passwords, health, money, addresses.
- "person" is "" for the person Cozmo was talking to, or a name for someone \
else they talked about.

Already known:
{known}

Waiting for approval or declined:
{waiting}

Conversation:
{transcript}

Reply with only JSON, no other text: {{"facts": [{{"fact": "...", "person": ""}}]}} \
- or {{"facts": []}} if there's nothing worth remembering."""


def parse_facts(text: str) -> list[tuple[str, str]]:
    """(fact, person) pairs from the model's reply. Tolerates code fences
    and text around the JSON; anything unparseable means no facts."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    items = data.get("facts") if isinstance(data, dict) else None
    out = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and str(item.get("fact") or "").strip():
            out.append((str(item["fact"]).strip(), str(item.get("person") or "").strip()))
    return out[:_MAX_PER_CHECK]


class SuggestionExtractor:
    def __init__(self, chat: ChatClient, memory: Memory, archive: HistoryArchive):
        self._chat = chat
        self._memory = memory
        self._archive = archive
        # How many of this run's archive records were already checked.
        self._checked = 0
        self._lock = threading.Lock()

    def check_in_background(self) -> None:
        """A conversation just ended: check it without holding anything up.
        Skipped if a check is already running - the next one covers it."""
        if self._lock.locked():
            return
        threading.Thread(target=self.check, name="memory-suggestions", daemon=True).start()

    def check(self) -> list[str]:
        """Check what was said since the last check. Returns the facts suggested."""
        with self._lock:
            if not self._archive.path.exists():
                return []
            records = list(read_records(self._archive.path))
            new = records[self._checked:]
            lines = [(r.get("ts", ""), who, text) for r in new if r.get("type") == "message"
                     for who, text in spoken_lines(r.get("message") or {})]
            human_words = sum(len(text.split()) for _, who, text in lines if who == "human")
            if human_words < _MIN_HUMAN_WORDS:
                self._checked = len(records)
                return []
            try:
                facts = self._ask(lines)
            except Exception as e:  # noqa: BLE001 - a failed check is retried with the next one
                logger.warning("Checking the conversation for things to remember failed: %s", e)
                return []
            self._checked = len(records)
            added = self._memory.add_suggestions(facts, datetime.now().strftime("%Y-%m-%d"))
            if added:
                logger.info("Suggested for memory (waiting for approval): %s", "; ".join(added))
            else:
                logger.info("Checked the conversation - nothing new worth remembering.")
            return added

    def _ask(self, lines: list[tuple[str, str, str]]) -> list[tuple[str, str]]:
        known = self._memory.prompt_section()
        waiting = "\n".join(f"- {s.fact}" for s in self._memory.suggestions(include_declined=True)) or "(none)"
        transcript = "\n".join(f"[{_hhmm(ts)}] {who}: {text}" for ts, who, text in lines)
        prompt = _PROMPT.format(today=datetime.now().strftime("%A %Y-%m-%d"), max_facts=_MAX_PER_CHECK,
                                known=known, waiting=waiting, transcript=transcript)
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        return parse_facts(self._chat.chat(messages).content or "")


def _hhmm(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).strftime("%H:%M")
    except ValueError:
        return "--:--"
