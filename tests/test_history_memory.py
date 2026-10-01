"""Test: the conversation archive (data/history/<date>/<run>.jsonl, photos
next to it, sessions, resume, old-file import, pruning), the memory file
(remember/forget/cap/hand edits/prompt) and search_memory, and the engine
wiring: every message archived, system prompt rebuilt each turn, memory
tools callable by the model."""
import base64
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import _harness  # noqa: F401 - repo on sys.path, real .env ignored
import fake_engine as fe

from cozmo_brain import history_archive as ha
from cozmo_brain.conversation import Conversation
from cozmo_brain.memory import Memory
from cozmo_brain.personality import build_system_prompt
from cozmo_brain.tools import build_tools

logging.disable(logging.CRITICAL)
failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: failures.append(name)

PNG = b"\x89PNG\r\n\x1a\nfakepng"
JPG = b"\xff\xd8\xff\xe0fakejpg"
tmp_root = Path(tempfile.mkdtemp(prefix="smartcozmo_hist_"))

# --- archive: writing ----------------------------------------------------------
hist = tmp_root / "history"
a = ha.HistoryArchive(hist, {"mode": "vad"})
check("no file until something is recorded", not a.path.exists())
a.record_event("run_end")
check("run_end alone leaves no file (a run that recorded nothing)", not a.path.exists())
check("file is history/<date>/<HH-MM-SS>.jsonl",
      a.path.parent.name == datetime.now().strftime("%Y-%m-%d") and len(a.path.stem) == 8)

conv = Conversation("sys", archive=a)
conv.mark("session_start", trigger="wake word")
conv.add_user("hello\n\n[Status: I'm off my charger right now.]")
conv.add_assistant("", tool_calls=[{"id": "1", "function": {"name": "say", "arguments": {"text": "Hi there!", "mood": "happy"}}}])
conv.add_tool_result("say", "OK: said it", tool_call_id="1")
conv.add_user("[Cozmo just looked around and captured a photo of what's in front of him.]",
              images=[base64.b64encode(PNG).decode()])
src = tmp_root / "snap.jpg"; src.write_bytes(JPG)
a.record_photo(str(src))
conv.mark("session_end")
a.record_event("run_end")
records = list(ha.read_records(a.path))
types = [r["type"] for r in records]
check("records in order: run_start, session, 4 messages, photo, session_end, run_end",
      types == ["run_start", "session_start", "message", "message", "message", "message", "photo", "session_end", "run_end"])
check("run_start carries run info", records[0].get("mode") == "vad")
check("every record has a timestamp", all(r.get("ts") for r in records))
check("session_start carries the trigger", records[1].get("trigger") == "wake word")
img_rec = records[5]
check("attached photo saved next to the transcript as .png, referenced by name",
      img_rec.get("images") == [f"{a.path.stem}-photo-01.png"] and (a.path.parent / img_rec["images"][0]).read_bytes() == PNG)
check("no base64 image data in the transcript", "images" not in img_rec["message"] and "fakepng" not in a.path.read_text())
check("vision-off photo copied next to the transcript (.jpg)",
      records[6].get("file") == f"{a.path.stem}-photo-02.jpg" and (a.path.parent / records[6]["file"]).read_bytes() == JPG)
check("the in-memory conversation still has the image for the model", conv.messages[-1].get("images"))
with a.path.open("a", encoding="utf-8") as f:
    f.write('{"ts": "2026-10-01T10:00:00", "type": "mess')   # torn last line (power cut)
check("a torn last line is skipped, not fatal", len(list(ha.read_records(a.path))) == len(records))

# --- archive: resume -----------------------------------------------------------
old_day = hist / "2026-09-30"; old_day.mkdir(parents=True)
with (old_day / "21-00-00.jsonl").open("w", encoding="utf-8") as f:
    for i in range(6):
        f.write(json.dumps({"ts": "2026-09-30T21:00:00", "type": "message", "message": {"role": "user", "content": f"old {i}"}}) + "\n")
b = ha.HistoryArchive(hist)       # a new run
recent = b.recent_messages(5)
check("resume takes the last N messages across earlier runs, oldest first",
      [m["content"] for m in recent][-1].startswith("[Cozmo just looked") and len(recent) == 5)
check("resume reaches back into an older day's run when needed",
      any(m["content"] == "old 5" for m in b.recent_messages(20)))
