"""Cozmo's long-term memory: a few important facts about the people he
talks to, in data/memory.md - plain markdown, meant to be edited by hand
too:

    ## Suneel (primary user)
    - Likes cricket.

    ## Asha
    - Suneel's daughter. Loves the dance gesture.

One fact per "- " line, under a "## <name>" heading. The section whose
heading says "primary user" is who Cozmo assumes he's talking to - he can't
tell voices apart. The whole file is re-read every turn and sent to the
chat model as part of the system prompt, so hand edits apply on the next
turn, no restart. Text above the first "##" heading is notes for humans and
is never sent.

The model edits it through the remember_fact/forget_fact tools, and
search() looks through these facts and the conversation archive
(history_archive.py) for the search_memory tool.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cozmo_brain.history_archive import read_records, run_files

logger = logging.getLogger(__name__)

TEMPLATE = """\
# Cozmo's memory

Facts Cozmo remembers about people, sent to the chat model with every turn.
Edit freely - changes apply on the next turn. One fact per "- " line, under
a "## <name>" heading. The section with "primary user" in its heading is
who Cozmo assumes he's talking to (rename it to your name, keeping
"(primary user)"). Cozmo adds and removes facts here when asked to.

Don't put anything sensitive here (passwords, health, money, addresses):
it's stored as plain text and sent to the chat provider with every turn.

