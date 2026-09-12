"""Safe, local fixtures for output bounds and process lifetime containment."""

from __future__ import annotations

import os
import sys
import threading
import time
import tracemalloc
from pathlib import Path

import pytest

from tasktopr.bounded_process import (
    BoundedProcessError,
    run_bounded_command,
    sanitized_environment,
)


def script(tmp_path: Path, content: str) -> list[str]:
    file = tmp_path / "command.py"
    file.write_text(content, encoding="utf-8")
    return [sys.executable, "-I", str(file)]


def test_normal_output_and_nonzero_status(tmp_path: Path) -> None:
    command = script(
        tmp_path, "import sys\nprint('out')\nprint('err', file=sys.stderr)\nsys.exit(7)\n"
    )
    result = run_bounded_command(command, tmp_path, 5)
    assert result.return_code == 7
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"
    assert result.cleanup_complete
    assert not result.timed_out
    assert not result.output_limit_exceeded


def test_both_streams_drain_without_deadlock_and_retention_is_bounded(tmp_path: Path) -> None:
    command = script(
        tmp_path,
        "import os\nfor _ in range(128):\n os.write(1,b'a'*4096)\n os.write(2,b'b'*4096)\n",
    )
    result = run_bounded_command(
        command, tmp_path, 10, output_limit_bytes=2**21, capture_limit_bytes=37
    )
    assert result.return_code == 0
    assert result.output_bytes == 2**20
    assert result.stdout == "a" * 37
    assert result.stderr == "b" * 37
    assert result.cleanup_complete


def test_output_budget_terminates_an_unbounded_producer(tmp_path: Path) -> None:
    command = script(
        tmp_path, "import os\nwhile True:\n os.write(1,b'x'*8192)\n os.write(2,b'y'*8192)\n"
    )
    result = run_bounded_command(
        command, tmp_path, 10, output_limit_bytes=32_768, capture_limit_bytes=19
    )
    assert result.return_code == 125
    assert result.output_limit_exceeded
    assert len(result.stdout) <= 19
    assert len(result.stderr) <= 19
    assert result.elapsed_seconds < 5
    assert result.cleanup_complete


def test_output_budget_catches_fast_exit_producer(tmp_path: Path) -> None:
    command = script(tmp_path, "import os\nos.write(1,b'x'*65536)\n")
    result = run_bounded_command(command, tmp_path, 5, output_limit_bytes=16)
    assert result.return_code == 125
    assert result.output_limit_exceeded


def test_32_mib_stream_does_not_become_32_mib_parent_capture(tmp_path: Path) -> None:
    command = script(tmp_path, "import os\nfor _ in range(4096): os.write(1,b'x'*8192)\n")
    tracemalloc.start()
    try:
        result = run_bounded_command(
            command, tmp_path, 15, output_limit_bytes=64 * 1024 * 1024, capture_limit_bytes=127
        )
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.return_code == 0
    assert result.output_bytes == 32 * 1024 * 1024
    assert len(result.stdout) == 127
    assert peak < 2 * 1024 * 1024


def test_timeout_retains_partial_output(tmp_path: Path) -> None:
    command = script(tmp_path, "import time\nprint('started',flush=True)\ntime.sleep(30)\n")
    result = run_bounded_command(command, tmp_path, 0.3)
    assert result.return_code == 124
    assert result.timed_out
    assert result.stdout.strip() == "started"
    assert result.elapsed_seconds < 3
    assert result.cleanup_complete


