# Forecast agent

Independent Track 2 statistical forecaster. The default joint Gaussian walk estimates aligned historical innovations, shrinks covariance 10% toward its diagonal and applies an eigenvalue floor. A vector residual-bootstrap alternative is available. No House model or text reasoning is used. A frozen retrospective daily rates/FX backtest reports official raw metrics; it is not a leaderboard or certified point-in-time result.

Run with the isolated Python 3.13 environment containing toolkit 2.4.4 and the official T2 contract helpers:

```powershell
..\.validation\py313-t234\Scripts\python.exe forecast.py forecast --panels <staged>/panels --text <staged>/text --asof 2024-06-28 --out <run>/forecast.parquet --seed 0
```

The card and optional forecast_spec.json must be beside panels/. Asset/horizon keys remain in card order. Monthly sampling steps use the official explicit-period helper; horizon integers are never converted by a calendar heuristic. Output validation rejects incomplete or duplicate grids, non-finite values, invalid metadata and missing rationale. The official scorer remains authoritative.

Outputs are forecast.parquet, forecast_meta.json and forecast_rationale.md. Existing outputs are not overwritten. Official source checkouts remain unchanged. Text retrieval is implemented below; live event extraction and platform submission remain later tasks. The participant image passes local strict runtime and full-roster contract checks; these establish no prediction accuracy score.

T2-05A now audits the staged corpus_index.json, filters by public-release timestamp,
and records lexical hits and source offsets in the rationale. Default forecasts
remain text-blind: retrieval does not automatically become a predictive adjustment.
`text_events.adjust_samples` accepts explicit evidence-bound event objects for
integration: event_id, asset, horizon, doc_id, span_start/end, quote,
mean_shift_sigma and vol_multiplier. Mean shifts are bounded to0.25 baseline
predictive standard deviations and volatility multipliers to[0.75,1.25] after
aggregation. Empty events preserve samples exactly. These bounds are uncalibrated;
synthetic tests establish interfaces only. Real extraction and paired quality
evaluation remain T2-05B and need House access.

`--method gaussian|bootstrap|contract-probe` chooses generation. Gaussian is the default; `contract-probe` retains the degenerate interface fixture. `--window 252` caps aligned fitting observations; at least 30 are required. `--shrinkage 0.1` is an unoptimized fixed choice. `--drift` enables historical mean changes for level series; cumulative log-return targets always sum the estimated log1p(simple-return) drift. Each draw follows one path shared across horizons. Monthly paths use calendar periods and account for different final available observations across assets. The rationale records fitting dates, transformation, covariance, drift, seed and missing-observation policy.

`--monthly-trend` is an exploratory, opt-in monthly-level alternative. It starts
from half the median of the latest 12 eligible monthly changes and damps the
additional mean step by 0.8 each future month. It requires a monthly level card,
Gaussian or bootstrap sampling, and no `--drift`. The default forecast does not
enable it. The card-shaped retrospective proxy and its vintage limitations are
recorded in `../project-evidence/t2-card-trend-review-20260927.md`.

The default seed is the organizer-injected `QFBENCH_SEED`, falling back to0 when
absent. An explicit `--seed` takes precedence. The actual seed is recorded in the
rationale. Repeated input/seed pairs reproduce the samples; this does not certify
general predictive performance.

The Docker build context at `.validation/t2-build-20260925` contains three
participant modules, five unchanged pinned official contract helpers, a minimal
package initializer, their license and Dockerfile. The initializer deliberately
omits the upstream scorer import. No task data or resolved outcomes are bundled.
Its manifest is `project-evidence/t2-container-build-context.json`. From the root:

```powershell
docker build --platform linux/amd64 -t forecast-agent:local-20260925 .validation/t2-build-20260925
```

The entrypoint accepts the leading `forecast` verb and defaults to UID65534.
The Dockerfile pins the same numerical dependency versions as the current
official Dockerfile and toolkit2.4.4. A build command alone is not runtime or
platform acceptance; consult the dated runtime evidence before selecting it.

The built local image ID is
`sha256:faa9e5cb19d4964f42dd97c4a9622f05c6507dea37656eb15eef672842bf8f6c`.
All 103 practice units plus the separate exemplar pass official g0–g3 gates in
this image. The strict three-case repeated probe passes six runs with identical
bytes. See `../project-evidence/t2-06-review.md` and `c05-t2-review.md` for scope,
versions and limits. No image is published and no submission is uploaded.

From this directory, run `python backtest.py --source-dir ../track2-forecasting-public/units/t2-F3-election-2024-joint --out <new-results-directory>` in the same isolated environment. The harness freezes its plan before evaluation, fits within each chronological fold, and invokes the pinned official T2 raw metric implementation. The 20 annual rates/FX folds are retained without tuning or model selection. Historical revision vintages are uncertified, monthly/log-return performance is not covered, and no official aggregate is defined. See `../project-evidence/t2-04-run-summary.json` for source hashes, per-fold results and repeat evidence.
