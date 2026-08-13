# Security policy

## Supported versions

Security fixes are applied to the latest released `0.x` version of TaskToPR. Earlier pre-release or development snapshots may not receive backports.

## Reporting a vulnerability

Please **do not** open a public GitHub Issue for suspected vulnerabilities, leaked credentials, policy bypasses, path traversal, command execution, secret-redaction failures, or Pull Request safety defects. Instead, use GitHub’s private security advisory reporting for this repository, or contact the maintainers through the repository’s security contact when one is configured.

A useful report explains the affected version, a minimal reproduction, the expected and observed behavior, attack preconditions, and any proof-of-concept that does not expose real secrets or harm third-party systems. We aim to acknowledge reports promptly, validate the report, prepare a fix, and coordinate a disclosure timeline in good faith.

## Security boundaries

TaskToPR is local-first and has deliberate safety constraints, but it is not a security sandbox. Users remain responsible for choosing a trustworthy repository, a safe runtime account, an approved model provider, and commands appropriate for their environment. Do not use it to bypass branch protection, review requirements, deployment controls, or organizational policies.

The project’s default policy protects Git internals, workflow files, deployment and Docker paths, auth/credential/secret-related paths, environment files, and common keys. It also avoids shell execution and blocks destructive/network/admin command categories. These controls reduce accidental and model-driven risk; they are not a substitute for code review, protected branches, secret scanning, or isolated execution environments.

See [docs/security-model.md](docs/security-model.md) for the implementation-level model and [README.md](README.md) for operational constraints.
