from __future__ import annotations

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


def test_preflight_exclusive_allow_does_not_apply_earlier_operation(demo_repo: Path) -> None:
    target = demo_repo / "calculator.py"
    before = target.read_text(encoding="utf-8")
    config = TaskToPRConfig()
    config.scope.exclusive_allow = True
    config.scope.allowed = ["calculator.py"]
    patch = PatchRequest(
        summary="later path is outside exclusive allow",
        operations=[
            _replace("calculator.py", "return numerator / denominator", "return 0.0"),
            PatchOperation(
                kind="create",
                path="out/of/scope.py",
                new_text="sneak\n",
                reason="must not apply",
            ),
        ],
    )

    with pytest.raises(SecurityError, match="protected path"):
        apply_patch(patch, _profile(demo_repo), config)

    assert target.read_text(encoding="utf-8") == before
    assert not (demo_repo / "out").exists()
