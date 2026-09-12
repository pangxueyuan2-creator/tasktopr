"""Independent synthetic attacks on exact-revision receipt provenance."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tasktopr.agents.reviewer import review_changes
from tasktopr.config import TaskToPRConfig
from tasktopr.events import RunJournal
from tasktopr.models import ChangePlan, CommandResult, Issue, PatchRequest, ReviewResult, RiskLevel
from tasktopr.pr import commit_changes
from tasktopr.receipt import ExecutionReceipt, snapshot
from tasktopr.security import SecurityError


def git(root: Path, *arguments: str, data: bytes | None = None) -> str:
    return (
        subprocess.run(["git", *arguments], cwd=root, input=data, capture_output=True, check=True)
        .stdout.decode()
        .strip()
    )


def prepare(root: Path) -> None:
    git(root, "config", "core.autocrlf", "false")
    (root / "a.py").write_text("value = 1\n", encoding="utf-8")
    git(root, "add", "a.py")
    git(root, "commit", "-m", "synthetic receipt fixture")


def patched(root: Path) -> ExecutionReceipt:
    prepare(root)
    receipt = ExecutionReceipt(root, RunJournal(root), TaskToPRConfig())
    receipt.task(Issue(number=1, title="Synthetic fixture"), [["python", "--version"]])
    (root / "a.py").write_text("value = 2\n", encoding="utf-8")
    patch = PatchRequest(
        summary="Synthetic safe change",
        operations=[
            dict(kind="replace", path="a.py", old_text="1", new_text="2", reason="fixture")
        ],
    )
    receipt.patched(patch, ["a.py"])
    return receipt


def verify(receipt: ExecutionReceipt) -> None:
    # Unit fixture: successful command results must never override stale identity.
    tests = [CommandResult(command=["python", "--version"], return_code=0, elapsed_seconds=0)]
    review = ReviewResult(approved=True, risk=RiskLevel.LOW, scope_ok=True, tests_ok=True)
    receipt.verified(tests, review)


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_masked_index_entries_are_rejected_at_intake(demo_repo: Path, flag: str) -> None:
    prepare(demo_repo)
    git(demo_repo, "update-index", flag, "a.py")
    with pytest.raises(SecurityError):
        snapshot(demo_repo)


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_masked_alternate_index_blob_cannot_receive_passing_receipt(
    demo_repo: Path, flag: str
) -> None:
    receipt = patched(demo_repo)
    alternate = git(demo_repo, "hash-object", "-w", "--stdin", data=b"value = 999\n")
    git(demo_repo, "update-index", "--cacheinfo", f"100644,{alternate},a.py")
    git(demo_repo, "update-index", flag, "a.py")
    assert (demo_repo / "a.py").read_text() == "value = 2\n"
    with pytest.raises(SecurityError):
        verify(receipt)


def test_unmasked_index_mutation_during_tests_is_also_stale(demo_repo: Path) -> None:
    receipt = patched(demo_repo)
    alternate = git(demo_repo, "hash-object", "-w", "--stdin", data=b"value = 999\n")
    git(demo_repo, "update-index", "--cacheinfo", f"100644,{alternate},a.py")
    with pytest.raises(SecurityError):
        verify(receipt)


@pytest.mark.parametrize("attribute", ["filter=fixture", "ident", "working-tree-encoding=UTF-8"])
def test_active_checkout_transforms_do_not_claim_raw_head_evidence(
    demo_repo: Path, attribute: str
) -> None:
    prepare(demo_repo)
    (demo_repo / ".gitattributes").write_text(f"a.py {attribute}\n", encoding="utf-8")
    git(demo_repo, "add", ".gitattributes")
    git(demo_repo, "commit", "-m", "synthetic transform policy")
    with pytest.raises(SecurityError):
        ExecutionReceipt(demo_repo, RunJournal(demo_repo), TaskToPRConfig())


@pytest.mark.parametrize("attribute", ["filter=fixture", "ident", "working-tree-encoding=UTF-8"])
def test_local_info_attributes_cannot_sneak_in_transforms(demo_repo: Path, attribute: str) -> None:
    receipt = patched(demo_repo)
    (demo_repo / ".git" / "info" / "attributes").write_text(f"a.py {attribute}\n", encoding="utf-8")
    with pytest.raises(SecurityError):
        verify(receipt)


def test_snapshot_rejects_filter_before_git_diff_can_execute_it(
    demo_repo: Path, tmp_path: Path
) -> None:
    prepare(demo_repo)
    marker = tmp_path / "filter-must-not-run"
    driver = tmp_path / "synthetic-filter.py"
    driver.write_text(
        "import sys\nfrom pathlib import Path\n"
        f"Path({str(marker)!r}).touch()\n"
        "sys.stdout.buffer.write(sys.stdin.buffer.read())\n",
        encoding="utf-8",
    )
    # This fixture is a harmless local marker, never a network/system operation.
    git(
        demo_repo,
        "config",
        "filter.fixture.clean",
        f'"{Path(sys.executable).as_posix()}" "{driver.as_posix()}"',
    )
    (demo_repo / ".git" / "info" / "attributes").write_text("a.py filter=fixture\n")
    (demo_repo / "a.py").write_text("value = 222\n")
    with pytest.raises(SecurityError):
        snapshot(demo_repo)
    assert not marker.exists(), "rejecting the report after executing its filter is too late"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("remote.origin.url", "https://example.invalid/other.git"),
        ("remote.origin.pushurl", "https://example.invalid/other.git"),
        ("core.hooksPath", "different-hooks"),
    ],
)
def test_tests_cannot_retarget_origin_or_hook_policy(demo_repo: Path, key: str, value: str) -> None:
    receipt = patched(demo_repo)
    git(demo_repo, "config", key, value)
    with pytest.raises(SecurityError):
        verify(receipt)


def test_commit_does_not_execute_repository_hook(demo_repo: Path, tmp_path: Path) -> None:
    prepare(demo_repo)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    marker = tmp_path / "hook-must-not-run"
    hook = hooks / "pre-commit"
    hook.write_text(
        f'#!/bin/sh\nprintf fixture > "{marker.as_posix()}"\nexit 88\n', encoding="utf-8"
    )
    hook.chmod(0o700)
    git(demo_repo, "config", "core.hooksPath", hooks.as_posix())
    (demo_repo / "a.py").write_text("value = 2\n", encoding="utf-8")
    plan = ChangePlan(
        summary="synthetic fixture",
        root_cause="synthetic fixture",
        steps=[dict(path="a.py", action="fixture", rationale="fixture")],
        test_plan=["fixture"],
    )
    revision = commit_changes(demo_repo, ["a.py"], Issue(number=1, title="Synthetic fixture"), plan)
    assert revision == git(demo_repo, "rev-parse", "HEAD")
    assert not marker.exists()


def test_reviewer_does_not_execute_fsmonitor_hook(demo_repo: Path, tmp_path: Path) -> None:
    prepare(demo_repo)
    marker = tmp_path / "fsmonitor-must-not-run"
    hook = tmp_path / "synthetic-fsmonitor"
    hook.write_text(
        f'#!/bin/sh\nprintf fixture > "{marker.as_posix()}"\nexit 0\n', encoding="utf-8"
    )
    hook.chmod(0o700)
    git(demo_repo, "config", "core.fsmonitor", hook.as_posix())
    (demo_repo / "a.py").write_text("value = 2\n", encoding="utf-8")
    tests = [CommandResult(command=["python", "--version"], return_code=0, elapsed_seconds=0)]
    review_changes(demo_repo, ["a.py"], tests, TaskToPRConfig())
    assert not marker.exists()
