# Changelog

All notable changes are documented here. TaskToPR follows semantic versioning once it reaches a stable `1.0.0` release.

## Unreleased

### Fixed

- `doctor` no longer crashes with `FileNotFoundError` when `git` or `gh` are missing; it reports a clear WARN instead.
- `is_protected` and `path_risk` now normalize backslashes so Windows-style paths (e.g. `.github\workflows\ci.yml`) are classified the same as forward-slash paths.
- `apply_patch` now preflights every operation in memory and writes all-or-nothing, rolling back any files already written if a later write fails.

### Added

- Regression tests for missing `gh` in `doctor` and for path-separator normalization in protected-path checks.
- Transactional apply regressions: second-operation failure, create-then-fail, mid-write OSError rollback, and sequential same-file edits.
- Optional fail-closed GuardSpec handoff via `GUARDSPEC_CHECK_JSON` or `.guardspec-check.json`. Absence of the default file keeps TaskToPR independent.

## [0.1.0] - 2026-08-13

### Added

- Local-first `plan`, `fix`, `review`, `status`, `doctor`, and `config` CLI commands.
- Modular Issue Intake, Repository Explorer, Planner, Coding, Test, Review, and PR components.
- Typed plan/patch JSON contracts and a provider-neutral `ModelProvider` protocol.
- OpenAI, Anthropic, and OpenAI-compatible HTTP providers with bounded retry/timeout behavior.
- A deterministic, explicitly demo-only provider and zero-division fixture that run real local checks.
- Per-run evidence bundles with redacted JSONL events, plan, patch metadata, test results, and summaries.
- Repository-root path isolation, protected paths, common secret redaction, safe command execution, exact patch replacement, review gates, and default-branch/force-push prevention.
- Python and Node/TypeScript quality-command discovery.
- Tests, coverage gate, linting, formatting, strict type checking, Bandit, packaging, CI, CodeQL, Dependabot, and community/security documentation.

### Not included

- Hosted service, queue, database, scheduler, GitHub App, auto-merge, unrestricted shell, remote sandbox, multi-repository tasks, vector search, or Go/Rust/Java adapters.
