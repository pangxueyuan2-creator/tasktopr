"""Explicit finite orchestration for TaskToPR planning and repair runs."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .agents import (
    apply_patch,
    create_plan,
    explore,
    git_root,
    load_issue,
    request_patch,
    review_changes,
    run_quality_checks,
)
from .config import TaskToPRConfig
from .events import RunJournal
from .guardspec_handoff import load_guardspec_decision
from .models import CommandResult, Issue, RunPhase, RunResult
from .pr import commit_changes, create_branch, push_and_create_pr
from .providers import ModelProvider
from .security import redact


def plan_issue(
    issue_number: int,
    *,
    start_dir: Path,
    config: TaskToPRConfig,
    provider: ModelProvider,
    demo: bool = False,
) -> RunResult:
    """Run the read-only intake, exploration and planning phases."""

    repo_root = git_root(start_dir)
    journal = RunJournal(repo_root)
    result: RunResult | None = None
    try:
        journal.event(RunPhase.ANALYZING_ISSUE, f"Reading Issue #{issue_number}.")
        issue = load_issue(repo_root, issue_number, demo=demo)
        journal.event(RunPhase.SCANNING_REPOSITORY, "Selecting bounded repository context.")
        profile = explore(repo_root, issue, config)
        journal.event(RunPhase.CREATING_PLAN, "Requesting and validating a structured plan.")
        plan = create_plan(provider, issue, profile, config)
        journal.write_json("plan.json", {"issue": issue, "plan": plan, "repository": profile})
        summary = _summary(
            issue, plan.summary, profile.relevant_files, [], "Plan ready; no files changed."
        )
        journal.write_markdown("summary.md", summary)
        journal.write_json("changes.json", {"changed_files": [], "mode": "plan"})
        journal.write_json("test-results.json", [])
        journal.event(RunPhase.COMPLETED, "Plan completed without modifying the repository.")
        result = RunResult(
            run_dir=journal.run_dir, issue=issue, plan=plan, success=True, message="Plan ready."
        )
    except Exception as exc:
        journal.event(RunPhase.FAILED, redact(str(exc)), level="error")
        journal.write_markdown("summary.md", f"# TaskToPR run failed\n\n{redact(str(exc))}\n")
        if result is None:
            issue = Issue(number=issue_number, title="Unavailable")
            result = RunResult(
                run_dir=journal.run_dir, issue=issue, success=False, message=redact(str(exc))
            )
    return result


def fix_issue(
    issue_number: int,
    *,
    start_dir: Path,
    config: TaskToPRConfig,
    provider: ModelProvider,
    dry_run: bool = False,
    no_pr: bool = False,
    demo: bool = False,
) -> RunResult:
    """Execute a finite repair run; failures produce evidence and never create a PR."""

    repo_root = git_root(start_dir)
    journal = RunJournal(repo_root)
    issue = Issue(number=issue_number, title="Unavailable")
    try:
        journal.event(RunPhase.ANALYZING_ISSUE, f"Reading Issue #{issue_number}.")
        issue = load_issue(repo_root, issue_number, demo=demo)
        journal.event(RunPhase.SCANNING_REPOSITORY, "Selecting bounded repository context.")
        profile = explore(repo_root, issue, config)
        journal.event(RunPhase.CREATING_PLAN, "Requesting and validating a structured plan.")
        plan = create_plan(provider, issue, profile, config)
        journal.write_json("plan.json", {"issue": issue, "plan": plan, "repository": profile})
        if dry_run:
            message = (
                "Dry run completed. No branch, files, tests, commit or Pull Request were created."
            )
            journal.write_json("changes.json", {"changed_files": [], "mode": "dry-run"})
            journal.write_json("test-results.json", [])
            journal.write_markdown("summary.md", _summary(issue, plan.summary, [], [], message))
            journal.event(RunPhase.COMPLETED, message)
            return RunResult(
                run_dir=journal.run_dir, issue=issue, plan=plan, success=True, message=message
            )

        journal.event(RunPhase.CREATING_BRANCH, "Creating an isolated feature branch.")
        branch = create_branch(repo_root, issue, profile.default_branch)
        journal.event(RunPhase.EDITING_FILE, "Requesting and applying a policy-checked patch.")
        patch = request_patch(provider, plan, profile, config)
        changed_files = apply_patch(patch, profile, config)
        changes_record: dict[str, object] = {"patch": patch, "changed_files": changed_files}
        guardspec = load_guardspec_decision(profile.root)
        if guardspec is not None:
            changes_record["guardspec"] = {
                "schema_version": guardspec.get("schema_version"),
                "policy_digest": guardspec.get("policy_digest"),
                "decision": guardspec.get("decision"),
            }
        journal.write_json("changes.json", changes_record)
        journal.event(RunPhase.RUNNING_TEST, "Executing discovered quality commands.")
        tests = run_quality_checks(profile, config)
        journal.write_json("test-results.json", tests)
        journal.event(
            RunPhase.REVIEWING_PATCH, "Checking scope, protected paths, diff whitespace and tests."
        )
        review = review_changes(repo_root, changed_files, tests, config)
        test_summary = _test_summary(tests)
        journal.write_markdown(
            "summary.md",
            _summary(issue, plan.summary, changed_files, review.findings, test_summary),
        )
        if not review.approved:
            message = "Review blocked the run. Inspect the evidence bundle; no commit or Pull Request was created."
            journal.event(
                RunPhase.FAILED, message, level="error", data={"findings": review.findings}
            )
            return RunResult(
                run_dir=journal.run_dir,
                issue=issue,
                plan=plan,
                patch=patch,
                tests=tests,
                review=review,
                branch=branch,
                success=False,
                message=message,
            )

        if no_pr:
            message = "Local branch is ready and reviewed. --no-pr prevented commit, push and Pull Request creation."
            journal.event(RunPhase.COMPLETED, message)
            return RunResult(
                run_dir=journal.run_dir,
                issue=issue,
                plan=plan,
                patch=patch,
                tests=tests,
                review=review,
                branch=branch,
                success=True,
                message=message,
            )

        journal.event(
            RunPhase.CREATING_PR, "Committing reviewed changes and creating a Pull Request."
        )
        commit_changes(repo_root, changed_files, issue, plan)
        pr_url = push_and_create_pr(
            repo_root,
            branch,
            profile.default_branch,
            issue,
            plan,
            review,
            test_summary,
            journal.run_dir / "pull-request.md",
        )
        message = f"Pull Request created: {pr_url}"
        journal.event(RunPhase.COMPLETED, message)
        return RunResult(
            run_dir=journal.run_dir,
            issue=issue,
            plan=plan,
            patch=patch,
            tests=tests,
            review=review,
            branch=branch,
            pr_url=pr_url,
            success=True,
            message=message,
        )
    except Exception as exc:
        message = redact(str(exc))
        journal.event(RunPhase.FAILED, message, level="error")
        journal.write_markdown("summary.md", f"# TaskToPR run failed\n\n{message}\n")
        return RunResult(run_dir=journal.run_dir, issue=issue, success=False, message=message)


def _test_summary(tests: Sequence[CommandResult]) -> str:
    results = list(tests)
    if not results:
        return "No test results were recorded."
    return "\n".join(
        f"- `{' '.join(result.command)}`: exit {result.return_code} in {result.elapsed_seconds}s"
        for result in results
    )


def _summary(
    issue: Issue,
    summary: str,
    changed_files: list[str],
    findings: list[str],
    outcome: str,
) -> str:
    files = "\n".join(f"- `{path}`" for path in changed_files) or "- None"
    review = "\n".join(f"- {finding}" for finding in findings) or "- No review findings."
    return f"""# TaskToPR run

## Issue

#{issue.number}: {issue.title}

## Summary

{summary}

## Files changed

{files}

## Review findings

{review}

## Outcome

{outcome}
"""
