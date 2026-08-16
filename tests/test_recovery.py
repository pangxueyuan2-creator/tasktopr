from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tasktopr.models import Issue
from tasktopr.pr import PullRequestError, create_branch


def test_create_branch_refuses_existing_tasktopr_branch(tmp_path: Path) -> None:
    """A second run for the same issue must not silently reuse or overwrite the
    branch left behind by a previous run."""

    git = ["git", "-C", str(tmp_path)]
    subprocess.run([*git, "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run([*git, "config", "user.email", "ci@example.invalid"], check=True)
    subprocess.run([*git, "config", "user.name", "CI"], check=True)
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run([*git, "add", "app.py"], check=True, capture_output=True)
    subprocess.run([*git, "commit", "-m", "base"], check=True, capture_output=True)
    subprocess.run([*git, "switch", "-c", "tasktopr/issue-7-fix-typo"], check=True)
    subprocess.run([*git, "switch", "main"], check=True)

    issue = Issue(number=7, title="Fix Typo!")
    with pytest.raises(PullRequestError, match="Refusing to reuse"):
        create_branch(tmp_path, issue, "main")

    branches = subprocess.run(
        [*git, "branch", "--list", "tasktopr/issue-7-fix-typo"],
        capture_output=True,
        text=True,
    )
    assert branches.stdout.strip(), "the pre-existing branch must be left untouched"


def test_push_failure_after_commit_leaves_observable_journal(
    demo_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure between commit and push must leave restart-observable state:
    the journal, the created branch and the committed change all survive."""

    import tasktopr.orchestrator as orchestrator_module
    from tasktopr.config import load_config
    from tasktopr.orchestrator import fix_issue
    from tasktopr.providers.demo import DemoProvider

    def failing_push(*args: object, **kwargs: object) -> str:
        raise PullRequestError("simulated push failure")

    monkeypatch.setattr(orchestrator_module, "push_and_create_pr", failing_push)
    result = fix_issue(
        1,
        start_dir=demo_repo,
        config=load_config(demo_repo),
        provider=DemoProvider(),
        demo=True,
    )

    assert result.success is False
    assert "simulated push failure" in result.message
    for artifact in ("summary.md", "events.jsonl", "changes.json", "test-results.json"):
        assert (result.run_dir / artifact).exists()
    events = (result.run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "CREATING_PR" in events and "FAILED" in events
    branches = subprocess.run(
        ["git", "-C", str(demo_repo), "branch", "--list", "tasktopr/issue-1-*"],
        capture_output=True,
        text=True,
    )
    assert branches.stdout.strip(), "the branch must survive the push failure"
    committed = subprocess.run(
        ["git", "-C", str(demo_repo), "log", "-1", "--format=%s"],
        capture_output=True,
        text=True,
    )
    assert "resolve #1" in committed.stdout
