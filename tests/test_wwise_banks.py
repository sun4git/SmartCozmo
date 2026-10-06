"""Test: the Wwise sound-bank reader (cozmo_brain/wwise_banks.py) on a tiny bank
built in memory - events -> play actions -> containers -> sound files, random
weights, sequence order, embedded media, and the consistency rules it relies on.
The real Cozmo.bnk is not in the repo (copyrighted), so nothing here needs it."""
import random
import struct
import sys
import tempfile
from pathlib import Path

import _harness  # noqa: F401 - repo on sys.path

from cozmo_brain.wwise_banks import (ACTION, ACTOR_MIXER, EVENT, RANDOM_SEQUENCE, SOUND, SWITCH,
                                     load_bank, pick)

failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


def obj(kind, oid, body):
    return struct.pack("<BII", kind, len(body) + 4, oid) + body


def sound(oid, file_id, stream):
    return obj(SOUND, oid, struct.pack("<IBI", 0x00040001, stream, file_id) + b"\x00" * 20)


def action(oid, kind, target):
    return obj(ACTION, oid, struct.pack("<HIB", kind, target, 0) + b"\x00" * 5)


def event(oid, action_ids):
    return obj(EVENT, oid, struct.pack("<I", len(action_ids)) + struct.pack(f"<{len(action_ids)}I", *action_ids))


def rand_seq(oid, children, weights, mode):
    """prefix (stand-in for the variable node parameters), mode, flags, children, playlist."""
    body = b"\x07" * 13 + bytes([mode, 0x00])
    body += struct.pack("<I", len(children)) + struct.pack(f"<{len(children)}I", *children)
    body += struct.pack("<H", len(children)) + b"".join(struct.pack("<Ii", c, w) for c, w in zip(children, weights))
    return obj(RANDOM_SEQUENCE, oid, body)


def plain_container(kind, oid, children):
    body = b"\x03" * 21 + struct.pack("<I", len(children)) + struct.pack(f"<{len(children)}I", *children)
    return obj(kind, oid, body)


def chunk(tag, payload):
    return tag + struct.pack("<I", len(payload)) + payload


def build_bank(path):
    media = b"RIFF-fake-wem-AAAA" + b"RIFF-fake-wem-BB"
    didx = struct.pack("<III", 900, 0, 18) + struct.pack("<III", 901, 18, 16)
    objs = b"".join([
        sound(10, 900, 0),                   # embedded
        sound(11, 901, 0),                   # embedded
        sound(12, 5000, 1),                  # streamed
        sound(13, 5001, 1),                  # streamed
        sound(14, 5002, 1),                  # streamed
        rand_seq(20, [10, 11, 12], [100, 100, 200], mode=0),    # random, weighted 25/25/50
        rand_seq(21, [13, 14], [50, 50], mode=1),               # sequence: 13 then 14
        plain_container(SWITCH, 22, [20, 21]),                  # switch over both
        plain_container(ACTOR_MIXER, 23, [12]),
        action(30, 0x0403, 20),              # play the random container
        action(31, 0x0403, 21),              # play the sequence
        action(32, 0x0102, 20),              # a STOP action - must be ignored
        action(33, 0x0403, 22),              # play the switch
        action(34, 0x0403, 10),              # play a sound directly
        event(40, [30]), event(41, [31]), event(42, [32]), event(43, [33]), event(44, [34]), event(45, [30, 31]),
    ])
    hirc = struct.pack("<I", 20) + objs
    data = (chunk(b"BKHD", struct.pack("<II", 120, 777) + b"\x00" * 24) + chunk(b"DIDX", didx)
            + chunk(b"DATA", media) + chunk(b"HIRC", hirc))
    Path(path).write_bytes(data)


tmp = Path(tempfile.gettempdir()) / "smartcozmo_test_bank.bnk"
build_bank(tmp)
bank = load_bank(tmp)

check("bank version and id read", bank.version == 120 and bank.bank_id == 777)
check("sounds, containers and events found", len(bank.sounds) == 5 and len(bank.containers) == 4 and len(bank.events) == 6)
check("stream type and file id read", bank.sounds[10].stream == 0 and bank.sounds[10].file_id == 900
      and bank.sounds[12].stream == 1 and bank.sounds[12].file_id == 5000)

rand = bank.containers[20]
check("random container: children and playlist weights found", rand.children == [10, 11, 12]
      and rand.playlist == [(10, 100), (11, 100), (12, 200)])
check("mode byte read (0 random, 1 sequence)", rand.mode == 0 and bank.containers[21].mode == 1)
check("switch / actor-mixer children found without a playlist",
      bank.containers[22].children == [20, 21] and bank.containers[23].children == [12])

ev = {s.file_id: p for s, p in bank.event_sounds(40)}
check("event -> play action -> random container: each file with its weight share",
      ev == {900: 0.25, 901: 0.25, 5000: 0.5})
check("a STOP action leads nowhere", bank.event_sounds(42) == [] and bank.play_targets(42) == [])
check("sequence container: all sounds, in order",
      [s.file_id for s, _ in bank.event_sounds(41)] == [5001, 5002])
check("a sound played directly", [s.file_id for s, _ in bank.event_sounds(44)] == [900])
sw = {s.file_id for s, _ in bank.event_sounds(43)}
check("switch container: every sound under it", sw == {900, 901, 5000, 5001, 5002})
check("an event with several play actions plays all their targets",
      {s.file_id for s, _ in bank.event_sounds(45)} == {900, 901, 5000, 5001, 5002})
check("unknown event: nothing", bank.event_sounds(999) == [])

check("embedded file bytes come out of the DATA chunk", bank.media_bytes(900) == b"RIFF-fake-wem-AAAA"
      and bank.media_bytes(901) == b"RIFF-fake-wem-BB" and bank.media_bytes(5000) is None)

# the same sounds are not each other's parent (every sound has at most one parent here)
parents = {}
for c in bank.containers.values():
    for ch in c.children:
        parents.setdefault(ch, []).append(c.id)
check("random container 20 is under the switch only", parents[20] == [22])

# pick(): chosen by weight
rng = random.Random(1)
counts = {}
for _ in range(2000):
    s = pick(bank.event_sounds(40), rng)
    counts[s.file_id] = counts.get(s.file_id, 0) + 1
check(f"pick() follows the weights (got {counts})", 400 < counts[900] < 600 and 400 < counts[901] < 600 and 900 < counts[5000] < 1100)
check("pick() of nothing is None", pick([]) is None)

print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
