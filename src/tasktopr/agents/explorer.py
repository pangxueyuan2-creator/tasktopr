"""Repository Explorer: bounded, local-only repository profiling and context selection."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ..config import TaskToPRConfig
from ..models import Issue, RepositoryProfile
from ..security import is_protected, redact

_IGNORED_PARTS = {
    ".git",
    ".tasktopr",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
}
_MAX_FILE_BYTES = 120_000


class RepositoryError(RuntimeError):
    """Raised when the current working directory is not a usable Git repository."""


def git_root(start: Path) -> Path:
    """Return the current repository root without interpreting shell text."""

    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        shell=False,
    )
    if completed.returncode != 0:
        raise RepositoryError("TaskToPR must run inside a Git repository.")
    return Path(completed.stdout.strip()).resolve()


def default_branch(repo_root: Path) -> str:
    """Resolve a safe base branch, preferring remote HEAD then common defaults."""

    completed = subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "--short"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        shell=False,
    )
    if completed.returncode == 0 and "/" in completed.stdout:
        return completed.stdout.strip().split("/", maxsplit=1)[1]
    for candidate in ("main", "master"):
        exists = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{candidate}"],
            cwd=repo_root,
            check=False,
            timeout=15,
            shell=False,
        )
        if exists.returncode == 0:
            return candidate
    return "main"


def explore(repo_root: Path, issue: Issue, config: TaskToPRConfig) -> RepositoryProfile:
    """Create bounded repository context without sending the entire codebase to a model."""

    files = _collect_files(repo_root)
    languages = _detect_languages(files)
    protected_hits = [path for path in files if is_protected(path, config.scope.protected)]
    relevant = _select_relevant(files, repo_root, issue, config.scope.max_context_files)
    commands = config.testing.commands or _discover_commands(repo_root, languages)
    return RepositoryProfile(
        root=repo_root,
        default_branch=default_branch(repo_root),
        languages=languages,
        test_commands=commands,
        relevant_files=relevant,
        protected_hits=protected_hits,
        file_tree=files[:500],
    )


def compact_context(profile: RepositoryProfile, issue: Issue, config: TaskToPRConfig) -> str:
    """Build a capped text context from selected files, excluding secrets and protected paths."""

    chunks = [
        f"Issue #{issue.number}: {redact(issue.title)}",
        "Repository tree:\n" + "\n".join(profile.file_tree[:120]),
        "Relevant files:",
    ]
    used = sum(len(chunk) for chunk in chunks)
    for relative_path in profile.relevant_files:
        path = profile.root / relative_path
        if is_protected(relative_path, config.scope.protected) or not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")[:_MAX_FILE_BYTES]
        except OSError:
            continue
        chunk = f"\n--- {relative_path} ---\n{redact(content)}\n"
        if used + len(chunk) > config.scope.max_context_bytes:
            break
        chunks.append(chunk)
        used += len(chunk)
    return "\n".join(chunks)


def _collect_files(root: Path) -> list[str]:
    paths: list[str] = []
    for path in root.rglob("*"):
        if any(part in _IGNORED_PARTS for part in path.relative_to(root).parts):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        paths.append(path.relative_to(root).as_posix())
    return sorted(paths)


def _detect_languages(files: list[str]) -> list[str]:
    extensions = {Path(path).suffix for path in files}
    languages: list[str] = []
    if ".py" in extensions or "pyproject.toml" in files:
        languages.append("python")
    if ".ts" in extensions or ".tsx" in extensions or "package.json" in files:
        languages.append("typescript")
    if ".js" in extensions or ".jsx" in extensions:
        languages.append("javascript")
    return languages


def _select_relevant(files: list[str], root: Path, issue: Issue, limit: int) -> list[str]:
    keywords = {
        token.casefold()
        for token in (issue.title + " " + issue.body).replace("/", " ").replace("_", " ").split()
        if len(token) >= 3
    }
    scored: list[tuple[int, str]] = []
    for relative_path in files:
        if is_protected(relative_path):
            continue
        score = sum(keyword in relative_path.casefold() for keyword in keywords) * 4
        if relative_path.startswith("test") or "/test" in relative_path:
            score += 1
        try:
            content = (root / relative_path).read_text(encoding="utf-8", errors="replace")[:20_000]
            score += sum(keyword in content.casefold() for keyword in keywords)
        except OSError:
            continue
        if score:
            scored.append((score, relative_path))
    if not scored:
        scored = [(0, path) for path in files if not is_protected(path)]
    return [path for _score, path in sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]]


def _discover_commands(root: Path, languages: list[str]) -> list[list[str]]:
    commands: list[list[str]] = []
    if "python" in languages:
        if (
            (root / "tests").exists()
            or (root / "pytest.ini").exists()
            or (root / "pyproject.toml").exists()
        ):
            commands.append(["python", "-m", "pytest", "-q"])
        if shutil.which("ruff") and (root / "pyproject.toml").exists():
            commands.append(["ruff", "check", "."])
        if shutil.which("mypy") and (root / "pyproject.toml").exists():
            commands.append(["mypy", "."])
    package_path = root / "package.json"
    if "typescript" in languages or "javascript" in languages:
        try:
            scripts = json.loads(package_path.read_text(encoding="utf-8")).get("scripts", {})
        except (OSError, json.JSONDecodeError):
            scripts = {}
        for name in ("test", "lint", "typecheck", "build"):
            if name in scripts and shutil.which("npm"):
                commands.append(["npm", "run", name])
    return commands
