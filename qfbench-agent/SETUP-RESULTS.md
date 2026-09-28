# Setup results — 2026-09-07

Historical record of the initial diagnostic setup, preserved at Git tag diagnostic-baseline.
For the subsequent model-driven implementation and current commands, see README.md and
BUILD-RESULTS.md. The current solve command requires organizer model configuration.

## Passed

- Public starter cloned at commit 81e3e072f8d93ecbddc5915e38710a6cce61313c.
- No tracked public repository files or practice tasks modified (`git status --short` clean).
- Python 3.13.0 environment created at track1-coding-public/.venv.
- qfbench2-common 2.3.1 installed from tag v2.3.1, commit 0f8e74fc54dc2be84fabf55d86beaa21a7c56330.
- Track package installed editable; scoring module import passes without PYTHONPATH.
- `qfbench2 --help`, exemplar card validation, and `pip check` pass.
- `finance-bench-sandbox:latest` built using the unchanged official Dockerfile and verified.
- `qfbench-agent:dev` built with the exact solve entrypoint and a digest-pinned Python base.
- Three unit tests pass: input preservation/output-only diagnostic, invalid/overlapping paths,
  and optional instruction/existing diagnostic handling.
- Local exemplar invocation created artifacts/local/diagnostic.json.
- Offline Docker invocation with read-only input and read-only root filesystem created
  artifacts/manual/diagnostic.json on the host. No agent reward artifacts were produced.
- Linux development verifier image qfbench-harness:dev builds and executes offline.
- Official Linux verification of the diagnostic passes g0_integrity, g1_schema, and
  g2_cutoff_resource. g3_domain_semantics runs trusted pytest and fails as expected:
  no price/Greeks deliverables (14 errors, 1 skipped). This is not a finance solution.

## Failures, diagnoses, and remaining limits

Docker initially had no running engine; starting Docker Desktop resolved it. Restricted-shell
network/Python access failures were resolved by running authorized setup commands outside the
sandbox. An early import attempt raced the editable installation; after completion the import
passes normally. No persistent dependency conflict remains.

The requested no-agent smoke does NOT pass: native Windows first fails the official manifest
walker because POSIX O_NOFOLLOW/O_DIRECTORY are unavailable. The Linux verifier resolves that
platform issue, then rejects the absent output at g1 with shared.schema.invalid_output.
Inspection of installed qfbench2_common/smoke.py confirms that it verifies an existing output
directory: it never builds a task image or performs the mock solve promised by the starter README.

The requested `--agent-image qfbench-agent:dev` smoke exits 2 with "unrecognized arguments".
The pinned 2.3.1 CLI has no such option. This is an upstream README/toolkit mismatch,
not an agent entrypoint failure. No scorer patches, version changes, mock answers, or invented
reward files were used to make it appear successful. An official harness-driven launch remains
unconfirmed until organizers supply a compatible, pinned runner or corrected instructions.
The manual Docker launch plus separate official Linux verification is working now.

Windows outputs use qfbench-agent/artifacts instead of the Unix /tmp paths. README.md contains
all fresh-install, rebuild, local test, manual Docker, original smoke, and Linux verifier commands.
No immediate manual repair is required for the working workflow. The unresolved organizer
runner mismatch cannot be fixed by reinstalling this same pinned release.

## Files created

Authored files (all under qfbench-agent): Dockerfile, Dockerfile.harness, requirements.txt,
README.md, SETUP-RESULTS.md, .gitignore, .dockerignore, agent/__init__.py, agent/__main__.py,
agent/cli.py, agent/solver.py, agent/task_reader.py, agent/workspace.py,
agent/model_client.py, tests/test_cli.py.

Generated files: development-environment.txt (installed dependency snapshot),
artifacts/local/diagnostic.json, artifacts/manual/diagnostic.json,
artifacts/shared-AGENTS.txt (downloaded shared repository instructions), and ignored Python
bytecode caches. The official clone includes its upstream files plus the ignored .venv and
editable package metadata. Temporary bootstrap tooling/cache was removed from the project root.

## Next step

First obtain the organizer-supported runner/version that actually launches --agent-image and
repeat that launch check. Keep the current diagnostic agent until that contract is confirmed.
Then implement only a small MODEL_ENDPOINT client tested against a local mock, including
timeouts and malformed/error responses. Add model-driven solving in a later step, budgeting
from each card's [agent].timeout_sec. No sophisticated solver was implemented in this setup.
