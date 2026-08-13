"""Test Agent: execute discovered/configured quality commands through the command gate."""

from __future__ import annotations

from ..config import TaskToPRConfig
from ..models import CommandResult, RepositoryProfile
from ..security import run_safe_command


def run_quality_checks(profile: RepositoryProfile, config: TaskToPRConfig) -> list[CommandResult]:
    """Execute each allowed quality command and retain evidence, including failures."""

    if not profile.test_commands:
        return [
            CommandResult(
                command=[],
                return_code=0,
                elapsed_seconds=0.0,
                blocked=True,
                reason="No recognized test command was found; review will not approve a PR.",
            )
        ]
    return [
        run_safe_command(command, profile.root, config.testing.timeout_seconds)
        for command in profile.test_commands
    ]
