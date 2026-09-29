# Track 3 simulation image

The current submitted Development image is the four-worker batch version used
in CodaBench submission **950514**. It scored **29,447.2373 events/s** over
all 71 scored units, versus **26,124.5133 events/s** for submission 948335.
All six batch units improved substantially. The `simulate` single-market path
was unchanged between those submissions; the 65 single-unit score differences
cannot establish a code regression. See
`../project-evidence/t23-scored-feedback-20260928.md` and the retained
per-unit CSV.

The submitted descriptor uses the public image
`docker.io/felix772/agenthon-2026-t3@sha256:df5e1b9d96abf06947a33cf499c3b4feed271fa31f6439de8ce49ff98a834489`.
Its matching local four-worker tag is
`simulation-agent:parallel-batch4-20260927` with image ID
`sha256:df5e1b9d96abf06947a33cf499c3b4feed271fa31f6439de8ce49ff98a834489`.
`build_parallel_batch4.py` records how that selected image was derived from
the earlier runtime image. The exact 71-unit local verifier and paired timing
review is in `../project-evidence/t3-parallel-batch4-full-roster-review-20260928.md`.

## Local optimization candidates

`Dockerfile.unlocked-queue` adds only `patch_unlocked_event_queue.py` over the
four-worker image. The patch substitutes a single-owner `heapq` queue for the
ABIDES kernel's locking `PriorityQueue`; it keeps the exact queued tuples and
comparison order. Its source-SHA guard rejects an unexpected ABIDES kernel.
The batch workers already run in separate processes; no scoring code, public
CLI, scenario detection, or output schema changes. The candidate is local,
tagged `simulation-agent:unlocked-queue-dev-20260928`, image ID
`sha256:94bda24a509530e2bf2dfb0f079a0c052b29422c87bc62163aac1701dfe194bc`.
It has **not** been published or submitted.

The candidate passed the current public developer verifier on **71/71 units**
(65 singles and six batches) with a 300-second hard limit. Every candidate
trace and message-ledger Parquet SHA and event count matched the incumbent
control exactly. The largest case took 214.91 seconds host-observed. See
`../project-evidence/t3-unlocked-queue-plan-20260928.md` and the incremental
records in `../project-evidence/t3-unlocked-queue-full-roster-20260928/`.
The full paired test then passed 426/426 strict runs. On the fixed 71-unit
roster, the candidate's local Development self-reported mean increased by
6.24%; the Docker start-to-exit mean changed by only +0.33% with wide
uncertainty. See `../project-evidence/t3-unlocked-queue-review-20260928.md`.
This supports a Development candidate, but does not establish a gain on the
organizer's dedicated Final timing instance.

The later scalar-latency derivative builds over that exact unlocked-queue
image and uses a guarded finite-scalar latency clip. Its local image ID is
`sha256:a12731a118178e10469608ec92172bc7db4b799a2b4ad8cd2b1acf939dd053c9`.
It passed a separate 71/71 semantic gate and a complete **426/426** strict
paired timing test against unlocked queue, with exact stable Parquet hashes
and event counts. On the 71 scored units, its local self-reported Development
rate improved **9.33%** (71/71 units); Docker start-to-exit rate improved
**5.50%** (61/71 units). A Docker outage split the test after unit 25 and
co-resident containers changed state, so these timings cannot establish an
organizer Final gain. The scalar image is the stronger local Development
candidate, but has not been published or submitted and has no official score.
See `SCALAR_LATENCY_EXPERIMENT.md`, `SCALAR_LATENCY_PAIRED.md`, and
`../project-evidence/t3-scalar-latency-review-20260929.md`.

To rebuild from a clean checkout on the configured Windows host, pull and
verify the submitted parent image, then create a context containing only the
two candidate build files. Run these PowerShell commands from the workspace
root:

```powershell
$parent = 'docker.io/felix772/agenthon-2026-t3@sha256:df5e1b9d96abf06947a33cf499c3b4feed271fa31f6439de8ce49ff98a834489'
docker pull $parent
docker tag $parent simulation-agent:parallel-batch4-20260927
docker image inspect simulation-agent:parallel-batch4-20260927 --format '{{.Id}}'
New-Item -ItemType Directory -Force .validation/t3-unlocked-queue-context-20260928 | Out-Null
Copy-Item simulation-agent/Dockerfile.unlocked-queue .validation/t3-unlocked-queue-context-20260928/Dockerfile
Copy-Item simulation-agent/patch_unlocked_event_queue.py .validation/t3-unlocked-queue-context-20260928/patch_unlocked_event_queue.py
docker build --network none --pull=false -t simulation-agent:unlocked-queue-dev-20260928 .validation/t3-unlocked-queue-context-20260928
docker image inspect simulation-agent:unlocked-queue-dev-20260928 --format '{{.Id}}'
```

Require the parent inspect output to equal the local four-worker image ID
above before building. `Dockerfile.unlocked-queue` uses that verified local tag
so the candidate cannot silently inherit another base. The two-file context
keeps tests, profiles, and public task data outside the image. The final
candidate ID may vary with the Docker builder; verify the installed kernel
SHA and run the official verifier on the rebuilt image before interpreting
any timings. The local test `test_unlocked_event_queue.py` compares 100,000
seeded interleaved operations against `PriorityQueue` and passed in the
candidate image.

## Earlier baseline

The earlier `Dockerfile.runtime` adds `WORKDIR /tmp` over the formatter-cache
image to allow ABIDES to create `./log` with a read-only root. Its image ID is
`sha256:c5724d34e2a8c951e4645b7c9aed1b95e3eabf1bd3dc24c0195055700cdbd45d`.
The formatter cache and runtime checks are recorded in
`../project-evidence/t3-04-review.md`, `../project-evidence/t3-06-review.md`,
and `../project-evidence/c05-t3-review.md`. These are historical local checks;
the submitted four-worker and current queue-candidate evidence above governs
the present decision.

## Competition sources

Before further code changes, recheck the current README and applicable
AGENTS.md, CONTRIBUTING.md, SUBMISSION_CLI.md, and linked rules in all five
official repositories. Follow the shared submission requirements and the
Track 3 CLI, schema, runtime, and validation contract:

- [Shared toolkit and competition instructions](https://github.com/Agenthon-2026/Agenthon2026-public)
- [Track 1 — Coding](https://github.com/Agenthon-2026/track1-coding-public)
- [Track 2 — Forecasting](https://github.com/Agenthon-2026/track2-forecasting-public)
- [Track 3 — Simulation](https://github.com/Agenthon-2026/track3-simulation-public)
- [Track 4 — Analysis](https://github.com/Agenthon-2026/track4-analysis-public)

The exact fetched refs and T3 documents used for these candidates are recorded
in `../project-evidence/t3-unlocked-queue-plan-20260928.md`.
