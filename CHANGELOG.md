# Changelog

All notable changes are documented here. TaskToPR follows semantic versioning once it reaches a stable `1.0.0` release.

## Unreleased

### Fixed

- `doctor` no longer crashes with `FileNotFoundError` when `git` or `gh` are missing; it reports a clear WARN instead.
- `is_protected` and `path_risk` now normalize backslashes so Windows-style paths (e.g. `.github\workflows\ci.yml`) are classified the same as forward-slash paths.
- Nested `.env*` files and the root `.tasktopr.toml` are protected by default.
- `git status --porcelain -z` review collection records both rename/copy paths.
- Issue text cannot add allowed paths or lift protected-path policy.
- Safe command execution now refuses interpreter inline payloads (`python -c`, `node --eval`) even when the executable is allowlisted.
- `--demo` Issue #1 now falls back to the documented builtin payload when `.tasktopr-demo-issue.json` is absent. A present but invalid file still errors.
- `review` now accepts `--boundary` so standalone review uses the same agent-boundary/v1 path policy as `plan` and `fix`.
- Independent PatchWitness evidence under `.patchwitness/` is ignored by working-tree review collection, and `.patchwitness/**` is protected by default so a model cannot write there as a requested path.
- Review no longer treats Git `core.autocrlf` continuation lines (`The file will have its original line endings in your working directory`) as whitespace failures. Those lines have no `warning:` prefix on Windows.
- `apply_patch` now preflights every operation against the current policy (including exclusive-allow) and rolls back partial writes if a later operation fails.
- `review` includes committed diffs against `main`/`master` (override with `--base`) so a clean working tree after a feature-branch commit cannot hide a protected-path change.
- Path policy now collapses `.` / `..` before matching. A raw `src/../.github/workflows/ci.yml` or `./.github/workflows/ci.yml` can no longer slip past `.github/workflows/**`; paths that leave the repository are deny-wins. Plan and patch models canonicalize the same way so `src/../src/app.py` stays in-scope instead of being rejected as invalid JSON.
- `apply_patch` re-checks policy on the resolved on-disk path and refuses symlink / hard-link writes. A Windows 8.3 name, in-repo symlink, or hard link to `.github/workflows/ci.yml` can no longer be written through an allowed spelling.
- Prefix and regex path matching now fold case on Windows, matching `Path.match` and NTFS. Mixed-case `.GITHUB/WORKFLOWS/ci.yml` stays protected and `SRC/app.py` stays inside exclusive `src/**`. Linux stays case-sensitive.

### Added

- `tasktopr --version` prints the installed package version and exits.
- Regression tests for missing `gh` in `doctor` and for path-separator normalization in protected-path checks.
- Optional `--boundary` loader for GuardSpec `agent-boundary/v1` JSON (denied/protected additive; exclusive-allow replaces the allow list).
- Jailbreak and exclusive-allow policy regressions.

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
