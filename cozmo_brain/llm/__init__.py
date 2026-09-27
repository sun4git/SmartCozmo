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

    if provider == "witai":
        from cozmo_brain.llm.witai_client import WitAIClient

        return WitAIClient(settings)

    raise ValueError(f"Unknown provider '{provider}'. Use 'groq', 'openai', 'local', or 'witai'.")


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


def _build_chat_provider(provider: str, settings: Settings) -> ChatClient:
    provider = provider.lower()

    if provider == "ollama":
        return OllamaClient(settings)

    if provider == "groq":
        from cozmo_brain.llm.groq_client import GroqChatClient

        return GroqChatClient(settings)

    if provider == "openai":
        from cozmo_brain.llm.openai_client import OpenAIChatClient

        return OpenAIChatClient(settings)

    raise ValueError(f"Unknown provider '{provider}'. Use 'ollama', 'groq', or 'openai'.")


class _ChatRouter:
    """Dispatches chat() to VISION_PROVIDER's client for any turn carrying
    an image, and to CHAT_PROVIDER's client otherwise - so the two can be
    different providers entirely (e.g. CHAT_PROVIDER=ollama +
    VISION_PROVIDER=groq), not just a different model within the same one
    (each client already does that switch internally via *_VISION_MODEL -
    see ollama_client.py/openai_compatible_chat.py - which still applies
    when this router isn't even in play, i.e. VISION_PROVIDER == CHAT_PROVIDER)."""

    def __init__(self, chat_client: ChatClient, vision_client: ChatClient):
        self._chat_client = chat_client
        self._vision_client = vision_client

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ChatResponse:
        client = self._vision_client if any(m.get("images") for m in messages) else self._chat_client
        return client.chat(messages, tools=tools)

    def is_reachable(self) -> bool:
        return self._chat_client.is_reachable()


def create_chat_client(settings: Settings) -> ChatClient:
    chat_provider = settings.chat_provider.lower()
    vision_provider = (settings.vision_provider or settings.chat_provider).lower()

    chat_client = _build_chat_provider(chat_provider, settings)
    if vision_provider == chat_provider:
        return chat_client

    vision_client = _build_chat_provider(vision_provider, settings)
    return _ChatRouter(chat_client, vision_client)


__all__ = [
    "OllamaClient",
    "ChatResponse",
    "ToolCall",
    "SpeechClient",
    "ChatClient",
    "create_speech_client",
    "create_chat_client",
]