@pytest.mark.parametrize("leader_exits", [False, True])
def test_descendants_cannot_survive_command_completion(tmp_path: Path, leader_exits: bool) -> None:
    marker = tmp_path / "unexpected-child-survival"
    child = tmp_path / "child.py"
    child.write_text(
        "import time\nfrom pathlib import Path\ntime.sleep(0.8)\n"
        f"Path({str(marker)!r}).write_text('survived')\n",
        encoding="utf-8",
    )
    command = script(
        tmp_path,
        "import subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable,'-I',{str(child)!r}])\n"
        "print('child launched',flush=True)\n" + ("" if leader_exits else "time.sleep(30)\n"),
    )
    result = run_bounded_command(command, tmp_path, 0.4)
    assert result.return_code == (0 if leader_exits else 124)
    assert "child launched" in result.stdout
    assert result.cleanup_complete
    time.sleep(0.9)
    assert not marker.exists()


def test_cancellation_kills_running_command(tmp_path: Path) -> None:
    command = script(tmp_path, "import time\ntime.sleep(30)\n")
    event = threading.Event()
    timer = threading.Timer(0.2, event.set)
    timer.start()
    try:
        result = run_bounded_command(command, tmp_path, 10, cancel_event=event)
    finally:
        timer.cancel()
        timer.join()
    assert result.cancelled
    assert result.return_code == 130
    assert result.cleanup_complete


def test_precancelled_command_never_starts(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    command = script(tmp_path, f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    event = threading.Event()
    event.set()
    result = run_bounded_command(command, tmp_path, 1, cancel_event=event)
    assert result.return_code == 130
    assert not marker.exists()


def test_credential_and_interpreter_environment_is_not_inherited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-fixture-value")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-fixture-value")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setenv("NODE_OPTIONS", "--require=untrusted")
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted.invalid")
    command = script(tmp_path, "import os,json\nprint(json.dumps(sorted(os.environ)))\n")
    result = run_bounded_command(command, tmp_path, 5)
    assert result.return_code == 0
    for key in (
        "GITHUB_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "PYTHONPATH",
        "NODE_OPTIONS",
        "HTTPS_PROXY",
    ):
        assert key not in result.stdout
        assert key not in sanitized_environment()


def test_explicit_environment_is_copied_and_not_mutated(tmp_path: Path) -> None:
    environment = sanitized_environment() | {"FIXTURE_ALLOWED": "approved"}
    before = environment.copy()
    command = script(tmp_path, "import os\nprint(os.environ['FIXTURE_ALLOWED'])\n")
    result = run_bounded_command(command, tmp_path, 5, env=environment)
    assert result.return_code == 0
    assert result.stdout.strip() == "approved"
    assert environment == before


def test_invalid_utf8_cannot_break_capture(tmp_path: Path) -> None:
    result = run_bounded_command(script(tmp_path, "import os\nos.write(1,b'\\xff')\n"), tmp_path, 5)
    assert result.return_code == 0
    assert result.stdout == "\ufffd"


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_timeout_is_rejected(tmp_path: Path, timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        run_bounded_command([sys.executable], tmp_path, timeout)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_output_limit_is_rejected(tmp_path: Path, limit: int) -> None:
    with pytest.raises(ValueError, match="Output limits"):
        run_bounded_command([sys.executable], tmp_path, 1, output_limit_bytes=limit)


def test_relative_executable_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute path"):
        run_bounded_command(["python"], tmp_path, 1)


def test_missing_executable_does_not_leave_capture_threads(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        run_bounded_command([str(tmp_path / "not-an-executable")], tmp_path, 1)
    assert not any(thread.name.startswith("tasktopr-output-") for thread in threading.enumerate())


@pytest.mark.skipif(os.name != "nt", reason="Windows job-assignment failure boundary")
def test_failed_job_assignment_never_runs_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tasktopr import bounded_process

    def reject(self: object, pid: int) -> None:
        raise BoundedProcessError("synthetic job failure")

    monkeypatch.setattr(bounded_process._WindowsJob, "assign_and_resume", reject)
    marker = tmp_path / "must-not-start"
    command = script(tmp_path, f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    with pytest.raises(BoundedProcessError, match="synthetic job failure"):
        run_bounded_command(command, tmp_path, 5)
    assert not marker.exists()
