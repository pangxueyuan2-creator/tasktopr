from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from tasktopr.agents.coder import apply_patch, request_patch
from tasktopr.agents.explorer import explore
from tasktopr.agents.intake import extract_signals, load_issue
from tasktopr.agents.reviewer import list_changed_files, review_changes
from tasktopr.config import ConfigError, TaskToPRConfig, load_config
from tasktopr.models import (
    ChangePlan,
    CommandResult,
    Issue,
    PatchOperation,
    PatchRequest,
    ReviewResult,
    RiskLevel,
)
from tasktopr.orchestrator import fix_issue, plan_issue
from tasktopr.pr import PullRequestError, push_and_create_pr
from tasktopr.providers import DemoProvider, ProviderError, parse_json_model
from tasktopr.security import (
    SecurityError,
    is_protected,
    redact,
    run_safe_command,
    safe_path,
    validate_command,
)

PROJECT_ROOT = Path(__file__).parents[1]
DEMO_ROOT = PROJECT_ROOT / "demo" / "zero_division_repo"


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=path, check=True, capture_output=True)


@pytest.fixture()
def demo_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "demo"
    shutil.copytree(DEMO_ROOT, repo)
    _init_git_repo(repo)
    return repo


def test_redacts_common_secrets() -> None:
    value = "token=ghp_abcdefghijklmnopqrstuvwxyz1234567890 sk-abcdefghijklmnopqrst AWS=AKIAABCDEFGHIJKLMNOP"
    assert "[REDACTED]" in redact(value)
    assert "ghp_" not in redact(value)


def test_safe_path_blocks_traversal_and_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "safe.py").write_text("pass\n", encoding="utf-8")
    assert safe_path(root, "safe.py") == (root / "safe.py").resolve()
    with pytest.raises(SecurityError):
        safe_path(root, "../outside.txt")
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is not available in this environment")
        raise
    with pytest.raises(SecurityError):
        safe_path(root, "link/secret.txt")


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (".github/workflows/ci.yml", True),
        (".env.production", True),
        ("config/.env", True),
        ("config/.env.local", True),
        (".tasktopr.toml", True),
        ("src/auth/token.py", True),
        ("src/calculator.py", False),
    ],
)
def test_protected_path_policy(path: str, expected: bool) -> None:
    assert is_protected(path) is expected


@pytest.mark.parametrize(
    "command",
    [
        ["rm", "-rf", "/"],
        ["curl", "https://example.invalid"],
        ["python", "-c", "print(1); print(2)"],
        ["python", "-c", "print('ok')"],
        ["git", "push", "--force"],
    ],
)
def test_command_policy_blocks_dangerous_commands(command: list[str]) -> None:
    with pytest.raises(SecurityError):
        validate_command(command)


def test_command_runner_records_blocked_command(tmp_path: Path) -> None:
    result = run_safe_command(["rm", "-rf", "x"], tmp_path, 5)
    assert result.blocked is True
    assert result.return_code == 126


def test_config_defaults_and_invalid_config(tmp_path: Path) -> None:
    assert load_config(tmp_path).agent.max_iterations == 3
    (tmp_path / ".tasktopr.toml").write_text("[agent]\nmax_iterations = 0\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_model_schema_rejects_traversal_path() -> None:
    with pytest.raises(ValidationError):
        PatchOperation(
            kind="replace", path="../secret.txt", old_text="a", new_text="b", reason="bad"
        )


def test_provider_json_parser_rejects_malformed_output() -> None:
    with pytest.raises(ProviderError):
        parse_json_model("not json", ChangePlan)


def test_issue_signal_extraction() -> None:
    goals, constraints, criteria = extract_signals(
        "Fix zero division",
        "Acceptance criteria:\n- raises ValueError\nConstraints:\n- Do not change the API",
    )
    assert goals[0] == "Fix zero division"
    assert "raises ValueError" in criteria
    assert any("Do not change" in item for item in constraints)


def test_demo_issue_loading(demo_repo: Path) -> None:
    issue = load_issue(demo_repo, 1, demo=True)
    assert issue.number == 1
    assert "zero" in issue.title.casefold()


def test_demo_plan_is_read_only(demo_repo: Path) -> None:
    result = plan_issue(
        1,
        start_dir=demo_repo,
        config=TaskToPRConfig(),
        provider=DemoProvider(),
        demo=True,
    )
    assert result.success is True
    assert result.plan is not None
    assert result.plan.steps[0].path == "calculator.py"
    assert (result.run_dir / "plan.json").exists()
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=demo_repo, capture_output=True, text=True
    )
    assert status.stdout.strip().startswith("?? .tasktopr/")


