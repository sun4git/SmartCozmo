"""Face helpers shared by the `who_is_this` tool and presence.py's idle check:
the reference photos in KNOWN_PEOPLE_DIR and the vision-LLM yes/no questions
asked about them.

There's no face-recognition library here - recognition is the vision model
comparing two blurry photos, so every answer is a guess (and each check sends
the photos to whichever VISION_PROVIDER is configured).
"""

from __future__ import annotations

import os
import threading

from cozmo_brain.imaging import encode_image_b64
from cozmo_brain.llm.chat_client import ChatClient

KNOWN_PHOTO_EXTS = (".jpg", ".jpeg", ".png")

COMPARE_FACES_PROMPT = (
    "You are comparing two photos taken by a low-resolution, often blurry robot "
    "camera with a warm/reddish color cast — ignore lighting and color entirely "
    "and judge only facial structure. The first photo is a reference photo of a "
    "person named {name}. The second photo was just taken. Is the person in the "
    "second photo the same person as in the first photo? Reply with exactly one "
    "word: yes or no."
)

FACE_VISIBLE_PROMPT = (
    "This photo was taken by a low-resolution, often blurry robot camera with a "
    "warm/reddish color cast. Is there a real person's face clearly visible in it "
    "(not a poster, screen, drawing or toy)? Reply with exactly one word: yes or no."
)


def known_people(known_dir: str) -> list[tuple[str, str]]:
    """(name, photo path) for every reference photo, sorted by filename."""
    if not os.path.isdir(known_dir):
        return []
    return [
        (os.path.splitext(f)[0], os.path.join(known_dir, f))
        for f in sorted(os.listdir(known_dir))
        if f.lower().endswith(KNOWN_PHOTO_EXTS)
    ]


def _says_yes(response) -> bool:
    return response.content.strip().lower().startswith("yes")


def face_visible(chat: ChatClient, photo_b64: str) -> bool:
    return _says_yes(chat.chat([{"role": "user", "content": FACE_VISIBLE_PROMPT, "images": [photo_b64]}]))


def find_match(chat: ChatClient, people: list[tuple[str, str]], photo_b64: str) -> str | None:
    """The first known person the model says is in the photo, else None."""
    for name, path in people:
        prompt = COMPARE_FACES_PROMPT.format(name=name)
        response = chat.chat([{"role": "user", "content": prompt, "images": [encode_image_b64(path), photo_b64]}])
        if _says_yes(response):
            return name
    return None


class Presence:
    """Who Cozmo last saw in front of him, shared with the system prompt."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._name: str | None = None
        self._stranger = False

    def set_person(self, name: str) -> None:
        with self._lock:
            self._name, self._stranger = name, False

    def set_stranger(self) -> None:
        with self._lock:
            self._name, self._stranger = None, True

    def clear(self) -> None:
        with self._lock:
            self._name, self._stranger = None, False

    def prompt_section(self) -> str:
        with self._lock:
            name, stranger = self._name, self._stranger
        if name:
            return (
                f"[Your camera last saw {name} in front of you - a guess from a blurry photo. "
                f"Use their name naturally, but if they say they're someone else, believe them.]"
            )
        if stranger:
            return (
                "[Your camera last saw a face you don't recognise in front of you - so don't "
                "assume this is the primary user.]"
            )
        return ""
