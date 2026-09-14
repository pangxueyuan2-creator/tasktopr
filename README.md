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

### Optional human approval before mutation

TaskToPR can require an explicit local approval after the model plan has passed its normal schema/policy validation but **before any branch is created or file is changed**:

```bash
tasktopr fix 123 --approval prompt
```

Or persist the opt-in policy:

```toml
[approval]
mode = "prompt"
```

`off` is the compatibility default; `prompt` requires an explicit `approve`, `edit`, or `reject` decision for mutating runs. A non-interactive `prompt` run rejects rather than treating missing input as consent. `--dry-run` remains read-only and never needs approval because it cannot cross the mutation boundary.

- `approve` keeps the validated plan unchanged.
- `edit` requires a complete replacement `ChangePlan` JSON and revalidates it through the same schema/path boundaries before mutation.
- `reject` exits before branch creation, patching, tests, commits, pushes, or PR creation.

Approval evidence is written to `plan-approval.json` with a versioned schema, decision, UTC timestamp, and SHA-256 identities for the original/final validated plans. The approval record deliberately does not copy raw prompts, credentials, provider responses, or free-form approval text. Approval is permission to proceed with the bounded local run; it is **not** merge or release authorization.

## Evidence

Each run creates a folder containing:

- `plan.json`
- `plan-approval.json` when approval mode is enabled for a mutating run
- `changes.json`
- `test-results.json` (real subprocess results)
- `summary.md`
- event log

These are ordinary files you can read and keep.

A completed exact-HEAD execution receipt can also be converted into a small, sanitized, versioned handoff for downstream Safe Delivery tooling:

```bash
tasktopr-export-evidence \
  .tasktopr/runs/<run>/execution-receipt.json \
  --tool-revision <reviewer-pinned-tasktopr-commit> \
  --output execution-handoff.json
```

See the [Safe Delivery execution handoff](docs/safe-delivery-handoff.md) for the schema and trust boundary. The handoff is integrity evidence, not a signature, PatchWitness policy, merge approval, or release authorization.

## Safety boundaries (current)

- Paths are resolved inside the Git root; path traversal is blocked
- Protected paths (workflows, lockfiles, secrets, etc.) are excluded by default
- Only a small allowlist of test/build commands is permitted
- Optional `prompt` approval is resolved before branch creation or file mutation and fails closed without an explicit decision
- No unrestricted shell is given to the model
- API keys are never written into logs or evidence files

Read the full [security model](docs/security-model.md) before using it on anything important.

## How it relates to the other two tools

These are separate projects that answer different questions. You can use any combination, or none of them.

- [GuardSpec](https://github.com/pangxueyuan2-creator/guardspec) — **before** work starts: check whether the repository’s explicit agent rules allow the proposed paths/commands
- [PatchWitness](https://github.com/pangxueyuan2-creator/patchwitness) — **after** a change exists: produce a Change Passport that records scope, protected paths, and which checks actually ran

TaskToPR does not depend on either tool. Its Safe Delivery handoff is a versioned producer boundary that PatchWitness can consume without TaskToPR claiming PatchWitness's independently derived subject or reviewer-owned policy.

## Demo

```bash
pip install -e .
./demo/run_demo.sh
```

## Status

Early v0.1. Aimed at small, single-Issue changes. Single maintainer.  
No production claims.

MIT.
