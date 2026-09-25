"""Filters known Whisper hallucination artifacts out of transcribed text.

Whisper (both Groq's and OpenAI's hosted versions) doesn't reliably return
empty text on silence/near-silence/background-noise audio - it hallucinates
a plausible sentence from its training data instead (a well-documented
failure mode, largely traced to its training data being dominated by
transcribed YouTube/news captions). VAD-side gating
(cozmo_brain/audio/vad.py's onset debounce) filters most of this out before
it ever reaches Whisper, but can't catch every case in a genuinely noisy
real environment - confirmed on real hardware: the same handful of exact
phrases below recurred verbatim across many separate capture windows in a
quiet room with no real speech attempted, which is the expected fingerprint
of this failure mode (similar noise/silence profiles tend to decode to the
same memorized caption each time), not random hallucinated variation.

Matching is exact (after normalizing case/punctuation/whitespace), not
fuzzy or substring-based, specifically so this can never misfire on real
intended speech that happens to contain similar words.
"""

from __future__ import annotations

import re
import unicodedata

# Sourced from widely-reported Whisper hallucination behavior, plus what was
# actually observed recurring on real hardware. This list is necessarily
# incomplete - it's a pragmatic backstop for known repeat offenders, not a
# general hallucination detector. Extend it as new recurring phrases show up.
#
# The bare "thank you"/"thanks" entries are a known, deliberate trade-off:
# confirmed recurring on real hardware as the dominant hallucinated text on
# Groq specifically (shorter than the full "...for watching" phrase), but
# unlike the other entries here, a real user might genuinely say this to
# Cozmo. Accepted the small risk of an occasional real "thank you" being
# silently ignored (low stakes - not a real command) over the alternative
# of this being the single most common false activation in practice.
_KNOWN_HALLUCINATIONS_RAW = {
    "thank you for watching",
    "thanks for watching",
    "thank you",
    "thanks",
    "thank you so much",
    "thank you very much",
    "please subscribe",
    "please subscribe to my channel",
    "don't forget to subscribe",
    "like and subscribe",
    "시청 해주셔서 감사합니다",
    "시청해주셔서 감사합니다",
    "구독과 좋아요 부탁드립니다",
    "MBC 뉴스 이덕영입니다",
}

_STRIP_RE = re.compile(r"[!.?,~\s]+")


def _normalize(text: str) -> str:
    # NFKC first: Hangul (and other combining-mark scripts) can come back
    # precomposed or decomposed depending on the API/library layer - visually
    # identical text otherwise fails a plain string comparison (confirmed:
    # decomposed "MBC 뉴스 이덕영입니다" has nearly double the codepoints of
    # the precomposed form typed directly into this file's source).
    text = unicodedata.normalize("NFKC", text)
    return _STRIP_RE.sub("", text.strip().lower())


_KNOWN_HALLUCINATIONS = {_normalize(phrase) for phrase in _KNOWN_HALLUCINATIONS_RAW}


def is_likely_hallucination(text: str) -> bool:
    """Whether `text` exactly matches a known Whisper hallucination phrase,
    after normalizing away case/punctuation/whitespace differences."""
    return _normalize(text) in _KNOWN_HALLUCINATIONS
