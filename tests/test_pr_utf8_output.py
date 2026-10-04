from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tasktopr import pr


def test_actual_git_commit_output_survives_cp936_default(
    demo_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "cp936")
    target = demo_repo / "calculator.py"
    target.write_text(
        target.read_text(encoding="utf-8") + "\n# synthetic change\n", encoding="utf-8"
    )
    pr._run_git(["git", "add", "--", "calculator.py"], demo_repo)
    title = "修复中文🙂 and café"
    result = pr._run_git(["git", "commit", "-m", title], demo_repo)
    assert title in result.stdout
    assert result.stderr == ""
    revision = pr._run_git(["git", "rev-parse", "HEAD"], demo_repo).stdout.strip()
    assert len(revision) == 40
    assert pr._run_git(["git", "log", "-1", "--format=%s"], demo_repo).stdout.strip() == title


def synthetic_output(root: Path, *, stdout: bytes, stderr: bytes, return_code: int) -> list[str]:
    script = root / "synthetic_output.py"
    script.write_text(
        "import sys\n"
        f"sys.stdout.buffer.write({stdout!r})\n"
        f"sys.stderr.buffer.write({stderr!r})\n"
        f"raise SystemExit({return_code})\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_invalid_output_fails_without_exposing_raw_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stream: str
) -> None:
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "cp936")
    marker = b"synthetic-private-command-content"
    raw = marker + b"\xff"
    command = synthetic_output(
        tmp_path,
        stdout=raw if stream == "stdout" else b"",
        stderr=raw if stream == "stderr" else b"",
        return_code=0 if stream == "stdout" else 1,
    )
    with pytest.raises(pr.PullRequestError, match="non-UTF-8 output") as caught:
        pr._run(command, tmp_path)
    assert marker.decode() not in str(caught.value)
    assert caught.value.__suppress_context__ is True


def test_valid_utf8_command_failure_keeps_redacted_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "cp936")
    token = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"
    command = synthetic_output(
        tmp_path, stdout=b"", stderr=f"提交错误: {token}".encode(), return_code=1
    )
    with pytest.raises(pr.PullRequestError) as caught:
        pr._run(command, tmp_path)
    assert "提交错误" in str(caught.value)
    assert "[REDACTED]" in str(caught.value)
    assert token not in str(caught.value)
