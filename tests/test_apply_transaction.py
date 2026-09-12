from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tasktopr.agents.coder import apply_patch
from tasktopr.agents.explorer import explore
from tasktopr.config import TaskToPRConfig
from tasktopr.models import Issue, PatchOperation, PatchRequest
from tasktopr.security import SecurityError


def _profile(repo: Path):
    return explore(repo, Issue(number=1, title="atomic patch"), TaskToPRConfig())


def _replace(path: str, old: str, new: str) -> PatchOperation:
    return PatchOperation(kind="replace", path=path, old_text=old, new_text=new, reason="test")


def test_preflight_failure_does_not_apply_earlier_operation(demo_repo: Path) -> None:
    target = demo_repo / "calculator.py"
    before = target.read_text(encoding="utf-8")
    patch = PatchRequest(
        summary="later operation is invalid",
        operations=[
            _replace("calculator.py", "return numerator / denominator", "return 0.0"),
            _replace("missing.py", "old", "new"),
        ],
    )

    with pytest.raises(SecurityError, match="Replace target does not exist"):
        apply_patch(patch, _profile(demo_repo), TaskToPRConfig())

    assert target.read_text(encoding="utf-8") == before


def test_dependency_manifest_edits_require_permission(demo_repo: Path) -> None:
    patch = PatchRequest(
        summary="dependency bump",
        operations=[
            PatchOperation(
                kind="replace",
                path="pyproject.toml",
                old_text='name = "zero-division-demo"',
                new_text='name = "zero-division-demo-v2"',
                reason="test",
            )
        ],
    )

    with pytest.raises(SecurityError, match="dependency manifest"):
        apply_patch(patch, _profile(demo_repo), TaskToPRConfig())

    assert 'name = "zero-division-demo"' in (demo_repo / "pyproject.toml").read_text(
        encoding="utf-8"
    )


def test_dependency_manifest_edits_allowed_with_permission(demo_repo: Path) -> None:
    config = TaskToPRConfig()
    config.permissions.allow_dependency_updates = True
    patch = PatchRequest(
        summary="dependency bump",
        operations=[
            PatchOperation(
                kind="replace",
                path="pyproject.toml",
                old_text='name = "zero-division-demo"',
                new_text='name = "zero-division-demo-v2"',
                reason="test",
            )
        ],
    )

    apply_patch(patch, _profile(demo_repo), config)
    assert 'name = "zero-division-demo-v2"' in (demo_repo / "pyproject.toml").read_text(
        encoding="utf-8"
    )


