"""Chat client for any OpenAI-compatible endpoint you configure yourself
(OpenRouter, NVIDIA, Kilo Code, a local server...), via the CUSTOMn_* slots.

Same wire format as Groq/OpenAI, so this only supplies the base URL, key and
model names. See openai_compatible_chat.py.
"""

from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlparse

from cozmo_brain.config import Settings
from cozmo_brain.llm.openai_compatible_chat import OpenAICompatibleChatClient

logger = logging.getLogger(__name__)

_COMPLETIONS_SUFFIX = "/chat/completions"


def normalize_base_url(url: str) -> str:
    """The base URL the client appends /chat/completions and /models to.

    Accepts a trailing slash, or the full .../chat/completions URL as copied
    from a provider's docs. Raises ValueError for anything that isn't http(s).
    """
    url = url.strip().rstrip("/")
    if url.endswith(_COMPLETIONS_SUFFIX):
        url = url[: -len(_COMPLETIONS_SUFFIX)].rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"Base URL must start with http:// or https:// and name a host, got {url!r}.")
    return url


def _is_local_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private


class CustomChatClient(OpenAICompatibleChatClient):
    def __init__(self, settings: Settings, provider: str):
        endpoint = settings.custom_endpoint(provider)
        base_url = normalize_base_url(endpoint.base_url)
        host = urlparse(base_url).hostname or ""

        if base_url.startswith("http://") and not _is_local_host(host):
            logger.warning(
                "%s uses plain http:// to %s - the API key travels unencrypted. Use https:// if the service offers it.",
                provider, host,
            )
        logger.info(
            "Custom chat endpoint %s (%s): host=%s, model=%s, vision model=%s",
            provider, endpoint.name, host, endpoint.model, endpoint.vision_model or endpoint.model,
        )

        super().__init__(
            base_url=base_url,
            api_key=endpoint.api_key,
            model=endpoint.model,
            vision_model=endpoint.vision_model or endpoint.model,
            timeout_s=settings.chat_timeout_s,
        )
