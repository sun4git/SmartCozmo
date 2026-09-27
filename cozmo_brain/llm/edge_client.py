"""Microsoft Edge's online text-to-speech, via the unofficial `edge-tts`
library - free, no API key, but needs internet (an unofficial, reverse-
engineered endpoint, not a published/supported Microsoft product; can
break if Microsoft changes something internally). TTS ONLY: edge-tts has
no speech-to-text counterpart, so this only ever satisfies
SpeechClient.synthesize(); transcribe() raises if actually called (an
STT_PROVIDER=edge config mistake, not a real code path).

edge-tts's own service only ever returns MP3 - confirmed against its
source (communicate.py): the output format sent to Microsoft's endpoint is
hardcoded to "audio-24khz-48kbitrate-mono-mp3", not configurable at all.
tts_postprocess.py's pitch/gain/ring-mod pipeline is built on Python's
stdlib `wave` module, which can't read MP3 - so this decodes MP3 -> raw
PCM -> a WAV container via PyAV (the `av` package) before handing audio
off to it. PyAV ships FFmpeg bundled in its own wheel (no system `ffmpeg`
install needed) and is already a dependency of faster-whisper
(STT_PROVIDER=local), so it's often already installed for free.

Measured directly (not assumed): MP3->WAV decode itself is negligible
(~8ms for a ~4.6s clip, after one-time codec init) next to the ~1.1s
network+synthesis round-trip it follows - decoding is not the bottleneck.

Requires `pip install edge-tts av` (commented out in requirements.txt by
default, like every other optional provider dependency).
"""

from __future__ import annotations

import io
import wave

from cozmo_brain.config import Settings
from cozmo_brain.llm.tts_postprocess import apply_cozmo_voice_character


def _speed_to_rate(speed: float) -> str:
    """Convert our >1.0=faster speed convention (matching
    GROQ_TTS_SPEED/OPENAI_TTS_SPEED/LOCAL_TTS_SPEED) into edge-tts's own
    signed-percentage rate string - confirmed via edge_tts.Communicate's
    own signature: rate: str = "+0%"."""
    percent = round((speed - 1.0) * 100)
    return f"{percent:+d}%"


class EdgeTTSClient:
    def __init__(self, settings: Settings):
        self._settings = settings

    def transcribe(self, wav_path: str) -> str:
        raise NotImplementedError(
            "edge-tts has no speech-to-text API - "
            "set STT_PROVIDER to groq/openai/local/witai instead of edge."
        )

    def synthesize(self, text: str, out_path: str, voice: str | None = None) -> str:
        """Generate speech for `text`, apply voice effects + gain, and write a Cozmo-ready WAV to out_path."""
        import asyncio

        import edge_tts

        rate = _speed_to_rate(self._settings.edge_tts_speed)

        async def _stream_mp3() -> bytes:
            communicate = edge_tts.Communicate(text, voice or self._settings.edge_tts_voice, rate=rate)
            mp3_bytes = bytearray()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    mp3_bytes.extend(chunk["data"])
            return bytes(mp3_bytes)

        mp3_bytes = asyncio.run(_stream_mp3())
        wav_bytes = self._mp3_to_wav(mp3_bytes)

        audio_bytes = apply_cozmo_voice_character(wav_bytes, self._settings)
        with open(out_path, "wb") as f:
            f.write(audio_bytes)
        return out_path

    @staticmethod
    def _mp3_to_wav(mp3_bytes: bytes) -> bytes:
        import av

        container = av.open(io.BytesIO(mp3_bytes))
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=stream.rate)

        pcm_chunks = []
        for frame in container.decode(stream):
            for rframe in resampler.resample(frame):
                pcm_chunks.append(rframe.to_ndarray().tobytes())
        pcm = b"".join(pcm_chunks)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(stream.rate)
            w.writeframes(pcm)
        return buf.getvalue()
