"""Typed contracts shared by TaskToPR agents, providers and evidence writers."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKED = "blocked"


class RunPhase(StrEnum):
    ANALYZING_ISSUE = "ANALYZING_ISSUE"
    SCANNING_REPOSITORY = "SCANNING_REPOSITORY"
    CREATING_PLAN = "CREATING_PLAN"
    CREATING_BRANCH = "CREATING_BRANCH"
    EDITING_FILE = "EDITING_FILE"
    RUNNING_TEST = "RUNNING_TEST"
    REVIEWING_PATCH = "REVIEWING_PATCH"
    CREATING_PR = "CREATING_PR"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Issue(BaseModel):
    number: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=500)
    body: str = ""
    url: str = ""
    labels: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


class RepositoryProfile(BaseModel):
    root: Path
    default_branch: str
    languages: list[str] = Field(default_factory=list)
    test_commands: list[list[str]] = Field(default_factory=list)
    relevant_files: list[str] = Field(default_factory=list)
    protected_hits: list[str] = Field(default_factory=list)
    file_tree: list[str] = Field(default_factory=list)


class PlanStep(BaseModel):
    path: str
    action: str = Field(min_length=1, max_length=1_000)
    rationale: str = Field(min_length=1, max_length=1_000)

    @field_validator("path")
    @classmethod
    def forbid_absolute_path(cls, value: str) -> str:
        if Path(value).is_absolute() or ".." in Path(value).parts:
            raise ValueError("Plan paths must be repository-relative and cannot traverse upward.")
        return value


class ChangePlan(BaseModel):
    summary: str = Field(min_length=1, max_length=2_000)
    root_cause: str = Field(min_length=1, max_length=2_000)
    steps: list[PlanStep] = Field(min_length=1, max_length=20)
    test_plan: list[str] = Field(min_length=1, max_length=10)
    non_goals: list[str] = Field(default_factory=list)
    risk: RiskLevel = RiskLevel.LOW


class PatchOperation(BaseModel):
    kind: str = Field(pattern="^(replace|create)$")
    path: str
    old_text: str = ""
    new_text: str
    reason: str = Field(min_length=1, max_length=1_000)

    @field_validator("path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        if Path(value).is_absolute() or ".." in Path(value).parts:
            raise ValueError("Patch path must be repository-relative and cannot traverse upward.")
        return value


class PatchRequest(BaseModel):
    summary: str = Field(min_length=1, max_length=2_000)
    operations: list[PatchOperation] = Field(min_length=1, max_length=10)


class CommandResult(BaseModel):
    command: list[str]
    return_code: int
    elapsed_seconds: float
    stdout: str = ""
    stderr: str = ""
    blocked: bool = False
    reason: str = ""


class ReviewResult(BaseModel):
    approved: bool
    risk: RiskLevel
    findings: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    scope_ok: bool = False
    tests_ok: bool = False


class RunResult(BaseModel):
    run_dir: Path
    issue: Issue
    plan: ChangePlan | None = None
    patch: PatchRequest | None = None
    tests: list[CommandResult] = Field(default_factory=list)
    review: ReviewResult | None = None
    branch: str | None = None
    pr_url: str | None = None
    success: bool = False
    message: str = ""
