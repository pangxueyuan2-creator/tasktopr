from __future__ import annotations

from tasktopr.models import RiskLevel
from tasktopr.security import is_dependency_path, is_protected, path_risk


def test_is_protected_normalizes_windows_separators() -> None:
    assert is_protected(r".github\workflows\ci.yml") is True
    assert is_protected(".github/workflows/ci.yml") is True
    assert is_protected(r".env") is True
    assert is_protected(r"src\normal\file.py") is False


def test_path_risk_blocks_backslash_protected_paths() -> None:
    assert path_risk(r".github\workflows\ci.yml") == RiskLevel.BLOCKED
    assert path_risk(".github/workflows/ci.yml") == RiskLevel.BLOCKED


def test_common_dependency_manifests_are_classified_by_basename() -> None:
    manifests = (
        "requirements-dev.txt",
        "requirements.in",
        "constraints.txt",
        "setup.py",
        "setup.cfg",
        "environment.yml",
        "environment.yaml",
        "conda-lock.yml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "libs.versions.toml",
        "mix.exs",
        "deno.json",
        "deno.lock",
        "flake.nix",
        "flake.lock",
        "packages.lock.json",
        "project.assets.json",
    )
    for manifest in manifests:
        assert is_dependency_path(manifest) is True
        assert is_dependency_path(f"services/worker/{manifest}") is True
        assert is_dependency_path(f"services\\worker\\{manifest}") is True


def test_non_manifest_files_are_not_classified_as_dependencies() -> None:
    assert is_dependency_path("src/setup.py.md") is False
    assert is_dependency_path("docs/requirements-dev.txt.example") is False
