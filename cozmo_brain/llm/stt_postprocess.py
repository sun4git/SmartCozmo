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

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

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
    punctuation/whitespace differences - or contains no letters or digits
    at all. Confirmed live 2026-09-28: Groq's Whisper returns a bare "."
    for clatter and motor hum, which used to reach the LLM as if the human
    had said it (`if not text` only catches the empty string)."""
    if not any(ch.isalnum() for ch in text):
        return True
    return _normalize(text) in _KNOWN_HALLUCINATIONS


def filter_whisper_segments(segments: list[dict], settings) -> str:
    """Join the text of the segments Whisper itself is reasonably sure are
    speech, dropping the rest - and log every segment's scores at INFO, so
    thresholds can be tuned from real audio instead of guessed.

    Each segment is a dict with `text` and, where the provider reports
    them, `no_speech_prob`, `avg_logprob`, `compression_ratio` (Groq and
    OpenAI `verbose_json`; faster-whisper's segment objects, converted by
    local_client.py). A segment is dropped if ANY of:
      - no_speech_prob > STT_NO_SPEECH_PROB: Whisper's own "this was
        silence/noise" estimate. Measured live: OpenAI whisper-1 gives
        0.93-0.97 on noise vs 0.00 on speech. Groq always reports 0.00, so
        this check simply never fires there.
      - avg_logprob < STT_MIN_AVG_LOGPROB: low decoding confidence. The only
        usable signal on Groq (speech ~-0.15 vs noise -0.57..-0.97 on clean
        test audio); the default -1.0 is Whisper's own, deliberately loose
        until real mic logs show where speech actually scores.
      - compression_ratio > STT_MAX_COMPRESSION_RATIO: a highly repetitive
        loop ("thank you thank you thank you..."), Whisper's classic
        runaway-decoding failure.
    Missing values never cause a drop, so providers/models that don't
    report them (or report partial data) fall back to plain text."""
    kept: list[str] = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        nsp = seg.get("no_speech_prob")
        lp = seg.get("avg_logprob")
        cr = seg.get("compression_ratio")
        reason = None
        if nsp is not None and nsp > settings.stt_no_speech_prob:
            reason = f"no_speech_prob {nsp:.2f} > {settings.stt_no_speech_prob}"
        elif lp is not None and lp < settings.stt_min_avg_logprob:
            reason = f"avg_logprob {lp:.2f} < {settings.stt_min_avg_logprob}"
        elif cr is not None and cr > settings.stt_max_compression_ratio:
            reason = f"compression_ratio {cr:.2f} > {settings.stt_max_compression_ratio}"
        logger.info(
            "STT segment %s (no_speech=%s logprob=%s compression=%s): %r%s",
            "DROPPED" if reason else "kept",
            _fmt(nsp), _fmt(lp), _fmt(cr), text,
            f" - {reason}" if reason else "",
        )
        if not reason and text:
            kept.append(text)
    return " ".join(kept).strip()


def whisper_request_fields(model: str, settings) -> dict:
    """Extra form fields for a Groq/OpenAI transcription request: the
    language (if STT_LANGUAGE is set), and verbose_json - which is what
    returns the per-segment scores above - but only for Whisper models.
    OpenAI's newer non-Whisper models (e.g. gpt-4o-transcribe) reject
    verbose_json, so they get plain text, as before."""
    fields: dict = {}
    if settings.stt_language:
        fields["language"] = settings.stt_language
    if "whisper" in model.lower():
        fields["response_format"] = "verbose_json"
    return fields


def text_from_whisper_response(body: dict, settings) -> str:
    """The transcript from a Groq/OpenAI response: segment-filtered when the
    response carries segments (verbose_json), plain `text` otherwise."""
    segments = body.get("segments")
    if isinstance(segments, list) and segments:
        return filter_whisper_segments(segments, settings)
    return (body.get("text") or "").strip()


def _fmt(value) -> str:
    return "n/a" if value is None else f"{value:.2f}"
