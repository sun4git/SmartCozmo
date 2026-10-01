"""Test: suggested memory facts - the after-conversation check
(memory_suggestions.py: skipped for tiny conversations, only new lines,
retried on failure, nothing re-suggested), the Suggested/Declined sections
(never sent as known facts, not counted, not searched), review_suggestion
keep/discard/later, the prompt telling Cozmo when to ask, and the engine's
session events."""
import json
import logging
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

import _harness  # noqa: F401 - repo on sys.path, real .env ignored
import fake_engine as fe

from cozmo_brain.conversation import Conversation
from cozmo_brain.history_archive import HistoryArchive
from cozmo_brain.memory import DECLINED, SUGGESTED, Memory
from cozmo_brain.memory_suggestions import SuggestionExtractor, parse_facts
from cozmo_brain.personality import build_system_prompt

logging.disable(logging.CRITICAL)
failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: failures.append(name)

root = Path(tempfile.mkdtemp(prefix="smartcozmo_sugg_"))

# --- parsing the model's reply ---------------------------------------------------
check("parse: plain JSON", parse_facts('{"facts": [{"fact": "Has a dog named Bruno.", "person": ""}]}')
      == [("Has a dog named Bruno.", "")])
check("parse: code fences and chatter around it",
      parse_facts('Sure!\n```json\n{"facts": [{"fact": "Likes tea.", "person": "Asha"}]}\n```') == [("Likes tea.", "Asha")])
check("parse: empty list -> nothing", parse_facts('{"facts": []}') == [])
check("parse: garbage -> nothing", parse_facts("I think nothing here.") == [] and parse_facts("{oops") == [])
check("parse: at most 3 per check", len(parse_facts(json.dumps({"facts": [{"fact": f"F{i}."} for i in range(6)]}))) == 3)

# --- the check ---------------------------------------------------------------------
class Chat:
    def __init__(self, replies): self.replies, self.prompts = list(replies), []
    def chat(self, messages, tools=None):
        self.prompts.append(messages[0]["content"])
        r = self.replies.pop(0)
        if isinstance(r, Exception): raise r
        return NS(content=r, tool_calls=[])

mem_path = root / "memory.md"
memory = Memory(mem_path, root / "history", max_facts=3)
memory.ensure_file()
memory.remember("Likes cricket.")
archive = HistoryArchive(root / "history")
conv = Conversation("sys", archive=archive)

chat = Chat([])
ex = SuggestionExtractor(chat, memory, archive)
check("no archive yet -> nothing, no LLM call", ex.check() == [] and not chat.prompts)
conv.add_user("spin around")
check("a tiny conversation (a command) -> no LLM call", ex.check() == [] and not chat.prompts)

conv.add_user("my dog Bruno chewed my slippers again\n\n[Status: I'm off my charger right now.]")
conv.add_assistant("", tool_calls=[{"id": "1", "function": {"name": "say", "arguments": {"text": "Bad Bruno!"}}}])
chat.replies = ['{"facts": [{"fact": "Has a dog named Bruno.", "person": ""}, '
                '{"fact": "Likes cricket", "person": ""}]}']
added = ex.check()
check("a meaningful line -> one LLM call, the new fact suggested", added == ["Has a dog named Bruno."] and len(chat.prompts) == 1)
check("an already-known fact isn't suggested", "Likes cricket" not in "".join(added))
prompt = chat.prompts[0]
check("the check sees only new lines, with the status note stripped",
      "Bruno chewed" in prompt and "spin around" not in prompt and "[Status" not in prompt)
check("the check sees what Cozmo said and what he already knows", "Cozmo: Bad Bruno!" in prompt and "Likes cricket." in prompt)
check("the check is told 'nothing' is the normal answer", "Returning no facts is the normal" in prompt)
text = mem_path.read_text()
check("suggestion written under the Suggested heading with today's date",
      f"## {SUGGESTED}" in text and "- Has a dog named Bruno. [20" in text)
check("nothing new said -> no second LLM call", ex.check() == [] and len(chat.prompts) == 1)

conv.add_user("my sister Asha is visiting from Pune next month")
chat.replies = [RuntimeError("provider down")]
check("a failed check suggests nothing", ex.check() == [])
chat.replies = ['{"facts": [{"fact": "Asha lives in Pune.", "person": "Asha"}]}']
check("...and the next check retries those lines", ex.check() == ["Asha lives in Pune."] and "Asha is visiting" in chat.prompts[-1])
check("a fact about someone else is tagged with their name", "- Asha lives in Pune. [about Asha, 20" in mem_path.read_text())

# --- not known, not counted, not searched --------------------------------------
section = memory.prompt_section()
check("suggestions are NOT in what Cozmo knows", "Bruno" not in section and "Suggested" not in section)
check("suggestions don't count toward MEMORY_MAX_FACTS", memory.remember("Has a cat.")[0])
check("search doesn't return unconfirmed suggestions as facts", "Saved facts" not in memory.search("Bruno dog"))
check("forget_fact doesn't touch suggestions", not memory.forget("Bruno")[0])