## Primary user
"""

_WORD = re.compile(r"[a-z0-9']+")
# Too common to say anything about what a search is for.
_STOPWORDS = frozenset(
    "the and for you your are was were what when where who how did does that this with have has "
    "about from they them then than but not can could would should just any all our out his her "
    "him she".split()
)
# What a search returns at most, to keep the tool result short.
_MAX_FACT_HITS = 10
_MAX_CONVERSATION_HITS = 8
_MAX_SNIPPET_CHARS = 200


def _words(text: str) -> set[str]:
    """Meaningful words, with a plural/verb "s" dropped so "like" matches "likes"."""
    words = set()
    for w in _WORD.findall(text.lower()):
        if len(w) >= 3 and w not in _STOPWORDS:
            words.add(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return words


def _person_name(heading: str) -> str:
    """'Suneel (primary user)' -> 'suneel'."""
    return re.sub(r"\(\s*primary user\s*\)", "", heading, flags=re.IGNORECASE).strip().lower()


@dataclass
class _Fact:
    line_index: int
    section: str
    text: str


class Memory:
    def __init__(self, path: str | Path, history_dir: str | Path | None = None, max_facts: int = 40):
        self.path = Path(path)
        self.history_dir = Path(history_dir) if history_dir else None
        self.max_facts = max_facts
        self._lock = threading.Lock()

    # --- file ------------------------------------------------------------

    def _read_lines(self) -> list[str]:
        try:
            return self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return TEMPLATE.splitlines()
        except OSError as e:
            logger.warning("Could not read memory file %s: %s", self.path, e)
            return TEMPLATE.splitlines()

    def _write_lines(self, lines: list[str]) -> None:
        # Written to a temp file, then swapped in, so a crash mid-write
        # never leaves a half-written memory file.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def ensure_file(self) -> None:
        """Create data/memory.md from the template if it doesn't exist yet."""
        if not self.path.exists():
            try:
                self._write_lines(TEMPLATE.splitlines())
            except OSError as e:
                logger.warning("Could not create memory file %s: %s", self.path, e)

    @staticmethod
    def _facts(lines: list[str]) -> list[_Fact]:
        facts, section = [], None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("## "):
                section = stripped[3:].strip()
            elif section is not None and stripped.startswith("- ") and stripped[2:].strip():
                facts.append(_Fact(i, section, stripped[2:].strip()))
        return facts

    # --- prompt ----------------------------------------------------------

    def prompt_section(self) -> str:
        """The memory as sent to the model: everything from the first "##"
        heading on, minus empty sections."""
        lines = self._read_lines()
        facts = self._facts(lines)
        if not facts:
            return "You don't have any saved facts about anyone yet."
        by_section: dict[str, list[str]] = {}
        for fact in facts:
            by_section.setdefault(fact.section, []).append(fact.text)
        if len(facts) > self.max_facts:
            logger.warning("Memory has %d facts, over MEMORY_MAX_FACTS=%d - all are still sent to the model.",
                           len(facts), self.max_facts)
        return "\n\n".join(f"## {name}\n" + "\n".join(f"- {t}" for t in texts) for name, texts in by_section.items())

    # --- edits -----------------------------------------------------------

    def remember(self, fact: str, person: str = "") -> tuple[bool, str]:
        """Add `fact` under `person`'s section (the primary user's if empty),
        creating the section if needed. Returns (ok, message for the model)."""
        fact = " ".join(fact.split()).lstrip("- ").strip()
        if not fact:
            return False, "Nothing to remember - the fact was empty."
        with self._lock:
            lines = self._read_lines()
            facts = self._facts(lines)
            if any(f.text.lower() == fact.lower() for f in facts):
                return True, f"I already remember that: {fact}"
            if len(facts) >= self.max_facts:
                return False, (f"My memory is full ({self.max_facts} facts). Ask which fact to forget "
                               "(forget_fact) before saving a new one.")
            headings = [(i, line.strip()[3:].strip()) for i, line in enumerate(lines) if line.strip().startswith("## ")]
            target = None
            wanted = person.strip().lower()
            for i, heading in headings:
                is_primary = "primary user" in heading.lower()
                if (not wanted and is_primary) or (wanted and _person_name(heading) == wanted):
                    target = (i, heading)
                    break
            if target is None:
                heading = person.strip() if wanted else "Primary user"
                if lines and lines[-1].strip():
                    lines.append("")
                lines.append(f"## {heading}")
                insert_at = len(lines)
                section_name = heading
            else:
                start, section_name = target
                insert_at = start + 1
                for f in facts:
                    if f.section == section_name and f.line_index >= start:
                        insert_at = f.line_index + 1
            lines.insert(insert_at, f"- {fact}")
            try:
                self._write_lines(lines)
            except OSError as e:
                return False, f"Couldn't save to my memory file: {e}"
        return True, f"Saved to memory under '{section_name}': {fact}"

    def forget(self, fact: str) -> tuple[bool, str]:
        """Remove the one saved fact that best matches `fact`. Several
        equally good matches -> nothing removed, they're listed instead."""
        query = fact.strip().lower()
        if not query:
            return False, "Nothing to forget - say which fact."
        with self._lock:
            lines = self._read_lines()
            facts = self._facts(lines)
            matches = [f for f in facts if f.text.lower() == query]
            if not matches:
                matches = [f for f in facts if query in f.text.lower() or f.text.lower() in query]
            if not matches:
                # Every meaningful word of the request must be in the fact:
                # "I like cricket" finds "Likes cricket.", "likes football" doesn't.
                wanted = _words(query)
                matches = [f for f in facts if wanted and wanted <= _words(f.text)]
            if not matches:
                return False, "I couldn't find that in my memory, so nothing was removed."
            if len(matches) > 1:
                listed = "; ".join(f"'{f.text}' ({f.section})" for f in matches[:5])
                return False, f"Several saved facts match, so nothing was removed - which one? {listed}"
            removed = matches[0]
            del lines[removed.line_index]
            try:
                self._write_lines(lines)
            except OSError as e:
                return False, f"Couldn't update my memory file: {e}"
        return True, f"Forgot '{removed.text}' ({removed.section})."

    # --- search ----------------------------------------------------------

    def search(self, query: str) -> str:
        """Saved facts and past conversation lines that share words with
        `query`, best matches first (most recent first on a tie)."""
        wanted = _words(query)
        if not wanted:
            return "Say what to search for, with at least one real word in it."
        need = max(1, (len(wanted) + 1) // 2)

        fact_hits = []
        for fact in self._facts(self._read_lines()):
            score = len(wanted & _words(fact.text))
            if score >= need:
                fact_hits.append((score, f"- {fact.text} ({fact.section})"))
        fact_hits.sort(key=lambda h: -h[0])

        convo_hits = sorted(self._conversation_lines(wanted, need), key=lambda h: (h[0], h[1]), reverse=True)

        parts = []
        if fact_hits:
            parts.append("Saved facts:\n" + "\n".join(h[1] for h in fact_hits[:_MAX_FACT_HITS]))
        if convo_hits:
            parts.append("Past conversations:\n" + "\n".join(h[2] for h in convo_hits[:_MAX_CONVERSATION_HITS]))
        if not parts:
            return f"Nothing in my memory or past conversations matches '{query}'."
        return "\n\n".join(parts)

    def _conversation_lines(self, wanted: set[str], need: int):
        """(score, timestamp, "[when] who: text") for each matching thing
        said - the human's words and Cozmo's `say` text, nothing else."""
        if self.history_dir is None:
            return
        for path in run_files(self.history_dir):
            for record in read_records(path):
                if record.get("type") != "message":
                    continue
                message = record.get("message") or {}
                for who, text in _spoken(message):
                    score = len(wanted & _words(text))
                    if score >= need:
                        ts = record.get("ts", "")
                        yield score, ts, f"[{_when(ts)}] {who}: {_snippet(text)}"


def _spoken(message: dict) -> list[tuple[str, str]]:
    if message.get("role") == "user":
        text = str(message.get("content", "")).split("\n\n[Status:")[0].strip()
        # Engine-made captions ("[Cozmo just looked around...]") aren't speech.
        return [] if not text or text.startswith("[") else [("human", text)]
    if message.get("role") == "assistant":
        said = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, str):  # some providers hand arguments over as a JSON string
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = None
            if fn.get("name") == "say" and isinstance(args, dict) and args.get("text"):
                said.append(("Cozmo", str(args["text"])))
        return said
    return []


def _when(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ts or "unknown time"


def _snippet(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _MAX_SNIPPET_CHARS else text[: _MAX_SNIPPET_CHARS - 3] + "..."