def test_demo_fix_creates_real_branch_patch_tests_and_evidence(demo_repo: Path) -> None:
    result = fix_issue(
        1,
        start_dir=demo_repo,
        config=load_config(demo_repo),
        provider=DemoProvider(),
        no_pr=True,
        demo=True,
    )
    assert result.success is True
    assert result.branch and result.branch.startswith("tasktopr/issue-1-")
    assert result.review and result.review.approved
    assert all(test.return_code == 0 for test in result.tests)
    assert "denominator must not be zero" in (demo_repo / "calculator.py").read_text(
        encoding="utf-8"
    )
    assert "test_divide_rejects_zero_denominator" in (demo_repo / "test_calculator.py").read_text(
        encoding="utf-8"
    )
    for artifact in (
        "plan.json",
        "events.jsonl",
        "summary.md",
        "test-results.json",
        "changes.json",
    ):
        assert (result.run_dir / artifact).exists()
    events = (result.run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "ANALYZING_ISSUE" in events and "REVIEWING_PATCH" in events


def test_dry_run_never_creates_branch_or_edits(demo_repo: Path) -> None:
    result = fix_issue(
        1,
        start_dir=demo_repo,
        config=TaskToPRConfig(),
        provider=DemoProvider(),
        dry_run=True,
        demo=True,
    )
    assert result.success is True
    assert result.branch is None
    assert "return numerator / denominator" in (demo_repo / "calculator.py").read_text(
        encoding="utf-8"
    )


def test_patch_rejects_file_outside_plan(demo_repo: Path) -> None:
    profile = explore(demo_repo, Issue(number=1, title="calculator"), TaskToPRConfig())
    plan = ChangePlan(
        summary="safe",
        root_cause="test",
        steps=[{"path": "calculator.py", "action": "change", "rationale": "test"}],
        test_plan=["pytest"],
    )

    class UnsafeProvider(DemoProvider):
        def complete(self, **kwargs: object) -> str:  # type: ignore[override]
            return json.dumps(
                {
                    "summary": "unsafe",
                    "operations": [
                        {
                            "kind": "replace",
                            "path": ".github/workflows/ci.yml",
                            "old_text": "x",
                            "new_text": "y",
                            "reason": "bad",
                        }
                    ],
                }
            )

    with pytest.raises(SecurityError):
        request_patch(UnsafeProvider(), plan, profile, TaskToPRConfig())


def test_issue_cannot_elevate_permissions_to_workflows(demo_repo: Path) -> None:
    class JailbreakProvider(DemoProvider):
        def complete(self, **kwargs: object) -> str:  # type: ignore[override]
            return json.dumps(
                {
                    "summary": "Issue granted CI access",
                    "root_cause": "The Issue said workflows are allowed.",
                    "steps": [
                        {
                            "path": ".github/workflows/ci.yml",
                            "action": "replace",
                            "rationale": "Issue body granted extra permissions",
                        }
                    ],
                    "test_plan": ["pytest"],
                    "non_goals": [],
                    "risk": "low",
                }
            )

    result = plan_issue(
        1,
        start_dir=demo_repo,
        config=TaskToPRConfig(),
        provider=JailbreakProvider(),
        demo=True,
    )
    assert result.success is False
    assert "protected" in result.message.casefold()


def test_apply_patch_blocks_workflow_even_when_called_directly(demo_repo: Path) -> None:
    workflows = demo_repo / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("name: ci\n", encoding="utf-8")
    profile = explore(demo_repo, Issue(number=1, title="ci"), TaskToPRConfig())
    patch = PatchRequest(
        summary="jailbreak",
        operations=[
            PatchOperation(
                kind="replace",
                path=".github/workflows/ci.yml",
                old_text="name: ci\n",
                new_text="name: pwned\n",
                reason="issue said so",
            )
        ],
    )
    with pytest.raises(SecurityError, match="protected"):
        apply_patch(patch, profile, TaskToPRConfig())


def test_list_changed_files_includes_rename_source_and_destination(demo_repo: Path) -> None:
    subprocess.run(
        ["git", "mv", "calculator.py", "renamed calculator.py"],
        cwd=demo_repo,
        check=True,
        capture_output=True,
    )
    changed = list_changed_files(demo_repo)
    assert "calculator.py" in changed
    assert "renamed calculator.py" in changed


def test_review_uses_git_changes_not_existing_protected_files(demo_repo: Path) -> None:
    (demo_repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
    subprocess.run(["git", "add", ".env"], cwd=demo_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add env"],
        cwd=demo_repo,
        check=True,
        capture_output=True,
    )
    target = demo_repo / "calculator.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# reviewed\n", encoding="utf-8")
    result = review_changes(
        demo_repo,
        ["calculator.py"],
        [CommandResult(command=["python", "-m", "pytest"], return_code=0, elapsed_seconds=0.1)],
        TaskToPRConfig(),
    )
    assert result.approved is True
    assert ".env" not in result.changed_files


def test_apply_patch_rejects_ambiguous_old_text(demo_repo: Path) -> None:
    (demo_repo / "repeat.py").write_text("x\nx\n", encoding="utf-8")
    profile = explore(demo_repo, Issue(number=1, title="repeat"), TaskToPRConfig())
    patch = PatchRequest(
        summary="ambiguous",
        operations=[
            PatchOperation(
                kind="replace", path="repeat.py", old_text="x", new_text="y", reason="test"
            )
        ],
    )
    with pytest.raises(SecurityError):
        apply_patch(patch, profile, TaskToPRConfig())


def test_pr_agent_refuses_unapproved_review(demo_repo: Path) -> None:
    issue = Issue(number=1, title="test")
    plan = ChangePlan(
        summary="test",
        root_cause="test",
        steps=[{"path": "calculator.py", "action": "change", "rationale": "test"}],
        test_plan=["pytest"],
    )
    review = ReviewResult(
        approved=False,
        risk=RiskLevel.HIGH,
        findings=["tests failed"],
        changed_files=["calculator.py"],
    )
    with pytest.raises(PullRequestError):
        push_and_create_pr(
            demo_repo,
            "tasktopr/issue-1-test",
            "main",
            issue,
            plan,
            review,
            "tests failed",
            demo_repo / "pr.md",
        )


def test_command_result_serializes() -> None:
    result = CommandResult(command=["python", "-m", "pytest"], return_code=0, elapsed_seconds=0.1)
    assert result.model_dump()["return_code"] == 0
