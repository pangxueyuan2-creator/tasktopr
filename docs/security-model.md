# Security model

TaskToPR treats model output as **untrusted data**. The model can propose a typed plan and a typed patch, but it cannot directly run a shell command, resolve arbitrary paths, write an arbitrary file, manipulate Git internals, push a default branch, or create a Pull Request until deterministic gates permit it.

## Trust boundaries

| Boundary | Input | Control | Residual responsibility |
| --- | --- | --- | --- |
| GitHub Issue | User-authored Issue body, title, labels. | Read through local `gh`; redact before evidence persistence. | Issue text can still describe a malicious or ambiguous task; the user must review the plan. |
| Repository | Current Git working tree. | Repository root resolution, path canonicalization, file/context caps, protected-path policy. | A trusted local checkout and account remain required. |
| Model provider | API output. | Provider key stays in environment; JSON schema validation; no free-form tool calls. | Provider selection, account controls, and data-sharing policy belong to the user. |
| Patch application | Exact replacement or file creation operations. | Relative path, symlink-escape, protected-path, scope, and exact-old-text checks. | Human review should validate semantic correctness. |
| Test commands | Discovered/configured quality commands. | Allowlisted executable, argument array, `shell=False`, denylist, timeout, output cap/redaction. | Only run TaskToPR in repositories whose test commands you are willing to execute. |
| GitHub PR | Branch, commit, push and `gh pr create`. | New branch, no `main`/`master` push, no force push, review/test gate required. | Branch protection and PR review must remain enabled in GitHub. |

## Path policy

All candidate paths are resolved against the Git root. A path is rejected if it is absolute, empty, resolves to the root, contains upward traversal, or escapes through a symbolic link. The default protected set includes `.git/**`, `.tasktopr/**`, `.env*`, `.github/workflows/**`, common credential/key files, auth/credential/secret-related path components, Docker/deployment files, and dependency lock files.

Protection is intentionally conservative. Version 0.1.0 does not offer a configuration flag that can disable Git-internal protection, allow default-branch push, enable force push, permit `rm -rf`, or invoke a shell. Additional project-specific protected globs can be added in `.tasktopr.toml`.

## Command policy

Testing and quality commands are not model-provided. They are discovered from known Python/Node project signals or explicitly configured. Before invocation, TaskToPR verifies the executable against a small allowlist and rejects shell metacharacters, destructive commands, network tools, privilege escalation, permission changes, force operations, and direct `.git` manipulation. Commands are passed to `subprocess.run` as an argument vector with `shell=False`, a fixed repository cwd, output capture, timeout, and redaction.

No command policy can safely execute every possible project build. The default is deliberately narrow; an unsupported stack should produce an evidence-backed “no recognized test command” result instead of silently executing arbitrary text.

## Secret handling

TaskToPR reads provider keys only from process environment variables. It applies pattern redaction to logs, evidence, terminal messages, Issue material, model errors, and test output for common GitHub tokens, OpenAI/Anthropic keys, AWS access-key IDs, private-key headers, and assignment-style password/token fields. Redaction is defense in depth, not a guarantee that every organization-specific secret format is recognized. Users should run their own secret scanners and never paste real credentials into Issues.

## Review and release gates

The deterministic review checks that changed files match the approved patch, protected paths have not changed, required checks are non-blocked and exit successfully, and `git diff --check` is clean. A failing gate produces a run summary and prevents automatic commit and PR creation. The `--no-pr` mode intentionally stops before commit/push/PR; `--dry-run` stops before branch creation and editing.

> TaskToPR supports a reviewable developer workflow. It is not designed to circumvent organizational controls, execute untrusted repository code without user consent, or replace human approval.
