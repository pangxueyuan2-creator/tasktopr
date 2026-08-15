from __future__ import annotations

import json
from pathlib import Path

import pytest

from tasktopr.agents.coder import apply_patch
from tasktopr.agents.explorer import explore
from tasktopr.config import TaskToPRConfig
from tasktopr.models import Issue, PatchOperation, PatchRequest
from tasktopr.security import SecurityError

CALC_OLD = "return numerator / denominator"
CALC_SAFE = "return numerator / denominator if denominator else 0.0"

DENY_DECISION = {
    "schema_version": "guardspec.check.v1",
    "policy_digest": "abc123",
    "decision": "deny",
    "matched_rules": ["deny-calculator"],
    "protected_paths": ["calculator.py"],
}


def _profile(demo_repo: Path):
    return explore(demo_repo, Issue(number=1, title="txn"), TaskToPRConfig())


def _replace(path: str, old: str, new: str) -> PatchOperation:
    return PatchOperation(kind="replace", path=path, old_text=old, new_text=new, reason="test")


def _create(path: str, text: str) -> PatchOperation:
    return PatchOperation(kind="create", path=path, old_text="", new_text=text, reason="test")


def test_second_replace_failure_leaves_no_partial_mutation(demo_repo: Path) -> None:
    profile = _profile(demo_repo)
    original = (demo_repo / "calculator.py").read_text(encoding="utf-8")
    patch = PatchRequest(
        summary="partial",
        operations=[
            _replace("calculator.py", CALC_OLD, CALC_SAFE),
            _replace("missing.py", "x", "y"),
        ],
    )
    with pytest.raises(SecurityError, match="does not exist"):
        apply_patch(patch, profile, TaskToPRConfig())
    assert (demo_repo / "calculator.py").read_text(encoding="utf-8") == original
    assert not (demo_repo / "missing.py").exists()


def test_create_then_failed_replace_does_not_leave_new_file(demo_repo: Path) -> None:
    profile = _profile(demo_repo)
    patch = PatchRequest(
        summary="create-then-fail",
        operations=[
            _create("notes/new.txt", "hello\n"),
            _replace("missing.py", "x", "y"),
        ],
    )
    with pytest.raises(SecurityError, match="does not exist"):
        apply_patch(patch, profile, TaskToPRConfig())
    assert not (demo_repo / "notes" / "new.txt").exists()


def test_sequential_edits_to_same_file_see_prior_staged_text(demo_repo: Path) -> None:
    profile = _profile(demo_repo)
    (demo_repo / "stack.py").write_text("alpha\n", encoding="utf-8")
    patch = PatchRequest(
        summary="stacked",
        operations=[
            _replace("stack.py", "alpha", "beta"),
            _replace("stack.py", "beta", "gamma"),
        ],
    )
    changed = apply_patch(patch, profile, TaskToPRConfig())
    assert changed == ["stack.py", "stack.py"]
    assert (demo_repo / "stack.py").read_text(encoding="utf-8") == "gamma\n"


def test_protected_second_path_does_not_apply_first(demo_repo: Path) -> None:
    profile = _profile(demo_repo)
    original = (demo_repo / "calculator.py").read_text(encoding="utf-8")
    patch = PatchRequest(
        summary="protected-second",
        operations=[
            _replace("calculator.py", CALC_OLD, CALC_SAFE),
            _replace(".github/workflows/ci.yml", "x", "y"),
        ],
    )
    with pytest.raises(SecurityError, match="protected"):
        apply_patch(patch, profile, TaskToPRConfig())
    assert (demo_repo / "calculator.py").read_text(encoding="utf-8") == original


