"""A small reader for Wwise sound banks (.bnk), enough to answer one question:
which sound file (.wem) does an event play?

Used offline by standalone/extract_clip_sounds.py to recover the original Cozmo
sounds that Anki's animation clips reference by event ID (PyCozmo skips them).
Not used while the robot runs.

Why not PyCozmo's own reader (pycozmo.audiokinetic.soundbank)? It only follows
an event straight to a sound file; 86 of the 96 events the curated clips use go
through Wwise containers (random / sequence / switch / actor-mixer), which it
skips.

Scope and honesty about the format: written against Cozmo's own Cozmo.bnk (bank
version 120) and checked by consistency tests (tests/test_wwise_banks.py: every
sound has at most one parent, every container's child list ends exactly where its
playlist ends, ...), not against Wwise's documentation. The part of each object
before its child list (Wwise's "node base parameters": effects, positioning,
RTPCs...) is variable-length and is NOT parsed; the child list is found by
searching for a count followed by that many IDs that all exist in the bank, which
is validated per object type (see _find_children).
"""

from __future__ import annotations

import random
import struct
from dataclasses import dataclass, field
from pathlib import Path

# HIRC object types (Wwise 2016, bank version 120)
SOUND = 2
ACTION = 3
EVENT = 4
RANDOM_SEQUENCE = 5
SWITCH = 6
ACTOR_MIXER = 7
BLEND = 9

ACTION_PLAY = 4  # high byte of the action type; scope is the low byte


@dataclass
class Sound:
    id: int
    stream: int  # 0 embedded in the bank's DATA chunk, 1 streamed .wem file, 2 prefetch
    file_id: int


@dataclass
class Container:
    id: int
    type: int
    children: list[int]
    # RANDOM_SEQUENCE only: (child id, weight) in play order, and the mode byte
    playlist: list[tuple[int, int]] = field(default_factory=list)
    mode: int | None = None  # 0 random, 1 sequence


@dataclass
class Bank:
    path: Path
    version: int
    bank_id: int
    data_offset: int
    media: dict[int, tuple[int, int]]  # file id -> (offset into DATA, length)
    sounds: dict[int, Sound]
    containers: dict[int, Container]
    events: dict[int, list[int]]  # event id -> action ids
    actions: dict[int, tuple[int, int]]  # action id -> (action type, target id)
    raw: bytes

    # ---- resolving an event to sound files --------------------------------
    def play_targets(self, event_id: int) -> list[int]:
        """Object IDs the event's Play actions point at."""
        out = []
        for action_id in self.events.get(event_id, []):
            kind, target = self.actions.get(action_id, (0, 0))
            if (kind >> 8) == ACTION_PLAY:
                out.append(target)
        return out

    def leaves(self, object_id: int, _seen: frozenset = frozenset()) -> list[tuple[Sound, float]]:
        """Every sound the object could play, each with the chance (0-1) that one
        play of the object picks it. Random containers pick by weight, sequence
        containers play in order (all, chance 1 each), switch / actor-mixer /
        blend containers: all children equally likely (we don't know which switch
        state the game would have set)."""
        if object_id in _seen:
            return []
        seen = _seen | {object_id}
        if object_id in self.sounds:
            return [(self.sounds[object_id], 1.0)]
        c = self.containers.get(object_id)
        if c is None:
            return []
        if c.type == RANDOM_SEQUENCE and c.mode == 1 and c.playlist:
            return [pair for child, _ in c.playlist for pair in self.leaves(child, seen)]
        weights = dict(c.playlist) if c.type == RANDOM_SEQUENCE and c.playlist else {}
        children = [ch for ch, _ in c.playlist] if weights else c.children
        total = sum(weights.get(ch, 1) or 1 for ch in children) or 1
        out: list[tuple[Sound, float]] = []
        for ch in children:
            share = (weights.get(ch, 1) or 1) / total
            out += [(s, p * share) for s, p in self.leaves(ch, seen)]
        return out

    def event_sounds(self, event_id: int) -> list[tuple[Sound, float]]:
        out: list[tuple[Sound, float]] = []
        for target in self.play_targets(event_id):
            out += self.leaves(target)
        return out

    def media_bytes(self, file_id: int) -> bytes | None:
        """An embedded file's bytes (a complete .wem), or None if it isn't embedded here."""
        entry = self.media.get(file_id)
        if entry is None:
            return None
        offset, length = entry
        start = self.data_offset + offset
        return self.raw[start:start + length]


