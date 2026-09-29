# T3 unlocked event-queue candidate: complete local review

The candidate adds a single-owner `heapq` wrapper to the ABIDES market event
queue in the submitted four-worker image. It changes the generic event path for
all single markets and batch submarkets; it does not select scenarios by ID or
change the scorer, interface, outputs, or four-worker scheduling. The image is
local only: `sha256:94bda24a509530e2bf2dfb0f079a0c052b29422c87bc62163aac1701dfe194bc`,
built over submitted parent
`sha256:df5e1b9d96abf06947a33cf499c3b4feed271fa31f6439de8ce49ff98a834489`.
Current official documentation refs, the strict source-hash guard, queue ABI
audit, and four-unit screen are in [the plan](t3-unlocked-queue-plan-20260928.md).

## Admission

The candidate passed the current Development verifier on all **71/71** public
units with a stricter 300-second hard cap: 65 singles and six batches. Every
stable trace and message-ledger Parquet SHA, plus every event count, exactly
matches the selected submitted image's retained control. The largest scenario
finished in 214.91 host-observed seconds. The full-roster semantic records are
in `t3-unlocked-queue-full-roster-20260928/`.

After T2 released the host, the frozen paired test ran 71 units × two images ×
three fresh Docker executions (one discarded warm-up, two measured), **426/426
admitted**, with no failures or changed stable outputs. It alternated which
image ran first across the measured pairs. The host fingerprint matched at
the beginning and end. [Plan, individual records, and summary](t3-unlocked-queue-paired-20260928/)
are retained incrementally. These are local, nonrankable results.

| Scored subset | Development self-reported rate, old → candidate | Difference | Docker start-to-exit rate, old → candidate | Difference |
| --- | ---: | ---: | ---: | ---: |
| All 71 | 11,267.62 → 11,970.64 | **+703.01 (+6.24%)** | 6,684.58 → 6,706.92 | +22.35 (+0.33%) |
| 65 singles | 10,291.13 → 10,987.50 | **+696.36 (+6.77%)** | 6,044.88 → 6,232.81 | +187.92 (+3.11%) |
| Six batches | 21,846.26 → 22,621.32 | **+775.05 (+3.55%)** | 13,614.60 → 11,843.19 | -1,771.41 (-13.01%) |

Each value is the arithmetic mean of per-unit medians across the two measured
repeats, in events/s. Self-reported rate improved in 66/71 units (61/65
singles, 5/6 batches). Docker rate improved in 51/71 (48/65 singles, 3/6
batches). There are no zero-scored or omitted units.

## Sensitivity and limits

Unit-bootstrap 95% intervals for the **absolute difference** are +583 to +819
events/s for all-71 self-reported rate and -320 to +299 for all-71 Docker
rate. Name-derived family-block bootstrap gives +586 to +833 and -368 to +271,
respectively. The family grouping has 13 blocks (`as`, `ca`, `eq`, `fastlob`,
`gb-single`, `gbatch`, `mp`, `mr`, `ra`, `s`, `sf`, `st`, miscellaneous single);
it is a sensitivity device, not an official workload model. These intervals
resample a fixed public roster and are not confidence bounds for the organizer's
Final run. The singles-only Docker family-block interval is +59 to +304; the
six batches form only one family, so no meaningful block interval exists for
that subset. Leaving out one family at a time moves the all-71 Docker mean
difference between -26 and +188 events/s.

A T2 host-side import preflight overlapped only the candidate's first measured
`t3-gb-horizon-240s` execution. [The co-residency note](t3-unlocked-queue-paired-20260928/co-residency-notes.md)
retains exact timestamps and the original measurements. Excluding that entire
unit, purely as sensitivity rather than a substitute 70-unit official score,
changes the self-reported difference to **+706.54 (+6.25%)** and Docker
difference to **+19.06 (+0.28%)**. Thus this known overlap does not explain
the all-roster Docker ambiguity. The user-owned Freqtrade container ran
throughout and another container repeatedly restarted; neither was stopped.
The local WSL host reported about 8.0 GiB memory, below the official 16 GiB
container limit, so these timings cannot reproduce the dedicated Final node.

The six batches dominate the uncertain Docker outcome: their container rates
are volatile even within the same image (for example, `t3-gbatch-homog-4`
incumbent repeats were 10,474 and 19,207 events/s). The queue optimization is
supported for the current Development self-reported score across the entire
roster. It is **not established as a Final-throughput improvement** on the
organizer's dedicated idle instance. The candidate has not been pushed,
packed, or submitted; the previously submitted four-worker image remains the
reference release pending a separate publication/submission decision.
