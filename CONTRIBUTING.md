# Contributing to TaskToPR

Thank you for improving TaskToPR. The project values small, reviewable changes that increase user control, correctness, transparency, or safety. Before starting substantial work, open or comment on an Issue so the proposed behavior, security impact, and test plan can be discussed.

## Development setup

Use Python 3.11 or later, Git, and a clean clone.

```bash
git clone https://github.com/pangxueyuan2-creator/tasktopr.git
cd tasktopr
python -m pip install -e .
pytest -q
```

The project intentionally does not require a live model API key for tests. Use mocked providers or the explicitly deterministic `DemoProvider`; tests must never call an external model endpoint or create a real GitHub Pull Request.

## Required checks

Run the following before opening a Pull Request:

```bash
pytest -q
ruff check .
ruff format --check .
mypy src/tasktopr
bandit -q -r src
python -m build
```

Core coverage is configured to remain at or above 80%. Include a test for normal behavior and an error/safety path when changing policy, provider parsing, path handling, command execution, Git behavior, or Pull Request handling.

## Design expectations

TaskToPR must preserve its deterministic boundaries. Model output remains data: it should be schema-validated before use and must not become raw shell text. Avoid adding unrestricted execution, hidden state, unaudited side effects, secret persistence, background services, default-branch writes, force push, auto-merge, or bypasses for protected paths.

Each user-visible side effect must produce an event and evidence in the run bundle. New configuration must be safe by default, documented in the README, validated with Pydantic, and displayed without secret values. If a change expands supported languages or test commands, document which commands can run and why they are safe.

## Pull Requests

Use the repository PR template. Explain the user problem, scope, safety impact, test evidence, documentation impact, and any behavior that intentionally does not change. Keep commits focused. Do not commit `.tasktopr/` run artifacts, API keys, local environments, build products, or copied external code without its license.

## Security reports

Do not open public Issues for security-sensitive defects. Follow [SECURITY.md](SECURITY.md) instead.
