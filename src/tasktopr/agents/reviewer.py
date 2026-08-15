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
) -> ReviewResult:
    """Review a working tree with deterministic scope and test gates."""

    findings: list[str] = []
    protected = [path for path in changed_files if policy_blocks(path, config)]
    if protected:
        findings.append(f"Protected files changed: {', '.join(protected)}")
    untracked_or_modified = list_changed_files(repo_root)
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
    whitespace_problem = _diff_check(repo_root)
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


def list_changed_files(repo_root: Path) -> list[str]:
    """Return working-tree paths from ``git status --porcelain -z``.

    Rename and copy entries contribute both the destination and the source so
    policy cannot miss a protected origin. ``.tasktopr`` and ``.patchwitness``
    evidence/cache directories are ignored.
    """

    completed = subprocess.run(
        ["git", "status", "--porcelain", "-z", "--untracked-files=all"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        shell=False,
    )
    if completed.returncode != 0:
        return ["[git-status-unavailable]"]
    ignored_parts = {
        ".tasktopr",
        ".patchwitness",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
    }
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
            normalized = item.replace("\\", "/").strip()
            if normalized and not any(part in ignored_parts for part in Path(normalized).parts):
                changed.append(normalized)
    return sorted(dict.fromkeys(changed))


def _diff_check(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "diff", "--check"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        shell=False,
    )
    return redact((completed.stderr or completed.stdout).strip()[-500:])
