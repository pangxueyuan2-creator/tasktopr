"""Exercise the built wheel as an external Safe Delivery evidence producer."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def fixture_receipt() -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "run_id": "external-consumer-secret-run-id",
        "started_at": "2026-09-14T00:00:00+00:00",
        "repository_identity": {"kind": "local-root-sha256", "sha256": "1" * 64},
        "base_sha": "2" * 40,
        "result_head_sha": "3" * 40,
        "tested_head_sha": "3" * 40,
        "policy": {"version": "tasktopr-execution-v1", "sha256": "4" * 64},
        "tool": {"name": "tasktopr", "version": "0.1.0", "source_sha256": "5" * 64},
        "task_sha256": "6" * 64,
        "patch_sha256": "7" * 64,
        "command_list_sha256": "8" * 64,
        "test_result_sha256": "9" * 64,
        "changed_file_manifest": [{"path_sha256": "a" * 64, "before": "b" * 64, "after": "c" * 64}],
        "protected_path_decision": "allow",
        "tests": {"status": "pass", "count": 1},
        "ci": "unknown",
        "human_review": "unknown",
        "decision": "REVIEW_REQUIRED",
        "trust_boundary": "local observation",
        "phase": "verified_head",
        "branch_sha256": "d" * 64,
    }
    return {"payload": payload, "receipt_sha256": digest(payload)}


def run(command: list[str], *, expected: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=60)
    if result.returncode != expected:
        raise RuntimeError(
            f"command returned {result.returncode}, expected {expected}: {command}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: installed_handoff_smoke.py DIST_DIRECTORY")
    wheels = sorted(Path(sys.argv[1]).glob("tasktopr-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("expected exactly one TaskToPR wheel")

    with tempfile.TemporaryDirectory(prefix="tasktopr-installed-consumer-") as temporary:
        root = Path(temporary)
        venv = root / "venv"
        run([sys.executable, "-m", "venv", str(venv)])
        python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        exporter = venv / (
            "Scripts/tasktopr-export-evidence.exe"
            if sys.platform == "win32"
            else "bin/tasktopr-export-evidence"
        )
        run([str(python), "-m", "pip", "install", "--no-deps", str(wheels[0])])
        if not exporter.is_file():
            raise RuntimeError("built wheel did not install the evidence exporter entry point")

        receipt_path = root / "execution-receipt.json"
        output_path = root / "execution-handoff.json"
        receipt_path.write_text(json.dumps(fixture_receipt()), encoding="utf-8")
        run(
            [
                str(exporter),
                str(receipt_path),
                "--tool-revision",
                "e" * 40,
                "--output",
                str(output_path),
            ]
        )
        report = json.loads(output_path.read_text(encoding="utf-8"))
        payload = report["payload"]
        if payload["schema_version"] != "tasktopr.dev/safe-delivery/execution/v1":
            raise RuntimeError("installed exporter emitted the wrong schema")
        if payload["change"]["head_sha"] != "3" * 40:
            raise RuntimeError("installed exporter lost exact-head identity")
        if payload["producer"]["git_revision"] != "e" * 40:
            raise RuntimeError("installed exporter lost the reviewer-pinned producer revision")
        serialized = json.dumps(report, sort_keys=True)
        if "external-consumer-secret-run-id" in serialized:
            raise RuntimeError("installed exporter leaked run-local metadata")

        tampered = fixture_receipt()
        tampered["receipt_sha256"] = "f" * 64
        receipt_path.write_text(json.dumps(tampered), encoding="utf-8")
        rejected_path = root / "rejected.json"
        run(
            [
                str(exporter),
                str(receipt_path),
                "--tool-revision",
                "e" * 40,
                "--output",
                str(rejected_path),
            ],
            expected=2,
        )
        if rejected_path.exists():
            raise RuntimeError("installed exporter wrote output for a tampered receipt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
