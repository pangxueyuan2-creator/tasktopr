from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tasktopr.agents.reviewer import review_changes
from tasktopr.config import TaskToPRConfig, load_config
from tasktopr.events import RunJournal
from tasktopr.models import CommandResult
from tasktopr.orchestrator import fix_issue
from tasktopr.providers import DemoProvider
from tasktopr.receipt import changed_paths, digest, head, snapshot, validate_path
from tasktopr.security import SecurityError


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _fix(repo: Path, *, no_pr: bool = True):
    return fix_issue(
        1, start_dir=repo, config=load_config(repo), provider=DemoProvider(), demo=True, no_pr=no_pr
    )


def _receipt(result):
    data = json.loads((result.run_dir / "execution-receipt.json").read_text(encoding="utf-8"))
    assert data["receipt_sha256"] == digest(data["payload"])
    return data["payload"]


@pytest.mark.parametrize(
    "name",
    [
        "../x",
        "a/../../x",
        "/etc/passwd",
        "C:/x",
        "C:x",
        "//server/share/x",
        "a\\b",
        "a//b",
        "a/./b",
        ".git/config",
        "A/.GIT/config",
        "a/..",
        "x.",
        "x ",
        "NUL",
        "CON.txt",
        "COM1.py",
        "LPT9.txt",
        "aux",
        "PRN",
        "a\x00b",
        "a\nb",
        "a\tb",
        "cafe\u0301.py",
        "",
        "a:stream",
        "a/NUL/x",
    ],
)
def test_ambiguous_windows_and_unicode_paths_fail_closed(tmp_path: Path, name: str) -> None:
    with pytest.raises(SecurityError):
        validate_path(tmp_path, name)


@pytest.mark.parametrize("name", ["a.py", "dir/a.py", "你好.py", "café.py", "a b.py"])
def test_canonical_paths_preserve_identity(tmp_path: Path, name: str) -> None:
    assert validate_path(tmp_path, name) == tmp_path / name


def test_local_receipt_is_content_addressed_without_claiming_head_or_ci(demo_repo: Path) -> None:
    base = head(demo_repo)
    result = _fix(demo_repo)
    assert result.success, result.message
    receipt = _receipt(result)
    assert receipt["base_sha"] == base
    assert receipt["result_head_sha"] is None and receipt["tested_head_sha"] is None
    assert receipt["decision"] == "REVIEW_REQUIRED"
    assert receipt["ci"] == receipt["human_review"] == "unknown"
    assert len(receipt["changed_file_manifest"]) == 2
    assert receipt["tests"] == {"status": "pass", "count": 1}


