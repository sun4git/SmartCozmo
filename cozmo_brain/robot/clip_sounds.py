"""Cozmo's own sounds for the real animation clips.

PyCozmo plays clips silent: a clip's audio keyframes are Wwise event IDs that it
skips. standalone/extract_clip_sounds.py recovers the sounds those events play
(from the Cozmo app's sound banks) as small WAV files plus a manifest; this
module mixes them into the audio of a clip - at the moment each is meant to
play, together with the speech if there is any - as the OutputAudio packets
PyCozmo sends to the robot.

Not on the robot and not in the repo by default: the sounds are Anki/DDL's
(see the extraction script). Without data/clip_sounds/manifest.json nothing is
loaded and clips stay silent, as before.
"""

from __future__ import annotations

import json
import logging
import random
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pycozmo

from cozmo_brain.robot import clips

logger = logging.getLogger(__name__)

_SOUND_RATE = 22050  # what extract_clip_sounds.py writes (PyCozmo plays 22050 or 48000)
_FRAME_SAMPLES = 744  # samples in one OutputAudio packet at 22050 Hz (1488 at 48000)
_PEAK = 0.98  # the mix is scaled down to this if it would clip

MODES = ("off", "effects", "full")


# --- mu-law frames ---------------------------------------------------------------

def ulaw_encode(pcm: np.ndarray) -> np.ndarray:
    """PyCozmo's u_law_encoding() (pycozmo/audio.py) on a whole array of int16
    samples. Checked identical to it for every 16-bit value (tests/test_clip_sounds.py),
    except that its one overflow (a full-scale negative sample, which makes the
    original raise) is clamped to 255."""
    s = pcm.astype(np.int32)
    sign = np.where(s < 0, 0x80, 0)
    a = np.minimum(np.abs(s) + 132, 0x7FFF)
    position = np.floor(np.log2(a)).astype(np.int32)  # highest set bit; 7..14 once the bias is added
    lsb = (a >> (position - 4)) & 0x0F
    code = sign | ((position - 7) << 4) | lsb
    return np.minimum(code + 1, 255).astype(np.uint8)


def packets_from_pcm(pcm: np.ndarray, rate: int) -> list:
    """OutputAudio packets for mono 16-bit samples at 22050 or 48000 Hz - the
    same frames pycozmo.audio.load_wav() makes from a WAV (48000 Hz is
    decimated to 24000 by taking every second sample, as it does). The last
    frame is padded with silence."""
    if rate not in (22050, 48000):
        raise ValueError(f"unsupported rate {rate}")
    if rate == 48000:
        pcm = pcm[0::2]
    silence = int(ulaw_encode(np.zeros(1, dtype=np.int16))[0])
    coded = ulaw_encode(pcm)
    pad = (-len(coded)) % _FRAME_SAMPLES
    if pad:
        coded = np.concatenate([coded, np.full(pad, silence, dtype=np.uint8)])
    return [
        pycozmo.protocol_encoder.OutputAudio(samples=bytes(coded[i:i + _FRAME_SAMPLES]))
        for i in range(0, len(coded), _FRAME_SAMPLES)
    ]


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    """A WAV as float samples in [-1, 1] (first channel if stereo) and its rate."""
    with wave.open(str(path)) as w:
        if w.getsampwidth() != 2:
            raise ValueError("only 16-bit WAVs are supported")
        channels, rate = w.getnchannels(), w.getframerate()
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return (x[0::channels] if channels > 1 else x), rate


def _resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst or len(x) == 0:
        return x
    n = int(round(len(x) * dst / src))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


# --- the sounds ------------------------------------------------------------------

@dataclass(frozen=True)
class _Event:
    t_ms: int
    event: str
    volume: float


