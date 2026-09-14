# Installed TaskToPR → PatchWitness Safe Delivery consumer

This repository includes an end-to-end consumer fixture in `scripts/installed_handoff_smoke.py`. The fixture proves the public package boundary rather than importing either project from its source checkout.

The flow is:

1. build the TaskToPR wheel from the exact checkout under test;
2. create a fresh virtual environment and install that wheel through `pip`;
3. build PatchWitness from the reviewer-pinned commit `e44d2c7ccea615bb4b43449e77573e02c0bcbb60` into a wheel and install it into the same isolated environment;
4. create a synthetic Git repository and local bare remote outside both source trees;
5. run the installed `tasktopr fix 1 --demo` command against the synthetic repository, using only the deterministic `DemoProvider`, the fixture's bounded unittest command, a local Git remote, and a fake local `gh pr create` boundary;
6. export the run's completed exact-HEAD `execution-receipt.json` through the installed `tasktopr-export-evidence` entry point;
7. compose a PR-stage Change Passport with the installed `patchwitness-safe-delivery tasktopr` adapter; and
8. independently re-verify the saved Passport with `patchwitness-safe-delivery verify`.

The fixture binds the exact TaskToPR checkout revision, PatchWitness revision, base commit, candidate commit, and SHA-256 identities of both installed wheels. Re-exporting the same execution receipt must produce identical handoff bytes, and repeated offline verification of the same Passport must preserve the decision and receipt digest.

## Fail-closed cases

The integration deliberately tests two negative paths after the clean installed flow succeeds:

- a handoff with a forged envelope receipt digest must be rejected and must not produce a Passport;
- a syntactically valid handoff whose execution evidence is marked incomplete must also be rejected instead of becoming a PatchWitness `PASS`.

A successful TaskToPR execution component is intentionally **not** overall merge authorization. The generated PatchWitness Passport remains PR-stage evidence with the other independent Safe Delivery components missing, so the overall decision is expected to remain `UNKNOWN` until separate trusted producers provide those facts.

## Trust and publication boundary

Safe to publish/share from the fixture:

- schema/version identifiers;
- exact Git revisions for the synthetic base/candidate and reviewed producer commits;
- wheel SHA-256 identities;
- sanitized TaskToPR handoff data;
- the PatchWitness Passport receipt and conservative decision.

Local-only or intentionally excluded:

- model credentials (none are used by this fixture);
- real GitHub credentials or API calls (none are used for the synthetic PR boundary);
- TaskToPR run IDs and timestamps removed by the handoff exporter;
- raw prompts/provider output beyond the local evidence bundle;
- arbitrary repository-owned commands.

The fixture fetches PatchWitness only from the exact reviewed Git commit above and uses a synthetic/public repository. It does not make TaskToPR depend on PatchWitness at runtime, modify branch protection, auto-merge anything, or require secrets.
