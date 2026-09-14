"""Exercise installed TaskToPR -> PatchWitness Safe Delivery end to end."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PATCHWITNESS_REPOSITORY = "https://github.com/pangxueyuan2-creator/patchwitness.git"
PATCHWITNESS_REVISION = "e44d2c7ccea615bb4b43449e77573e02c0bcbb60"
PATCHWITNESS_POLICY_PATH = ".pw-policy.toml"


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


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    expected: int = 0,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode != expected:
        raise RuntimeError(
            f"command returned {result.returncode}, expected {expected}: {command}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def git(root: Path, *arguments: str) -> str:
    return run(["git", "-C", str(root), *arguments]).stdout.strip()


def source_revision() -> str:
    root = Path(__file__).resolve().parents[1]
    revision = git(root, "rev-parse", "HEAD")
    if len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision):
        raise RuntimeError("TaskToPR checkout is not bound to an exact Git SHA")
    return revision


def create_fixture(root: Path) -> tuple[Path, str]:
    remote = root / "remote.git"
    repository = root / "consumer-repository"
    run(["git", "init", "--bare", str(remote)])
    run(["git", "init", "-b", "main", str(repository)])
    git(repository, "config", "user.name", "Installed Consumer Fixture")
    git(repository, "config", "user.email", "fixture@example.invalid")

    (repository / ".gitignore").write_text(".tasktopr/\n", encoding="utf-8")
    (repository / ".tasktopr.toml").write_text(
        """[agent]\nprovider = \"demo\"\n\n[testing]\ncommands = [[\"python\", \"-m\", \"unittest\", \"discover\", \"-v\"]]\n""",
        encoding="utf-8",
    )
    (repository / PATCHWITNESS_POLICY_PATH).write_text(
        'id = "installed-safe-delivery-fixture"\ngoal = "verify the exact installed consumer candidate"\n',
        encoding="utf-8",
    )
    demo_issue = {
        "number": 1,
        "title": "Prevent a crash when dividing by zero",
        "body": (
            "The calculator crashes when the denominator is zero.\n\n"
            "Acceptance criteria:\n"
            "- divide(8, 0) raises a clear ValueError\n"
            "- normal division continues to work\n\n"
            "Constraints:\n"
            "- Do not refactor unrelated arithmetic behavior"
        ),
        "url": "https://example.invalid/issues/1",
        "labels": [{"name": "bug"}],
    }
    (repository / ".tasktopr-demo-issue.json").write_text(
        json.dumps(demo_issue, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (repository / "calculator.py").write_text(
        "def divide(numerator: float, denominator: float) -> float:\n"
        "    return numerator / denominator\n",
        encoding="utf-8",
    )
    (repository / "test_calculator.py").write_text(
        "import unittest\n\n"
        "from calculator import divide\n\n\n"
        "class DivideTests(unittest.TestCase):\n"
        "    def test_divide_returns_quotient(self) -> None:\n"
        "        self.assertEqual(divide(8, 2), 4)\n",
        encoding="utf-8",
    )
    git(
        repository,
        "add",
        "--",
        ".gitignore",
        ".tasktopr.toml",
        PATCHWITNESS_POLICY_PATH,
        ".tasktopr-demo-issue.json",
        "calculator.py",
        "test_calculator.py",
    )
    git(repository, "commit", "-m", "fixture base")
    base = git(repository, "rev-parse", "HEAD")
    git(repository, "remote", "add", "origin", str(remote))
    git(repository, "push", "-u", "origin", "main")
    return repository, base


def create_fake_gh(root: Path) -> Path:
    tools = root / "tools"
    tools.mkdir()
    executable = tools / "gh"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if sys.argv[1:3] != ['pr', 'create']:\n"
        "    raise SystemExit(2)\n"
        "print('https://example.invalid/tasktopr/installed-consumer/pull/1')\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return tools


def newest_run(repository: Path) -> Path:
    runs = sorted((repository / ".tasktopr" / "runs").iterdir())
    if len(runs) != 1:
        raise RuntimeError("expected exactly one TaskToPR run in the synthetic repository")
    return runs[0]


def build_patchwitness_wheel(python: Path, root: Path) -> Path:
    wheels = root / "patchwitness-dist"
    wheels.mkdir()
    run(
        [
            str(python),
            "-m",
            "pip",
            "wheel",
            "--disable-pip-version-check",
            "--no-deps",
            "--wheel-dir",
            str(wheels),
            f"git+{PATCHWITNESS_REPOSITORY}@{PATCHWITNESS_REVISION}",
        ],
        cwd=root,
        timeout=240,
    )
    candidates = sorted(wheels.glob("patchwitness-*.whl"))
    if len(candidates) != 1:
        raise RuntimeError("expected exactly one PatchWitness wheel")
    return candidates[0]


def verify_rejected(
    cli: Path,
    handoff: Path,
    *,
    tasktopr_revision: str,
    base: str,
    repository: Path,
    output: Path,
) -> None:
    result = run(
        [
            str(cli),
            "--json",
            "tasktopr",
            "--handoff",
            str(handoff),
            "--tasktopr-revision",
            tasktopr_revision,
            "--base",
            base,
            "--policy-ref",
            base,
            "--policy-path",
            PATCHWITNESS_POLICY_PATH,
            "--output",
            str(output),
        ],
        cwd=repository,
        expected=2,
    )
    payload = json.loads(result.stdout)
    if payload.get("ok") is not False or output.exists():
        raise RuntimeError("PatchWitness did not fail closed for rejected execution evidence")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: installed_handoff_smoke.py DIST_DIRECTORY")
    tasktopr_wheels = sorted(Path(sys.argv[1]).glob("tasktopr-*.whl"))
    if len(tasktopr_wheels) != 1:
        raise RuntimeError("expected exactly one TaskToPR wheel")
    tasktopr_wheel = tasktopr_wheels[0].resolve()
    tasktopr_revision = source_revision()

    with tempfile.TemporaryDirectory(prefix="tasktopr-patchwitness-consumer-") as temporary:
        root = Path(temporary)
        venv = root / "venv"
        run([sys.executable, "-m", "venv", str(venv)])
        scripts = venv / ("Scripts" if sys.platform == "win32" else "bin")
        python = scripts / ("python.exe" if sys.platform == "win32" else "python")
        tasktopr = scripts / ("tasktopr.exe" if sys.platform == "win32" else "tasktopr")
        exporter = scripts / (
            "tasktopr-export-evidence.exe"
            if sys.platform == "win32"
            else "tasktopr-export-evidence"
        )
        patchwitness = scripts / (
            "patchwitness-safe-delivery.exe"
            if sys.platform == "win32"
            else "patchwitness-safe-delivery"
        )

        run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                str(tasktopr_wheel),
            ],
            cwd=root,
            timeout=240,
        )
        patchwitness_wheel = build_patchwitness_wheel(python, root)
        run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-deps",
                str(patchwitness_wheel),
            ],
            cwd=root,
        )
        for executable in (tasktopr, exporter, patchwitness):
            if not executable.is_file():
                raise RuntimeError(f"installed entry point is missing: {executable.name}")

        repository, base = create_fixture(root)
        tools = create_fake_gh(root)
        environment = os.environ.copy()
        environment["PATH"] = os.pathsep.join((str(tools), str(scripts), environment["PATH"]))
        environment["PYTHONNOUSERSITE"] = "1"
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)

        run([str(tasktopr), "fix", "1", "--demo"], cwd=repository, env=environment)
        run_dir = newest_run(repository)
        receipt = run_dir / "execution-receipt.json"
        if not receipt.is_file():
            raise RuntimeError("installed TaskToPR run did not emit an execution receipt")
        candidate = git(repository, "rev-parse", "HEAD")
        if candidate == base:
            raise RuntimeError("installed TaskToPR run did not produce a candidate commit")

        handoff = root / "execution-handoff.json"
        handoff_repeat = root / "execution-handoff-repeat.json"
        for target in (handoff, handoff_repeat):
            run(
                [
                    str(exporter),
                    str(receipt),
                    "--tool-revision",
                    tasktopr_revision,
                    "--output",
                    str(target),
                ],
                cwd=repository,
                env=environment,
            )
        if handoff.read_bytes() != handoff_repeat.read_bytes():
            raise RuntimeError("identical execution evidence did not export deterministically")

        handoff_report = json.loads(handoff.read_text(encoding="utf-8"))
        handoff_payload = handoff_report["payload"]
        if handoff_payload["schema_version"] != "tasktopr.dev/safe-delivery/execution/v1":
            raise RuntimeError("installed TaskToPR exporter emitted the wrong handoff schema")
        if handoff_payload["change"]["base_sha"] != base:
            raise RuntimeError("TaskToPR handoff lost the exact base revision")
        if handoff_payload["change"]["head_sha"] != candidate:
            raise RuntimeError("TaskToPR handoff lost the exact tested candidate revision")
        if handoff_payload["producer"]["git_revision"] != tasktopr_revision:
            raise RuntimeError("TaskToPR handoff lost the exact producer revision")

        passport = root / "safe-delivery.json"
        composed = run(
            [
                str(patchwitness),
                "--json",
                "tasktopr",
                "--handoff",
                str(handoff),
                "--tasktopr-revision",
                tasktopr_revision,
                "--base",
                base,
                "--policy-ref",
                base,
                "--policy-path",
                PATCHWITNESS_POLICY_PATH,
                "--output",
                str(passport),
            ],
            cwd=repository,
            env=environment,
        )
        composition = json.loads(composed.stdout)
        if composition["ok"] is not True or composition["decision"] != "UNKNOWN":
            raise RuntimeError("PatchWitness upgraded execution evidence into merge authority")

        first_verify = run(
            [str(patchwitness), "--json", "verify", str(passport)],
            cwd=repository,
            env=environment,
        )
        second_verify = run(
            [str(patchwitness), "--json", "verify", str(passport)],
            cwd=repository,
            env=environment,
        )
        verified = json.loads(first_verify.stdout)
        verified_repeat = json.loads(second_verify.stdout)
        if verified != verified_repeat:
            raise RuntimeError("offline verification was not deterministic for identical evidence")
        if verified["ok"] is not True or verified["decision"] != "UNKNOWN":
            raise RuntimeError("installed PatchWitness did not verify the Change Passport")
        if verified["receipt_sha256"] != composition["receipt_sha256"]:
            raise RuntimeError("offline verification changed the Change Passport receipt identity")

        tampered_report = json.loads(handoff.read_text(encoding="utf-8"))
        tampered_report["receipt_sha256"] = "0" * 64
        tampered_handoff = root / "tampered-handoff.json"
        tampered_handoff.write_text(json.dumps(tampered_report), encoding="utf-8")
        verify_rejected(
            patchwitness,
            tampered_handoff,
            tasktopr_revision=tasktopr_revision,
            base=base,
            repository=repository,
            output=root / "tampered-passport.json",
        )

        incomplete_report = json.loads(handoff.read_text(encoding="utf-8"))
        incomplete_report["payload"]["verification"]["complete"] = False
        incomplete_report["receipt_sha256"] = digest(incomplete_report["payload"])
        incomplete_handoff = root / "incomplete-handoff.json"
        incomplete_handoff.write_text(json.dumps(incomplete_report), encoding="utf-8")
        verify_rejected(
            patchwitness,
            incomplete_handoff,
            tasktopr_revision=tasktopr_revision,
            base=base,
            repository=repository,
            output=root / "incomplete-passport.json",
        )

        summary = {
            "decision": verified["decision"],
            "passport_receipt_sha256": verified["receipt_sha256"],
            "tasktopr_revision": tasktopr_revision,
            "tasktopr_wheel_sha256": file_digest(tasktopr_wheel),
            "patchwitness_revision": PATCHWITNESS_REVISION,
            "patchwitness_wheel_sha256": file_digest(patchwitness_wheel),
            "base_sha": base,
            "candidate_sha": candidate,
            "tampered_handoff_rejected": True,
            "incomplete_handoff_rejected": True,
        }
        print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
