from __future__ import annotations

import json

from tasktopr.approval import ApprovalDecision, ApprovalMode, PlanApproval
from tasktopr.config import load_config
from tasktopr.orchestrator import fix_issue
from tasktopr.providers import DemoProvider
from tasktopr.receipt import digest


def _receipt(result) -> dict[str, object]:
    envelope = json.loads((result.run_dir / "execution-receipt.json").read_text(encoding="utf-8"))
    assert envelope["receipt_sha256"] == digest(envelope["payload"])
    return envelope["payload"]


def test_default_off_mode_is_explicit_in_receipt_v2(demo_repo) -> None:
    result = fix_issue(
        1,
        start_dir=demo_repo,
        config=load_config(demo_repo),
        provider=DemoProvider(),
        demo=True,
        no_pr=True,
    )
    assert result.success, result.message
    receipt = _receipt(result)
    assert receipt["schema_version"] == 2
    assert receipt["plan_approval"] == {
        "mode": "off",
        "decision": "not_required",
        "edited": False,
        "original_plan_sha256": None,
        "final_plan_sha256": None,
        "record_sha256": None,
    }


def test_prompt_approval_record_is_content_bound_without_raw_timestamp(demo_repo) -> None:
    config = load_config(demo_repo)
    config.approval.mode = ApprovalMode.PROMPT
    result = fix_issue(
        1,
        start_dir=demo_repo,
        config=config,
        provider=DemoProvider(),
        demo=True,
        no_pr=True,
        plan_approver=lambda _plan: PlanApproval(decision=ApprovalDecision.APPROVE),
    )
    assert result.success, result.message
    approval_record = json.loads(
        (result.run_dir / "plan-approval.json").read_text(encoding="utf-8")
    )
    receipt = _receipt(result)
    approval = receipt["plan_approval"]
    assert isinstance(approval, dict)
    assert approval["mode"] == "prompt"
    assert approval["decision"] == "approve"
    assert approval["edited"] is False
    assert approval["original_plan_sha256"] == approval["final_plan_sha256"]
    assert approval["record_sha256"] == digest(approval_record)
    assert "decided_at" not in approval


def test_reject_is_recorded_before_no_mutation_exit(demo_repo) -> None:
    config = load_config(demo_repo)
    config.approval.mode = ApprovalMode.PROMPT
    result = fix_issue(
        1,
        start_dir=demo_repo,
        config=config,
        provider=DemoProvider(),
        demo=True,
        plan_approver=lambda _plan: PlanApproval(decision=ApprovalDecision.REJECT),
    )
    assert not result.success
    receipt = _receipt(result)
    approval = receipt["plan_approval"]
    assert isinstance(approval, dict)
    assert approval["decision"] == "reject"
    assert approval["final_plan_sha256"] is None
    assert receipt["phase"] == "approval_recorded"