def _find_children(body: bytes, known: set[int], own_id: int, rs: bool) -> tuple[list[int], list[tuple[int, int]]] | None:
    """Locate a container's child list inside its body: a u32 count n followed by n
    u32 IDs that all exist in the bank. For random/sequence containers the list is
    followed by a u16 playlist length m and m (id, weight) pairs, and the body ends
    exactly there - that alignment is required, which makes a false match
    practically impossible. Other types just take the last valid candidate."""
    n_bytes = len(body)
    best = None
    for i in range(0, n_bytes - 8 + 1):
        n = struct.unpack_from("<I", body, i)[0]
        if not 1 <= n <= 1000 or i + 4 + 4 * n > n_bytes:
            continue
        ids = list(struct.unpack_from(f"<{n}I", body, i + 4))
        if own_id in ids or any(c not in known for c in ids):
            continue
        end = i + 4 + 4 * n
        if rs:
            if end + 2 > n_bytes:
                continue
            m = struct.unpack_from("<H", body, end)[0]
            if end + 2 + 8 * m != n_bytes:
                continue
            pairs = [struct.unpack_from("<Ii", body, end + 2 + 8 * k) for k in range(m)]
            if any(pid not in ids for pid, _ in pairs):
                continue
            return ids, pairs
        best = (ids, [])
    return best


def load_bank(path: str | Path) -> Bank:
    path = Path(path)
    data = path.read_bytes()
    pos = 0
    chunks: dict[bytes, tuple[int, int]] = {}
    while pos + 8 <= len(data):
        tag = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        chunks[tag] = (pos + 8, size)
        pos += 8 + size

    version, bank_id = struct.unpack_from("<II", data, chunks[b"BKHD"][0])

    media: dict[int, tuple[int, int]] = {}
    if b"DIDX" in chunks:
        off, size = chunks[b"DIDX"]
        for k in range(size // 12):
            fid, o, ln = struct.unpack_from("<III", data, off + 12 * k)
            media[fid] = (o, ln)
    data_offset = chunks[b"DATA"][0] if b"DATA" in chunks else 0

    # pass 1: every HIRC object
    off, size = chunks[b"HIRC"]
    count = struct.unpack_from("<I", data, off)[0]
    p = off + 4
    objs: dict[int, tuple[int, bytes]] = {}
    for _ in range(count):
        t, ln, oid = struct.unpack_from("<BII", data, p)
        objs[oid] = (t, data[p + 9:p + 5 + ln])
        p += 5 + ln
    known = set(objs)

    sounds: dict[int, Sound] = {}
    containers: dict[int, Container] = {}
    events: dict[int, list[int]] = {}
    actions: dict[int, tuple[int, int]] = {}
    for oid, (t, body) in objs.items():
        if t == SOUND:
            # plugin u32, stream type u8, source (file) id u32 - the same reading
            # PyCozmo's reader uses, and it yields ids that match Cozmo.txt.
            _plugin, stream, file_id = struct.unpack_from("<IBI", body, 0)
            sounds[oid] = Sound(oid, stream, file_id)
        elif t == ACTION:
            kind, target = struct.unpack_from("<HI", body, 0)
            actions[oid] = (kind, target)
        elif t == EVENT:
            n = struct.unpack_from("<I", body, 0)[0]
            events[oid] = list(struct.unpack_from(f"<{n}I", body, 4))
        elif t in (RANDOM_SEQUENCE, SWITCH, ACTOR_MIXER, BLEND):
            found = _find_children(body, known, oid, rs=(t == RANDOM_SEQUENCE))
            if found is None:
                containers[oid] = Container(oid, t, [])
                continue
            children, playlist = found
            c = Container(oid, t, children, playlist)
            if t == RANDOM_SEQUENCE:
                # mode byte (0 random, 1 sequence) is the 3rd byte before the child count
                n_children = len(children)
                idx = len(body) - 2 - 8 * len(playlist) - 4 * n_children - 4
                c.mode = body[idx - 2]
            containers[oid] = c
    return Bank(path, version, bank_id, data_offset, media, sounds, containers, events, actions, data)


def pick(sounds_with_chance: list[tuple[Sound, float]], rng: random.Random | None = None) -> Sound | None:
    """One sound chosen the way a single play would (by the listed chances)."""
    if not sounds_with_chance:
        return None
    rng = rng or random
    r = rng.random() * sum(p for _, p in sounds_with_chance)
    for s, p in sounds_with_chance:
        r -= p
        if r <= 0:
            return s
    return sounds_with_chance[-1][0]
