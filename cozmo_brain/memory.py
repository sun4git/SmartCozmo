"""Cozmo's long-term memory: a few important facts about the people he
talks to, in data/memory.md - plain markdown, meant to be edited by hand
too:

    ## Alex (primary user)
    - Likes cricket.

    ## Maya
    - Alex's daughter. Loves the dance gesture.

One fact per "- " line, under a "## <name>" heading. The section whose
heading says "primary user" is who Cozmo assumes he's talking to - he can't
tell voices apart. The whole file is re-read every turn and sent to the
chat model as part of the system prompt, so hand edits apply on the next
turn, no restart. Text above the first "##" heading is notes for humans and
is never sent.

The model edits it through the remember_fact/forget_fact tools, and
search() looks through these facts and the conversation archive
(history_archive.py) for the search_memory tool.

Two more sections hold facts nobody has confirmed yet - never sent as
things Cozmo knows:

    ## Suggested (not yet approved)
    - Has a dog named Bruno. [2026-10-01]
    - Loves the dance gesture. [about Maya, 2026-10-01]

    ## Declined suggestions
    - Is tired today. [2026-09-30]

memory_suggestions.py adds to the first after a conversation, when it
heard something worth keeping. Cozmo asks about one when it fits the
conversation (review_suggestion): keep moves it into the person's section,
discard moves it to Declined (so it's never suggested again). Moving or
deleting lines by hand works too. Suggestions don't expire.
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

"Suggested (not yet approved)" below holds things Cozmo noticed but you
haven't confirmed - never treated as known. Move a line up into a section
(dropping its [...] tag) to keep it, or delete it.

## Primary user
"""

_WORD = re.compile(r"[a-z0-9']+")
# Too common to say anything about what a search is for.
_STOPWORDS = frozenset(
    "the and for you your are was were what when where who how did does that this with have has "
    "about from they them then than but not can could would should just any all our out his her "
    "him she".split()
)
SUGGESTED = "Suggested (not yet approved)"
DECLINED = "Declined suggestions"
_SPECIAL_SECTIONS = {SUGGESTED.lower(), DECLINED.lower()}
# A suggestion line: "<fact> [2026-10-01]" or "<fact> [about Maya, 2026-10-01]".
_SUGGESTION = re.compile(r"^(?P<fact>.*?)\s*\[(?:about (?P<person>[^,\]]+), )?(?P<date>\d{4}-\d{2}-\d{2})\]$")
# Most waiting suggestions shown to the model at once (newest first).
_MAX_SUGGESTIONS_SHOWN = 5

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
    """'Alex (primary user)' -> 'alex'."""
    return re.sub(r"\(\s*primary user\s*\)", "", heading, flags=re.IGNORECASE).strip().lower()


@dataclass
class _Fact:
    line_index: int
    section: str
    text: str

    @property
    def special(self) -> bool:
        return self.section.lower() in _SPECIAL_SECTIONS


@dataclass
class Suggestion:
    fact: str
    person: str  # "" = the primary user
    date: str
    declined: bool = False


def _parse_suggestion(f: _Fact) -> Suggestion:
    m = _SUGGESTION.match(f.text)
    if not m:  # hand-written without the [date] tag
        return Suggestion(f.text, "", "", f.section.lower() == DECLINED.lower())
    return Suggestion(m["fact"], (m["person"] or "").strip(), m["date"], f.section.lower() == DECLINED.lower())


def _best_matches(items: list, text_of, query: str) -> list:
    """The items whose text matches `query`: exact, else one containing the
    other, else every meaningful word of the query in it ("I like cricket"
    finds "Likes cricket.", "likes football" doesn't)."""
    query = query.strip().lower()
    matches = [i for i in items if text_of(i).lower() == query]
    if not matches:
        matches = [i for i in items if query in text_of(i).lower() or text_of(i).lower() in query]
    if not matches:
        wanted = _words(query)
        matches = [i for i in items if wanted and wanted <= _words(text_of(i))]
    return matches