def test_oserror_on_second_write_rolls_back_first(
    demo_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _profile(demo_repo)
    (demo_repo / "a.py").write_text("alpha\n", encoding="utf-8")
    (demo_repo / "b.py").write_text("beta\n", encoding="utf-8")
    original_write = Path.write_text

    def flaky(self: Path, data: str, *args: object, **kwargs: object) -> int:
        if self.name == "b.py":
            raise OSError("simulated write failure")
        return original_write(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", flaky)
    patch = PatchRequest(
        summary="oserror",
        operations=[_replace("a.py", "alpha", "ALPHA"), _replace("b.py", "beta", "BETA")],
    )
    with pytest.raises(OSError, match="simulated write failure"):
        apply_patch(patch, profile, TaskToPRConfig())
    assert (demo_repo / "a.py").read_text(encoding="utf-8") == "alpha\n"
    assert (demo_repo / "b.py").read_text(encoding="utf-8") == "beta\n"


def test_guardspec_deny_blocks_apply_and_records_digest(demo_repo: Path) -> None:
    (demo_repo / ".guardspec-check.json").write_text(json.dumps(DENY_DECISION), encoding="utf-8")
    profile = _profile(demo_repo)
    original = (demo_repo / "calculator.py").read_text(encoding="utf-8")
    patch = PatchRequest(
        summary="denied",
        operations=[_replace("calculator.py", CALC_OLD, "return 0")],
    )
    with pytest.raises(SecurityError, match="policy_digest=abc123"):
        apply_patch(patch, profile, TaskToPRConfig())
    assert (demo_repo / "calculator.py").read_text(encoding="utf-8") == original


def test_guardspec_allow_permits_apply(demo_repo: Path) -> None:
    payload = {**DENY_DECISION, "decision": "allow"}
    (demo_repo / ".guardspec-check.json").write_text(json.dumps(payload), encoding="utf-8")
    profile = _profile(demo_repo)
    patch = PatchRequest(
        summary="allowed",
        operations=[_replace("calculator.py", CALC_OLD, "return 0")],
    )
    apply_patch(patch, profile, TaskToPRConfig())
    assert "return 0" in (demo_repo / "calculator.py").read_text(encoding="utf-8")


def test_absent_decision_keeps_tasktopr_independent(demo_repo: Path) -> None:
    profile = _profile(demo_repo)
    patch = PatchRequest(
        summary="independent",
        operations=[_replace("calculator.py", CALC_OLD, "return 0")],
    )
    apply_patch(patch, profile, TaskToPRConfig())
    assert "return 0" in (demo_repo / "calculator.py").read_text(encoding="utf-8")


def test_env_path_missing_is_fail_closed(demo_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUARDSPEC_CHECK_JSON", str(demo_repo / "missing-check.json"))
    profile = _profile(demo_repo)
    original = (demo_repo / "calculator.py").read_text(encoding="utf-8")
    patch = PatchRequest(
        summary="missing-env",
        operations=[_replace("calculator.py", CALC_OLD, "return 0")],
    )
    with pytest.raises(SecurityError, match="missing"):
        apply_patch(patch, profile, TaskToPRConfig())
    assert (demo_repo / "calculator.py").read_text(encoding="utf-8") == original


def test_unreadable_decision_is_fail_closed(demo_repo: Path) -> None:
    (demo_repo / ".guardspec-check.json").write_text("{not-json", encoding="utf-8")
    profile = _profile(demo_repo)
    patch = PatchRequest(
        summary="bad-json",
        operations=[_replace("calculator.py", CALC_OLD, "return 0")],
    )
    with pytest.raises(SecurityError, match="unreadable"):
        apply_patch(patch, profile, TaskToPRConfig())


def test_missing_decision_field_is_fail_closed(demo_repo: Path) -> None:
    (demo_repo / ".guardspec-check.json").write_text(
        json.dumps({"schema_version": "guardspec.check.v1"}), encoding="utf-8"
    )
    profile = _profile(demo_repo)
    patch = PatchRequest(
        summary="no-decision",
        operations=[_replace("calculator.py", CALC_OLD, "return 0")],
    )
    with pytest.raises(SecurityError, match="missing a decision"):
        apply_patch(patch, profile, TaskToPRConfig())
