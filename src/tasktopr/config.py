"""Configuration loading and validation for .tasktopr.toml."""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

AGENT_BOUNDARY_SCHEMA = "https://patchwitness.dev/agent-boundary/v1"


class AgentConfig(BaseModel):
    provider: str = "openai"
    model: str = "gpt-4.1-mini"
    max_iterations: int = Field(default=3, ge=1, le=12)
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    max_tokens: int = Field(default=2_000, ge=256, le=16_000)
    timeout_seconds: int = Field(default=60, ge=5, le=300)
    retries: int = Field(default=1, ge=0, le=3)


class PermissionsConfig(BaseModel):
    allow_workflows: bool = False
    allow_dependency_updates: bool = False
    allow_pr_creation: bool = True


class TestingConfig(BaseModel):
    commands: list[list[str]] = Field(default_factory=list)
    timeout_seconds: int = Field(default=120, ge=5, le=900)


class ScopeConfig(BaseModel):
    protected: list[str] = Field(default_factory=list)
    denied: list[str] = Field(default_factory=list)
    allowed: list[str] = Field(default_factory=list)
    exclusive_allow: bool = False
    max_context_files: int = Field(default=12, ge=1, le=50)
    max_context_bytes: int = Field(default=80_000, ge=4_000, le=500_000)


class TaskToPRConfig(BaseModel):
    agent: AgentConfig = Field(default_factory=AgentConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    testing: TestingConfig = Field(default_factory=TestingConfig)
    scope: ScopeConfig = Field(default_factory=ScopeConfig)


class ConfigError(ValueError):
    """Raised for invalid or unreadable project configuration."""


def load_config(repo_root: Path) -> TaskToPRConfig:
    """Load `.tasktopr.toml` when present; defaults are safe and complete."""

    path = repo_root / ".tasktopr.toml"
    if not path.exists():
        return TaskToPRConfig()
    try:
        raw: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
        return TaskToPRConfig.model_validate(raw)
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as exc:
        raise ConfigError(f"Invalid .tasktopr.toml: {exc}") from exc


def _is_boundary_document(value: dict[str, Any]) -> bool:
    schema = str(value.get("schema", ""))
    if schema == AGENT_BOUNDARY_SCHEMA:
        return True
    if value.get("version") == 1 and "allowed_paths" in value and "policy" not in value:
        return True
    return False


def load_boundary(path: Path) -> dict[str, Any]:
    """Load a versioned agent-boundary/v1 JSON document from disk.

    Issue bodies, model output and other untrusted text must never be passed
    here. Only a caller-supplied file path is accepted.
    """

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Invalid agent-boundary document: {exc}") from exc
    if not isinstance(raw, dict) or not _is_boundary_document(raw):
        raise ConfigError(
            "Not an agent-boundary/v1 document. TaskToPR does not treat Issue "
            "text or arbitrary JSON as policy."
        )
    return raw


def apply_boundary(config: TaskToPRConfig, boundary: dict[str, Any]) -> TaskToPRConfig:
    """Merge an independent agent-boundary/v1 document into local config.

    Denied and protected paths are additive. Exclusive-allow, when present,
    tightens scope and is never relaxed by Issue content.
    """

    denied = [str(item) for item in boundary.get("denied_paths", [])]
    protected = [str(item) for item in boundary.get("protected_paths", [])]
    allowed = [str(item) for item in boundary.get("allowed_paths", [])]
    exclusive = bool(boundary.get("exclusive_allow", False))
    config.scope.denied = list(dict.fromkeys([*config.scope.denied, *denied]))
    config.scope.protected = list(
        dict.fromkeys([*config.scope.protected, *protected, *denied])
    )
    if exclusive:
        config.scope.exclusive_allow = True
        config.scope.allowed = allowed
    elif allowed:
        config.scope.allowed = list(dict.fromkeys([*config.scope.allowed, *allowed]))
    return config


def provider_api_key(provider: str) -> str | None:
    """Read a provider key only from the process environment."""

    variables = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openai-compatible": "OPENAI_COMPATIBLE_API_KEY",
        "demo": "TASKTOPR_DEMO_KEY",
    }
    variable = variables.get(provider)
    return os.environ.get(variable) if variable else None


def redacted_config(config: TaskToPRConfig) -> dict[str, Any]:
    """Return display-safe configuration without environment variable values."""

    payload = config.model_dump(mode="json")
    payload["provider_key_present"] = provider_api_key(config.agent.provider) is not None
    return payload
