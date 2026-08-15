# TaskToPR

Takes one GitHub Issue, makes a small change on an isolated branch, runs the real tests, and can open a PR. Every run leaves an evidence folder under `.tasktopr/runs/`.

It never force-pushes, never merges, never gives the model unrestricted shell, and never writes API keys into logs.

## Install

Python 3.11+, Git, and (for real Issues/PRs) the [GitHub CLI](https://cli.github.com/).

```bash
pip install tasktopr
# or from source
pip install -e .

tasktopr doctor
```

## Quick start

```bash
export OPENAI_API_KEY=...   # or ANTHROPIC_API_KEY

tasktopr plan 123                    # read-only plan
tasktopr fix 123 --dry-run           # plan only
tasktopr fix 123 --no-pr             # local branch + tests, no PR
tasktopr fix 123                     # full run, can open a PR
```

## Evidence

Each run creates a folder with `plan.json`, `changes.json`, `test-results.json`, `summary.md`, and an event log. Look at that before merging anything.

## Safety notes

- Paths are resolved inside the Git root; traversal and protected paths are blocked
- Only a small set of test/build commands is allowed
- Protected areas (workflows, locks, secrets, etc.) are excluded by default
- Read the full [security model](docs/security-model.md) before using it on anything important

## Related tools

These are separate projects that answer different questions:

- [GuardSpec](https://github.com/pangxueyuan2-creator/guardspec) — before the work starts, check whether the repository’s explicit agent rules allow the proposed paths/commands
- [PatchWitness](https://github.com/pangxueyuan2-creator/patchwitness) — after a change exists, produce a Change Passport that records scope, protected paths, and which checks actually ran

TaskToPR does not depend on either of them. You can use any combination, or none of them.

## Demo

```bash
pip install -e .
./demo/run_demo.sh
```

## Status

Early v0.1. Aimed at small, single-Issue changes. Single maintainer.

MIT.
