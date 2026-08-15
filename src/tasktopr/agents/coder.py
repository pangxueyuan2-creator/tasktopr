"""Coding Agent: obtain typed patches and apply them through strict path/text gates."""

from __future__ import annotations

from ..config import TaskToPRConfig
from ..models import ChangePlan, PatchRequest, RepositoryProfile
from ..providers import ModelProvider, parse_json_model
from ..security import SecurityError, policy_blocks, safe_path

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

    targets = [step.path for step in plan.steps]
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
        operation.path for operation in patch.operations if operation.path not in targets
    ]
    if extra_paths:
        raise SecurityError(
            f"Patch changes files outside the approved plan: {', '.join(extra_paths)}"
        )
    return patch


def apply_patch(
    patch: PatchRequest, profile: RepositoryProfile, config: TaskToPRConfig
) -> list[str]:
    """Apply a validated patch atomically per operation, refusing ambiguous replacements."""

    changed: list[str] = []
    for operation in patch.operations:
        if policy_blocks(operation.path, config):
            raise SecurityError(f"Refusing to edit protected path: {operation.path}")
        path = safe_path(profile.root, operation.path)
        if operation.kind == "create":
            if path.exists():
                raise SecurityError(
                    f"Refusing to overwrite existing path with create: {operation.path}"
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(operation.new_text, encoding="utf-8")
        else:
            if not path.is_file():
                raise SecurityError(f"Replace target does not exist: {operation.path}")
            original = path.read_text(encoding="utf-8")
            occurrences = original.count(operation.old_text)
            if occurrences != 1:
                raise SecurityError(
                    f"Expected one exact old_text match in {operation.path}; found {occurrences}."
                )
            path.write_text(
                original.replace(operation.old_text, operation.new_text, 1), encoding="utf-8"
            )
        changed.append(operation.path)
    return changed