# --- what Cozmo is told ------------------------------------------------------------
sp = memory.suggestions_prompt()
check("waiting suggestions listed, newest first, with who they're about",
      sp.index("Asha lives in Pune") < sp.index("Has a dog named Bruno") and "about Asha" in sp and "about the primary user" in sp)
check("told to ask only when it fits, one per conversation at most", "fits naturally" in sp and "At most one per conversation" in sp)
full = build_system_prompt(memory.prompt_section(), suggestions_section=sp)
check("system prompt marks them as NOT known facts", "NOT facts you know" in full and "Has a dog named Bruno" in full)
check("no suggestions -> no section in the prompt", "haven't confirmed" not in build_system_prompt("x"))

# --- review_suggestion -------------------------------------------------------------
ok, msg = memory.review("Bruno", "later")
check("later: nothing moves", ok and "Has a dog named Bruno. [" in mem_path.read_text())
check("after asking once, told not to raise another this conversation", "already asked" in memory.suggestions_prompt())
memory.new_session()
check("a new conversation may ask again", "fits naturally" in memory.suggestions_prompt())
check("bad decision word refused", not memory.review("Bruno", "maybe")[0])
check("no matching suggestion -> error", not memory.review("unicorn", "keep")[0])

memory.remember("Plays chess.")                              # 3 of 3 facts now
ok, msg = memory.review("Has a dog named Bruno.", "keep")
check("keep: refused while memory is full, suggestion still waiting",
      not ok and "full" in msg and "Has a dog named Bruno. [" in mem_path.read_text())
memory.forget("cat")
ok, msg = memory.review("dog named Bruno", "keep")
lines = mem_path.read_text()
check("keep: moved into the primary user's section as a real fact",
      ok and "- Has a dog named Bruno." in lines and "Has a dog named Bruno. [" not in lines
      and "Has a dog named Bruno." in memory.prompt_section())
ok, msg = memory.review("Pune", "discard")
lines = mem_path.read_text()
check("discard: moved to Declined, out of Suggested",
      ok and f"## {DECLINED}" in lines and lines.index(f"## {DECLINED}") < lines.index("Asha lives in Pune")
      and not memory.suggestions())
check("a declined fact is never suggested again",
      memory.add_suggestions([("Asha lives in Pune.", "Asha"), ("asha lives in pune", "")], "2026-10-02") == [])
check("a known fact is never suggested again", memory.add_suggestions([("Has a dog named Bruno", "")], "2026-10-02") == [])
sp = memory.suggestions_prompt()
check("nothing waiting, but Cozmo is told how many are declined (not what) and how to clear them",
      "1 declined suggestion" in sp and "Pune" not in sp and "clear_suggestions" in sp)

# --- clear_suggestions ---------------------------------------------------------------
memory.add_suggestions([("Plays the flute.", ""), ("Drinks chai daily.", "")], "2026-10-02")
memory.review("flute", "discard")
check("bad 'which' refused", not memory.clear_suggestions("everything")[0])
before = mem_path.read_text()
ok, msg = memory.clear_suggestions("declined")
after = mem_path.read_text()
check("clear declined: says how many, removes the section, keeps waiting ones and facts",
      ok and "2 declined" in msg and f"## {DECLINED}" not in after and "Drinks chai daily." in after
      and "- Has a dog named Bruno." in after)
check("a cleared declined fact may be suggested again",
      memory.add_suggestions([("Asha lives in Pune.", "Asha")], "2026-10-03") == ["Asha lives in Pune."])
ok, msg = memory.clear_suggestions("waiting")
check("clear waiting: both waiting ones gone, facts untouched",
      ok and "2 waiting" in msg and f"## {SUGGESTED}" not in mem_path.read_text() and "Plays chess." in mem_path.read_text())
check("clearing an empty list is fine", memory.clear_suggestions("both") == (True, "Cleared 0 waiting suggestion(s) and 0 declined suggestion(s)."))
check("nothing waiting or declined -> nothing to tell Cozmo", memory.suggestions_prompt() == "")

# --- engine session events ---------------------------------------------------------
engine, _robot, _chat = fe.make("sync", [])
engine.conversation = Conversation("sys", archive=HistoryArchive(root / "h2"))
seen = []
engine.session_listeners.append(lambda e, f: seen.append((e, f)))
engine.session_listeners.append(lambda e, f: 1 / 0)          # a broken listener
engine.session_event("session_start", trigger="tap")
engine.session_event("session_end")
check("session events reach listeners, even with a broken one alongside",
      seen == [("session_start", {"trigger": "tap"}), ("session_end", {})])
check("session events are marked in the archive",
      [json.loads(l)["type"] for l in engine.conversation.archive.path.read_text().splitlines()]
      == ["run_start", "session_start", "session_end"])

sys.exit(1 if failures else 0)
