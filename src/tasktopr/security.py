"""Security primitives for local-only repository operations."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from .models import CommandResult, RiskLevel
from .paths import (
    PathSecurityError,
    canonicalize_repo_relpath,
    normalize_relpath,
    resolved_repo_relpath as _resolved_repo_relpath,
)

if TYPE_CHECKING:
    from .config import TaskToPRConfig

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
    ".tasktopr.toml",
    ".patchwitness/**",
    ".env*",
    "**/.env*",
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
_WORKFLOW_PATTERN = ".github/workflows/**"
_SENSITIVE_TERMS = ("credential", "secret", "auth")

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
_ALLOWED_EXECUTABLES = {
    "python",
    "python.exe",
    "python3",
    "python3.exe",
    "py",
    "py.exe",
    "pytest",
    "pytest.exe",
    "ruff",
    "ruff.exe",
    "mypy",
    "mypy.exe",
    "npm",
    "npm.cmd",
    "npx",
    "npx.cmd",
    "node",
    "node.exe",
}
_INLINE_INTERPRETERS = {
    "python",
    "python.exe",
    "python3",
    "python3.exe",
    "py",
    "py.exe",
    "node",
    "node.exe",
}
_INLINE_PAYLOAD_FLAGS = {"-c", "/c", "-command", "-enc", "-encodedcommand", "--eval", "-e"}
_ACTIVE_INTERPRETER_NAMES = {
    "python",
    "python.exe",
    "python3",
    "python3.exe",
    "py",
    "py.exe",
}


class SecurityError(PathSecurityError):
    """Raised when an operation crosses TaskToPR's default security boundary."""


def redact(value: str) -> str:
    """Redact common token/key forms before writing logs, evidence or terminal output."""

    result = value
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def _normalize_relpath(relative_path: str) -> str:
    """Convert backslashes to forward slashes for consistent pattern matching."""

    return normalize_relpath(relative_path)


def resolved_repo_relpath(repo_root: Path, relative_path: str) -> str:
    """Resolve aliases and return the repository-relative path a write would hit."""

    try:
        return _resolved_repo_relpath(repo_root, relative_path)
    except PathSecurityError as exc:
        raise SecurityError(str(exc)) from exc


def safe_path(repo_root: Path, relative_path: str) -> Path:
    """Resolve a user/model path and prove it remains within the repository root."""

    rel = resolved_repo_relpath(repo_root, relative_path)
    return repo_root.resolve() / rel


def refuse_aliased_write(path: Path, relative_path: str) -> None:
    """Block writes that would also mutate another directory entry.

    Policy sees one repository-relative name. A symlink or hard link can make
    that name alias a protected file. Refuse both instead of trusting the
    requested spelling.
    """

    if path.exists() and path.is_symlink():
        raise SecurityError(f"Refusing to write through a symlink: {relative_path}")
    if path.is_file():
        try:
            nlink = path.stat().st_nlink
        except OSError:
            return
        if nlink > 1:
            raise SecurityError(f"Refusing to edit a hard-linked file: {relative_path}")


def case_insensitive_paths() -> bool:
    """Return True when policy globs must fold case like the host filesystem.

    Windows volumes treat ``.GITHUB/WORKFLOWS/ci.yml`` as
    ``.github/workflows/ci.yml``. ``Path.match`` is already OS-aware; the
    prefix/regex fallback must fold the same way or mixed-case spellings
    miss ``src/**`` / ``.github/workflows/**``. Linux stays case-sensitive.
    """

    return os.name == "nt"


def _fold(text: str) -> str:
    return text.casefold() if case_insensitive_paths() else text


def _path_matches(normalized: str, pattern: str) -> bool:
    """Match a repository-relative path against a glob.

    Path.match is kept so patterns such as ``Dockerfile`` and ``*.py`` still
    match from the right. Segment-aware matching is added so ``src/*.py``
    does not silently treat ``src/nested/deep.py`` as in-scope when callers
    use the glob helper directly, and so ``**/.env*`` covers nested env files.
    """

    if not pattern:
        return False
    path = Path(normalized)
    if path.match(pattern):
        return True
    return _glob_matches(normalized, pattern)


def _glob_matches(path: str, pattern: str) -> bool:
    normalized = pattern.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized in {"*", "**", "**/*"}:
        return True
    path_cmp = _fold(path)
    if normalized.endswith("/**"):
        prefix = normalized[:-3].rstrip("/")
        if not prefix:
            return True
        prefix_cmp = _fold(prefix)
        return path_cmp == prefix_cmp or path_cmp.startswith(prefix_cmp + "/")
    if normalized.endswith("/") and "*" not in normalized and "?" not in normalized:
        prefix = normalized.rstrip("/")
        if not prefix:
            return True
        prefix_cmp = _fold(prefix)
        return path_cmp == prefix_cmp or path_cmp.startswith(prefix_cmp + "/")
    if "*" not in normalized and "?" not in normalized:
        exact = _fold(normalized)
        return path_cmp == exact or path_cmp.startswith(exact + "/")
    return _glob_regex(normalized, case_insensitive_paths()).fullmatch(path) is not None