check("resumed photo messages carry a note instead of the image",
      "[photo:" in recent[-1]["content"] and "images" not in recent[-1])
conv2 = Conversation("NEW PROMPT", max_messages=6, archive=b)
check("Conversation.load() resumes from the archive", conv2.load())
check("resumed conversation keeps the CURRENT system prompt, not a saved one",
      conv2.messages[0] == {"role": "system", "content": "NEW PROMPT"})
check("resumed conversation starts at a user message", conv2.messages[1]["role"] == "user")
check("nothing to resume -> load() is False", not Conversation("s", archive=ha.HistoryArchive(tmp_root / "empty")).load())

# --- archive: legacy import, pruning ------------------------------------------
legacy_root = tmp_root / "legacy"; legacy_root.mkdir()
legacy = legacy_root / "conversation_history.json"
legacy.write_text(json.dumps([{"role": "system", "content": "old prompt"}, {"role": "user", "content": "from before"},
                              {"role": "user", "content": "pic", "images": [base64.b64encode(JPG).decode()]}]))
os.utime(legacy, (time.mktime((2026, 9, 1, 8, 30, 0, 0, 0, -1)),) * 2)
lh = tmp_root / "lhist"
check("old conversation_history.json is imported", ha.HistoryArchive(lh).import_legacy(legacy, legacy_root / "data"))
imported = ha.run_files(lh)
check("imported as its own run, dated by the old file", [p.relative_to(lh).as_posix() for p in imported] == ["2026-09-01/08-30-00-imported.jsonl"])
imp = list(ha.read_records(imported[0]))
check("import skips the old system prompt and keeps the old time",
      [r["message"]["content"] for r in imp if r["type"] == "message"] == ["from before", "pic"]
      and all(r["ts"].startswith("2026-09-01T08:30") for r in imp))
check("imported photos saved as files", any(r.get("images") for r in imp))
check("old file moved into data/ so it's imported once",
      not legacy.exists() and (legacy_root / "data" / "conversation_history.imported.json").exists())
check("no import when the archive already has runs", not ha.HistoryArchive(hist).import_legacy(legacy, legacy_root))

ph = tmp_root / "prune"
for d in ("2020-01-01", "2026-09-30", datetime.now().strftime("%Y-%m-%d"), "notes"):
    (ph / d).mkdir(parents=True)
check("retention 0 keeps everything", ha.prune_history(ph, 0) == 0 and (ph / "2020-01-01").exists())
ha.prune_history(ph, 7)
check("retention deletes only old YYYY-MM-DD folders",
      not (ph / "2020-01-01").exists() and (ph / datetime.now().strftime("%Y-%m-%d")).exists() and (ph / "notes").exists())

# --- memory --------------------------------------------------------------------
mem_path = tmp_root / "data" / "memory.md"
m = Memory(mem_path, hist, max_facts=4)
check("prompt with no facts says so", "don't have any saved facts" in m.prompt_section())
m.ensure_file()
check("ensure_file creates memory.md from the template", mem_path.exists() and "## Primary user" in mem_path.read_text())
ok, msg = m.remember("Likes cricket.")
check("remember: primary user by default", ok and "Primary user" in msg)
ok, _ = m.remember("Asha's dad.", person="")
ok2, msg2 = m.remember("Loves the dance gesture.", person="Asha")
check("remember: a named person gets their own section", ok2 and "## Asha" in mem_path.read_text())
check("remember: a duplicate isn't saved twice", m.remember("likes cricket.")[1].startswith("I already remember"))
m.remember("Has a cat.")
ok, msg = m.remember("One too many.")
check("remember: refused at MEMORY_MAX_FACTS, asks to forget first", not ok and "full" in msg)
text = mem_path.read_text()
check("facts land under their own section, in order",
      text.index("Likes cricket.") < text.index("Has a cat.") < text.index("## Asha") < text.index("Loves the dance"))
section = m.prompt_section()
check("prompt section has the facts but not the human notes above them",
      "- Likes cricket." in section and "Edit freely" not in section and section.startswith("## Primary user"))
mem_path.write_text(text.replace("## Primary user", "## Suneel (primary user)") + "- Hand-added fact.\n")
check("hand edits show up in the prompt without a restart",
      "## Suneel (primary user)" in m.prompt_section() and "Hand-added fact." in m.prompt_section())
