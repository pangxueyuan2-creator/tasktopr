"""Planner Agent: request a bounded, schema-validated change plan."""

from __future__ import annotations

from ..config import TaskToPRConfig
from ..models import ChangePlan, Issue, RepositoryProfile, RiskLevel
from ..providers import ModelProvider, parse_json_model
from ..security import is_protected
from .explorer import compact_context

_PLAN_SYSTEM = """You are TaskToPR's planning component. Produce only a JSON object matching this schema:
{
  "summary": "string",
  "root_cause": "string",
  "steps": [{"path": "relative/file", "action": "string", "rationale": "string"}],
  "test_plan": ["command description"],
  "non_goals": ["string"],
  "risk": "low|medium|high|blocked"
}
Plan the smallest change that resolves the Issue. Do not propose changes to protected files,
workflow files, secrets, dependency locks, authentication, deployment, .git, or files outside the repository.
Do not return Markdown or explanatory prose around JSON."""


def create_plan(
    provider: ModelProvider,
    issue: Issue,
    profile: RepositoryProfile,
    config: TaskToPRConfig,
) -> ChangePlan:
    """Ask the provider for a plan then enforce repository path policy deterministically."""

    context = compact_context(profile, issue, config)
    user = (
        "Create a change plan for this GitHub Issue.\n\n"
        f"Issue title: {issue.title}\nIssue body:\n{issue.body}\n\n"
        f"Acceptance signals: {issue.acceptance_criteria}\n"
        f"Allowed test commands: {profile.test_commands}\n\n{context}"
    )
    plan = parse_json_model(
        provider.complete(system=_PLAN_SYSTEM, user=user, config=config.agent), ChangePlan
    )
    protected = [
        step.path for step in plan.steps if is_protected(step.path, config.scope.protected)
    ]
    if protected:
        raise ValueError(f"Plan targets protected paths: {', '.join(protected)}")
    if plan.risk in {RiskLevel.BLOCKED, RiskLevel.HIGH}:
        raise ValueError(
            f"Plan was not approved for automatic execution because risk is {plan.risk}."
        )
    return plan
