from cozmo_brain.config import Settings
from cozmo_brain.llm.chat_client import ChatClient
from cozmo_brain.llm.ollama_client import ChatResponse, OllamaClient, ToolCall
from cozmo_brain.llm.speech_client import SpeechClient


def _build_speech_provider(provider: str, settings: Settings) -> SpeechClient:
    provider = provider.lower()

    if provider == "groq":
        from cozmo_brain.llm.groq_client import GroqClient

        return GroqClient(settings)

    if provider == "openai":
        from cozmo_brain.llm.openai_client import OpenAIClient

        return OpenAIClient(settings)

    if provider == "local":
        from cozmo_brain.llm.local_client import LocalClient

        return LocalClient(settings)

    raise ValueError(f"Unknown provider '{provider}'. Use 'groq', 'openai', or 'local'.")


class _SpeechRouter:
    """Dispatches transcribe() to STT_PROVIDER's client and synthesize() to
    TTS_PROVIDER's client, so the two can be configured independently while
    the rest of the app still sees one SpeechClient (see speech_client.py)."""

    def __init__(self, stt_client: SpeechClient, tts_client: SpeechClient):
        self._stt_client = stt_client
        self._tts_client = tts_client

    def transcribe(self, wav_path: str) -> str:
        return self._stt_client.transcribe(wav_path)

    def synthesize(self, text: str, out_path: str, voice: str | None = None) -> str:
        return self._tts_client.synthesize(text, out_path, voice)


def create_speech_client(settings: Settings) -> SpeechClient:
    stt_provider = settings.stt_provider.lower()
    tts_provider = settings.tts_provider.lower()

    stt_client = _build_speech_provider(stt_provider, settings)
    tts_client = stt_client if tts_provider == stt_provider else _build_speech_provider(tts_provider, settings)

    return _SpeechRouter(stt_client, tts_client)


def create_chat_client(settings: Settings) -> ChatClient:
    provider = settings.chat_provider.lower()

    if provider == "ollama":
        return OllamaClient(settings)

    raise ValueError(f"Unknown CHAT_PROVIDER '{provider}'. Only 'ollama' is implemented today.")


__all__ = [
    "OllamaClient",
    "ChatResponse",
    "ToolCall",
    "SpeechClient",
    "ChatClient",
    "create_speech_client",
    "create_chat_client",
]