class Memory:
    def __init__(self, path: str | Path, history_dir: str | Path | None = None, max_facts: int = 40):
        self.path = Path(path)
        self.history_dir = Path(history_dir) if history_dir else None
        self.max_facts = max_facts
        self._lock = threading.Lock()
        # Whether Cozmo already brought up a suggestion this conversation
        # (review_suggestion) - he's then told not to raise another one.
        # Reset by new_session() when a conversation starts.
        self.asked_this_session = False

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
    def _all_lines_facts(lines: list[str]) -> list[_Fact]:
        facts, section = [], None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("## "):
                section = stripped[3:].strip()
            elif section is not None and stripped.startswith("- ") and stripped[2:].strip():
                facts.append(_Fact(i, section, stripped[2:].strip()))
        return facts

    @classmethod
    def _facts(cls, lines: list[str]) -> list[_Fact]:
        """Confirmed facts only - not the Suggested/Declined sections."""
        return [f for f in cls._all_lines_facts(lines) if not f.special]

    def new_session(self) -> None:
        self.asked_this_session = False

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
            matches = _best_matches(self._facts(lines), lambda f: f.text, query)
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

    # --- suggestions -----------------------------------------------------

    def suggestions(self, include_declined: bool = False) -> list[Suggestion]:
        """Waiting suggestions, oldest first (and declined ones, if asked)."""
        out = []
        for f in self._all_lines_facts(self._read_lines()):
            if f.special and (include_declined or f.section.lower() == SUGGESTED.lower()):
                out.append(_parse_suggestion(f))
        return out

    def add_suggestions(self, items: list[tuple[str, str]], date: str) -> list[str]:
        """Add (fact, person) pairs to the Suggested section, skipping any
        already known, waiting, or declined. Returns the facts added."""
        added: list[str] = []
        with self._lock:
            lines = self._read_lines()
            seen = [f.text for f in self._facts(lines)] + [s.fact for s in self.suggestions(include_declined=True)]
            new_lines = []
            for fact, person in items:
                fact = " ".join(str(fact).split()).lstrip("- ").strip()
                person = " ".join(str(person or "").split())
                # Already known, waiting, or declined - in the same or nearly the same words.
                if not fact or _best_matches(seen, lambda t: t, fact):
                    continue
                tag = f"[about {person}, {date}]" if person and not self._is_primary(lines, person) else f"[{date}]"
                new_lines.append(f"- {fact} {tag}")
                seen.append(fact)
                added.append(fact)
            if not new_lines:
                return []
            start = self._section_start(lines, SUGGESTED)
            if start is None:
                if lines and lines[-1].strip():
                    lines.append("")
                lines.append(f"## {SUGGESTED}")
                start = len(lines) - 1
            insert_at = self._section_end(lines, start)
            lines[insert_at:insert_at] = new_lines
            try:
                self._write_lines(lines)
            except OSError as e:
                logger.warning("Could not save memory suggestions: %s", e)
                return []
        return added

    def review(self, suggestion: str, decision: str) -> tuple[bool, str]:
        """keep -> into the person's section (counts against max_facts);
        discard -> into Declined, never suggested again; later -> unchanged."""
        decision = decision.strip().lower()
        if decision not in ("keep", "discard", "later"):
            return False, "decision must be keep, discard, or later."
        self.asked_this_session = True
        with self._lock:
            lines = self._read_lines()
            waiting = [f for f in self._all_lines_facts(lines) if f.section.lower() == SUGGESTED.lower()]
            matches = _best_matches(waiting, lambda f: _parse_suggestion(f).fact, suggestion)
            if not matches:
                return False, "No waiting suggestion matches that."
            if len(matches) > 1:
                listed = "; ".join(f"'{_parse_suggestion(f).fact}'" for f in matches[:5])
                return False, f"Several waiting suggestions match - which one? {listed}"
            item, parsed = matches[0], _parse_suggestion(matches[0])
            if decision == "later":
                return True, f"Left '{parsed.fact}' waiting - don't bring it up again this conversation."
            if decision == "discard":
                del lines[item.line_index]
                start = self._section_start(lines, DECLINED)
                if start is None:
                    if lines and lines[-1].strip():
                        lines.append("")
                    lines.append(f"## {DECLINED}")
                    start = len(lines) - 1
                lines.insert(self._section_end(lines, start), f"- {item.text}")
                try:
                    self._write_lines(lines)
                except OSError as e:
                    return False, f"Couldn't update my memory file: {e}"
                return True, f"Won't remember '{parsed.fact}', and won't suggest it again."
            if len(self._facts(lines)) >= self.max_facts:
                return False, (f"My memory is full ({self.max_facts} facts) - the suggestion is still waiting. "
                               "Ask which fact to forget first.")
            del lines[item.line_index]
            try:
                self._write_lines(lines)
            except OSError as e:
                return False, f"Couldn't update my memory file: {e}"
        ok, message = self.remember(parsed.fact, parsed.person)
        return ok, message

    def clear_suggestions(self, which: str) -> tuple[bool, str]:
        """Empty the waiting list, the declined list, or both - removing the
        section and its heading. Declined ones may then be suggested again."""
        which = which.strip().lower()
        targets = {"waiting": [SUGGESTED], "declined": [DECLINED], "both": [SUGGESTED, DECLINED]}.get(which)
        if targets is None:
            return False, "which must be waiting, declined, or both."
        removed = {}
        with self._lock:
            lines = self._read_lines()
            for name in targets:
                start = self._section_start(lines, name)
                if start is None:
                    removed[name] = 0
                    continue
                end = start + 1
                while end < len(lines) and not lines[end].strip().startswith("## "):
                    end += 1
                removed[name] = sum(1 for line in lines[start + 1:end] if line.strip().startswith("- "))
                del lines[start:end]
            while lines and not lines[-1].strip():
                lines.pop()
            try:
                self._write_lines(lines)
            except OSError as e:
                return False, f"Couldn't update my memory file: {e}"
        parts = [f"{n} {'waiting' if name == SUGGESTED else 'declined'} suggestion(s)" for name, n in removed.items()]
        return True, "Cleared " + " and ".join(parts) + "."

    def suggestions_prompt(self) -> str:
        """The waiting suggestions as shown to the model (plus how many are
        declined, so the lists can be cleared by asking), or "" if neither."""
        waiting = self.suggestions()
        n_declined = sum(1 for s in self.suggestions(include_declined=True) if s.declined)
        declined_line = (f"(Also {n_declined} declined suggestion(s), not shown - never suggested again "
                         "unless cleared.)") if n_declined else ""
        clear_hint = (" If they ask to clear these lists, use `clear_suggestions` - say how many first "
                      "and check they mean it.")
        if not waiting:
            return declined_line + clear_hint if declined_line else ""
        shown = waiting[-_MAX_SUGGESTIONS_SHOWN:][::-1]
        lines = [f"- {s.fact} ({'about ' + s.person if s.person else 'about the primary user'}, "
                 f"noticed {s.date or 'earlier'})" for s in shown]
        if len(waiting) > len(shown):
            lines.append(f"(and {len(waiting) - len(shown)} older ones)")
        if self.asked_this_session:
            when = ("You already asked about one this conversation - don't bring them up again unless "
                    "they ask what you wanted to remember.")
        else:
            when = ("When it fits naturally - the conversation touches on it, or there's a relaxed "
                    "moment, never in the middle of a request or when they're busy - you may ask about "
                    "ONE of these, in your own words (\"Last time you mentioned a dog called Bruno - want "
                    "me to remember that?\"). At most one per conversation, and most conversations "
                    "shouldn't have one at all.")
        if declined_line:
            lines.append(declined_line)
        return "\n".join(lines) + "\n" + when + (
            " Record their answer with `review_suggestion`: keep, discard, or later (not now). If they "
            "ask what you wanted to remember, go through them.") + clear_hint

    def _is_primary(self, lines: list[str], person: str) -> bool:
        wanted = person.strip().lower()
        return any("primary user" in h.lower() and _person_name(h) == wanted
                   for h in (l.strip()[3:] for l in lines if l.strip().startswith("## ")))

    @staticmethod
    def _section_start(lines: list[str], name: str) -> int | None:
        for i, line in enumerate(lines):
            if line.strip().lower() == f"## {name}".lower():
                return i
        return None

    @staticmethod
    def _section_end(lines: list[str], start: int) -> int:
        """Where to insert a new line at the end of the section at `start`:
        after its last "- " line (or right after the heading)."""
        end = start + 1
        for i in range(start + 1, len(lines)):
            stripped = lines[i].strip()
            if stripped.startswith("## "):
                break
            if stripped.startswith("- "):
                end = i + 1
        return end

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
                for who, text in spoken_lines(message):
                    score = len(wanted & _words(text))
                    if score >= need:
                        ts = record.get("ts", "")
                        yield score, ts, f"[{_when(ts)}] {who}: {_snippet(text)}"


def spoken_lines(message: dict) -> list[tuple[str, str]]:
    """(who, text) for what a message actually said out loud: the human's
    words, or Cozmo's `say` text - nothing else."""
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
