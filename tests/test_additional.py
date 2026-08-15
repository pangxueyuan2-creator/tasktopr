from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import ClassVar

import pytest
from typer.testing import CliRunner

from tasktopr import __version__
from tasktopr.agents.explorer import compact_context, explore
from tasktopr.agents.intake import IssueIntakeError, load_issue
from tasktopr.agents.reviewer import review_changes
from tasktopr.cli import app
from tasktopr.config import TaskToPRConfig, provider_api_key, redacted_config
from tasktopr.models import ChangePlan, CommandResult, Issue, ReviewResult, RiskLevel
from tasktopr.pr import commit_changes, create_branch, push_and_create_pr
from tasktopr.providers.base import ProviderError, parse_json_model
from tasktopr.providers.http import AnthropicProvider, OpenAICompatibleProvider, build_provider
from tasktopr.security import run_safe_command

RUNNER = CliRunner()


def test_cli_version_prints_package_version() -> None:
    result = RUNNER.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_positive_command_and_timeout_are_evidenced(tmp_path: Path) -> None:
    ok_script = tmp_path / "ok.py"
    ok_script.write_text("print('ok')\n", encoding="utf-8")
    okay = run_safe_command(["python", str(ok_script)], tmp_path, 5)
    assert okay.return_code == 0
    assert "ok" in okay.stdout
    slow_script = tmp_path / "slow.py"
    slow_script.write_text("import time\ntime.sleep(1)\n", encoding="utf-8")
    timed_out = run_safe_command(["python", str(slow_script)], tmp_path, 0)
    assert timed_out.return_code == 124
    assert "Timed out" in timed_out.reason


def test_inline_interpreter_payload_is_blocked(tmp_path: Path) -> None:
    result = run_safe_command(["python", "-c", "print('ok')"], tmp_path, 5)
    assert result.blocked is True
    assert result.return_code == 126
    assert "Inline interpreter" in result.reason


def test_python_commands_use_active_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_safe_command(["python", "-m", "pytest", "-q"], tmp_path, 5)
    assert result.return_code == 0
    assert commands == [[sys.executable, "-m", "pytest", "-q"]]

    commands.clear()
    result = run_safe_command(["python.exe", "-m", "pytest", "-q"], tmp_path, 5)
    assert result.return_code == 0
    assert commands == [[sys.executable, "-m", "pytest", "-q"]]


def test_git_root_decodes_utf8_paths_with_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tasktopr.agents.explorer as explorer_module

    calls: list[dict[str, object]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout=str(tmp_path), stderr="")

    monkeypatch.setattr(explorer_module.subprocess, "run", fake_run)
    assert explorer_module.git_root(tmp_path) == tmp_path.resolve()
    assert calls[0].get("encoding") == "utf-8"
    assert calls[0].get("errors") == "replace"


def test_context_excludes_env_and_redacts_content(demo_repo: Path) -> None:
    (demo_repo / ".env").write_text("OPENAI_API_KEY=sk-abcdefghijklmnopqrst\n", encoding="utf-8")
    issue = Issue(number=1, title="calculator")
    config = TaskToPRConfig()
    profile = explore(demo_repo, issue, config)
    context = compact_context(profile, issue, config)
    assert ".env" not in profile.relevant_files
    assert "sk-abcdefghijklmnopqrst" not in context


def test_reviewer_approves_expected_change_with_passing_tests(demo_repo: Path) -> None:
    target = demo_repo / "calculator.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# reviewed\n", encoding="utf-8")
    result = review_changes(
        demo_repo,
        ["calculator.py"],
        [CommandResult(command=["python", "-m", "pytest"], return_code=0, elapsed_seconds=0.1)],
        TaskToPRConfig(),
    )
    assert result.approved is True
    assert result.scope_ok is True


