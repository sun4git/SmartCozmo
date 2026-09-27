"""Filters known STT hallucination artifacts out of transcribed text.

Originally Whisper-specific (both Groq's and OpenAI's hosted versions):
Whisper doesn't reliably return empty text on silence/near-silence/
background-noise audio - it hallucinates a plausible sentence from its
training data instead (a well-documented failure mode, largely traced to
its training data being dominated by transcribed YouTube/news captions).
VAD-side gating (cozmo_brain/audio/vad.py's onset debounce) filters most
of this out before it ever reaches Whisper, but can't catch every case in
a genuinely noisy real environment - confirmed on real hardware: the same
handful of exact phrases below recurred verbatim across many separate
capture windows in a quiet room with no real speech attempted, which is
the expected fingerprint of this failure mode (similar noise/silence
profiles tend to decode to the same memorized caption each time), not
random hallucinated variation.

Now also covers at least one non-Whisper artifact - Wit.ai
(STT_PROVIDER=witai) has its own, differently-caused fallback text (see
"hey facebook" below) - kept in this same list/function rather than a
parallel one, since every call site here just wants "is this text
probably not real speech" regardless of which provider produced it; each
entry's own comment says which provider/mechanism it's actually from.

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
# The bare "thank you"/"thanks"/"you" entries are a known, deliberate
# trade-off: confirmed recurring on real hardware as the dominant
# hallucinated text on Groq specifically (shorter than the full "...for
# watching" phrase), but unlike the other entries here, a real user might
# genuinely say one of these to Cozmo (in isolation, as a full utterance -
# not as part of a longer real sentence, since matching is exact). Accepted
# the small risk of an occasional real one-word reply being silently
# ignored (low stakes - not a real command) over the alternative of these
# being the most common false activations in practice.
_KNOWN_HALLUCINATIONS_RAW = {
    "thank you for watching",
    "thanks for watching",
    "thank you",
    "thanks",
    "thank you so much",
    "thank you very much",
    "you",
    "please subscribe",
    "please subscribe to my channel",
    "don't forget to subscribe",
    "like and subscribe",
    "시청 해주셔서 감사합니다",
    "시청해주셔서 감사합니다",
    "구독과 좋아요 부탁드립니다",
    "MBC 뉴스 이덕영입니다",
    # Wit.ai (STT_PROVIDER=witai), not Whisper - confirmed recurring on
    # real hardware and separately during verification (cozmo_brain/llm/
    # witai_client.py's own docstring): the exact same phrase, verbatim,
    # on unrelated audio. Looks like a low-confidence fallback specific to
    # a fresh, untrained Wit.ai app rather than genuine transcription -
    # different root cause than Whisper's YouTube-caption hallucinations
    # above, same practical fix.
    "hey facebook",
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
    """Whether `text` exactly matches a known STT hallucination/fallback
    phrase (Whisper or otherwise), after normalizing away case/
    punctuation/whitespace differences."""
    return _normalize(text) in _KNOWN_HALLUCINATIONS
