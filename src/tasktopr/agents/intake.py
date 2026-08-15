"""Issue Intake Agent: acquire an Issue and derive compact task signals."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ..models import Issue
from ..security import redact


class IssueIntakeError(RuntimeError):
    """Raised when an Issue cannot be retrieved or parsed."""


# Documented --demo Issue #1. Used only when `.tasktopr-demo-issue.json` is
# absent. A present-but-invalid file still errors so corruption is not hidden.
_BUILTIN_DEMO_ISSUE_NUMBER = 1
_BUILTIN_DEMO_ISSUE: dict[str, Any] = {
    "number": 1,
    "title": "Prevent a crash when dividing by zero",
    "body": (
        "The calculator crashes when the denominator is zero.\n\n"
        "Acceptance criteria:\n"
        "- divide(8, 0) raises a clear ValueError\n"
        "- normal division continues to work\n\n"
        "Constraints:\n"
        "- Do not refactor unrelated arithmetic behavior"
    ),
    "url": "https://example.invalid/issues/1",
    "labels": [{"name": "bug"}],
}


def load_issue(repo_root: Path, issue_number: int, *, demo: bool = False) -> Issue:
    """Fetch one Issue using `gh`, or a local demo Issue when explicitly requested."""

    if demo:
        return _load_demo_issue(repo_root, issue_number)
    command = [
        "gh",
        "issue",
        "view",
        str(issue_number),
        "--json",
        "number,title,body,url,labels",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IssueIntakeError(f"Could not execute GitHub CLI: {redact(str(exc))}") from exc
    if completed.returncode != 0:
        raise IssueIntakeError(
            "GitHub CLI could not retrieve the Issue. Run `gh auth status` and confirm the issue exists. "
            f"Details: {redact(completed.stderr[-500:])}"
        )
    try:
        raw = json.loads(completed.stdout)
        return _issue_from_mapping(raw)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise IssueIntakeError("GitHub CLI returned an invalid Issue payload.") from exc


def _load_demo_issue(repo_root: Path, issue_number: int) -> Issue:
    path = repo_root / ".tasktopr-demo-issue.json"
    if not path.exists():
        if issue_number == _BUILTIN_DEMO_ISSUE_NUMBER:
            return _issue_from_mapping(_BUILTIN_DEMO_ISSUE)
        raise IssueIntakeError(
            "Demo issue file `.tasktopr-demo-issue.json` was not found "
            f"and there is no builtin demo for #{issue_number}."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        issue = _issue_from_mapping(raw)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise IssueIntakeError("Demo issue file is invalid.") from exc
    if issue.number != issue_number:
        raise IssueIntakeError(
            f"Demo issue contains #{issue.number}, not requested #{issue_number}."
        )
    return issue


def _issue_from_mapping(raw: dict[str, Any]) -> Issue:
    body = str(raw.get("body") or "")
    title = str(raw["title"])
    goals, constraints, criteria = extract_signals(title, body)
    labels = [str(item.get("name", "")) for item in raw.get("labels", [])]
    return Issue(
        number=int(raw["number"]),
        title=title,
        body=body,
        url=str(raw.get("url") or ""),
        labels=[label for label in labels if label],
        goals=goals,
        constraints=constraints,
        acceptance_criteria=criteria,
    )


def extract_signals(title: str, body: str) -> tuple[list[str], list[str], list[str]]:
    """Extract lightweight goals, constraints and acceptance-style list items without an LLM."""

    lines = [line.strip(" -*\t") for line in body.splitlines() if line.strip()]
    goals = [title]
    constraints: list[str] = []
    criteria: list[str] = []
    active: str | None = None
    for line in lines:
        lower = line.casefold()
        if "acceptance" in lower or "expected" in lower:
            active = "criteria"
            continue
        if "constraint" in lower or "must not" in lower or "do not" in lower:
            active = "constraints"
            constraints.append(line)
            continue
        if active == "criteria":
            criteria.append(line)
        elif active == "constraints":
            constraints.append(line)
        elif len(goals) < 4:
            goals.append(line)
    return goals, constraints, criteria
