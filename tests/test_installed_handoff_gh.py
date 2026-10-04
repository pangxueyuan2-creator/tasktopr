from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


def smoke_module() -> ModuleType:
    script = Path(__file__).resolve().parents[1] / "scripts" / "installed_handoff_smoke.py"
    spec = importlib.util.spec_from_file_location("installed_handoff_smoke", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def synthetic_cli(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("installed-gh-fixture")
    subprocess.run(
        [sys.executable, "-m", "venv", str(root / "venv")], check=True, capture_output=True
    )
    scripts = root / "venv" / ("Scripts" if sys.platform == "win32" else "bin")
    python = scripts / ("python.exe" if sys.platform == "win32" else "python")
    smoke = smoke_module()
    tools = smoke.create_fake_gh(root, python)
    environment = dict(os.environ)
    environment["PATH"] = os.pathsep.join((str(tools), str(scripts), environment["PATH"]))
    environment["PYTHONNOUSERSITE"] = "1"
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    return smoke, root, python, tools, environment


def test_synthetic_gh_is_executable_and_verified(synthetic_cli) -> None:
    smoke, root, python, tools, environment = synthetic_cli
    smoke.verify_fake_gh(python, tools, environment, cwd=root)
    executable = tools / ("gh.exe" if sys.platform == "win32" else "gh")
    result = smoke.run([str(executable), "pr", "create"], cwd=root, env=environment)
    assert result.stdout.strip() == "https://example.invalid/tasktopr/installed-consumer/pull/1"
    if sys.platform == "win32":
        assert executable.read_bytes().startswith(b"MZ")


def test_synthetic_issue_wire_output_is_utf8(synthetic_cli) -> None:
    smoke, root, _python, tools, environment = synthetic_cli
    executable = tools / ("gh.exe" if sys.platform == "win32" else "gh")
    result = smoke.run(
        [str(executable), "issue", "view", "1", "--json", "number,title,body,url,labels"],
        cwd=root,
        env=dict(environment, PYTHONUTF8="0"),
    )
    assert json.loads(result.stdout) == smoke.FAKE_GH_ISSUE


@pytest.mark.parametrize("arguments", [["auth", "status"], ["issue", "view", "1"]])
def test_synthetic_gh_rejects_other_operations(synthetic_cli, arguments: list[str]) -> None:
    smoke, root, _python, tools, environment = synthetic_cli
    executable = tools / ("gh.exe" if sys.platform == "win32" else "gh")
    result = smoke.run([str(executable), *arguments], cwd=root, env=environment, expected=2)
    assert result.stdout == ""
    assert result.stderr == ""


def test_missing_synthetic_binary_fails_before_any_probe(
    synthetic_cli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    smoke, root, python, _tools, environment = synthetic_cli

    def unexpected_run(*_args, **_kwargs):
        pytest.fail("missing synthetic entry point must not execute any probe")

    monkeypatch.setattr(smoke, "run", unexpected_run)
    with pytest.raises(RuntimeError, match="entry point is missing"):
        smoke.verify_fake_gh(python, tmp_path / "missing", environment, cwd=root)


def test_absent_path_resolution_cannot_fall_back_to_real_gh(synthetic_cli) -> None:
    smoke, root, python, tools, environment = synthetic_cli
    empty_path = dict(environment, PATH="")
    with pytest.raises(RuntimeError, match="not the resolved gh"):
        smoke.verify_fake_gh(python, tools, empty_path, cwd=root)


def test_foreign_path_is_rejected_without_invoking_it(
    synthetic_cli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    smoke, root, python, tools, environment = synthetic_cli
    foreign = tmp_path / ("gh.exe" if sys.platform == "win32" else "gh")
    foreign.write_text("this synthetic foreign path must never be executed\n", encoding="utf-8")
    foreign.chmod(0o755)
    foreign_environment = dict(
        environment, PATH=os.pathsep.join((str(tmp_path), environment["PATH"]))
    )
    real_run = smoke.run
    called = []

    def record_run(command, **kwargs):
        called.append(command)
        return real_run(command, **kwargs)

    monkeypatch.setattr(smoke, "run", record_run)
    with pytest.raises(RuntimeError, match="not the resolved gh"):
        smoke.verify_fake_gh(python, tools, foreign_environment, cwd=root)
    assert len(called) == 1
    assert called[0][0] == str(python)
    assert str(foreign) not in called[0]


def test_wrong_identity_is_rejected(synthetic_cli, monkeypatch: pytest.MonkeyPatch) -> None:
    smoke, root, python, tools, environment = synthetic_cli
    real_run = smoke.run

    def wrong_identity(command, **kwargs):
        if command[-1] == "--tasktopr-smoke-shim":
            return subprocess.CompletedProcess(command, 0, "unexpected identity\n", "")
        return real_run(command, **kwargs)

    monkeypatch.setattr(smoke, "run", wrong_identity)
    with pytest.raises(RuntimeError, match="identity probe failed"):
        smoke.verify_fake_gh(python, tools, environment, cwd=root)
