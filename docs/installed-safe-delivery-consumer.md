# Installed TaskToPR → PatchWitness Safe Delivery consumer

This repository includes an end-to-end consumer fixture in `scripts/installed_handoff_smoke.py`.
The fixture proves the public package boundary rather than importing either project from its
source checkout.

The flow is:

1. build the TaskToPR wheel from the exact checkout under test;
2. create a fresh virtual environment and install that wheel through `pip`;
3. confirm the installed TaskToPR distribution is exactly version `0.1.0`;
4. build PatchWitness from reviewer-pinned commit
   `36e00e7d2b7339ea278b3ccca797e95217b7bd59` into a wheel and install it into the same
   isolated environment;
5. create a synthetic Git repository and local bare remote outside both source trees;
6. run the installed TaskToPR producer with the deterministic `DemoProvider` and a test-only
   approval callback that stands in for the trusted local UI boundary;
7. export the completed exact-HEAD execution receipt through the installed
   `tasktopr-export-evidence` entry point;
8. compose a PR-stage Change Passport with the installed PatchWitness consumer while requiring
   all of the following reviewer-controlled compatibility constraints:
   - the exact TaskToPR Git revision under test;
   - producer version `0.1.0`;
   - handoff schema `tasktopr.dev/safe-delivery/execution/v2`;
   - explicit prompt-mode plan-approval provenance; and
9. independently re-verify the saved Passport with `patchwitness-safe-delivery verify`.

The fixture binds the exact TaskToPR checkout revision, TaskToPR release version, v2 handoff
schema, PatchWitness revision, base commit, candidate commit, and SHA-256 identities of both
installed wheels. Re-exporting the same execution receipt must produce identical handoff bytes,
and repeated offline verification of the same Passport must preserve the decision and receipt
digest.

## Fail-closed cases

After the clean installed flow succeeds, the integration verifies four independent negative
paths. Every case must return a failure and must not create a Passport:

- the same valid handoff presented with the wrong reviewer-pinned TaskToPR version;
- the v2 handoff presented while the reviewer requires the supported-but-wrong v1 schema;
- a handoff with a forged envelope receipt digest; and
- a syntactically valid handoff whose execution evidence is marked incomplete.

The version and schema checks are compatibility constraints, not artifact authentication. The
exact PatchWitness commit remains independently pinned, and the fixture records wheel digests so
CI output can identify the tested artifacts. Binary signing, attestations, and distribution
channel provenance are separate trust decisions.

A successful TaskToPR execution component is intentionally **not** overall merge authorization.
The generated PatchWitness Passport remains PR-stage evidence with the other independent Safe
Delivery components missing, so the overall decision is expected to remain `UNKNOWN` until
separate trusted producers provide those facts.

## Trust and publication boundary

Safe to publish/share from the fixture:

- schema/version identifiers;
- exact Git revisions for the synthetic base/candidate and reviewed producer/consumer commits;
- wheel SHA-256 identities;
- sanitized TaskToPR handoff data;
- the PatchWitness Passport receipt and conservative decision.

Local-only or intentionally excluded:

- model credentials (none are used by this fixture);
- real GitHub credentials or API calls (none are used for the synthetic PR boundary);
- TaskToPR run IDs and timestamps removed by the handoff exporter;
- raw prompts/provider output beyond the local evidence bundle;
- arbitrary repository-owned commands.

The approval callback is test-only evidence that the installed v2 interoperability path can carry
and enforce approval provenance. It is **not** a claim that an external human reviewed this CI
fixture. The fixture does not make TaskToPR depend on PatchWitness at runtime, modify branch
protection, auto-merge anything, or require secrets.
