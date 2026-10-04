# Windows process boundaries

GitHub CLI Issue JSON and Git/PR command output are decoded explicitly as UTF-8,
independently of the Windows locale. Capture pipes as bytes and decode on the
calling thread: an invalid byte sequence produces a generic domain error without
raw output in the exception. Valid command failures retain the existing redacted
diagnostic. This avoids Windows subprocess reader-thread failures under a legacy
locale while preserving Chinese text, emoji and accented characters.

The installed interoperability smoke uses an isolated synthetic GitHub CLI. On
Windows it installs a locally generated wheel with a real `gh.exe` console entry
point; POSIX retains the executable script. Before creating the fixture or running
TaskToPR, the smoke verifies exact PATH resolution and the synthetic CLI identity.
Missing, shadowed or unexpected entry points stop the smoke before the pipeline.
The synthetic CLI serves a fixed UTF-8 Issue and synthetic PR URL and performs no
GitHub network operation.

Run the canonical installed consumer check after building the candidate:

```text
python -m build
python scripts/installed_handoff_smoke.py dist
```

The script installs the candidate wheel and the existing pinned PatchWitness
consumer, then exercises Issue intake, plan approval, patch, tests, Unicode Git
commit, local bare-repository push, execution export and offline passport
verification. The plan-approval callback is test-only. The resulting decision
remains `UNKNOWN`; this fixture does not supply hosted CI, external approval or
merge authority. Setup fetches the pinned consumer and package dependencies;
the synthetic pipeline itself requires no model API or GitHub credentials.

Focused regressions exercise actual subprocess pipes with a cp936 default,
malformed UTF-8 on both streams, redaction, real Unicode Git output and installed
synthetic entry-point resolution. The process tests do not change system locale
or security settings.
