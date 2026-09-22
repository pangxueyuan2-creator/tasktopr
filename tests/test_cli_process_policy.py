"""CLI diagnostics must use the same safe process boundaries as execution."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

import tasktopr.cli as cli_module
from tasktopr.cli import app

RUNNER = CliRunner()


def _hide_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    real_which = shutil.which

    def which(command: str, mode: int = os.F_OK, path: str | None = None) -> str | None:
        return None if command == "gh" else real_which(command, mode=mode, path=path)

    monkeypatch.setattr(shutil, "which", which)


@pytest.mark.parametrize("command", ["doctor", "review"])
def test_cli_git_inspection_never_executes_fsmonitor(
    demo_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    marker = tmp_path / "fsmonitor-must-not-run"
    hook = tmp_path / "synthetic-fsmonitor"
    hook.write_text(
        f'#!/bin/sh\nprintf fixture > "{marker.as_posix()}"\nexit 0\n', encoding="utf-8"
    )
    hook.chmod(0o700)
    subprocess.run(["git", "config", "core.fsmonitor", hook.as_posix()], cwd=demo_repo, check=True)
    target = demo_repo / "calculator.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# local edit\n", encoding="utf-8")
    _hide_gh(monkeypatch)
    monkeypatch.chdir(demo_repo)

    result = RUNNER.invoke(app, [command])

    assert result.exit_code == (0 if command == "doctor" else 1)
    assert not marker.exists(), "a diagnostic must reject hooks before invoking Git"


@pytest.mark.parametrize("command", ["doctor", "review"])
@pytest.mark.parametrize("failure", ["nonzero", "timeout"])
def test_cli_git_failure_is_unavailable_instead_of_clean(
    demo_repo: Path, monkeypatch: pytest.MonkeyPatch, command: str, failure: str
) -> None:
    real_run = subprocess.run

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str | bytes]:
        if argv[-2:] == ["rev-parse", "--show-toplevel"]:
            return real_run(argv, **kwargs)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 15)
        output = "" if kwargs.get("text") else b""
        return subprocess.CompletedProcess(argv, 1, stdout=output, stderr=output)

    _hide_gh(monkeypatch)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.chdir(demo_repo)

    result = RUNNER.invoke(app, [command])

    assert result.exit_code == (0 if command == "doctor" else 2)
    assert "unavailable" in result.stdout.lower()
    assert "clean" not in result.stdout.lower()


@pytest.mark.parametrize("failure", ["timeout", "oserror"])
def test_doctor_gh_uses_resolved_path_timeout_and_safe_failure_message(
    demo_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    real_which, real_run = shutil.which, subprocess.run
    executable = str(tmp_path / "external-tools" / "gh.exe")
    calls: list[list[str]] = []
    sentinel = "ghp_" + "a" * 36

    def which(command: str, mode: int = os.F_OK, path: str | None = None) -> str | None:
        return executable if command == "gh" else real_which(command, mode=mode, path=path)

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str | bytes]:
        if argv[-2:] != ["auth", "status"]:
            return real_run(argv, **kwargs)
        calls.append(argv)
        assert argv[0] == executable
        assert kwargs.get("cwd") == demo_repo
        assert kwargs.get("shell") is False
        timeout = kwargs.get("timeout")
        assert isinstance(timeout, int | float) and 0 < timeout <= 30
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, timeout, output=sentinel)
        raise OSError(sentinel)

    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.chdir(demo_repo)

    result = RUNNER.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert calls
    assert (
        "timed out" in result.stdout if failure == "timeout" else "not executable" in result.stdout
    )
    assert sentinel not in result.stdout


def test_doctor_rejects_repository_gh_executable(
    demo_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_which, real_run = shutil.which, subprocess.run
    shadow = demo_repo / "gh.exe"
    shadow.write_text("not executable", encoding="utf-8")
    auth_calls: list[list[str]] = []

    def which(command: str, mode: int = os.F_OK, path: str | None = None) -> str | None:
        return str(shadow) if command == "gh" else real_which(command, mode=mode, path=path)

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str | bytes]:
        if argv[-2:] == ["auth", "status"]:
            auth_calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.chdir(demo_repo)

    result = RUNNER.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert not auth_calls
    assert "blocked" in result.stdout


def test_doctor_recovers_from_real_auth_process_timeout(
    demo_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Use a real harmless sleeper as the auth process, with a shorter deadline
    # only after proving the production call supplied its finite timeout.
    (demo_repo / "auth").write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    real_run, real_which = subprocess.run, shutil.which
    auth_calls: list[list[str]] = []

    def which(command: str, mode: int = os.F_OK, path: str | None = None) -> str | None:
        return sys.executable if command == "gh" else real_which(command, mode=mode, path=path)

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str | bytes]:
        if argv[-2:] == ["auth", "status"]:
            auth_calls.append(argv)
            assert kwargs.get("timeout") == 15
            kwargs["timeout"] = 0.05
        return real_run(argv, **kwargs)

    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(cli_module, "resolve_executable", lambda _name, _root: sys.executable)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.chdir(demo_repo)

    result = RUNNER.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert auth_calls
    assert "timed out" in result.stdout


def test_doctor_preserves_relative_path_resolution_from_repository_subdirectory(
    demo_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = demo_repo / "nested"
    start.mkdir()
    executable = tmp_path / "external-tools" / "gh.exe"
    executable.parent.mkdir()
    executable.write_text("placeholder; process call is captured", encoding="utf-8")
    relative_executable = os.path.relpath(executable, start)
    real_run, real_which = subprocess.run, shutil.which
    auth_calls: list[list[str]] = []

    def which(command: str, mode: int = os.F_OK, path: str | None = None) -> str | None:
        if command == "gh":
            return relative_executable
        return real_which(command, mode=mode, path=path)

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str | bytes]:
        if argv[-2:] == ["auth", "status"]:
            auth_calls.append(argv)
            assert argv[0] == str(executable.resolve())
            assert kwargs.get("cwd") == demo_repo
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.chdir(start)

    result = RUNNER.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert auth_calls
    assert "authenticated" in result.stdout
