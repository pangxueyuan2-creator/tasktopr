# TaskToPR

**Turn a GitHub Issue into a transparent, tested Pull Request.**

TaskToPR is a local CLI that reads one Issue, plans a small change, applies it on an isolated branch, runs real tests, and can open a PR. Every run leaves an evidence bundle under `.tasktopr/runs/`.

It never force-pushes, never merges, never gives the model unrestricted shell access, and never writes API keys into logs.

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
tasktopr fix 123 --dry-run           # plan only, no changes
tasktopr fix 123 --no-pr             # local branch + tests, no PR
tasktopr fix 123                     # full run, can open a PR
```

## Evidence

Each run creates a folder with `plan.json`, `changes.json`, `test-results.json`, `summary.md`, and an event log. Review that before merging anything.

## Safety notes

- Paths are resolved inside the Git root; traversal and protected paths are blocked
- Only a small set of test/build commands is allowed
- Protected areas (workflows, locks, secrets, etc.) are excluded by default
- Review the full [security model](docs/security-model.md) before using on important repos

## Demo

```bash
pip install -e .
./demo/run_demo.sh
```

## Status

Early v0.1. Aimed at small, single-Issue changes. Single maintainer.

MIT License.
