# Track 3 four-worker batch candidate: complete local public-roster review

The selected no-flag, four-worker batch image passed the current public verifier on the complete 71-unit roster. Its local 71-unit mean of per-unit median measured rates was 6,696.927 events/s versus 6,119.058 for the baseline, a gain of 577.869 events/s (+9.444%). This is a nonrankable same-host public proxy, not an official Final result or a general single-simulation speedup. Retain the image as a local batch candidate for dedicated validation; do not publish or submit it on this evidence alone.

## Frozen comparison and completeness

- [September 28 plan](t3-parallel-batch4-full-roster-20260928/plan.json): 65 single units and six batch units; baseline `sha256:c5724d34e2a8c951e4645b7c9aed1b95e3eabf1bd3dc24c0195055700cdbd45d`; selected `sha256:df5e1b9d96abf06947a33cf499c3b4feed271fa31f6439de8ce49ff98a834489`; current Track 3 ref `f910de231209ebbca060efee47fe2c41aabe4bce`; verifier archive SHA-256 `ba591213a97659d11e8e9617075cee410518dfbee119c283eb2d47fecd6c121c`.
- [Records](t3-parallel-batch4-full-roster-20260928/records.json) contain 438 distinct passing scheduled keys: 12 selected-image no-flag batch preflight attempts, 142 full-roster warm-ups, and 284 measured runs (71 units × two images × two repeats). Every passing row has a current-verifier G0–G3 admission, expected image ID, zero container exit code, and a rate equal to its event count divided by Docker container time. There were no participant failures.
- The 439th record is the retained selected `t3-st04` warm-up attempt that failed before simulation with Docker exit 125 because an older interrupted run had left a same-name container. The original attempt and records were backed up with hashes, the failure was classified as infrastructure, and attempt 2 passed before measured timing. See the [retry audit](t3-parallel-batch4-full-roster-20260928/infrastructure-retry-20260928.md). No failed attempt contributes a timing value.
- All 71 units have the same stable output hashes and event counts across baseline and selected images, warm-up and measured repeats. The six batch preflight outputs also agree. The [summary](t3-parallel-batch4-full-roster-20260928/summary.json) has `batch_smoke_passed`, `full_warmups_passed`, `timing_complete`, and `semantic_gate` all true; each variant resolves all 71 units.
- Maximum observed container time was 201.760 seconds for baseline and 194.303 seconds for selected, below the 300-second deadline. Peak host memory was 3,313,045,504 and 3,337,662,464 bytes respectively; maximum output was 45,151,753 bytes. No run exceeded its resource or output gate.

## Throughput result

| Frozen unit set | Baseline events/s | Selected events/s | Change |
| --- | ---: | ---: | ---: |
| All 71, equal unit weight | 6,119.058 | 6,696.927 | +9.444% |
| 65 single simulations | 6,127.931 | 6,133.359 | +0.089% |
| Six batches | 6,022.940 | 12,802.251 | +112.558% |

The paired difference of the equal-weight 71-unit means is +577.869 events/s. The predeclared 10,000-resample **unit-paired local** bootstrap interval is [+135.107, +1,120.972] events/s. It is not the official family-clustered interval. All six batches improved; 32 of 65 single units improved and 33 slowed. Seventeen single units slowed by more than 5%; the largest relative regression was about 25.45% on `t3-eq001-pareto-latency-tail`. About 99.14% of the aggregate gain comes from the six batch units. This supports a batch-specific optimization, while the single-unit measurements do not establish a speedup.

The source recipe is hash-guarded and [locally rebuildable](t3-parallel-batch4-build-20260927.json) from retained workspace files and the pinned parent image. The workspace root and `simulation-agent` are not Git repositories, and the selected image is local and unpublished; it is not a committed release artifact.

## Comparability and decision

The [post-timing node fingerprint](t3-parallel-batch4-full-roster-20260928/node-fingerprint-after-timing.json) matches the frozen plan on all five comparability fields: CPU model/count, memory bytes, GPU model/count. The earlier September 27 partial run had a 4 KiB different reported memory size and was **not pooled** with this run. The runner checked its frozen plan at each invocation/resume; it did not re-collect the fingerprint after every container, so the post-run match does not prove continuous stability.

This run took place in Docker Desktop WSL with about 8 GiB of reported host memory, below the official 16 GiB container cap. A separate user-owned Freqtrade container was active and a Claude app container was restarting at one [co-residency snapshot](t3-parallel-batch4-full-roster-20260928/host-co-residency-20260928.json). These conditions do not satisfy the competition's dedicated, otherwise-idle fixed-SKU Final timing protocol; changing background load can affect local paired timings. The current public verifier and exact-output checks provide correctness evidence, while the throughput estimate remains directional.

Keep the four-worker image as the locally selected batch candidate and the original single-simulation behavior unchanged. The next promotion gate is a fresh, dedicated-host timing run under the current official protocol, followed by packaging/versioning the exact build inputs. No image was pushed or submitted as part of this local validation.