@lru_cache(maxsize=256)
def _glob_regex(pattern: str, ignore_case: bool = False) -> re.Pattern[str]:
    """Compile a policy glob where * and ? do not cross '/'."""

    pieces: list[str] = []
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                index += 2
                if index < len(pattern) and pattern[index] == "/":
                    pieces.append("(?:.*/)?")
                    index += 1
                else:
                    pieces.append(".*")
            else:
                pieces.append("[^/]*")
                index += 1
        elif character == "?":
            pieces.append("[^/]")
            index += 1
        else:
            pieces.append(re.escape(character))
            index += 1
    flags = re.IGNORECASE if ignore_case else 0
    return re.compile("^" + "".join(pieces) + "$", flags)


def is_protected(
    relative_path: str,
    additional_patterns: list[str] | None = None,
    *,
    allow_workflows: bool = False,
) -> bool:
    """Return whether a relative path is protected by default or configured policy.

    ``allow_workflows`` opts out of only the default ``.github/workflows/**``
    pattern. Extra protected/denied patterns from a boundary file still apply.
    """

    try:
        normalized = canonicalize_repo_relpath(relative_path)
    except PathSecurityError:
        return True
    path = Path(normalized)
    defaults = _DEFAULT_PROTECTED
    if allow_workflows:
        defaults = tuple(item for item in defaults if item != _WORKFLOW_PATTERN)
    patterns = (*defaults, *(additional_patterns or []))
    return any(_path_matches(normalized, pattern) for pattern in patterns) or any(
        term in part.casefold() for part in path.parts for term in _SENSITIVE_TERMS
    )


def policy_blocks(relative_path: str, config: TaskToPRConfig) -> bool:
    """Return whether repository policy forbids writing ``relative_path``.

    Deny/protect wins. Exclusive-allow with an empty allow list denies every
    path. A non-empty allow list requires a match. Issue text is never read
    here — only ``TaskToPRConfig`` (defaults, ``.tasktopr.toml``, boundary JSON).
    """

    try:
        normalized = canonicalize_repo_relpath(relative_path)
    except PathSecurityError:
        return True
    extra = [*config.scope.protected, *config.scope.denied]
    if is_protected(
        normalized,
        extra,
        allow_workflows=config.permissions.allow_workflows,
    ):
        return True
    if config.scope.exclusive_allow and not config.scope.allowed:
        return True
    if config.scope.allowed:
        if not any(_path_matches(normalized, pattern) for pattern in config.scope.allowed):
            return True
    return False


def path_risk(
    relative_path: str,
    additional_patterns: list[str] | None = None,
    *,
    allow_workflows: bool = False,
) -> RiskLevel:
    """Classify a path; protected paths block automatic changes."""

    try:
        normalized = canonicalize_repo_relpath(relative_path)
    except PathSecurityError:
        return RiskLevel.BLOCKED
    if is_protected(normalized, additional_patterns, allow_workflows=allow_workflows):
        return RiskLevel.BLOCKED
    github_prefix = _fold(".github/")
    if _fold(normalized).startswith(github_prefix) or "deploy" in normalized.casefold():
        return RiskLevel.HIGH
    return RiskLevel.LOW


def validate_command(command: list[str]) -> None:
    """Accept only a small, non-networked test/build command surface."""

    if not command:
        raise SecurityError("An empty command cannot be executed.")
    executable = Path(command[0]).name.lower()
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
    if executable in _INLINE_INTERPRETERS and any(
        token.lower() in _INLINE_PAYLOAD_FLAGS for token in command[1:]
    ):
        raise SecurityError("Inline interpreter payloads are not allowed.")


def _with_active_interpreter(command: list[str]) -> list[str]:
    """Rewrite a bare interpreter name to the running process executable.

    Windows CreateProcess('python') can ignore PATH and launch the Store
    alias. Explicit paths are left unchanged.
    """

    raw = command[0]
    if Path(raw).name != raw:
        return command
    if raw.lower() in _ACTIVE_INTERPRETER_NAMES:
        return [sys.executable, *command[1:]]
    return command


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
        execution_command = _with_active_interpreter(command)
        completed = subprocess.run(
            execution_command,
            cwd=cwd,
            check=False,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
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
