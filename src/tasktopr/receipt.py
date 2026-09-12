"""Local execution provenance. Digests identify bytes; they do not encrypt them.

This is an observation receipt, not an attestation or an execution sandbox.
Concurrent writers must be excluded by the caller. Persistent drift is rejected.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .config import TaskToPRConfig
from .events import RunJournal
from .models import CommandResult, Issue, PatchRequest, ReviewResult
from .security import SecurityError, is_dependency_path, is_protected, resolve_executable

MAX_FILES = 10_000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
POLICY_VERSION = "tasktopr-execution-v1"
_EPHEMERAL = {".tasktopr", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
_DEVICE = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", re.IGNORECASE)


def digest(value: object) -> str:
    """SHA-256 of canonical UTF-8 JSON, without insignificant whitespace."""
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def git(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    """Read local Git metadata with external diff/text conversion disabled."""
    executable = resolve_executable("git", root)
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", GIT_NO_REPLACE_OBJECTS="1")
    result = subprocess.run(
        [executable, "--no-pager", "-c", "core.fsmonitor=false", *args],
        cwd=root,
        env=env,
        capture_output=True,
        timeout=15,
        check=False,
        shell=False,
        input=input_bytes,
    )
    if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
        raise SecurityError("Execution evidence Git read failed or exceeded its budget.")
    return result.stdout


def head(root: Path) -> str:
    value = git(root, "rev-parse", "--verify", "HEAD").decode("ascii").strip()
    if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", value):
        raise SecurityError("Invalid repository revision.")
    return value


def branch(root: Path) -> str:
    return git(root, "symbolic-ref", "--short", "HEAD").decode("utf-8").strip()


def validate_path(root: Path, relative: str) -> Path:
    """Reject ambiguous cross-platform paths and links without normalizing identity."""
    parts = relative.split("/")
    if (
        not relative
        or "\\" in relative
        or ":" in relative
        or unicodedata.normalize("NFC", relative) != relative
        or any(ord(char) < 32 for char in relative)
        or any(
            part in {"", ".", ".."} or part.endswith((".", " ")) or _DEVICE.match(part)
            for part in parts
        )
        or any(part.casefold() == ".git" for part in parts)
    ):
        raise SecurityError("Ambiguous execution evidence path.")
    target = root
    for part in parts:
        target /= part
        if target.is_symlink() or (hasattr(target, "is_junction") and target.is_junction()):
            raise SecurityError("Linked execution evidence path is unsupported.")
        if target != root / relative and (target / ".git").exists():
            raise SecurityError("Nested repositories are unsupported execution evidence.")
    if not target.resolve().is_relative_to(root.resolve()):
        raise SecurityError("Execution evidence escaped the repository.")
    return target


def _checked_attributes(root: Path, names: set[str]) -> bytes:
    attributes = git(
        root,
        "check-attr",
        "--all",
        "--stdin",
        "-z",
        input_bytes=b"\0".join(name.encode("utf-8") for name in sorted(names)) + b"\0",
    )
    fields = attributes.split(b"\0")
    if any(
        fields[i] in {b"filter", b"ident", b"working-tree-encoding"}
        and fields[i + 1] not in {b"unspecified", b"unset"}
        for i in range(1, len(fields) - 1, 3)
    ):
        raise SecurityError("Custom Git content transformations cannot bind execution evidence.")
    return attributes


def changed_paths(root: Path) -> list[str]:
    inventory = git(root, "ls-files", "-z") + git(
        root, "ls-files", "--others", "--exclude-standard", "-z"
    )
    _checked_attributes(root, {p.decode("utf-8") for p in inventory.split(b"\0") if p})
    tracked = git(
        root,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        "--name-only",
        "-z",
        "HEAD",
        "--",
    )
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z")
    # Only untracked caches are excluded. Tracked control-plane/cache changes count.
    names = {p.decode("utf-8") for p in tracked.split(b"\0") if p}
    names.update(
        p.decode("utf-8")
        for p in untracked.split(b"\0")
        if p and not set(p.decode("utf-8").split("/")) & _EPHEMERAL
    )
    return sorted(names)


def snapshot(root: Path) -> dict[str, str]:
    """Hash tracked and visible untracked regular files, with explicit size budgets."""
    entries = git(root, "ls-files", "--stage", "-z").split(b"\0")
    flags = git(root, "ls-files", "-v", "-z").split(b"\0")
    if any(entry and not entry.startswith(b"H ") for entry in flags):
        raise SecurityError("Masked index flags cannot bind execution evidence.")
    names: set[str] = set()
    for entry in entries:
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        mode, _oid, stage = metadata.split()
        if mode not in {b"100644", b"100755"} or stage != b"0":
            raise SecurityError(
                "Submodule, symlink, or unmerged index cannot bind execution evidence."
            )
        names.add(path.decode("utf-8"))
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z")
    names.update(
        p.decode("utf-8")
        for p in untracked.split(b"\0")
        if p and not set(p.decode("utf-8").split("/")) & _EPHEMERAL
    )
    if len(names) > MAX_FILES:
        raise SecurityError("Execution evidence file-count budget exceeded.")
    if len({name.casefold() for name in names}) != len(names):
        raise SecurityError("Case-colliding execution evidence paths.")
    result: dict[str, str] = {}
    attributes = _checked_attributes(root, names)
    result["\0attributes"] = digest(attributes.hex())
    total = 0
    for name in sorted(names):
        target = validate_path(root, name)
        if not target.exists():
            result[name] = "deleted"
            continue
        before = target.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
            raise SecurityError("Execution evidence requires bounded regular files.")
        with target.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        total += len(data)
        if len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise SecurityError("Execution evidence byte budget exceeded.")
        after = target.stat()
        validate_path(root, name)
        if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_mode,
        ):
            raise SecurityError("File changed while recording execution evidence.")
        result[name] = digest(
            {"bytes": hashlib.sha256(data).hexdigest(), "mode": stat.S_IMODE(after.st_mode)}
        )
    return result


class ExecutionReceipt:
    """Checkpoint a run without publishing prompts, output, URLs or command arguments."""

    def __init__(self, root: Path, journal: RunJournal, config: TaskToPRConfig) -> None:
        self.root, self.journal, self.config = root, journal, config
        self.base = head(root)
        self.expected_branch = branch(root)
        self.baseline = snapshot(root)
        self.tested: dict[str, str] | None = None
        self.index_digest: str | None = None
        self.control_digest = digest(git(root, "config", "--list", "--null", "--show-origin").hex())
        self.payload: dict[str, Any] = {
            "schema_version": 1,
            "run_id": journal.run_dir.name,
            "started_at": datetime.now(UTC).isoformat(),
            "repository_identity": {
                "kind": "local-root-sha256",
                "sha256": digest(str(root.resolve())),
            },
            "base_sha": self.base,
            "result_head_sha": None,
            "tested_head_sha": None,
            "policy": {"version": POLICY_VERSION, "sha256": digest(config.model_dump(mode="json"))},
            "tool": {
                "name": "tasktopr",
                "version": __version__,
                "source_sha256": digest(
                    {
                        p.relative_to(Path(__file__).parent).as_posix(): hashlib.sha256(
                            p.read_bytes()
                        ).hexdigest()
                        for p in sorted(Path(__file__).parent.rglob("*.py"))
                    }
                ),
            },
            "task_sha256": None,
            "patch_sha256": None,
            "command_list_sha256": None,
            "test_result_sha256": None,
            "changed_file_manifest": [],
            "protected_path_decision": "unknown",
            "tests": {"status": "unknown", "count": 0},
            "ci": "unknown",
            "human_review": "unknown",
            "decision": "UNKNOWN",
            "trust_boundary": "local observation; not signed; hashes are not confidentiality",
        }
        self.checkpoint("started")

    def checkpoint(self, phase: str) -> None:
        self.payload["phase"] = phase
        self.payload["branch_sha256"] = digest(self.expected_branch)
        self.journal.write_json(
            "execution-receipt.json",
            {"payload": self.payload, "receipt_sha256": digest(self.payload)},
        )

    def task(self, issue: Issue, commands: list[list[str]]) -> None:
        self.payload["task_sha256"] = digest(issue.model_dump(mode="json"))
        self.payload["command_list_sha256"] = digest(commands)
        self.checkpoint("planned")

    def require_clean_base(self, base_branch: str) -> None:
        if (
            branch(self.root) != base_branch
            or head(self.root) != self.base
            or changed_paths(self.root)
        ):
            raise SecurityError("Execution requires the clean declared base branch.")
        if snapshot(self.root) != self.baseline:
            raise SecurityError("Repository changed after task intake.")

    def patched(self, patch: PatchRequest, paths: list[str]) -> None:
        self.expected_branch = branch(self.root)
        self.assert_identity(self.base)
        actual = changed_paths(self.root)
        if set(actual) != set(paths):
            raise SecurityError("Actual changed-file manifest differs from the approved patch.")
        protected = any(
            is_protected(p, self.config.scope.protected)
            or (is_dependency_path(p) and not self.config.permissions.allow_dependency_updates)
            for p in actual
        )
        self.payload["protected_path_decision"] = "deny" if protected else "allow"
        if protected:
            raise SecurityError("Protected path in actual changed-file manifest.")
        self.tested = snapshot(self.root)
        self.index_digest = digest(git(self.root, "ls-files", "--stage", "-z").hex())
        self.payload["patch_sha256"] = digest(patch.model_dump(mode="json"))
        self.payload["changed_file_manifest"] = [
            {"path_sha256": digest(p), "before": self.baseline.get(p), "after": self.tested.get(p)}
            for p in actual
        ]
        self.payload["tested_snapshot_sha256"] = digest(self.tested)
        self.checkpoint("patched")

    def assert_identity(self, expected_head: str) -> None:
        if head(self.root) != expected_head or branch(self.root) != self.expected_branch:
            raise SecurityError("Branch or HEAD changed during execution.")
        if (
            digest(git(self.root, "config", "--list", "--null", "--show-origin").hex())
            != self.control_digest
        ):
            raise SecurityError("Git configuration or remote changed during execution.")

    def verified(
        self, tests: list[CommandResult], review: ReviewResult, *, committed_head: str | None = None
    ) -> None:
        self.assert_identity(committed_head or self.base)
        if self.tested is None or snapshot(self.root) != self.tested:
            raise SecurityError("Repository changed during verification; test evidence is stale.")
        if digest(git(self.root, "ls-files", "--stage", "-z").hex()) != self.index_digest:
            raise SecurityError("Index changed during verification; test evidence is stale.")
        if digest([test.command for test in tests]) != self.payload["command_list_sha256"]:
            raise SecurityError("Executed commands differ from planned verification commands.")
        okay = (
            bool(tests)
            and review.approved
            and all(t.return_code == 0 and not t.blocked for t in tests)
        )
        self.payload["test_result_sha256"] = digest([t.model_dump(mode="json") for t in tests])
        self.payload["tests"] = {"status": "pass" if okay else "fail", "count": len(tests)}
        self.payload["decision"] = "REVIEW_REQUIRED" if okay else "FAIL"
        if committed_head:
            if changed_paths(self.root):
                raise SecurityError("Post-commit tests left a dirty worktree.")
            self.payload["tested_head_sha"] = committed_head
        self.checkpoint("verified_head" if committed_head else "verified_worktree")

    def committed(self, revision: str) -> None:
        self.assert_identity(revision)
        if snapshot(self.root) != self.tested or changed_paths(self.root):
            raise SecurityError("Committed content differs from verified working files.")
        if git(self.root, "rev-parse", "HEAD^").decode().strip() != self.base:
            raise SecurityError("Candidate commit is not based on the captured base.")
        self.payload["result_head_sha"] = revision
        self.index_digest = digest(git(self.root, "ls-files", "--stage", "-z").hex())
        self.checkpoint("committed")

    def failed(self) -> None:
        self.payload["decision"] = "FAIL"
        self.checkpoint("failed")
