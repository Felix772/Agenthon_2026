# Development feedback: T2 and T3 second submissions

Read from the logged-in CodaBench Development detailed-results view on 2026-09-28 (EDT). These are practice-board observations, not Final/Verification ranks. Both submissions scored all 71 units with zero participant or organizer non-scores.

## T2

Submission `947121` and new monthly-trend image submission `950513` both show primary normalized score `1.0135` (CodaBench leaderboard `-1.0135`, where the lower normalized value is better). Every one of the 71 displayed per-unit scores is identical to four decimals. The new image's trend branch activated on four of the 103 public practice cards; none of those four appears among the 71 scored cards. Therefore this Development submission did not evaluate the proposed monthly-trend change. The 104/104 local gate result established admissibility only.

The scored-unit identifiers and displayed scores are in `t2-codabench-scored-950513-20260928.csv`. Their published card shapes are 57 daily level and 14 daily log-return, with no monthly card. Any next candidate must change the general daily path and be tested on its own merits without using cross-unit target values.

## T3

The original image's submission `948335` scored `26124.5133` events/s; four-worker batch image submission `950514` scored `29447.2373`, an increase of `3322.7239` events/s (`12.7188%`). All 71 units scored. The six batch units improved by `28677.1727` to `59316.8812` events/s each; their displayed mean went from `22149.8412` to `63688.5991`. Among 65 single-market units, 13 improved and 52 declined; their displayed mean went from `26491.4061` to `26286.4962` (`-0.7735%`). The batch worker change did not alter the single-market code path, so that single-unit difference cannot by itself establish a code regression. CodaBench Development timing and the local shared Docker host are not dedicated Final timing evidence.

The full platform per-unit paired values are in `t3-codabench-paired-948335-950514-20260928.csv`. They are measured values, not target outputs, and should guide profiling rather than per-unit hardcoded behavior.
