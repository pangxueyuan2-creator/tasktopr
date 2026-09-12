# Execution receipts

`tasktopr fix` checkpoints `execution-receipt.json` in its existing local run
directory. The envelope binds its `payload` with SHA-256 over sorted, compact,
ASCII-escaped JSON (`allow_nan=False`). Recompute this digest before consuming it.
Changing any field invalidates the envelope; the digest does not authenticate
the writer. This is a local observation, not a signed attestation or a sandbox.

The payload binds a local repository-root identity, base commit, task digest,
patch digest, policy version/configuration digest, TaskToPR source digest,
ordered command-list digest, test-result digest, and changed-file manifest.
Paths, task text, command arguments, outputs and remote URLs are excluded from
this shareable file. Manifest entries identify path and before/after content
by digest. The existing local `changes.json` maps those identities to filenames.
Local legacy journals may contain private task text and redacted outputs: do not
publish the whole run directory automatically. Hashes provide identity and
integrity, **not confidentiality**; low-entropy private inputs can be guessed.

A modifying run requires the clean declared base branch. Its manifest must
exactly match the patch. Snapshots include tracked files and visible untracked
files, excluding only untracked tool caches/journal artifacts. Tracked caches
remain in scope. Snapshots reject ambiguous Windows paths, case collisions,
non-NFC names, symlinks/junctions, gitlinks and nested repositories. The default
budget is 10,000 files, 8 MiB per file and 64 MiB of file contents. Unsupported
or over-budget evidence fails closed; it never becomes an empty clean manifest.
These are snapshot boundaries, not a complete build-input inventory: ignored
dependencies, the installed toolchain and OS configuration are outside it.

The repository revision, branch and contents must remain unchanged during
verification. `--no-pr` preserves its previous behavior (no commit or push),
records a tested working snapshot and leaves `tested_head_sha` null. When PR
creation is enabled, checks run again after committing; only that second run can
populate `tested_head_sha`. A failing second run keeps the candidate locally for
inspection and prevents push. An error checkpoint retains already-recorded
commit identity so a push failure is distinguishable from failure before commit.
Incomplete checkpoints require investigation; retrying does not reuse an
existing task branch or claim success for a prior run.

Successful local tests yield `REVIEW_REQUIRED`. CI and human review remain
`unknown`: these receipts never independently authorize merge or release.
Checkpoints use atomic replacement and reject linked journal paths. They do not
prevent an adversarial concurrent process from changing and restoring files
between observations. Run in an exclusive workspace; isolated execution and
external exact-head CI/review evidence are separate requirements.

Verification also binds the full index state and effective Git configuration.
Masked index entries and custom clean/smudge, ident and encoding transformations
are rejected before any diff can invoke them. Origin/push URL or configuration
drift invalidates verification. Local Git writes disable repository hooks and
fsmonitor; Git and GitHub executables are resolved outside repository content.
Ordinary Git end-of-line normalization remains supported: the commit identity
and raw working snapshot are recorded separately, not asserted byte-identical.

Quality commands keep their existing authorization policy and now use a bounded
transport: 1 MiB combined stdout/stderr, at most 12,000 retained bytes per stream,
a deadline, no interactive input, and cleanup of child processes. The default
environment contains only OS essentials; credentials, PATH, interpreter preload
settings and proxy variables are not inherited. Commands requiring additional
environment or child tool lookup may fail and require explicit future modeling.
Output-budget/cleanup failures never count as a passing test.

Windows commands start suspended and join a kill-on-close Job Object before
running. POSIX cleanup covers the process group; deliberate session escape needs
an external sandbox. Neither implementation restricts filesystem/network access
or proves containment of a fully hostile program. In particular, Job Objects
do not contain processes created through external services such as WMI. The
suspended-before-assignment Windows sequence has a small crash window that can
leave an inert suspended process; no target code runs before job assignment.
See Microsoft's [Job Objects documentation](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).
