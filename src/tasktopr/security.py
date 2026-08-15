"""Security primitives for local-only repository operations."""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

from .models import CommandResult, RiskLevel

_SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA|EC|OPENSSH|PRIVATE) KEY-----"),
    re.compile(r"(?i)(?:api[_-]?key|token|secret|password)\s*[=:]\s*[^\s\"']{8,}"),
)

_DEFAULT_PROTECTED = (
    ".git/**",
    ".tasktopr/**",
    ".env*",
    ".github/workflows/**",
    "**/*credential*",
    "**/*secret*",
    "**/*auth*",
    "**/id_rsa*",
    "**/*.pem",
    "**/*.key",
    "Dockerfile",
    "docker-compose*.yml",
    "docker-compose*.yaml",
    "package-lock.json",
    "poetry.lock",
    "uv.lock",
)

_DENIED_TOKENS = {
    "rm",
    "sudo",
    "chmod",
    "chown",
    "curl",
    "wget",
    "ssh",
    "scp",
    "nc",
    "ncat",
    "docker",
}
_SHELL_METACHARACTERS = (";", "&&", "||", "|", "`", "$(", ">", "<")
_ALLOWED_EXECUTABLES = {"python", "python3", "pytest", "ruff", "mypy", "npm", "npx", "node"}


class SecurityError(ValueError):
    """Raised when an operation crosses TaskToPR's default security boundary."""


def redact(value: str) -> str:
    """Redact common token/key forms before writing logs, evidence or terminal output."""

    result = value
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def _normalize_relpath(relative_path: str) -> str:
    """Convert backslashes to forward slashes for consistent pattern matching."""

    return relative_path.replace("\\", "/")


def safe_path(repo_root: Path, relative_path: str) -> Path:
    """Resolve a user/model path and prove it remains within the repository root."""

    if not relative_path or Path(relative_path).is_absolute():
        raise SecurityError("Only non-empty repository-relative paths are allowed.")
    root = repo_root.resolve()
    candidate = (root / relative_path).resolve()
    if candidate == root or root not in candidate.parents:
        raise SecurityError(f"Path escapes the repository root: {relative_path}")
    return candidate


def is_protected(relative_path: str, additional_patterns: list[str] | None = None) -> bool:
    """Return whether a relative path is protected by default or configured policy."""

    normalized = _normalize_relpath(relative_path)
    path = Path(normalized)
    patterns = (*_DEFAULT_PROTECTED, *(additional_patterns or []))
    sensitive_terms = ("credential", "secret", "auth")
    return any(path.match(pattern) for pattern in patterns) or any(
        term in part.casefold() for part in path.parts for term in sensitive_terms
    )


def path_risk(relative_path: str, additional_patterns: list[str] | None = None) -> RiskLevel:
    """Classify a path; protected paths block automatic changes."""

    normalized = _normalize_relpath(relative_path)
    if is_protected(normalized, additional_patterns):
        return RiskLevel.BLOCKED
    if normalized.startswith(".github/") or "deploy" in normalized.casefold():
        return RiskLevel.HIGH
    return RiskLevel.LOW


def validate_command(command: list[str]) -> None:
    """Accept only a small, non-networked test/build command surface."""

    if not command:
        raise SecurityError("An empty command cannot be executed.")
    executable = Path(command[0]).name
    if executable not in _ALLOWED_EXECUTABLES:
        raise SecurityError(f"Executable is not allowlisted: {executable}")
    joined = " ".join(command)
    if any(token in command for token in _DENIED_TOKENS):
        raise SecurityError("Command contains a denied executable.")
    if "--force" in command or ("-f" in command and executable == "git"):
        raise SecurityError("Force operations are not allowed.")
    if any(marker in joined for marker in _SHELL_METACHARACTERS):
        raise SecurityError("Shell metacharacters are not allowed.")
    if "rm -rf" in joined or ".git" in command:
        raise SecurityError("Destructive or Git-internal operations are not allowed.")


def run_safe_command(command: list[str], cwd: Path, timeout_seconds: int) -> CommandResult:
    """Run a validated command without a shell and retain redacted, bounded evidence."""

    try:
        validate_command(command)
    except SecurityError as exc:
        return CommandResult(
            command=command,
            return_code=126,
            elapsed_seconds=0.0,
            blocked=True,
            reason=str(exc),
        )

    started = time.monotonic()
    try:
        execution_command = (
            [sys.executable, *command[1:]] if command[0] in {"python", "python3"} else command
        )
        completed = subprocess.run(
            execution_command,
            cwd=cwd,
            check=False,
            shell=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_seconds,
        )
        return CommandResult(
            command=command,
            return_code=completed.returncode,
            elapsed_seconds=round(time.monotonic() - started, 3),
            stdout=redact(completed.stdout[-12_000:]),
            stderr=redact(completed.stderr[-12_000:]),
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            command=command,
            return_code=124,
            elapsed_seconds=round(time.monotonic() - started, 3),
            stdout=redact(_timeout_text(exc.stdout)[-12_000:]),
            stderr=redact(_timeout_text(exc.stderr)[-12_000:]),
            reason=f"Timed out after {timeout_seconds} seconds.",
        )


def _timeout_text(value: str | bytes | None) -> str:
    """Normalize subprocess timeout output before redacting it."""

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""