def test_reviewer_ignores_git_autocrlf_continuation_without_warning_prefix(
    demo_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tasktopr.agents.reviewer as reviewer_module

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command[:2] == ["git", "diff"] and "--check" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="",
                stderr="The file will have its original line endings in your working directory\n",
            )
        if command[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(reviewer_module.subprocess, "run", fake_run)
    result = review_changes(
        demo_repo,
        ["calculator.py"],
        [CommandResult(command=["python", "-m", "pytest"], return_code=0, elapsed_seconds=0.1)],
        TaskToPRConfig(),
    )
    assert result.approved is True
    assert not any("whitespace" in finding.casefold() for finding in result.findings)


def test_reviewer_ignores_git_autocrlf_warnings(
    demo_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tasktopr.agents.reviewer as reviewer_module

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command[:2] == ["git", "diff"] and "--check" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="",
                stderr=(
                    "warning: LF will be replaced by CRLF in calculator.py.\n"
                    "The file will have its original line endings in your working directory\n"
                ),
            )
        if command[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(reviewer_module.subprocess, "run", fake_run)
    result = review_changes(
        demo_repo,
        ["calculator.py"],
        [CommandResult(command=["python", "-m", "pytest"], return_code=0, elapsed_seconds=0.1)],
        TaskToPRConfig(),
    )
    assert result.approved is True
    assert not any("whitespace" in finding.casefold() for finding in result.findings)


def test_reviewer_still_reports_real_whitespace_errors(
    demo_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tasktopr.agents.reviewer as reviewer_module

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command[:2] == ["git", "diff"] and "--check" in command:
            return subprocess.CompletedProcess(
                command,
                2,
                stdout="calculator.py:3: trailing whitespace.\n",
                stderr="",
            )
        if command[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(reviewer_module.subprocess, "run", fake_run)
    result = review_changes(
        demo_repo,
        ["calculator.py"],
        [CommandResult(command=["python", "-m", "pytest"], return_code=0, elapsed_seconds=0.1)],
        TaskToPRConfig(),
    )
    assert result.approved is False
    assert any("trailing whitespace" in finding for finding in result.findings)


def test_reviewer_blocks_unexpected_change(demo_repo: Path) -> None:
    (demo_repo / "extra.py").write_text("pass\n", encoding="utf-8")
    result = review_changes(
        demo_repo,
        ["calculator.py"],
        [CommandResult(command=["python"], return_code=0, elapsed_seconds=0.1)],
        TaskToPRConfig(),
    )
    assert result.approved is False
    assert result.risk == RiskLevel.BLOCKED


def test_branch_and_local_commit_are_isolated(demo_repo: Path) -> None:
    issue = Issue(number=2, title="Add safe divide")
    plan = ChangePlan(
        summary="Guard the denominator.",
        root_cause="Missing validation.",
        steps=[{"path": "calculator.py", "action": "edit", "rationale": "test"}],
        test_plan=["pytest"],
    )
    branch = create_branch(demo_repo, issue, "main")
    target = demo_repo / "calculator.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# branch change\n", encoding="utf-8")
    revision = commit_changes(demo_repo, ["calculator.py"], issue, plan)
    active = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=demo_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert active == branch
    assert len(revision) == 40


def test_pr_agent_refuses_default_branch_even_with_approval(demo_repo: Path) -> None:
    issue = Issue(number=3, title="test")
    plan = ChangePlan(
        summary="test",
        root_cause="test",
        steps=[{"path": "calculator.py", "action": "test", "rationale": "test"}],
        test_plan=["pytest"],
    )
    review = ReviewResult(
        approved=True,
        risk=RiskLevel.LOW,
        changed_files=["calculator.py"],
        scope_ok=True,
        tests_ok=True,
    )
    with pytest.raises(Exception, match="default or base branch"):
        push_and_create_pr(
            demo_repo,
            "main",
            "main",
            issue,
            plan,
            review,
            "passed",
            demo_repo / "body.md",
        )


def test_provider_parses_fenced_json_and_rejects_missing_key() -> None:
    plan = parse_json_model(
        '```json\n{"summary": "s", "root_cause": "r", "steps": [{"path": "a.py", "action": "a", "rationale": "r"}], "test_plan": ["pytest"]}\n```',
        ChangePlan,
    )
    assert plan.summary == "s"
    with pytest.raises(ProviderError):
        parse_json_model("{}", ChangePlan)


def test_http_provider_response_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    import tasktopr.providers.http as http_module

    monkeypatch.setattr(
        http_module,
        "_post_with_retry",
        lambda *args, **kwargs: {"choices": [{"message": {"content": "answer"}}]},
    )
    provider = OpenAICompatibleProvider(api_key="test", base_url="https://example.invalid/v1")
    assert provider.complete(system="s", user="u", config=TaskToPRConfig().agent) == "answer"
    monkeypatch.setattr(
        http_module,
        "_post_with_retry",
        lambda *args, **kwargs: {"content": [{"type": "text", "text": "hello"}]},
    )
    assert (
        AnthropicProvider(api_key="test").complete(
            system="s", user="u", config=TaskToPRConfig().agent
        )
        == "hello"
    )


def test_environment_provider_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "secret")
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "https://example.invalid/v1")
    assert isinstance(build_provider("openai-compatible"), OpenAICompatibleProvider)
    assert provider_api_key("openai-compatible") == "secret"
    config = TaskToPRConfig()
    config.agent.provider = "openai-compatible"
    assert redacted_config(config)["provider_key_present"] is True
    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY")
    monkeypatch.delenv("OPENAI_COMPATIBLE_BASE_URL")


def test_issue_intake_reports_bad_demo_file(demo_repo: Path) -> None:
    (demo_repo / ".tasktopr-demo-issue.json").write_text("not json", encoding="utf-8")
    with pytest.raises(IssueIntakeError):
        load_issue(demo_repo, 1, demo=True)


def test_demo_issue_builtin_fallback_when_file_missing(demo_repo: Path) -> None:
    (demo_repo / ".tasktopr-demo-issue.json").unlink()
    issue = load_issue(demo_repo, 1, demo=True)
    assert issue.number == 1
    assert "zero" in issue.title.casefold()
    with pytest.raises(IssueIntakeError, match="no builtin demo"):
        load_issue(demo_repo, 2, demo=True)


def test_cli_help_plan_status_and_config(demo_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(demo_repo)
    assert RUNNER.invoke(app, ["--help"]).exit_code == 0
    planned = RUNNER.invoke(app, ["plan", "1", "--demo"])
    assert planned.exit_code == 0
    assert "Plan ready" in planned.stdout
    status = RUNNER.invoke(app, ["status"])
    assert status.exit_code == 0
    displayed = RUNNER.invoke(app, ["config"])
    assert displayed.exit_code == 0


def test_cli_doctor_runs_inside_repository(
    demo_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(demo_repo)
    result = RUNNER.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "TaskToPR doctor" in result.stdout


def test_cli_doctor_survives_missing_gh(demo_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """doctor must not crash when the GitHub CLI binary is absent."""

    monkeypatch.chdir(demo_repo)
    real_which = shutil.which

    def fake_which(cmd: str, mode: int = os.F_OK, path: str | None = None) -> str | None:
        if cmd == "gh":
            return None
        return real_which(cmd, mode=mode, path=path)

    monkeypatch.setattr(shutil, "which", fake_which)
    result = RUNNER.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "TaskToPR doctor" in result.stdout
    assert "gh not found" in result.stdout or "WARN" in result.stdout


def test_cli_doctor_uses_explicit_subprocess_decoding(
    demo_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tasktopr.cli as cli_module

    monkeypatch.chdir(demo_repo)
    monkeypatch.setattr(cli_module, "git_root", lambda _start: demo_repo)
    monkeypatch.setattr(cli_module.shutil, "which", lambda _command: "available")
    calls: list[dict[str, object]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    result = RUNNER.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert calls
    assert all(call.get("encoding") == "utf-8" for call in calls)
    assert all(call.get("errors") == "replace" for call in calls)


def test_cli_fix_dry_run_and_review(demo_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(demo_repo)
    fixed = RUNNER.invoke(app, ["fix", "1", "--demo", "--dry-run"])
    assert fixed.exit_code == 0
    assert "Dry run completed" in fixed.stdout
    reviewed = RUNNER.invoke(app, ["review"])
    assert reviewed.exit_code == 0


def test_cli_review_applies_boundary(demo_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(demo_repo)
    notes = demo_repo / "notes.md"
    notes.write_text("out of scope\n", encoding="utf-8")
    help_result = RUNNER.invoke(app, ["review", "--help"])
    assert help_result.exit_code == 0
    assert "--boundary" in help_result.stdout
    unconstrained = RUNNER.invoke(app, ["review"])
    assert unconstrained.exit_code == 0
    boundary = demo_repo.parent / "review-boundary.json"
    boundary.write_text(
        json.dumps(
            {
                "schema": "https://patchwitness.dev/agent-boundary/v1",
                "version": 1,
                "id": "review-scope",
                "exclusive_allow": True,
                "allowed_paths": ["calculator.py", "test_calculator.py"],
                "denied_paths": [".github/workflows/**"],
                "protected_paths": [".github/workflows/**"],
                "required_checks": [],
            }
        ),
        encoding="utf-8",
    )
    constrained = RUNNER.invoke(app, ["review", "--boundary", str(boundary)])
    assert constrained.exit_code == 1
    assert "Protected files changed" in constrained.stdout


def test_orchestrator_records_provider_failure(demo_repo: Path) -> None:
    from tasktopr.orchestrator import fix_issue, plan_issue
    from tasktopr.providers.base import ModelProvider

    class BrokenProvider(ModelProvider):
        def complete(self, **kwargs: object) -> str:  # type: ignore[override]
            raise ProviderError("provider key sk-abcdefghijklmnopqrst failed")

    planned = plan_issue(
        1,
        start_dir=demo_repo,
        config=TaskToPRConfig(),
        provider=BrokenProvider(),
        demo=True,
    )
    assert planned.success is False
    assert "[REDACTED]" in planned.message
    repaired = fix_issue(
        1,
        start_dir=demo_repo,
        config=TaskToPRConfig(),
        provider=BrokenProvider(),
        demo=True,
    )
    assert repaired.success is False
    assert (repaired.run_dir / "summary.md").exists()


def test_http_retry_success_and_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    import tasktopr.providers.http as http_module

    class Response:
        def __init__(self, payload: object, error: Exception | None = None) -> None:
            self.payload = payload
            self.error = error

        def raise_for_status(self) -> None:
            if self.error:
                raise self.error

        def json(self) -> object:
            return self.payload

    class Client:
        calls: ClassVar[int] = 0
        responses: ClassVar[list[Response]] = []

        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, *args: object, **kwargs: object) -> Response:
            del args, kwargs
            Client.calls += 1
            return Client.responses.pop(0)

    monkeypatch.setattr(http_module.httpx, "Client", Client)
    Client.responses = [Response({"message": "ok"})]
    assert http_module._post_with_retry(
        "https://example.invalid", headers={}, payload={}, timeout=1, retries=0
    ) == {"message": "ok"}
    Client.responses = [Response([], None)]
    with pytest.raises(ProviderError, match="non-object"):
        http_module._post_with_retry(
            "https://example.invalid", headers={}, payload={}, timeout=1, retries=0
        )
    Client.responses = [Response({}, http_module.httpx.ConnectError("down"))] * 2
    with pytest.raises(ProviderError, match="Model request failed"):
        http_module._post_with_retry(
            "https://example.invalid", headers={}, payload={}, timeout=1, retries=1
        )


def test_provider_response_errors_and_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    import tasktopr.providers.http as http_module

    monkeypatch.setattr(http_module, "_post_with_retry", lambda *args, **kwargs: {})
    with pytest.raises(ProviderError, match="message content"):
        OpenAICompatibleProvider(api_key="x", base_url="https://example.invalid").complete(
            system="s", user="u", config=TaskToPRConfig().agent
        )
    with pytest.raises(ProviderError, match="Unknown provider"):
        build_provider("unknown")


def test_pr_body_creation_path_with_fake_remote_calls(
    demo_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tasktopr.pr as pr_module

    issue = Issue(number=4, title="test")
    plan = ChangePlan(
        summary="summary",
        root_cause="root",
        steps=[{"path": "calculator.py", "action": "edit", "rationale": "test"}],
        test_plan=["pytest"],
    )
    review = ReviewResult(
        approved=True,
        risk=RiskLevel.LOW,
        changed_files=["calculator.py"],
        scope_ok=True,
        tests_ok=True,
    )

    class Completed:
        stdout = "https://github.com/example/repo/pull/4\n"

    calls: list[list[str]] = []

    def fake_run(command: list[str], cwd: Path) -> Completed:
        del cwd
        calls.append(command)
        return Completed()

    monkeypatch.setattr(pr_module, "_run_git", fake_run)
    monkeypatch.setattr(pr_module, "_run", fake_run)
    url = push_and_create_pr(
        demo_repo,
        "tasktopr/issue-4-test",
        "main",
        issue,
        plan,
        review,
        "passed",
        demo_repo / "body.md",
    )
    assert url.endswith("/4")
    assert (demo_repo / "body.md").read_text(encoding="utf-8").startswith("## Problem")
    assert calls[0][:3] == ["git", "push", "-u"]
