# TaskToPR

> **Turn a GitHub Issue into a transparent, tested Pull Request.**

TaskToPR is a local-first, provider-neutral software engineering Agent for small, reviewable GitHub Issues. It reads one Issue, selects bounded repository context, produces a structured plan, creates an isolated branch, applies a schema-validated patch, runs real local checks, performs an independent deterministic review, and only then can create a Pull Request.

> **Transparency over magic.** Every run writes a local evidence bundle. TaskToPR never uses unrestricted model shell access, force-pushes, merges Pull Requests, or silently edits your default branch.

| What TaskToPR does | What it deliberately does not do |
| --- | --- |
| Works in the current local Git repository. | Run a hosted service, worker queue, vector database, or background daemon. |
| Supports OpenAI, Anthropic, and OpenAI-compatible endpoints. | Persist API keys or place credentials in run logs. |
| Uses typed plans and exact-text patches. | Give an LLM unrestricted terminal or filesystem authority. |
| Creates a feature branch, tests, reviews, commits, pushes, and can open a PR. | Force-push, auto-merge, push `main`/`master`, or modify `.git`. |
| Produces evidence under `.tasktopr/runs/`. | Claim a patch or test result that was not actually produced locally. |

## Installation

TaskToPR requires **Python 3.11+**, Git, and optionally the [GitHub CLI](https://cli.github.com/) for real Issue retrieval and Pull Request creation.

```bash
python -m pip install tasktopr
# or install a development checkout
python -m pip install -e .
```

Confirm the command is installed and the local environment is usable:

```bash
tasktopr --help
tasktopr doctor
```

GitHub CLI stores authentication in its configured credential store and documents `repo`, `read:org`, and `gist` as the minimum classic-token scopes for its general login flow.[1] TaskToPR delegates Issue/PR operations to your local `gh` installation; it does not read or store your GitHub token.

## Quick start

First, inspect an Issue without changing the working tree:

```bash
export OPENAI_API_KEY="..."
tasktopr plan 123 --provider openai --model gpt-4.1-mini
```

Next, generate a complete evidence-backed plan without creating a branch, file edit, test run, commit, or PR:

```bash
tasktopr fix 123 --dry-run
```

To apply, test, and review changes only on a new **local** branch, use `--no-pr`:

```bash
tasktopr fix 123 --no-pr
```

To create a real Pull Request, run the default command from a clean, writable clone with `origin` configured:

```bash
tasktopr fix 123
```

The PR body cites `Fixes #123`, which GitHub CLI documents as a way to link the Issue and close it when the PR is merged.[2] TaskToPR never merges the PR itself.

## Commands

| Command | Effect |
| --- | --- |
| `tasktopr plan <issue>` | Read-only Issue intake, repository exploration, structured planning, and evidence bundle. |
| `tasktopr fix <issue> --dry-run` | Read-only plan-only run. It creates no branch, edits, tests, commit, push, or PR. |
| `tasktopr fix <issue> --no-pr` | Create an isolated local branch, apply safe patch operations, test, and review; no commit/push/PR. |
| `tasktopr fix <issue>` | Same safety gates, then commit the reviewed scope, normal-push the feature branch, and create a PR. |
| `tasktopr review` | Inspect current working-tree scope and safety against the deterministic review gate. |
| `tasktopr status` | Render the most recent local evidence summary. |
| `tasktopr doctor` | Check Git, working tree, GitHub CLI auth, Python, Node, and provider configuration. |
| `tasktopr config` | Validate and display effective configuration without secret values. |

## Model providers

TaskToPR uses environment variables only. It never writes provider credentials to `.tasktopr`, Git, console evidence, or Pull Request text.

| Provider | Required environment | Example |
| --- | --- | --- |
| OpenAI | `OPENAI_API_KEY` | `tasktopr plan 123 --provider openai --model gpt-4.1-mini` |
| Anthropic | `ANTHROPIC_API_KEY` | `tasktopr plan 123 --provider anthropic --model claude-sonnet-4-20250514` |
| OpenAI-compatible | `OPENAI_COMPATIBLE_API_KEY`, `OPENAI_COMPATIBLE_BASE_URL` | `tasktopr plan 123 --provider openai-compatible --model your-model` |
| Demo-only | No network key | `tasktopr fix 1 --demo --no-pr` inside the included demo repository. |

The OpenAI chat-completions endpoint accepts a list of conversation messages.[3] Anthropic’s Messages API accepts a structured list of messages and a top-level system prompt.[4] TaskToPR adapts both behind one small `ModelProvider` protocol. Network failures are bounded by configured timeout and retry settings; invalid JSON is rejected before it can propose a file write.

## Configuration

Place an optional `.tasktopr.toml` in the repository root. Safe defaults apply when no file exists.

```toml
[agent]
provider = "openai"
model = "gpt-4.1-mini"
max_iterations = 3
temperature = 0.0
max_tokens = 2000
timeout_seconds = 60
retries = 1

[testing]
# Explicit commands override language-aware discovery.
commands = [["python", "-m", "pytest", "-q"], ["ruff", "check", "."]]
timeout_seconds = 120

[scope]
protected = ["infra/**"]
max_context_files = 12
max_context_bytes = 80000
```

The first release recognizes Python and Node/TypeScript repository signals. It can discover Python `pytest`, `ruff`, and `mypy` commands plus `npm run test`, `lint`, `typecheck`, and `build` scripts where available. Any discovered command still passes through the command policy before execution.

## Evidence bundle

Every invocation creates `.tasktopr/runs/<timestamp>-<id>/` in the target repository.

| Artifact | Purpose |
| --- | --- |
| `events.jsonl` | Append-only, redacted phase events powering the live terminal display. |
| `plan.json` | Validated Issue, bounded repository profile, and structured plan. |
| `changes.json` | Requested patch metadata and applied file list. |
| `test-results.json` | Actual commands, exit codes, elapsed time, and redacted output. |
| `summary.md` | Human-readable issue, files, review findings, and outcome. |
| `pull-request.md` | PR body when a PR is actually created. |

Run artifacts are intentionally local and are ignored by Git. They let a reviewer inspect what the Agent was asked to do, what it changed, what it ran, and why a run was blocked.

## Safety model

TaskToPR is built for **reviewable automation**, not unrestricted autonomy. It resolves every model-provided path inside the current Git root and rejects traversal or symbolic-link escapes. It excludes `.git`, `.env*`, common credential/key files, dependency/build directories, and protected paths from model context. It also redacts common GitHub, OpenAI, Anthropic, AWS, and private-key patterns from terminal and evidence output.

The default policy blocks automatic changes to `.github/workflows/**`, dependency locks, Docker/deployment files, auth/credential/secret paths, and Git internals. It runs subprocesses with argument arrays and `shell=False`, permits only a small test/build command surface, and denies destructive/network/admin patterns such as `rm`, `sudo`, `curl`, `wget`, and force operations. A reviewer gate blocks commit and PR creation if scope differs from the validated patch, a protected file appears, a required command fails, or `git diff --check` finds whitespace errors.

Read the complete [security model](docs/security-model.md) before enabling PR creation on an unfamiliar repository. To report a vulnerability, see [SECURITY.md](SECURITY.md).

## Repeatable 60-second demo

The repository contains a deterministic demo that fixes a genuine zero-division bug in a temporary Git repo. The Demo provider is explicitly limited to this fixture; it produces typed plan/patch JSON, after which TaskToPR performs real local file edits, tests, lint, type checks, review, and evidence logging.

```bash
python -m pip install -e .
./demo/run_demo.sh
```

Expected terminal phases are generated by the live run: `ANALYZING_ISSUE`, `SCANNING_REPOSITORY`, `CREATING_PLAN`, `CREATING_BRANCH`, `EDITING_FILE`, `RUNNING_TEST`, `REVIEWING_PATCH`, and `COMPLETED`. The script then displays `git diff` and re-runs the standard-library `unittest` suite; it does not print a fabricated transcript.

## Limits and non-goals in v0.1.0

TaskToPR is intentionally scoped to small, single-Issue changes. It does not promise a correct solution from any model and cannot infer product intent missing from an Issue. It does not operate as a GitHub App, scheduled worker, auto-merge bot, remote sandbox, multi-repository executor, or vector-search platform. It does not currently build language adapters for Go, Rust, or Java, although the command and explorer boundaries are designed for them.

TaskToPR should be used only on repositories you understand and have permission to change. Review every diff and every Pull Request before merging.

## Development

```bash
python -m pip install -e .
pytest -q
ruff check .
ruff format --check .
mypy src/tasktopr
bandit -q -r src
python -m build
```

Please read [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), and the [roadmap](ROADMAP.md) before opening a contribution.

## License

TaskToPR is released under the [MIT License](LICENSE).

## References

[1] [GitHub CLI manual, “gh auth login.”](https://cli.github.com/manual/gh_auth_login)

[2] [GitHub CLI manual, “gh pr create.”](https://cli.github.com/manual/gh_pr_create)

[3] [OpenAI API reference, “Chat Completions Overview.”](https://developers.openai.com/api/reference/chat-completions/overview/)

[4] [Anthropic API reference, “Messages.”](https://platform.claude.com/docs/en/api/messages)
