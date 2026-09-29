# Scalar latency: completed scored-roster paired timing

The scalar candidate passed its 71/71 strict semantic gate before this paired
test began. The frozen plan completed **426/426 strict runs** across all 71
scored units, and every stable parquet hash and event count matched the
unlocked-queue control. The full review is
`../project-evidence/t3-scalar-latency-review-20260929.md`; the immutable
plan, records, summary, and Docker recovery note are retained under
`../project-evidence/t3-scalar-latency-paired-20260928/`.

The compared local image IDs are frozen:

- Unlocked control: `sha256:94bda24a509530e2bf2dfb0f079a0c052b29422c87bc62163aac1701dfe194bc`
- Scalar derivative: `sha256:a12731a118178e10469608ec92172bc7db4b799a2b4ad8cd2b1acf939dd053c9`

The all-71 mean self-reported Development proxy increased from
13,222.68 to 14,456.42 events/s (**+9.33%; 71/71 units improved**).
The Docker start-to-exit proxy increased from 7,091.72 to 7,481.99
events/s (**+5.50%; 61/71 units improved**). Singles improved by
+10.05% and +6.03%, respectively; batches improved by +5.72% and +2.57%.
The six-batch Docker unit-bootstrap interval crosses zero. These are local,
nonrankable results; the scalar image has no official submission or score.

The ordered 71-unit names and manifest-checked solver-facing input hashes have
canonical SHA-256 `daed56d432146c2c985872848ec4809bde1e0e062db489bbe3250c9c97508bb0`.
The 71 public card hashes have canonical SHA-256
`a3184d90b0e0bda0a8bbd0c669e542c40e9296b4bda3db530af9a3f8eb634943`.
The verifier archive is pinned to
`ba591213a97659d11e8e9617075cee410518dfbee119c283eb2d47fecd6c121c`;
the current Track 3 rules were read at Git ref
`f910de231209ebbca060efee47fe2c41aabe4bce`. The script also records all
five official repository refs in its immutable plan.

With the retained Linux T3 validation Python, from the workspace root:

```bash
python simulation-agent/run_scalar_latency_paired.py
```

For a planned pause, use `--max-runs N` and rerun the same command to resume.
Only a contiguous prefix of passed runs is resumed. A failed or interrupted
attempt remains on disk and requires diagnosis; the runner does not overwrite
it. The one Docker-outage warm-up after 150 passed runs was diagnosed and
recovered with the guarded `--recover-interrupted-151` option, retaining the
original failed attempt and using attempt 2. Results were written under
`project-evidence/t3-scalar-latency-paired-20260928/`.

Each of 71 scored units (65 single, six batch) receives one discarded warm-up
per image, followed by two measured pairs. Image order reverses in the second
pair: unlocked/scalar, then scalar/unlocked. Thus the schedule contains 426
strict container runs. Every run uses the retained 4-CPU/16-GiB, read-only,
nonroot, no-network, 300-second launcher; the current public developer
verifier; and exact stable parquet SHA/event-count equality to the unlocked
semantic control. The plan freezes ordered runs, image IDs, input hashes, card
hashes, official scenario-family keys, node fingerprint, and verifier digest.

The summary reports per-unit medians and the arithmetic mean of absolute
events/sec separately for the full roster, 65 singles, and six batches. It
keeps checked self-reported Development throughput separate from Docker
start-to-exit throughput, which is only a local proxy for planned Final timing.
Paired unit and official `scenario_family` block bootstrap intervals are
diagnostics for sensitivity; they do not make this local host rankable or
measure uncertainty in the competition's fixed public roster. Record any
co-resident CPU load before interpreting a small timing difference.
The plan and final node fingerprints matched, but a user-owned Freqtrade
container stopped during the Docker outage and later restarted; another
container repeatedly restarted. The first 25 units completed before the
outage and the remaining 46 after it. Both subsets favored scalar on both
rates, yet their different scenarios and shifting host load prevent a claim
about organizer Final throughput. The official score remains unmeasured.

The current public instructions were rechecked before this code change:
[shared competition](https://github.com/Agenthon-2026/Agenthon2026-public),
[Track 1](https://github.com/Agenthon-2026/track1-coding-public),
[Track 2](https://github.com/Agenthon-2026/track2-forecasting-public),
[Track 3](https://github.com/Agenthon-2026/track3-simulation-public), and
[Track 4](https://github.com/Agenthon-2026/track4-analysis-public). The shared
README, AGENTS, CONTRIBUTING, image-submission/runtime guides, and Track 3
README, AGENTS, CONTRIBUTING, SUBMISSION_CLI, throughput, stable-repeat and
starter-pack runtime/descriptor guidance were reviewed at the refs in the
runner. No image is published or submitted by this experiment.
