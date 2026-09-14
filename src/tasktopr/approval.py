"""Explicit human approval boundary between read-only planning and mutation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from .models import ChangePlan

APPROVAL_SCHEMA = "tasktopr.dev/plan-approval/v1"


class ApprovalMode(StrEnum):
    """Configuration modes for the pre-mutation approval boundary."""

    OFF = "off"
    PROMPT = "prompt"


class ApprovalDecision(StrEnum):
    """Human decisions supported by the approval boundary."""

    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


class PlanApproval(BaseModel):
    """One bounded approval response returned by a trusted local UI."""

    decision: ApprovalDecision
    plan: ChangePlan | None = None


class PlanApprovalError(ValueError):
    """Raised when an approval response cannot safely authorize mutation."""


PlanApprover = Callable[[ChangePlan], PlanApproval]


def plan_sha256(plan: ChangePlan) -> str:
    """Return a stable digest for the validated plan without leaking raw plan text."""

    encoded = json.dumps(
        plan.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def apply_plan_approval(
    plan: ChangePlan,
    *,
    mode: ApprovalMode,
    approver: PlanApprover | None,
    decided_at: datetime | None = None,
) -> tuple[ChangePlan | None, dict[str, Any]]:
    """Apply one approval decision and return the final plan plus digest-only evidence.

    Approval is deliberately separate from model planning. Edited plans are round-tripped
    through the same ChangePlan schema before they can cross the mutation boundary.
    """

    if mode is ApprovalMode.OFF:
        raise PlanApprovalError("approval processing is only valid when approval mode is enabled")
    if approver is None:
        raise PlanApprovalError("approval mode requires an explicit human approval callback")

    original_digest = plan_sha256(plan)
    response = approver(plan.model_copy(deep=True))
    if not isinstance(response, PlanApproval):
        raise PlanApprovalError("approval callback returned an invalid response")

    final_plan: ChangePlan | None
    if response.decision is ApprovalDecision.REJECT:
        if response.plan is not None:
            raise PlanApprovalError("rejected approval must not supply an edited plan")
        final_plan = None
    elif response.decision is ApprovalDecision.APPROVE:
        if response.plan is not None:
            raise PlanApprovalError("approve keeps the validated plan unchanged; use edit to replace it")
        final_plan = plan.model_copy(deep=True)
    elif response.decision is ApprovalDecision.EDIT:
        if response.plan is None:
            raise PlanApprovalError("edit approval requires a complete replacement plan")
        try:
            final_plan = ChangePlan.model_validate(response.plan.model_dump(mode="json"))
        except Exception as exc:
            raise PlanApprovalError("edited plan failed the normal ChangePlan validation boundary") from exc
    else:  # pragma: no cover - StrEnum exhaustiveness guard
        raise PlanApprovalError("unknown approval decision")

    final_digest = plan_sha256(final_plan) if final_plan is not None else None
    timestamp = decided_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise PlanApprovalError("approval timestamp must be timezone-aware")
    record: dict[str, Any] = {
        "schema_version": APPROVAL_SCHEMA,
        "mode": mode.value,
        "decision": response.decision.value,
        "original_plan_sha256": original_digest,
        "final_plan_sha256": final_digest,
        "edited": final_digest is not None and final_digest != original_digest,
        "decided_at": timestamp.astimezone(UTC).isoformat(),
    }
    return final_plan, record
