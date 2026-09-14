"""Sanitized, versioned execution-evidence handoff for Safe Delivery consumers.

The handoff intentionally exposes identities, bounded counts and digests only. It
is not a signature and it does not let TaskToPR define PatchWitness policy or its
candidate manifest. Consumers must authenticate the producer revision separately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, cast

HANDOFF_SCHEMA = "tasktopr.dev/safe-delivery/execution/v1"
MAX_RECEIPT_BYTES = 512 * 1024
MAX_CHANGED_FILES = 10_000
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_SHA1 = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}")


class HandoffError(ValueError):
    """Raised when execution evidence cannot be exported safely."""


def content_digest(value: object) -> str:
    """Return the TaskToPR canonical JSON SHA-256 digest."""
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HandoffError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise HandoffError(f"{name} must be a JSON object")
    return cast(dict[str, Any], value)


def _list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise HandoffError(f"{name} must be a JSON array")
    return value


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise HandoffError(f"{name} must be a string")
    return value


def _digest(value: Any, name: str) -> str:
    text = _string(value, name)
    if not _DIGEST.fullmatch(text):
        raise HandoffError(f"{name} must be a lowercase SHA-256 digest")
    return text


def _revision(value: Any, name: str) -> str:
    text = _string(value, name)
    if not _SHA.fullmatch(text):
        raise HandoffError(f"{name} must be an exact Git revision")
    return text


def load_execution_receipt(path: Path) -> dict[str, Any]:
    """Load a bounded regular JSON file while rejecting duplicate keys and races."""
    try:
        before = path.lstat()
    except OSError as exc:
        raise HandoffError(f"unable to stat execution receipt: {exc}") from exc
    if path.is_symlink() or not stat.S_ISREG(before.st_mode):
        raise HandoffError("execution receipt must be a regular non-symlink file")
    if before.st_size > MAX_RECEIPT_BYTES:
        raise HandoffError("execution receipt exceeds the byte budget")
    try:
        data = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        raise HandoffError(f"unable to read execution receipt: {exc}") from exc
    if len(data) > MAX_RECEIPT_BYTES:
        raise HandoffError("execution receipt exceeds the byte budget")
    if (before.st_size, before.st_mtime_ns, before.st_mode) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_mode,
    ):
        raise HandoffError("execution receipt changed while it was being read")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HandoffError("execution receipt is not valid UTF-8 JSON") from exc
    return _object(value, "execution receipt")


def _validated_manifest(value: Any) -> list[dict[str, Any]]:
    entries = _list(value, "changed_file_manifest")
    if len(entries) > MAX_CHANGED_FILES:
        raise HandoffError("changed-file manifest exceeds the file-count budget")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(entries):
        entry = _object(item, f"changed_file_manifest[{index}]")
        if set(entry) != {"path_sha256", "before", "after"}:
            raise HandoffError("changed-file manifest has unexpected fields")
        _digest(entry["path_sha256"], "changed-file path identity")
        for field in ("before", "after"):
            state = entry[field]
            if state is not None and state != "deleted":
                _digest(state, f"changed-file {field} identity")
        result.append(entry)
    return result


def build_execution_handoff(receipt: dict[str, Any], *, tool_revision: str) -> dict[str, Any]:
    """Validate an exact-head TaskToPR receipt and emit bounded portable evidence."""
    if not _SHA1.fullmatch(tool_revision):
        raise HandoffError("tool revision must be an exact 40-character Git revision")
    if set(receipt) != {"payload", "receipt_sha256"}:
        raise HandoffError("execution receipt envelope has unexpected fields")
    payload = _object(receipt["payload"], "receipt payload")
    receipt_sha256 = _digest(receipt["receipt_sha256"], "receipt_sha256")
    if content_digest(payload) != receipt_sha256:
        raise HandoffError("execution receipt digest does not match its payload")
    if payload.get("schema_version") != 1:
        raise HandoffError("unsupported TaskToPR execution receipt schema")

    repository = _object(payload.get("repository_identity"), "repository_identity")
    if set(repository) != {"kind", "sha256"} or repository.get("kind") != "local-root-sha256":
        raise HandoffError("unsupported repository identity")
    repository_sha256 = _digest(repository["sha256"], "repository_identity.sha256")
    base_sha = _revision(payload.get("base_sha"), "base_sha")
    result_head_sha = _revision(payload.get("result_head_sha"), "result_head_sha")
    tested_head_sha = _revision(payload.get("tested_head_sha"), "tested_head_sha")
    if result_head_sha != tested_head_sha:
        raise HandoffError("receipt is not bound to the exact tested result HEAD")
    if payload.get("phase") not in {"verified_head", "pr_created"}:
        raise HandoffError("receipt has not completed exact-head verification")
    if payload.get("decision") != "REVIEW_REQUIRED":
        raise HandoffError("only review-required successful execution receipts can be handed off")
    if payload.get("protected_path_decision") != "allow":
        raise HandoffError("protected-path decision is not allow")

    tests = _object(payload.get("tests"), "tests")
    if set(tests) != {"status", "count"} or tests.get("status") != "pass":
        raise HandoffError("receipt does not contain passing exact-head tests")
    count = tests.get("count")
    if type(count) is not int or not 1 <= count <= 10_000:
        raise HandoffError("test count is invalid or outside the budget")

    policy = _object(payload.get("policy"), "policy")
    if set(policy) != {"version", "sha256"}:
        raise HandoffError("policy identity has unexpected fields")
    policy_version = _string(policy["version"], "policy.version")
    if not _VERSION.fullmatch(policy_version):
        raise HandoffError("policy version is invalid")
    policy_sha256 = _digest(policy["sha256"], "policy.sha256")

    tool = _object(payload.get("tool"), "tool")
    if set(tool) != {"name", "version", "source_sha256"} or tool.get("name") != "tasktopr":
        raise HandoffError("tool identity has unexpected fields")
    tool_version = _string(tool["version"], "tool.version")
    if not _VERSION.fullmatch(tool_version):
        raise HandoffError("tool version is invalid")
    tool_source_sha256 = _digest(tool["source_sha256"], "tool.source_sha256")

    manifest = _validated_manifest(payload.get("changed_file_manifest"))
    command_list_sha256 = _digest(payload.get("command_list_sha256"), "command_list_sha256")
    test_result_sha256 = _digest(payload.get("test_result_sha256"), "test_result_sha256")
    change_scope_sha256 = content_digest(manifest)

    handoff = {
        "schema_version": HANDOFF_SCHEMA,
        "component": "execution",
        "producer": {
            "name": "tasktopr",
            "version": tool_version,
            "git_revision": tool_revision,
            "source_sha256": tool_source_sha256,
        },
        "change": {
            "repository_sha256": repository_sha256,
            "base_sha": base_sha,
            "head_sha": tested_head_sha,
            "changed_file_count": len(manifest),
            "change_scope_sha256": change_scope_sha256,
        },
        "policy": {"version": policy_version, "sha256": policy_sha256},
        "verification": {
            "decision": "REVIEW_REQUIRED",
            "complete": True,
            "tests_status": "pass",
            "tests_count": count,
            "command_list_sha256": command_list_sha256,
            "test_result_sha256": test_result_sha256,
            "protected_path_decision": "allow",
        },
        "source_receipt": {"schema_version": 1, "sha256": receipt_sha256},
        "trust_boundary": (
            "sanitized TaskToPR execution evidence; identity/integrity only; "
            "not confidentiality, a signature, producer authentication, or merge authorization"
        ),
    }
    return {"payload": handoff, "receipt_sha256": content_digest(handoff)}


def _write_report(path: Path, report: dict[str, Any]) -> None:
    if not path.parent.is_dir() or path.is_symlink():
        raise HandoffError("output must be a non-symlink file in an existing directory")
    descriptor, temporary = tempfile.mkstemp(prefix=".tasktopr-handoff-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, sort_keys=True, indent=2, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if path.is_symlink():
            raise HandoffError("output path became a symlink during export")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def export_receipt_file(
    receipt_path: Path, *, tool_revision: str, output_path: Path
) -> dict[str, Any]:
    """Export one execution receipt to a deterministic portable handoff file."""
    report = build_execution_handoff(
        load_execution_receipt(receipt_path), tool_revision=tool_revision
    )
    _write_report(output_path, report)
    return report


def main() -> int:
    """Console entry point for installed/external Safe Delivery consumers."""
    parser = argparse.ArgumentParser(
        prog="tasktopr-export-evidence",
        description="Export a sanitized exact-head TaskToPR execution handoff.",
    )
    parser.add_argument("receipt", type=Path, help="Path to execution-receipt.json")
    parser.add_argument(
        "--tool-revision",
        required=True,
        help="Reviewer-pinned exact 40-character Git revision of the TaskToPR producer.",
    )
    parser.add_argument("--output", required=True, type=Path, help="Output JSON path")
    args = parser.parse_args()
    try:
        export_receipt_file(
            args.receipt,
            tool_revision=args.tool_revision,
            output_path=args.output,
        )
    except HandoffError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