check("remember by the primary user's name goes to their section",
      Memory(mem_path, max_facts=99).remember("Prefers tea.", person="suneel")[1].startswith("Saved to memory under 'Suneel (primary user)'"))
check("forget: no match -> nothing removed", not m.forget("likes football")[0])
ok, msg = m.forget("I like cricket")
check("forget: natural wording ('I like cricket') finds 'Likes cricket.'",
      ok and "Likes cricket." in msg and "cricket" not in mem_path.read_text())
ok, msg = m.forget("cat")
check("forget: a partial match removes that one fact", ok and "Has a cat." in msg and "cat." not in mem_path.read_text())
mem_path.write_text(mem_path.read_text() + "- Likes red cars.\n- Likes red apples.\n")
ok, msg = m.forget("likes red")
check("forget: ambiguous -> nothing removed, both listed", not ok and "red cars" in msg and "red apples" in msg
      and "red cars" in mem_path.read_text())

# --- search_memory ---------------------------------------------------------------
day = hist / "2026-09-29"; day.mkdir()
with (day / "18-00-00.jsonl").open("w", encoding="utf-8") as f:
    f.write(json.dumps({"ts": "2026-09-29T18:05:00+05:30", "type": "message",
                        "message": {"role": "user", "content": "I'm going to Goa next week\n\n[Status: I'm on my charger and charging.]"}}) + "\n")
    f.write(json.dumps({"ts": "2026-09-29T18:05:03+05:30", "type": "message",
                        "message": {"role": "assistant", "content": "", "tool_calls": [
                            {"id": "x", "function": {"name": "say", "arguments": json.dumps({"text": "Bring me back some Goa sand!"})}}]}}) + "\n")
result = m.search("Goa trip")
check("search finds the human's words with date and time", "[2026-09-29 18:05] human: I'm going to Goa next week" in result)
check("search finds Cozmo's say text (arguments as a JSON string)", "Cozmo: Bring me back some Goa sand!" in result)
check("search strips the engine's status notes", "Status" not in result)
check("search finds saved facts too", "Saved facts:" in m.search("dance") and "Loves the dance gesture." in m.search("dance"))
check("search with no match says so", m.search("submarine").startswith("Nothing in my memory"))
check("search ignores stopword-only queries", m.search("what did").startswith("Say what to search for"))

# --- engine wiring ---------------------------------------------------------------
eh = tmp_root / "engine_hist"
em = Memory(tmp_root / "engine_mem.md", eh)
earch = ha.HistoryArchive(eh)
engine, robot, chat = fe.make("sync", [
    fe.NS(content="", tool_calls=[fe.call("remember_fact", fact="Likes jalebi."), fe.call("say", text="Noted!")]),
])
engine.conversation = Conversation("sys", archive=earch)
s = engine._settings
tools = build_tools(robot, fe.Speech(), chat, s, memory=em)
engine._tools, engine._tools_by_name = tools, {t.name: t for t in tools}
engine._system_prompt_fn = lambda: build_system_prompt(em.prompt_section())
check("memory tools are offered to the model", {"remember_fact", "forget_fact", "search_memory"} <= set(engine._tools_by_name))
check("without memory, build_tools adds no memory tools",
      "remember_fact" not in {t.name for t in build_tools(robot, fe.Speech(), chat, s)})
engine.handle_turn("Remember I like jalebi")
check("the model's remember_fact call saved the fact", "- Likes jalebi." in (tmp_root / "engine_mem.md").read_text())
prompt = engine.conversation.messages[0]["content"]
check("system prompt was rebuilt for the turn (date + memory section)",
      prompt.startswith(engine.conversation.messages[0]["content"][:20]) and "right now." in prompt and "What you remember" in prompt)
engine.handle_turn("And now?")
check("next turn's prompt includes the fact saved last turn", "Likes jalebi." in engine.conversation.messages[0]["content"])
erecs = [r for r in ha.read_records(earch.path) if r["type"] == "message"]
roles = [r["message"]["role"] for r in erecs]
check("every message of the turn was archived (user, assistant, both tool results)",
      roles[:4] == ["user", "assistant", "tool", "tool"])
check("the system prompt is not archived", "system" not in roles)
engine.add_note("I asked if you wanted me to go charge.")
check("unprompted notes are archived too",
      list(ha.read_records(earch.path))[-1]["message"]["content"] == "I asked if you wanted me to go charge.")

sys.exit(1 if failures else 0)
