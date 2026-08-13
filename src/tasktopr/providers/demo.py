"""Deterministic provider for tests and the documented zero-division demo only."""

from __future__ import annotations

import json

from ..config import AgentConfig
from .base import ModelProvider, ProviderError


class DemoProvider(ModelProvider):
    """Return constrained structured outputs for the bundled zero-division demonstration."""

    def complete(self, *, system: str, user: str, config: AgentConfig) -> str:
        del system, config
        lowered = user.casefold()
        if "patch" in lowered:
            return json.dumps(
                {
                    "summary": "Guard zero divisors and add a regression test.",
                    "operations": [
                        {
                            "kind": "replace",
                            "path": "calculator.py",
                            "old_text": "def divide(numerator: float, denominator: float) -> float:\n    return numerator / denominator\n",
                            "new_text": 'def divide(numerator: float, denominator: float) -> float:\n    if denominator == 0:\n        raise ValueError("denominator must not be zero")\n    return numerator / denominator\n',
                            "reason": "Return a clear domain error instead of leaking ZeroDivisionError.",
                        },
                        {
                            "kind": "replace",
                            "path": "test_calculator.py",
                            "old_text": "from calculator import divide\n\n\ndef test_divide_returns_quotient() -> None:\n    assert divide(8, 2) == 4\n",
                            "new_text": 'import pytest\n\nfrom calculator import divide\n\n\ndef test_divide_returns_quotient() -> None:\n    assert divide(8, 2) == 4\n\n\ndef test_divide_rejects_zero_denominator() -> None:\n    with pytest.raises(ValueError, match="denominator must not be zero"):\n        divide(8, 0)\n',
                            "reason": "Cover the reported crash and preserve the happy path.",
                        },
                    ],
                }
            )
        if "plan" in lowered:
            return json.dumps(
                {
                    "summary": "Prevent division-by-zero crashes while preserving valid division.",
                    "root_cause": "divide() performs an unchecked division when divisor is zero.",
                    "steps": [
                        {
                            "path": "calculator.py",
                            "action": "Validate the divisor before division.",
                            "rationale": "The issue is isolated to the public divide function.",
                        },
                        {
                            "path": "test_calculator.py",
                            "action": "Add a regression test for zero divisor behavior.",
                            "rationale": "The crash requires a focused regression test.",
                        },
                    ],
                    "test_plan": ["python -m pytest -q"],
                    "non_goals": ["Do not refactor unrelated arithmetic functions."],
                    "risk": "low",
                }
            )
        raise ProviderError("DemoProvider only accepts bounded plan or patch prompts.")