class ClipSounds:
    """The recovered sounds: which a clip plays and when, and the mixing."""

    def __init__(self, directory: Path, manifest: dict, mode: str, volume: float,
                 rng: random.Random | None = None):
        self._dir = directory
        self._events = manifest["events"]
        self._clips = {
            clip: [_Event(int(e["t_ms"]), str(e["event"]), float(e.get("volume", 1.0))) for e in evs]
            for clip, evs in manifest["clips"].items()
        }
        self.mode = mode
        self.volume = volume
        self._rng = rng or random.Random()
        self._cache: dict[tuple[str, int], np.ndarray] = {}

    @classmethod
    def load(cls, directory: str | Path, mode: str, volume: float) -> "ClipSounds | None":
        """None (clips stay silent) when switched off or the sounds aren't there."""
        mode = (mode or "off").strip().lower()
        if mode not in MODES:
            logger.warning("CLIP_SOUNDS=%r is not one of %s - clip sounds are off.", mode, ", ".join(MODES))
            return None
        if mode == "off":
            return None
        directory = Path(directory)
        manifest_path = directory / "manifest.json"
        if not manifest_path.exists():
            logger.info("Clip sounds: no %s - clips stay silent (see standalone/extract_clip_sounds.py).", manifest_path)
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            sounds = cls(directory, manifest, mode, max(0.0, min(volume, 2.0)))
        except Exception as e:  # noqa: BLE001 - a broken manifest must never stop the robot starting
            logger.warning("Clip sounds: could not read %s (%s) - clips stay silent.", manifest_path, e)
            return None
        logger.info("Clip sounds: %d clips with sounds, mode '%s', volume %.2f.", len(sounds._clips), mode, sounds.volume)
        return sounds

    # ---- what plays ----------------------------------------------------------------
    @staticmethod
    def _is_effect(name: str) -> bool:
        """Screen blips and servo noises ('Robot_Sfx__...'), as opposed to his
        voice ('Robot_VO__...')."""
        n = name.lower()
        return "_sfx_" in n

    def has_sounds(self, clip: str) -> bool:
        return bool(self._clips.get(clip))

    def _wanted(self, clip: str, with_speech: bool) -> list[_Event]:
        events = self._clips.get(clip, [])
        # Alongside speech, the chosen mode decides; on its own (a start/end
        # animation, a standalone clip) there is nothing for his noises to talk over.
        if with_speech and self.mode == "effects":
            events = [e for e in events if self._is_effect(self._events[e.event]["name"])]
        return events

    def _sound(self, event: str, rate: int) -> np.ndarray | None:
        """One playback's choice among the event's variants (by their chance), at `rate`."""
        options = self._events.get(event, {}).get("sounds", [])
        if not options:
            return None
        pick = self._rng.choices(options, weights=[max(o["chance"], 1e-6) for o in options])[0]
        key = (pick["file"], rate)
        if key not in self._cache:
            x, src = read_wav(self._dir / pick["file"])
            self._cache[key] = _resample(x, src, rate)
        return self._cache[key]

    # ---- mixing -------------------------------------------------------------------
    def build_audio(self, clip: str, ppclip, speech: tuple[np.ndarray, int] | None = None) -> list | None:
        """OutputAudio packets: the speech (if given, from its first frame) with this
        clip's sounds mixed in at the moment each plays. None when there is nothing
        to add - the caller then plays the speech (or nothing) exactly as before."""
        mixed = self.mix(clip, ppclip, speech)
        return None if mixed is None else packets_from_pcm(*mixed)

    def mix(self, clip: str, ppclip, speech: tuple[np.ndarray, int] | None = None) -> tuple[np.ndarray, int] | None:
        """The mix as mono 16-bit samples and their rate (see build_audio)."""
        events = self._wanted(clip, with_speech=speech is not None)
        if not events:
            return None
        rate = speech[1] if speech is not None else _SOUND_RATE
        tick_map = clips.key_tick_map(ppclip)
        mix = speech[0].astype(np.float32).copy() if speech is not None else np.zeros(0, dtype=np.float32)
        added = 0
        for e in events:
            x = self._sound(e.event, rate)
            if x is None or not len(x):
                continue
            start = int(round(clips.seconds_at(tick_map, e.t_ms) * rate))
            end = start + len(x)
            if end > len(mix):
                mix = np.concatenate([mix, np.zeros(end - len(mix), dtype=np.float32)])
            mix[start:end] += x * (self.volume * e.volume)
            added += 1
        if not added:
            return None
        peak = float(np.abs(mix).max())
        if peak > _PEAK:
            mix *= _PEAK / peak
        return (mix * 32767).astype("<i2"), rate
