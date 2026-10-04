from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tasktopr.agents import intake


def synthetic_gh(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
    return_code: int = 0,
) -> None:
    # The resolved interpreter sees `issue` as a local script. No real gh is invoked.
    (root / "issue").write_text(
        "import sys\n"
        f"sys.stdout.buffer.write({stdout!r})\n"
        f"sys.stderr.buffer.write({stderr!r})\n"
        f"raise SystemExit({return_code})\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(intake, "resolve_executable", lambda *_args: sys.executable)
    # Exercise actual subprocess pipe decoding with the Windows legacy locale.
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "cp936")


def test_issue_utf8_survives_cp936_subprocess_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "number": 12,
        "title": "修复中文🙂和 café",
        "body": "Acceptance criteria:\n- 保留中文和 emoji 🧪\nConstraints:\n- Do not 修改无关代码",
        "url": "https://example.invalid/issues/12",
        "labels": [{"name": "错误🐛"}],
    }
    synthetic_gh(
        tmp_path, monkeypatch, stdout=json.dumps(payload, ensure_ascii=False).encode("utf-8")
    )
    issue = intake.load_issue(tmp_path, 12)
    assert issue.title == payload["title"]
    assert issue.body == payload["body"]
    assert issue.labels == ["错误🐛"]
    assert issue.acceptance_criteria == ["保留中文和 emoji 🧪"]


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_invalid_utf8_has_a_safe_domain_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stream: str
) -> None:
    private_marker = b"synthetic-private-issue-do-not-log"
    raw = private_marker + b"\xff"
    synthetic_gh(
        tmp_path,
        monkeypatch,
        stdout=raw if stream == "stdout" else b"{}",
        stderr=raw if stream == "stderr" else b"",
        return_code=0 if stream == "stdout" else 1,
    )
    with pytest.raises(intake.IssueIntakeError, match="non-UTF-8 output") as caught:
        intake.load_issue(tmp_path, 12)
    assert private_marker.decode() not in str(caught.value)
    assert caught.value.__suppress_context__ is True


def test_valid_utf8_failure_remains_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synthetic_token = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"
    synthetic_gh(
        tmp_path,
        monkeypatch,
        stderr=f"凭证错误: {synthetic_token}".encode(),
        return_code=1,
    )
    with pytest.raises(intake.IssueIntakeError) as caught:
        intake.load_issue(tmp_path, 12)
    assert "凭证错误" in str(caught.value)
    assert "[REDACTED]" in str(caught.value)
    assert synthetic_token not in str(caught.value)
