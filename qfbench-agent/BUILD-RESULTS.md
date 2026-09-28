# Agent implementation results

Latest verification (2026-09-20 UTC): see [RUNTIME-TEST-RESULTS.md](RUNTIME-TEST-RESULTS.md)
for repaired House API compatibility, toolkit v2.4.3 and strict Development runtime tests.
The entries below are historical and do not supersede that report.

Built a task-independent single-agent generate/execute/repair loop on the official numerical
sandbox. Added the required interface label, organizer-only model client, per-card deadline,
bounded execution and logs, safe deliverable publication, a local suite launcher, a structured
official-verifier adapter, and official-schema metadata validation.

The initial diagnostic implementation is preserved in Git tag `diagnostic-baseline` (16456af).
The public repository, practice tasks and official scorer were not edited.

## Verification evidence

- Agent Docker image built successfully; interface_version label inspected as 2.0.
- Eleven final Linux tests passed in 15.976 seconds, including real CLI repair, input
  preservation, Parquet output, missing-deliverable repair, JSON validation, token/retry behavior,
  missing model configuration, timeouts, and denied reward writes/network/checks access.
- Synthetic full Docker integration PASS: artifacts/selftest-6e780c415c/summary.json.
- Actual agent CLI exited 0 in 2.122 seconds on that synthetic run.
- Unchanged official Linux verifier exited 0 in 3.814 seconds; all four gates passed.
- The mock endpoint ran on a temporary internal Docker network; the agent root and task mounts
  were read-only, checks were absent from agent input, and the verifier had no network.
- Test helper container and network were removed afterward. No public finance answers are
  included in the scripted mock or shipped agent image.

The synthetic pass proves integration only. It does not measure finance task performance.
Docker Desktop currently exposes 22 CPUs and about 7.5 GiB RAM. The synthetic Docker test
used a 1 GiB cap. Full competition resource availability (cards commonly request 128 GB)
has not been reproduced locally; memory-heavy finance tasks may need organizer compute.
The tested agent image ID is
`sha256:33716c417369c08a8bdfb7f362d266350628ade536e51270187ee9168593f8d4`.
The final suite ran against this same image. The evaluator's missing-configuration path
also fails with a clear actionable error. `git diff --check` passes and the public repo's
tracked files remain unchanged.

## Remaining requirements

MODEL_ENDPOINT and MODEL_NAME were absent from both the restricted and user sessions. The real
house-model request, exemplar solve, broader finance suite, and statistical reliability cannot
be validated without organizer access. No score or public-task success is claimed.

The development evaluator executes one attempt per task and keeps failures in its denominator.
Its basic output checks are not the hidden grader: completed_unverified means files were produced,
not that the finance answer is correct. Worker Python hooks are accident-prevention guardrails;
container isolation must remain enabled. This first version only supports single-process Python.

Registration status, platform-specific submission procedure, team/competition IDs, model
disclosures and registry destination are not available. Nothing has been registered, pushed,
messaged to organizers or submitted. The pinned toolkit's documented --agent-image mismatch
remains; local Docker launch plus official Linux verification works independently of it.

Next: configure the actual organizer endpoint/model and development network, run the exemplar
through tools/evaluate.py, inspect its structured verdict, then evaluate several task categories
before broad tuning and official development submission.

## Local evaluator and release preparation update

- The evaluator now mounts the complete task at `/input`, grader-owned checks at `/tests`, task
  data at `/app/data`, and immediate task-data files at `/app/<name>` only for the trusted
  verifier. The agent still receives staged input without checks or reference data.
- The synthetic end-to-end test now requires all of those paths and passed: agent 2.267 seconds,
  verifier 5.853 seconds, artifact `artifacts/selftest-d248ee89f9/summary.json`.
- Fifteen tests passed in the organizer-toolkit environment. The same suite passed in the slim
  agent image with the descriptor-validation test skipped because that development-only toolkit is
  intentionally absent there.
- Release preparation now includes an Apache-2.0 license, a digest-only `tools/build_release.py`
  builder for `linux/amd64`, and `tools/submission.py --zip`, which writes an archive containing
  exactly root-level `submission.json` after official descriptor validation.

An actual release image cannot yet be built truthfully: the organizer has not published an
immutable official sandbox reference, endpoint model metadata, or submission destination. No
image has been pushed and no descriptor using placeholder identifiers has been retained.