def test_write_failure_rolls_back_every_attempted_file(
    demo_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = demo_repo / "calculator.py"
    second = demo_repo / "second.py"
    second.write_text("old\n", encoding="utf-8")
    first_before = first.read_text(encoding="utf-8")
    real_write_text = Path.write_text
    failed = False

    def fail_second_once(path: Path, content: str, **kwargs: object) -> int:
        nonlocal failed
        if path == second and content == "new\n" and not failed:
            failed = True
            real_write_text(path, "partial\n", encoding="utf-8")
            raise OSError("simulated write failure")
        return real_write_text(path, content, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_second_once)
    patch = PatchRequest(
        summary="second write fails",
        operations=[
            _replace("calculator.py", "return numerator / denominator", "return 0.0"),
            _replace("second.py", "old\n", "new\n"),
        ],
    )

    with pytest.raises(OSError, match="simulated write failure"):
        apply_patch(patch, _profile(demo_repo), TaskToPRConfig())

    assert first.read_text(encoding="utf-8") == first_before
    assert second.read_text(encoding="utf-8") == "old\n"


def test_write_failure_removes_created_file_and_parent(
    demo_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = demo_repo / "calculator.py"
    before = target.read_text(encoding="utf-8")
    real_write_text = Path.write_text
    failed = False

    def fail_existing_once(path: Path, content: str, **kwargs: object) -> int:
        nonlocal failed
        if path == target and content != before and not failed:
            failed = True
            raise OSError("simulated write failure")
        return real_write_text(path, content, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_existing_once)
    patch = PatchRequest(
        summary="created path must roll back",
        operations=[
            PatchOperation(
                kind="create",
                path="new/subdir/generated.py",
                new_text="created\n",
                reason="test",
            ),
            _replace("calculator.py", "return numerator / denominator", "return 0.0"),
        ],
    )

    with pytest.raises(OSError, match="simulated write failure"):
        apply_patch(patch, _profile(demo_repo), TaskToPRConfig())

    assert target.read_text(encoding="utf-8") == before
    assert not (demo_repo / "new").exists()


def test_lf_patch_passes_git_whitespace_check_without_autocrlf(demo_repo: Path) -> None:
    target = demo_repo / "portable.py"
    target.write_bytes(b"def value():\n    return 1\n")
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=demo_repo, check=True)
    subprocess.run(["git", "add", "portable.py"], cwd=demo_repo, check=True)
    subprocess.run(["git", "commit", "-m", "LF fixture"], cwd=demo_repo, check=True)
    apply_patch(
        PatchRequest(
            summary="portable patch", operations=[_replace("portable.py", "return 1", "return 2")]
        ),
        _profile(demo_repo),
        TaskToPRConfig(),
    )
    check = subprocess.run(["git", "diff", "--check"], cwd=demo_repo, capture_output=True)
    assert check.returncode == 0, check.stdout
    assert target.read_bytes() == b"def value():\n    return 2\n"


def test_failed_patch_restores_mixed_newline_bytes(
    demo_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = demo_repo / "first.py", demo_repo / "second.py"
    before = b"# mixed newlines\r\nvalue = 1\n# bare CR\r"
    first.write_bytes(before)
    second.write_bytes(b"old\r\n")
    real_write = Path.write_text

    def fail_second(path: Path, content: str, **kwargs: object) -> int:
        if path == second:
            raise OSError("simulated second write failure")
        return real_write(path, content, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_second)
    patch = PatchRequest(
        summary="exact rollback",
        operations=[
            _replace("first.py", "value = 1", "value = 2"),
            _replace("second.py", "old", "new"),
        ],
    )
    with pytest.raises(OSError, match="second write"):
        apply_patch(patch, _profile(demo_repo), TaskToPRConfig())
    assert first.read_bytes() == before
    assert second.read_bytes() == b"old\r\n"


def _make_dir_alias(repo: Path, link: str, target: str) -> bool:
    """Create a directory alias (Windows junction, POSIX symlink fallback)."""

    try:
        completed = subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(repo / link), str(repo / target)],
            capture_output=True,
        )
        if completed.returncode == 0:
            return True
    except OSError:
        pass
    try:
        (repo / link).symlink_to(repo / target, target_is_directory=True)
        return True
    except OSError:
        return False


def test_alias_to_protected_workflow_is_refused(demo_repo: Path) -> None:
    workflow = demo_repo / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text("on: push\n", encoding="utf-8")
    if not _make_dir_alias(demo_repo, "alias-wf", ".github/workflows"):
        pytest.skip("directory aliases are unavailable in this environment")
    patch = PatchRequest(
        summary="alias into workflows",
        operations=[
            _replace(
                "alias-wf/ci.yml",
                "on: push",
                "on: push\njobs:\n  evil:\n    runs-on: ubuntu-latest",
            )
        ],
    )

    with pytest.raises(SecurityError, match="protected path through an alias"):
        apply_patch(patch, _profile(demo_repo), TaskToPRConfig())

    assert "evil" not in workflow.read_text(encoding="utf-8")


def test_file_alias_to_dependency_manifest_is_refused(demo_repo: Path) -> None:
    manifest = demo_repo / "pyproject.toml"
    try:
        (demo_repo / "alias.txt").symlink_to(manifest)
    except OSError as exc:
        pytest.skip(f"file symlinks are unavailable in this environment: {exc}")
    patch = PatchRequest(
        summary="alias into manifest",
        operations=[_replace("alias.txt", 'name = "zero-division-demo"', 'name = "evil"')],
    )

    with pytest.raises(SecurityError, match="dependency manifest through an alias"):
        apply_patch(patch, _profile(demo_repo), TaskToPRConfig())

    assert 'name = "evil"' not in manifest.read_text(encoding="utf-8")


def test_benign_alias_to_regular_file_still_works(demo_repo: Path) -> None:
    directory = demo_repo / "src2"
    directory.mkdir(exist_ok=True)
    (directory / "note.md").write_text("# note\n", encoding="utf-8")
    if not _make_dir_alias(demo_repo, "alias-src", "src2"):
        pytest.skip("directory aliases are unavailable in this environment")
    patch = PatchRequest(
        summary="alias to regular file",
        operations=[_replace("alias-src/note.md", "# note", "# changed through alias")],
    )

    apply_patch(patch, _profile(demo_repo), TaskToPRConfig())

    assert "# changed through alias" in (directory / "note.md").read_text(encoding="utf-8")
