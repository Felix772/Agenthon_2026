# T3 scalar-latency candidate: complete local review

The scalar image adds a guarded finite-scalar fast path for ABIDES message
latency clipping on top of the unlocked-queue candidate. The compared immutable
local image IDs are unlocked
`sha256:94bda24a509530e2bf2dfb0f079a0c052b29422c87bc62163aac1701dfe194bc`
and scalar
`sha256:a12731a118178e10469608ec92172bc7db4b799a2b4ad8cd2b1acf939dd053c9`.
The scalar image is not published, packed, or submitted.

The preliminary screen passed 24/24 strict runs, and the separate full-roster
semantic gate passed 71/71 units. The frozen full paired test then completed
**426/426 passed strict Docker runs**: 71 units (65 singles, six batches), two
images, one discarded warm-up and two alternating measured runs per image.
Every run passed the current public developer verifier's g0–g3 gates. Its
stable trace and message-ledger Parquet hashes and event count exactly equaled
the frozen unlocked-queue semantic control. The retained [plan, records, and
summary](t3-scalar-latency-paired-20260928/) pin image IDs, ordered schedule,
input and card hashes, verifier digest, official rule refs, and node fingerprint.
An independent read-only audit recomputed the aggregates, all six reported
bootstrap intervals, and the hashes of every staged input without discrepancy.

| Scored subset | Self-reported Development proxy, unlocked → scalar (events/s) | Difference | Docker start-to-exit proxy, unlocked → scalar (events/s) | Difference |
| --- | ---: | ---: | ---: | ---: |
| All 71 | 13,222.68 → 14,456.42 | **+1,233.75 (+9.33%)** | 7,091.72 → 7,481.99 | **+390.28 (+5.50%)** |
| 65 singles | 12,040.21 → 13,250.33 | **+1,210.12 (+10.05%)** | 6,556.14 → 6,951.80 | **+395.66 (+6.03%)** |
| Six batches | 26,032.74 → 27,522.44 | **+1,489.70 (+5.72%)** | 12,893.84 → 13,225.78 | +331.94 (+2.57%) |

Each figure is the arithmetic mean across units of each image's two measured
per-unit rates (their median equals their mean for two observations). All
71 self-reported unit rates improved. Docker start-to-exit rate improved in
61/71 units (57/65 singles, 4/6 batches). The all-71 official
`scenario_family` block-bootstrap 95% diagnostic interval for the absolute
difference is **+1,087 to +1,355 events/s** for self-reported rate and
**+303 to +458 events/s** for Docker rate. The six-batch Docker unit-bootstrap
interval is **-39 to +683 events/s**, so its subset evidence is inconclusive.
These resampling intervals assess local sensitivity; they are not uncertainty
bounds for the fixed competition roster or the organizer's Final host.

Docker became unavailable after 150 successful runs, at the next unit's
discarded unlocked warm-up. That attempt exited 255 with no participant output.
After recovery, the runner verified the exact retained 150-run prefix,
unchanged images and node fingerprint, and retried only that warm-up as attempt
2; the first attempt remains on disk. Units 1–25 completed before the outage
and units 26–71 after it. The self-reported difference was +9.62% before and
+9.22% after; Docker difference was +6.81% and +5.08%, respectively. These
are different scenario subsets, so phase figures are only a direction check.
The user-owned Freqtrade container had stopped during the outage and later
restarted; `claude-app-1` repeatedly restarted. The CPU/GPU/kernel/memory
fingerprint matched from plan through completion, but co-resident load was
not constant. The WSL node had about 8.0 GiB physical memory despite the
16-GiB container limit. See the retained
[recovery note](t3-scalar-latency-paired-20260928/interruption-recovery-20260929.md).

The scalar derivative is the better **local Development candidate** than the
unlocked-queue parent on these fixed public units. The Docker timing direction
is positive in this experiment, but the host conditions prevent a claim of
improved organizer Final throughput. There is **no new CodaBench score** for
this image; the submitted four-worker image from submission 950514 remains the
official reference.
