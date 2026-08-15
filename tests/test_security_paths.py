from __future__ import annotations

from tasktopr.models import RiskLevel
from tasktopr.security import is_protected, path_risk


def test_is_protected_normalizes_windows_separators() -> None:
    assert is_protected(r".github\workflows\ci.yml") is True
    assert is_protected(".github/workflows/ci.yml") is True
    assert is_protected(r".env") is True
    assert is_protected(r"src\normal\file.py") is False


def test_path_risk_blocks_backslash_protected_paths() -> None:
    assert path_risk(r".github\workflows\ci.yml") == RiskLevel.BLOCKED
    assert path_risk(".github/workflows/ci.yml") == RiskLevel.BLOCKED
