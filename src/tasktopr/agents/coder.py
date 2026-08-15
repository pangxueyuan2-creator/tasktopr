"""Coding Agent: obtain typed patches and apply them through strict path/text gates."""

from __future__ import annotations

from pathlib import Path

from ..config import TaskToPRConfig
from ..models import ChangePlan, PatchRequest, RepositoryProfile
from ..providers import ModelProvider, parse_json_model
from ..security import (
    SecurityError,
    policy_blocks,
    refuse_aliased_write,
    resolved_repo_relpath,
    safe_path,
)

_PATCH_SYSTEM = """You are TaskToPR's coding component. Produce only a JSON object matching this schema:
{
  "summary": "string",
  "operations": [
    {"kind": "replace|create", "path": "relative/file", "old_text": "exact text", "new_text": "text", "reason": "string"}
  ]
}
Use the smallest safe patch. A replace operation must provide an exact, unique old_text. Do not modify
protected files, workflows, dependencies, authentication, deployment configuration, .git, secrets, or files outside the repository.
Do not return Markdown or prose around JSON."""


def request_patch(
    provider: ModelProvider,
    plan: ChangePlan,
    profile: RepositoryProfile,
    config: TaskToPRConfig,
) -> PatchRequest:
    """Request a patch restricted to the plan's target files."""

    targets = [resolved_repo_relpath(profile.root, step.path) for step in plan.steps]
    sources: list[str] = []
    for path in targets:
        if policy_blocks(path, config):
            raise SecurityError(f"Plan target is protected: {path}")
        target = safe_path(profile.root, path)
        if target.exists():
            sources.append(
                f"--- {path} ---\n{target.read_text(encoding='utf-8', errors='replace')[:60_000]}"
            )
    user = (
        "Produce the approved patch as JSON.\n\n"
        f"Approved plan:\n{plan.model_dump_json(indent=2)}\n\n"
        f"Only target these plan files: {targets}\n\nExisting source:\n" + "\n".join(sources)
    )
    patch = parse_json_model(
        provider.complete(system=_PATCH_SYSTEM, user=user, config=config.agent), PatchRequest
    )
    extra_paths = [
        resolved_repo_relpath(profile.root, operation.path)
        for operation in patch.operations
        if resolved_repo_relpath(profile.root, operation.path) not in targets
    ]
    if extra_paths:
        raise SecurityError(
            f"Patch changes files outside the approved plan: {', '.join(extra_paths)}"
        )
    return patch


def apply_patch(
    patch: PatchRequest, profile: RepositoryProfile, config: TaskToPRConfig
) -> list[str]:
    """Preflight every operation, then apply all writes or roll them back."""

    staged: dict[Path, str] = {}
    originals: dict[Path, str | None] = {}
    order: list[Path] = []

    def current_text(path: Path) -> str | None:
        if path in staged:
            return staged[path]
        if path not in originals:
            if path.is_file():
                originals[path] = path.read_text(encoding="utf-8")
            elif path.exists():
                raise SecurityError(f"Refusing to edit non-regular path: {path}")
            else:
                originals[path] = None
        return originals[path]

    for operation in patch.operations:
        rel = resolved_repo_relpath(profile.root, operation.path)
        if policy_blocks(rel, config):
            raise SecurityError(f"Refusing to edit protected path: {operation.path}")
        path = safe_path(profile.root, rel)
        refuse_aliased_write(path, operation.path)
        if path not in order:
            order.append(path)
        if operation.kind == "create":
            if current_text(path) is not None:
                raise SecurityError(
                    f"Refusing to overwrite existing path with create: {operation.path}"
                )
            staged[path] = operation.new_text
            continue
        original = current_text(path)
        if original is None:
            raise SecurityError(f"Replace target does not exist: {operation.path}")
        occurrences = original.count(operation.old_text)
        if occurrences != 1:
            raise SecurityError(
                f"Expected one exact old_text match in {operation.path}; found {occurrences}."
            )
        staged[path] = original.replace(operation.old_text, operation.new_text, 1)

    attempted: list[Path] = []
    created_dirs: set[Path] = set()
    root = profile.root.resolve()
    try:
        for path in order:
            if originals[path] is None:
                parent = path.parent
                while parent != root and not parent.exists():
                    created_dirs.add(parent)
                    parent = parent.parent
                path.parent.mkdir(parents=True, exist_ok=True)
            attempted.append(path)
            path.write_text(staged[path], encoding="utf-8")
    except Exception:
        rollback_errors: list[str] = []
        for path in reversed(attempted):
            try:
                prior = originals[path]
                if prior is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(prior, encoding="utf-8")
            except OSError as exc:
                rollback_errors.append(f"{path}: {exc}")
        for directory in sorted(created_dirs, key=lambda item: len(item.parts), reverse=True):
            try:
                directory.rmdir()
            except FileNotFoundError:
                pass
            except OSError as exc:
                rollback_errors.append(f"{directory}: {exc}")
        if rollback_errors:
            raise SecurityError(
                "Patch apply failed and rollback was incomplete: " + "; ".join(rollback_errors)
            ) from None
        raise

    return [operation.path for operation in patch.operations]
