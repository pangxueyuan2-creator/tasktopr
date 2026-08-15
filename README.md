# TaskToPR

Takes one GitHub Issue, makes a small change on an isolated branch, runs the real tests, and can open a PR. Every run leaves an evidence folder under `.tasktopr/runs/`.

It never force-pushes, never merges, never gives the model unrestricted shell, and never writes API keys into logs.

## Install

Requires Python 3.11+, Git. For real Issues and PRs you also need the [GitHub CLI](https://cli.github.com/) (`gh`) authenticated.

```bash
pip install tasktopr
# or from this repository
pip install -e .

tasktopr doctor
```

`doctor` checks the local environment and tells you what is missing.

## Quick start

```bash
export OPENAI_API_KEY=...          # or ANTHROPIC_API_KEY

tasktopr plan 123                  # read-only plan, no changes
tasktopr fix 123 --dry-run         # plan only
tasktopr fix 123 --no-pr           # local branch + real tests, no PR
tasktopr fix 123                   # full run (can open a PR)
```

Always inspect the evidence folder under `.tasktopr/runs/` before merging anything.

## Evidence

Each run creates a folder containing:

- `plan.json`
- `changes.json`
- `test-results.json` (real subprocess results)
- `summary.md`
- event log

These are ordinary files you can read and keep.

## Safety boundaries (current)

- Paths are resolved inside the Git root; path traversal is blocked
- Protected paths (workflows, lockfiles, secrets, etc.) are excluded by default
- Only a small allowlist of test/build commands is permitted
- No unrestricted shell is given to the model
- API keys are never written into logs or evidence files

Read the full [security model](docs/security-model.md) before using it on anything important.

## How it relates to the other two tools

These are separate projects that answer different questions. You can use any combination, or none of them.

- [GuardSpec](https://github.com/pangxueyuan2-creator/guardspec) — **before** work starts: check whether the repository’s explicit agent rules allow the proposed paths/commands
- [PatchWitness](https://github.com/pangxueyuan2-creator/patchwitness) — **after** a change exists: produce a Change Passport that records scope, protected paths, and which checks actually ran

TaskToPR does not depend on either tool. If `GUARDSPEC_CHECK_JSON` or `.guardspec-check.json` is present, apply refuses anything other than `decision: allow` and records `policy_digest` in the run evidence. Absence of that file keeps TaskToPR independent.

## Demo

```bash
pip install -e .
./demo/run_demo.sh
```

## Status

Early v0.1. Aimed at small, single-Issue changes. Single maintainer.  
No production claims.

MIT.
