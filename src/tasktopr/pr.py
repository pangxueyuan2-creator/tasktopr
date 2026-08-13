"""Safe branch, commit, push and Pull Request operations."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .models import ChangePlan, Issue, ReviewResult
from .security import SecurityError, redact


class PullRequestError(RuntimeError):
    """Raised when a branch or Pull Request action cannot complete safely."""


def create_branch(repo_root: Path, issue: Issue, base_branch: str) -> str:
    """Create a fresh task branch from the declared base; default branches are never targets."""

    if base_branch in {"main", "master"}:
        _run_git(["git", "switch", base_branch], repo_root)
    branch = f"tasktopr/issue-{issue.number}-{_slug(issue.title)}"
    existing = _run_git(["git", "branch", "--list", branch], repo_root).stdout.strip()
    if existing:
        raise PullRequestError(f"Refusing to reuse an existing TaskToPR branch: {branch}")
    _run_git(["git", "switch", "-c", branch], repo_root)
    return branch


def commit_changes(
    repo_root: Path,
    changed_files: list[str],
    issue: Issue,
    plan: ChangePlan,
) -> str:
    """Stage only approved files and create a concise, traceable commit."""

    if not changed_files:
        raise PullRequestError("No changed files are available to commit.")
    if any(path.startswith(".git/") for path in changed_files):
        raise SecurityError("Git internals can never be committed by TaskToPR.")
    _run_git(["git", "add", "--", *changed_files], repo_root)
    message = f"fix: resolve #{issue.number} {issue.title[:60]}"
    _run_git(["git", "commit", "-m", message, "-m", plan.summary], repo_root)
    return _run_git(["git", "rev-parse", "HEAD"], repo_root).stdout.strip()


def push_and_create_pr(
    repo_root: Path,
    branch: str,
    base_branch: str,
    issue: Issue,
    plan: ChangePlan,
    review: ReviewResult,
    test_summary: str,
    body_path: Path,
) -> str:
    """Push a reviewed feature branch and create a non-interactive linked Pull Request."""

    if not review.approved:
        raise PullRequestError("A rejected review cannot be pushed or opened as a Pull Request.")
    if branch in {"main", "master", base_branch}:
        raise SecurityError("TaskToPR will not push a default or base branch.")
    _run_git(["git", "push", "-u", "origin", branch], repo_root)
    body = _render_pr_body(issue, plan, review, test_summary)
    body_path.write_text(body, encoding="utf-8")
    completed = _run(
        [
            "gh",
            "pr",
            "create",
            "--base",
            base_branch,
            "--head",
            branch,
            "--title",
            f"Fix #{issue.number}: {issue.title}",
            "--body-file",
            str(body_path),
            "--no-maintainer-edit",
        ],
        repo_root,
    )
    return completed.stdout.strip().splitlines()[-1]


def _render_pr_body(issue: Issue, plan: ChangePlan, review: ReviewResult, test_summary: str) -> str:
    files = "\n".join(f"- `{path}`" for path in review.changed_files)
    risks = (
        "\n".join(f"- {finding}" for finding in review.findings)
        or "- Low: deterministic review approved."
    )
    return f"""## Problem
Fixes #{issue.number}. {issue.title}

## Root cause
{plan.root_cause}

## Changes
{plan.summary}

## Tests
{test_summary}

## Risks
{risks}

## Files changed
{files}
"""


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:32] or "change"


def _run_git(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return _run(command, cwd)


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PullRequestError(f"Command could not complete: {redact(str(exc))}") from exc
    if completed.returncode != 0:
        raise PullRequestError(f"Command failed: {redact(completed.stderr[-800:])}")
    return completed
