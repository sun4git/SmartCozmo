from cozmo_brain.config import Settings
from cozmo_brain.llm.ollama_client import ChatResponse, OllamaClient, ToolCall
from cozmo_brain.llm.speech_client import SpeechClient


def create_speech_client(settings: Settings) -> SpeechClient:
    provider = settings.audio_provider.lower()

    if provider == "openai":
        from cozmo_brain.llm.openai_client import OpenAIClient

        return OpenAIClient(settings)

    if provider == "groq":
        from cozmo_brain.llm.groq_client import GroqClient

        return GroqClient(settings)

    raise ValueError(f"Unknown AUDIO_PROVIDER '{provider}'. Use 'groq' or 'openai'.")


__all__ = ["OllamaClient", "ChatResponse", "ToolCall", "SpeechClient", "create_speech_client"]
