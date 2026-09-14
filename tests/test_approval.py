from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from tasktopr.approval import ApprovalDecision, ApprovalMode, PlanApproval
from tasktopr.config import TaskToPRConfig, load_config
from tasktopr.models import ChangePlan, PlanStep
from tasktopr.orchestrator import fix_issue
from tasktopr.providers import DemoProvider

PROJECT_ROOT = Path(__file__).parents[1]
DEMO_ROOT = PROJECT_ROOT / "demo" / "zero_division_repo"


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(DEMO_ROOT, root)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Approval Fixture")
    _git(root, "config", "user.email", "approval@example.invalid")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return root


def _prompt_config(root: Path) -> TaskToPRConfig:
    config = load_config(root)
    config.approval.mode = ApprovalMode.PROMPT
    return config


def test_approved_plan_crosses_mutation_boundary_and_records_digest(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    result = fix_issue(
        1,
        start_dir=root,
        config=_prompt_config(root),
        provider=DemoProvider(),
        no_pr=True,
        demo=True,
        plan_approver=lambda _plan: PlanApproval(decision=ApprovalDecision.APPROVE),
    )

    assert result.success is True
    assert result.branch is not None
    approval = json.loads((result.run_dir / "plan-approval.json").read_text(encoding="utf-8"))
    assert approval["schema_version"] == "tasktopr.dev/plan-approval/v1"
    assert approval["mode"] == "prompt"
    assert approval["decision"] == "approve"
    assert approval["edited"] is False
    assert approval["final_plan_sha256"] == approval["original_plan_sha256"]
    assert "denominator must not be zero" in (root / "calculator.py").read_text(encoding="utf-8")


def test_rejected_plan_has_no_branch_or_repository_mutation(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    baseline = _git(root, "rev-parse", "HEAD")

    result = fix_issue(
        1,
        start_dir=root,
        config=_prompt_config(root),
        provider=DemoProvider(),
        demo=True,
        plan_approver=lambda _plan: PlanApproval(decision=ApprovalDecision.REJECT),
    )

    assert result.success is False
    assert result.branch is None
    assert _git(root, "rev-parse", "HEAD") == baseline
    assert _git(root, "branch", "--show-current") == "main"
    assert _git(root, "branch", "--format=%(refname:short)").splitlines() == ["main"]
    assert "return numerator / denominator" in (root / "calculator.py").read_text(encoding="utf-8")
    approval = json.loads((result.run_dir / "plan-approval.json").read_text(encoding="utf-8"))
    assert approval["decision"] == "reject"
    assert approval["final_plan_sha256"] is None
    changes = json.loads((result.run_dir / "changes.json").read_text(encoding="utf-8"))
    assert changes == {"changed_files": [], "mode": "rejected"}


def test_bounded_edit_is_revalidated_and_becomes_the_final_plan(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    def edit(plan: ChangePlan) -> PlanApproval:
        edited = plan.model_copy(update={"summary": "Human-approved bounded edit"}, deep=True)
        return PlanApproval(decision=ApprovalDecision.EDIT, plan=edited)

    result = fix_issue(
        1,
        start_dir=root,
        config=_prompt_config(root),
        provider=DemoProvider(),
        no_pr=True,
        demo=True,
        plan_approver=edit,
    )

    assert result.success is True
    assert result.plan is not None
    assert result.plan.summary == "Human-approved bounded edit"
    persisted = json.loads((result.run_dir / "plan.json").read_text(encoding="utf-8"))
    assert persisted["plan"]["summary"] == "Human-approved bounded edit"
    approval = json.loads((result.run_dir / "plan-approval.json").read_text(encoding="utf-8"))
    assert approval["decision"] == "edit"
    assert approval["edited"] is True
    assert approval["final_plan_sha256"] != approval["original_plan_sha256"]


def test_invalid_edit_fails_closed_before_branch_creation(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    baseline = _git(root, "rev-parse", "HEAD")

    def invalid_edit(plan: ChangePlan) -> PlanApproval:
        bad_step = PlanStep.model_construct(path="../escape.py", action="edit", rationale="bad")
        invalid = ChangePlan.model_construct(
            summary=plan.summary,
            root_cause=plan.root_cause,
            steps=[bad_step],
            test_plan=plan.test_plan,
            non_goals=plan.non_goals,
            risk=plan.risk,
        )
        return PlanApproval(decision=ApprovalDecision.EDIT, plan=invalid)

    result = fix_issue(
        1,
        start_dir=root,
        config=_prompt_config(root),
        provider=DemoProvider(),
        demo=True,
        plan_approver=invalid_edit,
    )

    assert result.success is False
    assert "validation boundary" in result.message
    assert result.branch is None
    assert _git(root, "rev-parse", "HEAD") == baseline
    assert _git(root, "branch", "--format=%(refname:short)").splitlines() == ["main"]
    assert "return numerator / denominator" in (root / "calculator.py").read_text(encoding="utf-8")


def test_prompt_mode_without_approval_callback_never_implies_consent(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    result = fix_issue(
        1,
        start_dir=root,
        config=_prompt_config(root),
        provider=DemoProvider(),
        demo=True,
    )

    assert result.success is False
    assert "explicit human approval callback" in result.message
    assert result.branch is None
    assert _git(root, "branch", "--format=%(refname:short)").splitlines() == ["main"]


def test_dry_run_remains_read_only_even_when_prompt_approval_is_configured(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    result = fix_issue(
        1,
        start_dir=root,
        config=_prompt_config(root),
        provider=DemoProvider(),
        dry_run=True,
        demo=True,
    )

    assert result.success is True
    assert result.branch is None
    assert not (result.run_dir / "plan-approval.json").exists()
    assert _git(root, "branch", "--format=%(refname:short)").splitlines() == ["main"]
