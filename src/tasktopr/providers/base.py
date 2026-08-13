"""Provider protocol and strict structured-output parsing."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..config import AgentConfig
from ..security import redact

ModelT = TypeVar("ModelT", bound=BaseModel)


class ProviderError(RuntimeError):
    """Raised when a model provider cannot return a valid response."""


class ModelProvider(ABC):
    """Minimal provider contract; providers return text and never mutate repositories."""

    @abstractmethod
    def complete(self, *, system: str, user: str, config: AgentConfig) -> str:
        """Return a model response for a bounded prompt."""


def parse_json_model(raw: str, model_type: type[ModelT]) -> ModelT:
    """Parse a JSON object, including a fenced JSON block, into a Pydantic model."""

    content = raw.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            content = "\n".join(lines[1:-1])
    try:
        payload = json.loads(content)
        return model_type.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        snippet = redact(raw[:500])
        raise ProviderError(
            f"Provider returned invalid {model_type.__name__} JSON. Response preview: {snippet}"
        ) from exc
