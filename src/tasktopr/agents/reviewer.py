"""Review Agent: independently gate a patch before commit or Pull Request creation."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..config import TaskToPRConfig
from ..models import CommandResult, ReviewResult, RiskLevel
from ..receipt import changed_paths, git
from ..security import is_protected, redact


def review_changes(
    repo_root: Path,
    changed_files: list[str],
    tests: list[CommandResult],
    config: TaskToPRConfig,
) -> ReviewResult:
    """Review a working tree with deterministic scope and test gates."""

    findings: list[str] = []
    protected = [path for path in changed_files if is_protected(path, config.scope.protected)]
    if protected:
        findings.append(f"Protected files changed: {', '.join(protected)}")
    untracked_or_modified = _changed_files(repo_root)
    unexpected = sorted(set(untracked_or_modified) - set(changed_files))
    if unexpected:
        findings.append(
            f"Unexpected files changed outside the requested patch: {', '.join(unexpected)}"
        )
    failed_tests = [result for result in tests if result.return_code != 0 or result.blocked]
    if failed_tests or not tests:
        findings.append(
            "At least one required test/quality command failed, timed out, or was unavailable."
        )
    whitespace_problem = (
        "unavailable"
        if "[git-status-unavailable]" in untracked_or_modified
        else _diff_check(repo_root)
    )
    if whitespace_problem:
        findings.append(f"Git whitespace check failed: {whitespace_problem}")
    risk = RiskLevel.LOW
    if protected or unexpected:
        risk = RiskLevel.BLOCKED
    elif failed_tests or not tests or whitespace_problem:
        risk = RiskLevel.HIGH
    approved = not findings
    return ReviewResult(
        approved=approved,
        risk=risk,
        findings=findings,
        changed_files=untracked_or_modified,
        scope_ok=not protected and not unexpected,
        tests_ok=bool(tests) and not failed_tests,
    )


def _changed_files(repo_root: Path) -> list[str]:
    try:
        return changed_paths(repo_root)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return ["[git-status-unavailable]"]


def _diff_check(repo_root: Path) -> str:
    try:
        return redact(
            git(repo_root, "diff", "--no-ext-diff", "--no-textconv", "--check").decode("utf-8")
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return "whitespace evidence unavailable or whitespace errors detected"
