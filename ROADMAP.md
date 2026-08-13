# Roadmap

TaskToPR will remain local-first and evidence-first. The roadmap is intentionally ordered by safe developer experience rather than by raw autonomy.

| Horizon | Planned direction | Guardrail |
| --- | --- | --- |
| v0.2 | Add richer repository adapters for Go, Rust, and Java, plus clearer unsupported-command diagnostics. | New commands will remain allowlisted, argument-array based, timed, and covered by tests. |
| v0.3 | Add optional human plan approval/editing before any branch creation. | Approval state will be recorded in the run evidence bundle. |
| v0.4 | Improve context selection with symbol/import relationships and user-controlled context budgets. | No entire-repository upload or hidden external index will be introduced by default. |
| v0.5 | Add a dry-run PR preview artifact and repository policy profiles. | Profiles may add restrictions but cannot enable force push, default-branch writes, shell execution, or Git-internal edits. |
| 1.0 | Stabilize configuration, provider contracts, evidence schema, and extension interfaces. | Backward compatibility and security review will be release criteria. |

Hosted execution, scheduled autonomous Issue pickup, auto-merge, cross-repository changes, and unrestricted command agents are explicitly out of scope unless the project first develops a credible isolation, consent, audit, and governance model.
