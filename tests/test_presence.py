"""Test: presence check - head tilt, silent for known/empty, one casual ask for a stranger."""
import dataclasses
import logging
import os
import sys
import threading
import time
from types import SimpleNamespace

import _harness  # noqa: F401 - repo on sys.path, real .env ignored
from cozmo_brain.config import Settings
from cozmo_brain.idle_fidget import IdleFidgeter
from cozmo_brain.people import Presence
from cozmo_brain.presence import PresenceChecker
from cozmo_brain.robot.simulated import SimulatedRobot

logging.basicConfig(level=logging.WARNING)
failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


class R(SimulatedRobot):
    def __init__(self):
        super().__init__()
        self.heads, self.photo_existed = [], False

    def set_head_angle_deg(self, a, duration=0.4):
        self.heads.append(a)

    def capture_photo(self, path):
        super().capture_photo(path)
        self.photo_existed = os.path.exists(path)
        self.last_path = path
        return path


class E:
    last_interaction_monotonic = 0.0
    listening_window_open = False

    def __init__(self):
        self.turn_lock = threading.Lock()
        self.spoken, self.notes = [], []

    def speak(self, text, mood="neutral"):
        self.spoken.append(text)
        return True

    def add_note(self, text):
        self.notes.append(text)


class Chat:
    """Fake vision model: `face` answers the face-visible question, `who` is
    the one reference name that matches (or None)."""

    def __init__(self, face, who):
        self.face, self.who = face, who

    def chat(self, messages):
        prompt = messages[0]["content"]
        if "face clearly visible" in prompt:
            return SimpleNamespace(content="yes" if self.face else "no")
        return SimpleNamespace(content="yes" if self.who and f"named {self.who}." in prompt else "no")


def make(face, who, people=("anna",)):
    known = _harness.tmp("presence_people")
    os.makedirs(known, exist_ok=True)
    for f in os.listdir(known):
        os.remove(os.path.join(known, f))
    for n in people:
        SimulatedRobot().capture_photo(os.path.join(known, f"{n}.jpg"))
    s = dataclasses.replace(
        Settings(),
        presence_check_enabled=True,
        known_people_dir=known,
        camera_snapshot_path=os.path.join(known, "..", "presence_look.png"),
        idle_fidget_after_s=0,
    )
    r, e, p = R(), E(), Presence()
    return r, e, p, PresenceChecker(r, e, Chat(face, who), s, p), s


# known person: silent, name reaches the prompt, head tilted then lowered, photo deleted
r, e, p, c, s = make(True, "anna")
check("due before first look", c.due())
c.run()
check("known: no speech", e.spoken == [] and e.notes == [])
check("known: name in prompt section", "anna" in p.prompt_section())
check("head tilted to PRESENCE_HEAD_ANGLE_DEG then back to 0", r.heads == [35.0, 0.0])
check("photo existed during look, deleted after", r.photo_existed and not os.path.exists(r.last_path))
check("not due again straight away", not c.due())

# nobody there: silent, clears previous person
r, e, p, c, s = make(False, None)
p.set_person("anna")
c.run()
check("empty: silent and prompt cleared", e.spoken == [] and p.prompt_section() == "")

# stranger: one casual line + a note naming the known faces, then cooldown
r, e, p, c, s = make(True, None)
c.run()
check("stranger: one line spoken", len(e.spoken) == 1)
check("stranger: note tells the model to use remember_person", "remember_person" in e.notes[0] and "anna" in e.notes[0])
check("stranger: prompt says not the primary user", "don't recognise" in p.prompt_section())
check("stranger: no second ask within cooldown", not c.due())

# nobody enrolled yet: still asks, without 'where is' small talk
r, e, p, c, s = make(True, None, people=())
c.run()
check("no known people: asks who they are", len(e.spoken) == 1 and "where" not in e.notes[0])

# picked up: no photo
r, e, p, c, s = make(True, None)
r.is_picked_up = lambda: True
c.run()
check("picked up: no photo taken", r.heads == [] and e.spoken == [])

# vision error: swallowed, retried later
r, e, p, c, s = make(True, None)
c._chat = SimpleNamespace(chat=lambda m: (_ for _ in ()).throw(RuntimeError("boom")))
c.run()
check("vision error: swallowed, head lowered again", r.heads[-1] == 0.0 and e.spoken == [] and not c.due())

# disabled: never due
r, e, p, c, s = make(True, None)
c._settings = dataclasses.replace(s, presence_check_enabled=False)
check("disabled: never due", not c.due())

# via the fidgeter: replaces the gesture, and does nothing while the turn lock is held
r, e, p, c, s = make(True, "anna")
gestures = []
r.run_gesture = lambda name, wheels=True: gestures.append(name)
f = IdleFidgeter(r, e, s, c)
e.turn_lock.acquire()
f._check_once()
check("turn lock held: no look, no gesture", p.prompt_section() == "" and gestures == [])
e.turn_lock.release()
f._last_fidget_monotonic = 0
f._check_once()
check("idle: look happens instead of a gesture", "anna" in p.prompt_section() and gestures == [])
f._last_fidget_monotonic = 0
f._check_once()
check("next idle cycle: ordinary gesture again", len(gestures) == 1)

# fidget disabled but presence on: look still happens, no gesture
r, e, p, c, s = make(True, "anna")
s2 = dataclasses.replace(s, idle_fidget_enabled=False)
gestures = []
r.run_gesture = lambda name, wheels=True: gestures.append(name)
f = IdleFidgeter(r, e, s2, c)
f._last_fidget_monotonic = 0
f._check_once()
f._last_fidget_monotonic = 0
f._check_once()
check("fidget off: look still runs, no gestures", "anna" in p.prompt_section() and gestures == [])

print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
