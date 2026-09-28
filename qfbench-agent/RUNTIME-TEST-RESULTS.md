# API compatibility and Development runtime verification

Verified 2026-09-20 UTC. This is local compatibility evidence, not official submission
acceptance or a measurement of finance-task accuracy.

## Changes

- Read the injected route origin and append `/v1/chat/completions`; require `MODEL_TOKEN`
  and send Bearer authentication. Reject endpoint paths to catch stale `/v1` configuration.
- Preserve upper/lowercase proxy variables in the evaluator. Generated code receives no
  model or proxy credentials. No redirects or external model fallback are allowed.
- Cap output at 4,000 tokens and sends at 25, including retries. Retain conservative token
  reservations for uncertain failures; restore them for explicit pre-admission 401/403.
- Apply non-root/read-only restrictions, the published tmpfs and ulimits, no swap, and the
  card's CPU/memory limits in the evaluator. Reject aggregate outputs over 64 MiB.
- Fix worker startup: it previously tried to raise the inherited 64 MiB hard file limit to
  512 MiB, which failed under non-root execution. It now honors tighter inherited limits.
- Rebuild the verifier image with official toolkit tag v2.4.3, resolved during this build to
  commit `358656a32094b19eff6fa95fe44b6a6671dc5041` (installed version reported 2.4.3).

## Results

- Full Linux suite with official toolkit: **20 tests passed, no skips**, in 20.996 seconds.
  [Test log](artifacts/runtime-toolkit-tests.log). Includes authenticated proxy routing,
  API paths/authentication, retry accounting, request ceiling, repair loop, Parquet output,
  execution restrictions and aggregate output-size rejection.
- Agent-only image suite before the final proxy test addition: 18 passed, one descriptor
  test skipped because the development toolkit is intentionally absent from the agent image.
  [Agent image test log](artifacts/runtime-unit-tests.log).
- Synthetic API → actual agent image → unchanged official verifier: **PASS**, all g0–g3 gates.
  Agent 2.169 seconds; verifier 4.992 seconds; complete output tree 776 bytes.
  [Summary](artifacts/selftest-7844fa598d/summary.json),
  [verdict](artifacts/selftest-7844fa598d/verdict.json).
- In-container runtime probe: **PASS**. UID/GID 65534, no effective capabilities,
  no-new-privileges, denied writes to root/input, writable dual output mounts, 64 MiB tmpfs
  with noexec/nosuid/nodev (execution actually refused), nofile 1024, nproc/PIDs 256,
  file size 64 MiB, CPU quota 16, memory cgroup 128 GiB, swap cgroup zero.
  [Runtime evidence](artifacts/selftest-7844fa598d/runtime.json).
- Tested agent image ID:
  `sha256:af2eb929306d4b06997860e7e6d8d4ae7807c4989571ad79e14fd6ab5e7c4684`.

The initial strict test run exposed the worker hard-limit bug (seven failures); the same
execution and repair tests passed after the fix. The API mock now rejects missing Bearer
authentication, incorrect paths and oversized output requests instead of accepting them silently.

## Limits of this evidence

Docker Desktop exposes 22 CPUs and 8,015,478,784 bytes (~7.5 GiB) of actual memory. The
128 GiB cgroup ceiling was verified, but this machine cannot validate a 128 GiB workload.
No GPU, live organizer House service, audited organizer proxy, TLS proxy tunnel or official
platform ingestion deadline was exercised. HTTP proxy authentication was tested locally;
the end-to-end mock used an isolated internal Docker network. No finance answers were used.
The toolkit test validates the existing descriptor helper, not first-upload team-claim packaging.

## Reproduction

From this directory in PowerShell (Docker Desktop running):

```powershell
docker build -t qfbench-agent:dev .
docker build -t qfbench-harness:dev -f Dockerfile.harness .
..\track1-coding-public\.venv\Scripts\python.exe tools/selftest.py
```

The selftest checks runtime restrictions before inference, fails on a violated restriction,
uses a synthetic task and credential, removes its helper containers/network, and retains evidence.
The README gives the agent-only regression command; replace its image with `qfbench-harness:dev`
and set `OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1` to run all toolkit tests.

## Official references consulted

The current README and available AGENTS/CONTRIBUTING/SUBMISSION_CLI files from all five
required repositories were retrieved before implementation (copies in
`artifacts/upstream-20260920`). The shared repository has no root SUBMISSION_CLI.md (404);
the Track 1 contract supplies it. Linked House, Development runtime, image, Track 1 runtime,
heritage, concepts and baseline documents were also consulted.

- [Shared toolkit](https://github.com/Agenthon-2026/Agenthon2026-public)
- [Track 1](https://github.com/Agenthon-2026/track1-coding-public)
- [Track 2](https://github.com/Agenthon-2026/track2-forecasting-public)
- [Track 3](https://github.com/Agenthon-2026/track3-simulation-public)
- [Track 4](https://github.com/Agenthon-2026/track4-analysis-public)
- [House API](https://github.com/Agenthon-2026/Agenthon2026-public/blob/main/docs/HOUSE-MODEL.md)
- [Development runtime](https://github.com/Agenthon-2026/Agenthon2026-public/blob/main/docs/DEVELOPMENT-RUNTIME.md)

Recheck upstream instructions for each new code-change task as required by workspace AGENTS.md.
