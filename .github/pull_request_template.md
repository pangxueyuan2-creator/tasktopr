## Problem and scope

Describe the user problem, the Issue being addressed, and what is deliberately out of scope.

## Changes

Explain the implementation and affected files.

## Safety impact

State whether this changes path policy, command policy, provider behavior, credential handling, Git/PR behavior, or evidence logging. If it does, explain the new default and abuse cases considered.

## Validation

- [ ] `pytest -q`
- [ ] `ruff check .`
- [ ] `ruff format --check .`
- [ ] `mypy src/tasktopr`
- [ ] `bandit -q -r src`
- [ ] Documentation updated when user-visible behavior changed.

## Checklist

- [ ] No secrets, run artifacts, generated environments, or unrelated formatting changes are included.
- [ ] Model output remains schema-validated and cannot acquire unrestricted shell/filesystem authority.
- [ ] The change does not weaken default-branch, force-push, protected-path, or review gates.
