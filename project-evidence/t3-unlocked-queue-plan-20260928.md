# T3 scored-unit optimization experiment plan

This candidate replaces the per-market `queue.PriorityQueue` wrapper with a
single-owner heap. It changes the event-loop queue in every single market and
every batch sub-market, rather than selecting public unit IDs or outputs. The
previous four-worker batch adapter and the two required CLI verbs remain intact.
This is a Development experiment; no new image will be published or submitted
until the local evidence supports it.

## Current rules consulted before the source change

On 2026-09-28, `origin/main` was fetched for all five official repositories:

| Repository | Fetched commit |
| --- | --- |
| Agenthon2026-public | `95a0de3d9a814f3883c151b7efdbbcf579139244` |
| track1-coding-public | `1a60fc48024c4f0e9978416d84e8370dbb0236cd` |
| track2-forecasting-public | `28a6cae9674f69e63a07a19165a9217e85eacfff` |
| track3-simulation-public | `f910de231209ebbca060efee47fe2c41aabe4bce` |
| track4-analysis-public | `7b2bce1d80d96f5d5667d7f67bfaa945fa5d1491` |

All five current READMEs were consulted. Applicable instructions read: shared
`AGENTS.md`, `CONTRIBUTING.md`, `docs/IMAGE-SUBMISSIONS.md`,
`docs/DEVELOPMENT-RUNTIME.md`, `starter-packs/track3/{AGENTS.md,
RUNTIME-ENVIRONMENT.md,SUBMISSION-DESCRIPTOR.md,TEAM-CLAIM.md}`; Track 3
`AGENTS.md`, `CONTRIBUTING.md`, `SUBMISSION_CLI.md`, `regression_suite/README.md`
and `throughput/README.md`. The `units/` tree is unchanged between the frozen
source commit `1504b37b8472f7f949d4b8f80b7c8b2b71ab0e43` and current
Track 3 ref. The official Development scorer uses the self-reported rate when
no local host-metrics record exists; planned Final timing uses trusted Docker
timing. Both must be measured and labelled separately.

## Candidate and local screen

Incumbent four-worker image ID:
`sha256:df5e1b9d96abf06947a33cf499c3b4feed271fa31f6439de8ce49ff98a834489`.
Candidate local image ID:
`sha256:94bda24a509530e2bf2dfb0f079a0c052b29422c87bc62163aac1701dfe194bc`.
The candidate retains every incumbent image layer as a prefix and the same
runtime config. Its build script refuses any unexpected kernel source SHA.
`test_unlocked_event_queue.py` compares the actual installed class with
`queue.PriorityQueue` under 100,000 seeded interleaved operations, including
many equal timestamps; it passed.

The installed-code ABI scan covered `abides_core`, `abides_markets` and the
organizer adapter. The queue has one direct `len(.queue)` inspection, three
`.empty()` calls, one `.get()` call and four `.put()` calls; no caller uses
`qsize`, `task_done`, `join`, locks or condition variables. The actual kernel
item is `(deliver_at, (sender_id, recipient_id, message))`, **not** a separate
`(priority, counter, item)` wrapper. Both old `PriorityQueue` and the candidate
apply Python's same `heapq` tuple comparison to that item, so ties retain the
same comparison behavior. The wrapper removes only thread synchronization and
unfinished-task bookkeeping. The official adapter calls each market in one
thread; batches fork independent worker processes, and scenario configs map
only to four fixed scheduled-agent classes. A sealed scenario that somehow
introduced concurrent producers into one kernel would violate this assumption;
the published adapter does not offer such an injection path.

The predeclared four-unit screen used one warm-up per image/unit and two
alternating measured repeats. All 24 runs passed the current developer
verifier, with identical stable parquet hashes and event counts across image
and repeat. Reported-rate median ratios (candidate/incumbent) were 0.9867,
1.0573, 1.0218 for three distinct single-market workloads and 1.2093 for one
batch. The four-unit mean is a selected diagnostic; it is **not** a 71-unit
score estimate. Docker-rate medians were much noisier on short and batch
cases. Freqtrade and a restarting Claude container were co-resident, so all
performance readings are directional.

## Promotion gates

1. Run all 71 current public units once with the candidate. Require 65/65
   singles and 6/6 batches admitted by the current developer verifier, under
   the strict Development resource settings and a 300-second hard timeout.
   Compare every stable parquet SHA and event count with the retained
   incumbent's 71-unit warm-up record. Save each result immediately and stop
   on the first failure. This deliberately exceeds the official semantic gate
   by requiring byte identity to the known exact implementation.
2. Only after gate 1 passes and CPU coordination is clear, run alternating
   paired timings on the **whole** 71-unit roster, with fresh warm-ups on both
   images and at least two measured repeats each. Record both the candidate's
   self-reported `events_per_sec` (the current online Development practice
   input) and Docker start-to-exit event rate (a local Final timing proxy).
   Group the 65 singles and six batches separately. Compute the arithmetic
   mean over all 71 **per-unit median absolute rates** and the paired absolute
   difference; never project from the four-unit screen's batch-heavy weight.
   Report per-unit negatives and uncertainty from paired resampling. Stable
   parquet hashes and event counts must match on every timed repeat.
3. Promote only if semantics remain perfect and a real score improvement is
   supported by the full-roster paired data without material regressions.
   The shared local WSL host is not the organizer's fixed idle B200 timing
   instance, so even a local pass cannot certify Final ranking.

The six batch gains in the live 950514 submission are real Development practice
feedback. The 52/65 single-unit score reductions versus 948335 cannot be a
four-worker code regression: the `simulate` path in those two submitted images
was identical. Shared-queue timing and submission-to-submission variation are
the plausible causes, but not proven by that one comparison.

## Full-roster semantic gate result

The candidate passed all 71 public units (65 singles and six batches) under
the current verifier and stricter 300-second hard limit. Every stable parquet
hash and event count exactly matched the retained incumbent control. There
were no participant or verifier failures. The largest case,
`t3-gb-mega-throughput`, took 214.91 seconds host-observed and 202.82 seconds
container start-to-exit. Complete incremental records are in
`project-evidence/t3-unlocked-queue-full-roster-20260928/`.

This gate establishes semantics, not a score change. Its 71 runs were not
paired with fresh incumbent runs. The planned alternating full-roster paired
test began only after T2 finished its Docker checks and released the host.
