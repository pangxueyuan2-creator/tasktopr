from __future__ import annotations

import json
from pathlib import Path

import pytest

from tasktopr.handoff import (
    HANDOFF_SCHEMA,
    HandoffError,
    build_execution_handoff,
    content_digest,
    export_receipt_file,
    load_execution_receipt,
)


def _receipt() -> dict[str, object]:
    manifest = [
        {"path_sha256": "1" * 64, "before": "2" * 64, "after": "3" * 64},
        {"path_sha256": "4" * 64, "before": None, "after": "5" * 64},
    ]
    payload: dict[str, object] = {
        "schema_version": 1,
        "run_id": "private-run-id",
        "started_at": "2026-09-14T00:00:00+00:00",
        "repository_identity": {"kind": "local-root-sha256", "sha256": "6" * 64},
        "base_sha": "a" * 40,
        "result_head_sha": "b" * 40,
        "tested_head_sha": "b" * 40,
        "policy": {"version": "tasktopr-execution-v1", "sha256": "7" * 64},
        "tool": {"name": "tasktopr", "version": "0.1.0", "source_sha256": "8" * 64},
        "task_sha256": "9" * 64,
        "patch_sha256": "a" * 64,
        "command_list_sha256": "b" * 64,
        "test_result_sha256": "c" * 64,
        "changed_file_manifest": manifest,
        "protected_path_decision": "allow",
        "tests": {"status": "pass", "count": 2},
        "ci": "unknown",
        "human_review": "unknown",
        "decision": "REVIEW_REQUIRED",
        "trust_boundary": "local observation",
        "phase": "pr_created",
        "branch_sha256": "d" * 64,
    }
    return {"payload": payload, "receipt_sha256": content_digest(payload)}


def test_build_handoff_is_sanitized_and_deterministic() -> None:
    receipt = _receipt()
    first = build_execution_handoff(receipt, tool_revision="e" * 40)
    second = build_execution_handoff(receipt, tool_revision="e" * 40)

    assert first == second
    payload = first["payload"]
    assert isinstance(payload, dict)
    assert payload["schema_version"] == HANDOFF_SCHEMA
    assert payload["component"] == "execution"
    assert payload["producer"] == {
        "name": "tasktopr",
        "version": "0.1.0",
        "git_revision": "e" * 40,
        "source_sha256": "8" * 64,
    }
    assert payload["change"] == {
        "repository_sha256": "6" * 64,
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "changed_file_count": 2,
        "change_scope_sha256": content_digest(
            receipt["payload"]["changed_file_manifest"]  # type: ignore[index]
        ),
    }
    serialized = json.dumps(first, sort_keys=True)
    assert "private-run-id" not in serialized
    assert "started_at" not in serialized
    assert "task_sha256" not in serialized
    assert first["receipt_sha256"] == content_digest(payload)


def test_build_rejects_tampered_receipt() -> None:
    receipt = _receipt()
    receipt["receipt_sha256"] = "f" * 64
    with pytest.raises(HandoffError, match="digest does not match"):
        build_execution_handoff(receipt, tool_revision="e" * 40)


def test_build_rejects_non_exact_head_or_failed_policy() -> None:
    receipt = _receipt()
    payload = receipt["payload"]
    assert isinstance(payload, dict)
    payload["tested_head_sha"] = "c" * 40
    receipt["receipt_sha256"] = content_digest(payload)
    with pytest.raises(HandoffError, match="exact tested result HEAD"):
        build_execution_handoff(receipt, tool_revision="e" * 40)

    receipt = _receipt()
    payload = receipt["payload"]
    assert isinstance(payload, dict)
    payload["protected_path_decision"] = "deny"
    receipt["receipt_sha256"] = content_digest(payload)
    with pytest.raises(HandoffError, match="protected-path"):
        build_execution_handoff(receipt, tool_revision="e" * 40)


def test_build_rejects_incomplete_tests_and_invalid_manifest() -> None:
    receipt = _receipt()
    payload = receipt["payload"]
    assert isinstance(payload, dict)
    payload["tests"] = {"status": "unknown", "count": 0}
    receipt["receipt_sha256"] = content_digest(payload)
    with pytest.raises(HandoffError, match="passing exact-head tests"):
        build_execution_handoff(receipt, tool_revision="e" * 40)

    receipt = _receipt()
    payload = receipt["payload"]
    assert isinstance(payload, dict)
    payload["changed_file_manifest"] = [{"path": "secret.py"}]
    receipt["receipt_sha256"] = content_digest(payload)
    with pytest.raises(HandoffError, match="unexpected fields"):
        build_execution_handoff(receipt, tool_revision="e" * 40)


def test_load_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    receipt = tmp_path / "execution-receipt.json"
    receipt.write_text('{"payload":{},"payload":{},"receipt_sha256":"' + "a" * 64 + '"}')
    with pytest.raises(HandoffError, match="duplicate JSON key"):
        load_execution_receipt(receipt)


def test_export_writes_report_and_does_not_overwrite_on_validation_failure(
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "execution-receipt.json"
    output_path = tmp_path / "execution-handoff.json"
    receipt = _receipt()
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    report = export_receipt_file(
        receipt_path,
        tool_revision="e" * 40,
        output_path=output_path,
    )
    assert json.loads(output_path.read_text(encoding="utf-8")) == report

    receipt["receipt_sha256"] = "f" * 64
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    before = output_path.read_bytes()
    with pytest.raises(HandoffError, match="digest does not match"):
        export_receipt_file(
            receipt_path,
            tool_revision="e" * 40,
            output_path=output_path,
        )
    assert output_path.read_bytes() == before


def test_rejects_invalid_tool_revision_and_symlink_input(tmp_path: Path) -> None:
    with pytest.raises(HandoffError, match="40-character"):
        build_execution_handoff(_receipt(), tool_revision="not-a-revision")

    target = tmp_path / "target.json"
    target.write_text(json.dumps(_receipt()), encoding="utf-8")
    link = tmp_path / "linked.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(HandoffError, match="non-symlink"):
        load_execution_receipt(link)
