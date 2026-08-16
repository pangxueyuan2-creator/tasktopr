"""Executable resolution regressions: shadowing, shims and missing tools."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from tasktopr.security import run_safe_command


def _plant_shadow(root: Path, name: str) -> None:
    """Plant a real interpreter named like an allowlisted tool.

    If it were executed with ["-c", "import sys; sys.exit(77)"] it would
    exit 77, distinguishing it from both a real tool and a blocked run.
    """

    if os.name == "nt":
        shutil.copy(sys.executable, root / f"{name}.exe")
    else:
        shutil.copy(sys.executable, root / name)
        (root / name).chmod(0o755)


def test_repository_root_shadow_never_runs(tmp_path: Path) -> None:
    """A binary planted at the repository root must not shadow a real tool.

    Windows CreateProcess searches the parent's current directory before
    PATH; on other platforms this documents that PATH resolution never
    consults the working directory either.
    """

    _plant_shadow(tmp_path, "node")
    result = run_safe_command(
        ["node", "-c", "import sys; sys.exit(77)"],
        cwd=tmp_path,
        timeout_seconds=15,
    )
    assert result.return_code != 77


def test_windows_batch_shims_are_blocked(tmp_path: Path) -> None:
    """npm.cmd cannot run without a shell; it must block instead of raising."""

    if os.name != "nt":
        pytest.skip("batch shims are a Windows concern")
    result = run_safe_command(["npm", "--version"], cwd=tmp_path, timeout_seconds=15)
    assert result.blocked is True
    assert "batch shim" in (result.reason or "")


def test_python_command_uses_interpreter_not_repo_root_shadow(tmp_path: Path) -> None:
    """python commands substitute sys.executable, so a repo-root python shadow
    is never consulted (Windows CreateProcess cwd search included)."""

    marker = tmp_path / "shadow-ran.txt"
    shadow_name = "python.exe" if os.name == "nt" else "python"
    (tmp_path / shadow_name).write_text(
        f"import pathlib\npathlib.Path({str(marker)!r}).write_text('ran')\n",
        encoding="utf-8",
    )
    if os.name != "nt":
        (tmp_path / shadow_name).chmod(0o755)

    result = run_safe_command(
        ["python", "-c", "print('real-interpreter')"],
        cwd=tmp_path,
        timeout_seconds=15,
    )

    assert result.return_code == 0
    assert "real-interpreter" in result.stdout
    assert not result.blocked
    assert not marker.exists()


def test_pytest_resolution_skips_repo_root_shadow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pytest resolves on PATH, never through a repo-root pytest shadow; the
    planted copy of the interpreter would print a Python banner, not pytest's."""

    _plant_shadow(tmp_path, "pytest")
    scripts = Path(sys.executable).parent
    monkeypatch.setenv(
        "PATH", str(scripts) + os.pathsep + os.environ.get("PATH", "")
    )
    result = run_safe_command(["pytest", "--version"], cwd=tmp_path, timeout_seconds=15)

    assert result.return_code == 0
    assert not result.blocked
    assert "pytest" in result.stdout


def test_missing_executable_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tool that cannot be resolved on PATH must be a clean blocked result."""

    monkeypatch.setenv("PATH", str(tmp_path))
    result = run_safe_command(["pytest", "--version"], cwd=tmp_path, timeout_seconds=15)
    assert result.blocked is True
    assert result.return_code in {126, 127}
