"""HTTP-backed model providers with bounded retries and redacted failures."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from ..config import AgentConfig, provider_api_key
from ..security import redact
from .base import ModelProvider, ProviderError


class OpenAICompatibleProvider(ModelProvider):
    """Call a chat-completions-compatible endpoint."""

    def __init__(self, *, api_key: str, base_url: str) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def complete(self, *, system: str, user: str, config: AgentConfig) -> str:
        payload = {
            "model": config.model,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        response = _post_with_retry(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            payload=payload,
            timeout=config.timeout_seconds,
            retries=config.retries,
        )
        try:
            return str(response["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                "OpenAI-compatible response did not contain message content."
            ) from exc


class OpenAIProvider(OpenAICompatibleProvider):
    """OpenAI's standard chat-completions endpoint provider."""

    @classmethod
    def from_environment(cls) -> OpenAIProvider:
        api_key = provider_api_key("openai")
        if not api_key:
            raise ProviderError("OPENAI_API_KEY is required for the OpenAI provider.")
        return cls(api_key=api_key, base_url="https://api.openai.com/v1")


class AnthropicProvider(ModelProvider):
    """Anthropic Messages API provider."""

    def __init__(self, *, api_key: str) -> None:
        self.api_key = api_key

    @classmethod
    def from_environment(cls) -> AnthropicProvider:
        api_key = provider_api_key("anthropic")
        if not api_key:
            raise ProviderError("ANTHROPIC_API_KEY is required for the Anthropic provider.")
        return cls(api_key=api_key)

    def complete(self, *, system: str, user: str, config: AgentConfig) -> str:
        payload = {
            "model": config.model,
            "system": system,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "messages": [{"role": "user", "content": user}],
        }
        response = _post_with_retry(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            payload=payload,
            timeout=config.timeout_seconds,
            retries=config.retries,
        )
        try:
            blocks = response["content"]
            text_blocks = [block["text"] for block in blocks if block.get("type") == "text"]
            return "\n".join(text_blocks)
        except (KeyError, TypeError) as exc:
            raise ProviderError("Anthropic response did not contain text content.") from exc


def _post_with_retry(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int,
    retries: int,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict):
                    raise ProviderError("Provider returned a non-object JSON response.")
                return result
        except (httpx.HTTPError, ValueError, ProviderError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
    raise ProviderError(f"Model request failed: {redact(str(last_error))}") from last_error


def build_provider(provider_name: str) -> ModelProvider:
    """Create a supported network provider from environment-only configuration."""

    if provider_name == "openai":
        return OpenAIProvider.from_environment()
    if provider_name == "anthropic":
        return AnthropicProvider.from_environment()
    if provider_name == "openai-compatible":
        api_key = provider_api_key(provider_name)
        base_url = os.environ.get("OPENAI_COMPATIBLE_BASE_URL")
        if not api_key or not base_url:
            raise ProviderError(
                "OPENAI_COMPATIBLE_API_KEY and OPENAI_COMPATIBLE_BASE_URL are required."
            )
        return OpenAICompatibleProvider(api_key=api_key, base_url=base_url)
    raise ProviderError(f"Unknown provider: {provider_name}")