def test_public_receipt_omits_prompt_command_and_output(
    demo_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tasktopr.orchestrator as module

    private = "PRIVATE_PROMPT_AND_COMMAND_OUTPUT_12345"
    original = module.load_issue

    def issue(*args, **kwargs):
        value = original(*args, **kwargs)
        value.body = private
        return value

    monkeypatch.setattr(module, "load_issue", issue)
    monkeypatch.setattr(
        module,
        "run_quality_checks",
        lambda p, c: [
            CommandResult(
                command=p.test_commands[0], return_code=0, elapsed_seconds=1, stdout=private
            )
        ],
    )
    result = _fix(demo_repo)
    assert result.success, result.message
    text = (result.run_dir / "execution-receipt.json").read_text()
    assert private not in text and "unittest" not in text and str(demo_repo) not in text
    assert _receipt(result)["test_result_sha256"]


@pytest.mark.parametrize("mutation", ["same_file", "new_file", "tracked_cache", "branch", "head"])
def test_test_time_mutation_never_becomes_approved_evidence(
    demo_repo: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    import tasktopr.orchestrator as module

    if mutation == "tracked_cache":
        (demo_repo / ".pytest_cache").mkdir()
        (demo_repo / ".pytest_cache" / "tracked.py").write_text("x=1\n")
        _git(demo_repo, "add", "-f", ".pytest_cache/tracked.py")
        _git(demo_repo, "commit", "-m", "tracked cache fixture")

    def altered(profile, config):
        if mutation == "same_file":
            (demo_repo / "calculator.py").write_text("BROKEN = True\n")
        elif mutation == "new_file":
            (demo_repo / "surprise.py").write_text("x=1\n")
        elif mutation == "tracked_cache":
            (demo_repo / ".pytest_cache" / "tracked.py").write_text("x=2\n")
        elif mutation == "branch":
            _git(demo_repo, "branch", "-m", "other-branch")
        else:
            _git(
                demo_repo, "-c", "core.hooksPath=", "commit", "--allow-empty", "-m", "interference"
            )
        return [CommandResult(command=profile.test_commands[0], return_code=0, elapsed_seconds=0)]

    monkeypatch.setattr(module, "run_quality_checks", altered)
    result = _fix(demo_repo)
    assert not result.success
    assert _receipt(result)["decision"] == "FAIL"


def test_committed_candidate_is_tested_again_before_push(
    demo_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tasktopr.orchestrator as module

    seen = []
    original = module.run_quality_checks

    def record(profile, config):
        seen.append(head(demo_repo))
        return original(profile, config)

    def push(*args, **kwargs):
        assert len(seen) == 2 and seen[0] != seen[1]
        assert args[5].changed_files == ["calculator.py", "test_calculator.py"]
        return "https://example.invalid/pr/1"

    monkeypatch.setattr(module, "run_quality_checks", record)
    monkeypatch.setattr(module, "push_and_create_pr", push)
    result = _fix(demo_repo, no_pr=False)
    assert result.success, result.message
    receipt = _receipt(result)
    assert receipt["tested_head_sha"] == receipt["result_head_sha"] == head(demo_repo)
    assert receipt["base_sha"] == seen[0]
    assert receipt["phase"] == "pr_created"


def test_postcommit_failure_preserves_candidate_but_never_pushes(
    demo_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tasktopr.orchestrator as module

    original = module.run_quality_checks
    calls = []

    def record(profile, config):
        calls.append(head(demo_repo))
        if len(calls) == 2:
            return [
                CommandResult(command=profile.test_commands[0], return_code=1, elapsed_seconds=0)
            ]
        return original(profile, config)

    monkeypatch.setattr(module, "run_quality_checks", record)
    monkeypatch.setattr(
        module, "push_and_create_pr", lambda *a: pytest.fail("must not push failed candidate")
    )
    result = _fix(demo_repo, no_pr=False)
    assert not result.success and len(calls) == 2
    receipt = _receipt(result)
    assert receipt["decision"] == "FAIL" and receipt["result_head_sha"] == head(demo_repo)


def test_dirty_intake_is_rejected_before_patch(demo_repo: Path) -> None:
    (demo_repo / "unrelated.py").write_text("x=1\n")
    result = _fix(demo_repo)
    assert not result.success
    assert "clean declared base" in result.message
    assert _git(demo_repo, "branch", "--show-current") == "main"


def test_nonbase_branch_does_not_plan_against_other_content(demo_repo: Path) -> None:
    _git(demo_repo, "switch", "-c", "unrelated")
    result = _fix(demo_repo)
    assert not result.success and "clean declared base" in result.message


def test_nul_manifest_preserves_unicode_and_spaces(demo_repo: Path) -> None:
    name = "你好 world.py"
    (demo_repo / name).write_text("x=1\n", encoding="utf-8")
    assert changed_paths(demo_repo) == [name]
    assert name in snapshot(demo_repo)


def test_empty_tests_are_not_a_pass(demo_repo: Path) -> None:
    review = review_changes(demo_repo, [], [], TaskToPRConfig())
    assert not review.approved and not review.tests_ok


@pytest.mark.parametrize("limit", ["MAX_FILES", "MAX_FILE_BYTES", "MAX_TOTAL_BYTES"])
def test_evidence_budget_failure_is_explicit(
    demo_repo: Path, monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    import tasktopr.receipt as module

    monkeypatch.setattr(module, limit, 1)
    with pytest.raises(SecurityError):
        snapshot(demo_repo)


def test_nested_git_repository_is_rejected(demo_repo: Path) -> None:
    (demo_repo / "nested" / ".git").mkdir(parents=True)
    (demo_repo / "nested" / "a.py").write_text("x=1\n")
    with pytest.raises(SecurityError, match="Nested"):
        snapshot(demo_repo)


@pytest.mark.parametrize("name", ["../outside", "C:stream", "a/b", "a\\b"])
def test_journal_rejects_non_filename_artifacts(demo_repo: Path, name: str) -> None:
    journal = RunJournal(demo_repo)
    with pytest.raises(SecurityError):
        journal.write_json(name, {})


def test_atomic_checkpoint_preserves_previous_content_on_serialization_error(
    demo_repo: Path,
) -> None:
    journal = RunJournal(demo_repo)
    path = journal.write_json("receipt.json", {"safe": 1})

    class BadString:
        def __str__(self):
            raise ValueError("cannot serialize")

    with pytest.raises(ValueError):
        journal.write_json("receipt.json", BadString())
    assert json.loads(path.read_text()) == {"safe": 1}
