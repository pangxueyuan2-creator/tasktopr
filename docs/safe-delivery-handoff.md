# Safe Delivery execution handoff

TaskToPR can export a completed exact-HEAD execution receipt into a small, versioned JSON document for downstream Safe Delivery consumers.

The exporter is deliberately narrow. It exposes identities, bounded counts, decisions, and SHA-256 digests; it does **not** copy prompts, Issue text, command arguments, test output, raw paths, run IDs, timestamps, or repository contents into the portable handoff.

## Installed CLI

Build or install TaskToPR, then export a final `execution-receipt.json`:

```bash
tasktopr-export-evidence \
  .tasktopr/runs/<run>/execution-receipt.json \
  --tool-revision <reviewer-pinned-40-character-tasktopr-commit> \
  --output execution-handoff.json
```

The producer revision is an explicit input rather than something trusted from the candidate receipt. A reviewer or integration layer must pin and authenticate that revision independently.

## Acceptance requirements

The exporter fails closed unless all of the following hold:

- the receipt is a bounded regular non-symlink UTF-8 JSON file;
- duplicate JSON keys are absent and the receipt envelope has the expected fields;
- the receipt's canonical SHA-256 matches its payload;
- the TaskToPR receipt schema is version 1;
- `result_head_sha` and `tested_head_sha` are exact Git revisions and are identical;
- the receipt phase is `verified_head` or `pr_created`;
- the deterministic review decision is `REVIEW_REQUIRED` and the protected-path decision is `allow`;
- exact-head tests are recorded as passing with a bounded positive count;
- repository, policy, tool-source, command-list, test-result, and changed-file identities use validated SHA-256 values;
- the changed-file manifest contains only hashed path identities and bounded before/after identities;
- the separately supplied TaskToPR producer pin is an exact 40-character Git commit.

Invalid evidence never produces a new handoff file. Output uses an atomic replace after validation.

## Schema v1

The envelope is content-addressed:

```json
{
  "payload": {
    "schema_version": "tasktopr.dev/safe-delivery/execution/v1",
    "component": "execution",
    "producer": {
      "name": "tasktopr",
      "version": "0.1.0",
      "git_revision": "<reviewer-pinned commit>",
      "source_sha256": "<TaskToPR source digest>"
    },
    "change": {
      "repository_sha256": "<local repository identity digest>",
      "base_sha": "<base commit>",
      "head_sha": "<exact tested candidate commit>",
      "changed_file_count": 2,
      "change_scope_sha256": "<digest of TaskToPR's hashed changed-file manifest>"
    },
    "policy": {
      "version": "tasktopr-execution-v1",
      "sha256": "<TaskToPR execution-policy digest>"
    },
    "verification": {
      "decision": "REVIEW_REQUIRED",
      "complete": true,
      "tests_status": "pass",
      "tests_count": 2,
      "command_list_sha256": "<digest>",
      "test_result_sha256": "<digest>",
      "protected_path_decision": "allow"
    },
    "source_receipt": {
      "schema_version": 1,
      "sha256": "<original execution receipt digest>"
    },
    "trust_boundary": "..."
  },
  "receipt_sha256": "<canonical payload digest>"
}
```

Field order is not semantically significant; the envelope digest is calculated from canonical JSON.

## PatchWitness integration boundary

This handoff is producer evidence, not a PatchWitness `ChangeSubject` and not a Change Passport by itself.

In particular, `change_scope_sha256` is the digest of TaskToPR's privacy-preserving hashed-path manifest. A PatchWitness adapter must still derive the candidate change manifest independently from trusted Git data. Likewise, the TaskToPR policy digest identifies TaskToPR's execution configuration; it must **not** be substituted for PatchWitness's reviewer-controlled Safe Delivery composition policy digest.

A downstream adapter should therefore:

1. authenticate or otherwise independently trust the pinned TaskToPR producer revision;
2. verify the candidate repository and exact base/head subject using its own trusted Git view;
3. independently derive the PatchWitness manifest and reviewer-owned policy identity;
4. bind the TaskToPR handoff digest into execution-component details;
5. preserve `REVIEW_REQUIRED`, `FAIL`, and `UNKNOWN` conservatively rather than upgrading incomplete evidence to `PASS`.

## Trust limits

SHA-256 provides content identity and integrity, not confidentiality, digital signatures, or producer authentication. Anyone who controls both a document and its hash can replace both. A passing TaskToPR execution handoff proves only the bounded facts encoded in the validated receipt. It does not prove code correctness, hosted CI status, human approval, merge eligibility, or release authorization.

The installed-consumer CI fixture builds the real wheel, installs it without TaskToPR's runtime dependencies, exercises the exported console entry point from that wheel, verifies exact-head and producer-pin preservation, and confirms a tampered receipt is rejected without producing output.
