# TaskToPR architecture

TaskToPR is a local-first command-line agent that turns one GitHub Issue into a **reviewable**, tested Pull Request. It runs inside the current Git repository, keeps an evidence bundle for every run, and gives the model only a narrow patch-production capability rather than unrestricted shell access.

> **Design rule:** the model may propose a typed plan and a typed patch; the deterministic runtime owns paths, subprocesses, Git state, tests, review gates, commits, and Pull Request creation.

## Agent pipeline

```text
Issue Intake → Repository Explorer → Planner → Coding Agent
                                              ↓
                                      Test Agent → Review Agent → PR Agent
```

Each component has one typed input and output boundary and produces runtime events. The orchestrator is a finite, explicit phase sequence—not an open-ended agent loop. A failed gate ends the run with an evidence bundle and does not create a PR.

| Component | Responsibility | Model authority | Deterministic safety boundary |
| --- | --- | --- | --- |
| Issue Intake | Retrieves title, body, labels, URL, goal and acceptance signals. | None. | Uses `gh issue view` or the documented local demo issue source. |
| Repository Explorer | Identifies repository root, language, test commands, protected paths and bounded relevant files. | None. | Excludes private/config paths and caps context bytes/files. |
| Planner | Produces a structured change plan, target files, test plan, non-goals and risk level. | JSON only. | Schema validation and protected-path policy run after parsing. |
| Coding Agent | Requests a JSON patch composed of exact textual replacements or safe file creation. | JSON patch only. | Canonical path checks, protected-path checks, size caps and exact-old-text matching. |
| Test Agent | Discovers then runs allowlisted commands. | None. | No shell, fixed working directory, timeout, denylist and output redaction. |
| Review Agent | Evaluates changed files, scope, diff whitespace, policy hits, test result and review notes. | Optional future extension. | Independent deterministic gate; a block prevents commit and PR. |
| PR Agent | Creates branch, commit, push and `gh pr create` only after approval. | None. | Never pushes default branch, never force-pushes, never creates PR in dry-run/no-PR mode. |

## Evidence-first runs

A run creates `.tasktopr/runs/<UTC-timestamp>-<random>/` under the repository root:

| Artifact | Content |
| --- | --- |
| `events.jsonl` | Append-only structured events with phase, timestamp, severity and redacted message. |
| `plan.json` | Validated issue and plan. |
| `changes.json` | Requested and applied patch metadata; content is redacted in logs. |
| `test-results.json` | Actual invoked commands, exit codes, elapsed time and redacted output. |
| `summary.md` | Human-readable result, scope, risk, tests and next action. |

Terminal progress is rendered from these same events. No terminal transcript or demo result is fabricated.

## Security model

TaskToPR is designed for the currently checked-out repository only. Every path is resolved relative to the Git root and rejected when it escapes that root or traverses a symlink outside it. Reads and context selection exclude `.git`, `.tasktopr`, `.env*`, SSH locations, credential-like files, virtual environments, dependencies and build directories.

The default policy blocks changing workflow files, dependency/deployment manifests, authentication paths, secret-like paths and `.git` internals. Such paths raise risk to `high` and block automatic patching. Configuration can add protected globs but cannot enable `.git`, credential paths, direct default-branch push, `rm -rf`, force push or shell execution.

The command runner uses `subprocess.run` with argument arrays and `shell=False`. It only accepts known read/test/build commands. It rejects destructive and network/admin patterns such as `rm -rf`, `git push --force`, `sudo`, `curl`, `wget`, `chmod`, `chown` and shell metacharacters. Output, issue text, plans and logs are redacted for common GitHub, OpenAI, Anthropic, AWS and private-key patterns.

## Provider layer

`ModelProvider` returns plain text from a bounded prompt. `OpenAICompatibleProvider` uses a chat-completions-compatible endpoint. `OpenAIProvider` specializes its default endpoint and environment variable. `AnthropicProvider` uses the Messages endpoint. A test/demo-only `DemoProvider` returns deterministic plan and patch JSON for the included zero-division fixture. Provider output is parsed as JSON and validated with Pydantic before any file modification.

Provider credentials are read only from the process environment and are never written to a run artifact. API request error messages are redacted before presentation.

## Repository support

Version 0.1.0 detects Python and Node/TypeScript repositories. Python test candidates include `python -m pytest`, `ruff check .`, `mypy .` and package build when the corresponding configuration/tool exists. Node candidates include package scripts such as `test`, `lint`, `typecheck` and `build`; commands are executed only when their executable is installed and allowed. The command policy and language adapters are deliberately isolated so Go, Rust and Java adapters can be added later.

## PR integration

GitHub CLI is used only after a clean review result. GitHub documents that `gh pr create` can create a PR non-interactively with a supplied title/body and link it to an Issue using `Fixes #<number>`.[1] TaskToPR includes that reference in its PR body but does not merge, approve or auto-close issues itself.

## References

[1] [GitHub CLI manual, “gh pr create.”](https://cli.github.com/manual/gh_pr_create)

[2] [GitHub Docs, “About GitHub Copilot cloud agent.”](https://docs.github.com/copilot/concepts/agents/cloud-agent/about-cloud-agent)

[3] [Anthropic API reference, “Messages.”](https://platform.claude.com/docs/en/api/messages)

[4] [OpenAI API reference, “Chat Completions Overview.”](https://developers.openai.com/api/reference/chat-completions/overview/)
