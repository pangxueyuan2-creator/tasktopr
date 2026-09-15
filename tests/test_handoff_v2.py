from __future__ import annotations

from copy import deepcopy

import pytest

from tasktopr.handoff import (
    HANDOFF_SCHEMA_V2,
    HandoffError,
    build_execution_handoff,
    content_digest,
)


def _receipt(*, mode: str = "prompt", decision: str = "approve") -> dict[str, object]:
    original = "1" * 64
    final = "2" * 64 if decision == "edit" else original
    if mode == "off":
        approval: dict[str, object] = {
            "mode": "off",
            "decision": "not_required",
            "edited": False,
            "original_plan_sha256": None,
            "final_plan_sha256": None,
            "record_sha256": None,
        }
    else:
        approval = {
            "mode": mode,
            "decision": decision,
            "edited": final != original,
            "original_plan_sha256": original,
            "final_plan_sha256": final,
            "record_sha256": "3" * 64,
        }
    payload: dict[str, object] = {
        "schema_version": 2,
        "repository_identity": {"kind": "local-root-sha256", "sha256": "4" * 64},
        "base_sha": "a" * 40,
        "result_head_sha": "b" * 40,
        "tested_head_sha": "b" * 40,
        "policy": {"version": "tasktopr-execution-v1", "sha256": "5" * 64},
        "tool": {"name": "tasktopr", "version": "0.1.0", "source_sha256": "6" * 64},
        "command_list_sha256": "7" * 64,
        "test_result_sha256": "8" * 64,
        "changed_file_manifest": [{"path_sha256": "9" * 64, "before": "a" * 64, "after": "b" * 64}],
        "protected_path_decision": "allow",
        "tests": {"status": "pass", "count": 1},
        "plan_approval": approval,
        "decision": "REVIEW_REQUIRED",
        "phase": "pr_created",
    }
    return {"payload": payload, "receipt_sha256": content_digest(payload)}


def _payload(receipt: dict[str, object]) -> dict[str, object]:
    payload = receipt["payload"]
    assert isinstance(payload, dict)
    return payload


def _rehash(receipt: dict[str, object]) -> None:
    receipt["receipt_sha256"] = content_digest(_payload(receipt))


def test_v2_prompt_approval_is_sanitized_and_bound() -> None:
    report = build_execution_handoff(_receipt(), tool_revision="c" * 40)
    payload = report["payload"]
    assert isinstance(payload, dict)
    assert payload["schema_version"] == HANDOFF_SCHEMA_V2
    assert payload["plan_approval"] == {
        "mode": "prompt",
        "decision": "approve",
        "edited": False,
        "original_plan_sha256": "1" * 64,
        "final_plan_sha256": "1" * 64,
        "record_sha256": "3" * 64,
    }
    assert payload["source_receipt"] == {
        "schema_version": 2,
        "sha256": _receipt()["receipt_sha256"],
    }


def test_v2_off_mode_is_explicit_without_claiming_approval() -> None:
    report = build_execution_handoff(_receipt(mode="off"), tool_revision="c" * 40)
    payload = report["payload"]
    assert isinstance(payload, dict)
    approval = payload["plan_approval"]
    assert isinstance(approval, dict)
    assert approval["mode"] == "off"
    assert approval["decision"] == "not_required"
    assert approval["record_sha256"] is None


@pytest.mark.parametrize("decision", ["pending", "reject"])
def test_v2_nonfinal_human_decision_cannot_export_success(decision: str) -> None:
    receipt = _receipt()
    approval = _payload(receipt)["plan_approval"]
    assert isinstance(approval, dict)
    approval["decision"] = decision
    _rehash(receipt)
    with pytest.raises(HandoffError, match="completed human decision"):
        build_execution_handoff(receipt, tool_revision="c" * 40)


def test_v2_rejects_rehashed_inconsistent_approval_identity() -> None:
    receipt = deepcopy(_receipt())
    approval = _payload(receipt)["plan_approval"]
    assert isinstance(approval, dict)
    approval["edited"] = True
    _rehash(receipt)
    with pytest.raises(HandoffError, match="edit identity"):
        build_execution_handoff(receipt, tool_revision="c" * 40)
