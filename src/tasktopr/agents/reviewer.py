"""Review Agent: independently gate a patch before commit or Pull Request creation."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..config import TaskToPRConfig
from ..models import CommandResult, ReviewResult, RiskLevel
from ..security import policy_blocks, redact


def review_changes(
    repo_root: Path,
    changed_files: list[str],
    tests: list[CommandResult],
    config: TaskToPRConfig,
    base: str | None = None,
) -> ReviewResult:
    """Review working-tree and committed diffs with deterministic scope gates."""

    findings: list[str] = []
    protected = [path for path in changed_files if policy_blocks(path, config)]
    if protected:
        findings.append(f"Protected files changed: {', '.join(protected)}")
    untracked_or_modified = list_changed_files(repo_root, base=base)
    unexpected = sorted(set(untracked_or_modified) - set(changed_files))
    if unexpected:
        findings.append(
            f"Unexpected files changed outside the requested patch: {', '.join(unexpected)}"
        )
    failed_tests = [result for result in tests if result.return_code != 0 or result.blocked]
    if failed_tests:
        findings.append(
            "At least one required test/quality command failed, timed out, or was unavailable."
        )
    whitespace_problem = _diff_check(repo_root, base=base)
    if whitespace_problem:
        findings.append(f"Git whitespace check failed: {whitespace_problem}")
    risk = RiskLevel.LOW
    if protected or unexpected:
        risk = RiskLevel.BLOCKED
    elif failed_tests or whitespace_problem:
        risk = RiskLevel.HIGH
    approved = not findings
    return ReviewResult(
        approved=approved,
        risk=risk,
        findings=findings,
        changed_files=untracked_or_modified,
        scope_ok=not protected and not unexpected,
        tests_ok=not failed_tests,
    )


_IGNORED_CHANGE_PARTS = {
    ".tasktopr",
    ".patchwitness",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
}


def _rev_exists(repo_root: Path, ref: str) -> bool:
    completed = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        shell=False,
    )
    return completed.returncode == 0


def default_review_base(repo_root: Path) -> str | None:
    """Prefer ``main``/``master`` so committed feature-branch diffs stay visible."""

    for candidate in ("main", "master"):
        if _rev_exists(repo_root, candidate):
            return candidate
    return None


def resolve_review_base(repo_root: Path, base: str | None) -> str | None:
    """Return an explicit base, or the default branch, after verifying it exists."""

    if base is not None:
        if not base.strip():
            raise RuntimeError("Review --base must be a non-empty Git ref.")
        if not _rev_exists(repo_root, base):
            raise RuntimeError(f"Review --base is not a valid Git ref: {base}")
        return base
    return default_review_base(repo_root)


def _normalize_changed_path(item: str) -> str:
    normalized = item.replace("\\", "/").strip()
    if not normalized:
        return ""
    if any(part in _IGNORED_CHANGE_PARTS for part in Path(normalized).parts):
        return ""
    return normalized


def _porcelain_changed_files(repo_root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "status", "--porcelain", "-z", "--untracked-files=all"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        shell=False,
    )
    if completed.returncode != 0:
        return ["[git-status-unavailable]"]
    changed: list[str] = []
    tokens = completed.stdout.split("\0")
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token:
            continue
        if len(token) < 3:
            continue
        status = token[:2]
        path = token[3:] if token[2] == " " else token[2:].lstrip()
        paths = [path]
        if "R" in status or "C" in status:
            if index < len(tokens) and tokens[index]:
                paths.append(tokens[index])
                index += 1
        for item in paths:
            normalized = _normalize_changed_path(item)
            if normalized:
                changed.append(normalized)
    return changed


def _diff_changed_files(repo_root: Path, base: str) -> list[str]:
    completed = subprocess.run(
        ["git", "diff", "--name-only", "-z", "--no-renames", base],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        shell=False,
    )
    if completed.returncode != 0:
        return ["[git-diff-unavailable]"]
    changed: list[str] = []
    for item in completed.stdout.split("\0"):
        normalized = _normalize_changed_path(item)
        if normalized:
            changed.append(normalized)
    return changed


def list_changed_files(repo_root: Path, base: str | None = None) -> list[str]:
    """Return working-tree paths plus committed paths since ``base``.

    Rename and copy entries contribute both the destination and the source so
    policy cannot miss a protected origin. ``.tasktopr`` and ``.patchwitness``
    evidence/cache directories are ignored. When ``base`` is omitted, ``main``
    or ``master`` is used if that ref exists so a clean working tree after a
    feature-branch commit is still reviewed.
    """

    resolved = resolve_review_base(repo_root, base)
    changed = _porcelain_changed_files(repo_root)
    if resolved:
        changed.extend(_diff_changed_files(repo_root, resolved))
    return sorted(dict.fromkeys(changed))


def _diff_check(repo_root: Path, base: str | None = None) -> str:
    command = ["git", "diff", "--check"]
    resolved = resolve_review_base(repo_root, base)
    if resolved:
        command.append(resolved)
    completed = subprocess.run(
        command,
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        shell=False,
    )
    remaining = [
        line
        for line in _combined_diff_check_text(completed).splitlines()
        if line.strip() and not _is_autocrlf_warning(line)
    ]
    if not remaining:
        return ""
    return redact("\n".join(remaining).strip()[-500:])


def _combined_diff_check_text(completed: subprocess.CompletedProcess[str]) -> str:
    parts = [part for part in (completed.stderr, completed.stdout) if part]
    return "\n".join(parts)


def _is_autocrlf_warning(line: str) -> bool:
    """Ignore Git core.autocrlf chatter from ``git diff --check``.

    Windows Git emits a ``warning:`` line plus a continuation without that
    prefix: ``The file will have its original line endings in your working
    directory``. Requiring the prefix let the continuation become a HIGH
    finding even when every real whitespace check passed.
    """

    lowered = line.strip().casefold()
    if not lowered:
        return False
    return (
        "lf will be replaced by crlf" in lowered
        or "crlf will be replaced by lf" in lowered
        or "the file will have its original line endings" in lowered
    )
