"""Security primitives for local-only repository operations."""

from __future__ import annotations

import os
import re
import shutil
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

DEPENDENCY_FILES = frozenset(
    {
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lock",
        "bun.lockb",
        "pyproject.toml",
        "poetry.lock",
        "uv.lock",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements.in",
        "constraints.txt",
        "setup.py",
        "setup.cfg",
        "environment.yml",
        "environment.yaml",
        "conda-lock.yml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "libs.versions.toml",
        "mix.exs",
        "deno.json",
        "deno.lock",
        "flake.nix",
        "flake.lock",
        "packages.lock.json",
        "project.assets.json",
        "pipfile",
        "pipfile.lock",
        "go.mod",
        "go.sum",
        "cargo.toml",
        "cargo.lock",
        "gemfile",
        "gemfile.lock",
        "composer.json",
        "composer.lock",
    }
)


def is_dependency_path(relative_path: str) -> bool:
    """Return whether a repository-relative path names a dependency manifest."""

    normalized = _normalize_relpath(relative_path)
    return Path(normalized).name.casefold() in DEPENDENCY_FILES


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


def resolve_executable(executable: str, cwd: Path) -> str:
    """Resolve an allowlisted executable on PATH, never through the repository.

    Windows CreateProcess searches the parent process's current directory
    before PATH, so a binary planted at the repository root could shadow a
    real tool. Windows .cmd/.bat shims cannot be executed without a shell
    and are rejected explicitly instead of crashing the run.
    """

    resolved = shutil.which(executable)
    if resolved is None:
        raise SecurityError(f"Executable is not available on PATH: {executable}")
    try:
        resolved_path = Path(resolved).resolve()
        repository_root = cwd.resolve()
    except OSError as exc:
        raise SecurityError(f"Executable could not be resolved safely: {executable}") from exc
    if resolved_path.parent == repository_root:
        raise SecurityError(
            f"Executable resolves to the repository root and could shadow a real tool: {executable}"
        )
    if os.name == "nt" and Path(resolved).suffix.casefold() in {".cmd", ".bat"}:
        raise SecurityError(
            f"Executable is a Windows batch shim that cannot run without a shell: {executable}"
        )
    return resolved


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
        if command[0] in {"python", "python3"}:
            execution_command = [sys.executable, *command[1:]]
        else:
            execution_command = [resolve_executable(command[0], cwd), *command[1:]]
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
    except SecurityError as exc:
        return CommandResult(
            command=command,
            return_code=126,
            elapsed_seconds=round(time.monotonic() - started, 3),
            stdout="",
            stderr="",
            blocked=True,
            reason=str(exc),
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
    except OSError as exc:
        return CommandResult(
            command=command,
            return_code=127,
            elapsed_seconds=round(time.monotonic() - started, 3),
            stdout="",
            stderr="",
            blocked=True,
            reason=f"Executable could not be started: {redact(str(exc))}",
        )


def _timeout_text(value: str | bytes | None) -> str:
    """Normalize subprocess timeout output before redacting it."""

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""
