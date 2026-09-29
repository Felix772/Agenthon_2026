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

`--method gaussian|bootstrap|reference-walk|online-ensemble|m0-control|contract-probe` chooses generation. Gaussian is the default; `contract-probe` retains the degenerate interface fixture. `--window 252` caps aligned fitting observations; at least 30 are required. `--shrinkage 0.1` is an unoptimized fixed choice. `--drift` enables historical mean changes for level series; cumulative log-return targets always sum the estimated log1p(simple-return) drift. Each draw follows one path shared across horizons. Monthly paths use calendar periods and account for different final available observations across assets. The rationale records fitting dates, transformation, covariance, drift, seed and missing-observation policy.

`reference-walk` is an optional daily-only, 500-draw statistical control inspired
by the publicly documented M0 construction: trailing 300 observations, a
gap-filtered aligned mean and covariance, and a single joint draw grid. It
honors `QFBENCH_SEED` or an explicit `--seed`; it does not read an organizer
reference scale or another unit's panel. `online-ensemble` selects per card
among the admitted incumbent, reference walk and their equal-weight draw pool.
It scores three nonoverlapping historical windows taken only from that card's
input panel; each retains the card's asset and horizon grid. The two older
windows select a candidate that improves by at least 2% and wins both; the
newest window vetoes a candidate that underperforms the incumbent. Fewer
than three usable windows select the incumbent. The evaluator imports the
shared CRPS/variogram code and implements the published Track 2 pinball formula
in a short attributed function checked against the current official helper.
No cross-card model or tuned parameter file is shipped.
The online path is opt-in in source; `Dockerfile.scored-daily-dev` enables it in
an unsubmitted local experiment. All 71 scored daily cards passed the public
g0-g3 contract, but the independent third-fold result for a first-two-fold
proposal was not conclusive: 9 wins, 11 losses, 51 ties; equal-card mean
candidate-minus-incumbent -0.0394, bootstrap 95% interval [-0.1134, 0.0268].
This is insufficient evidence to promote the selector. The frozen
`Dockerfile.m0-control` image intentionally excludes `online_model.py`, so
manual `--method online-ensemble` invocation is unavailable in that image.

`m0-control` is a separate conservative daily profile that follows the
published M0 300-row joint walk and uses `crc32(unit_id) & 0x7fffffff` for its
draw seed, ignoring the harness's sampling seed by design. The
`Dockerfile.m0-control` image activates it only on daily cards while retaining
the existing monthly trend behavior. It copies no online-selection module.
The frozen local image `sha256:273582dca87206f49ea5edde63016a227f8c7374b386b2ab9d78b548d2708ff1`
completed a strict Docker run on all 71 currently scored daily cards: 71/71
passed the current public g0-g3 gates, and the maximum keyed draw difference
from the cutoff-only host audit was `2.842170943040401e-14`. One initial host
scorer attempt lacked `jsonschema`; its failed record remains in the local
report, and a pinned Python 3.13 retry completed the gate. See
`../project-evidence/t2-m0-control-docker-review-20260928.md`. This is an
admissibility and reproducibility result; no official quality score has been
measured for this image. All target assets occur in exactly one staged panel
file, satisfying M0's
first-matching-file rule on this roster. The independently calculated public
worked example matched every draw exactly. The official guide warns that
three of 103 answered public cards have a sealed cell order different from
the usual sorted order; their identities are not published, so exact M0 draw
parity and an official 1.0 score cannot be guaranteed on every scored card.
The [current official M0 specification, §6](https://github.com/Agenthon-2026/track2-forecasting-public/blob/28a6cae9674f69e63a07a19165a9217e85eacfff/docs/M0-BASELINE.md)
also says the Development scoring image may predate the refreshed pinball-tail
scorer. Even exact M0 draw parity would therefore not guarantee a 1.0 score
on the currently deployed Development leaderboard.
Before Final use on unseen cards, review any asset appearing in multiple panel
files: this participant loader combines files, whereas published M0 uses the
first sorted matching file. The current 71-card Development gate does not
establish parity for that unseen layout.

To rebuild this unsubmitted control image from a clean checkout, pull the
public admitted parent at its immutable digest, tag it locally, then build
from this `forecast-agent` directory:

```powershell
docker pull docker.io/felix772/agenthon-2026-t2@sha256:faa9e5cb19d4964f42dd97c4a9622f05c6507dea37656eb15eef672842bf8f6c
docker tag docker.io/felix772/agenthon-2026-t2@sha256:faa9e5cb19d4964f42dd97c4a9622f05c6507dea37656eb15eef672842bf8f6c forecast-agent:local-20260925
$parentId = docker image inspect forecast-agent:local-20260925 --format '{{.Id}}'
if ($parentId -ne 'sha256:faa9e5cb19d4964f42dd97c4a9622f05c6507dea37656eb15eef672842bf8f6c') { throw "Unexpected parent image ID: $parentId" }
docker build --network none --pull=false --platform linux/amd64 -f Dockerfile.m0-control -t forecast-agent:m0-control-20260928 .
```

The parent contains the pinned toolkit, contract helpers and their upstream
MIT license at `/app/licenses/track2-LICENSE`; the control layer copies only
participant source. The historical local build-context command below records
how the original prototype was made and is not needed for this clean rebuild.

An earlier exploratory global proxy used a later revised panel snapshot to
select parameters for historical cards, and its sample shapes overrepresented
single-cell forecasts. Its selection was discarded. Under the official Track 2
artifact policy, model-selection data must have been available by each card's
cutoff; the online selector reads only that card's supplied as-of panel.

`--monthly-trend` is an exploratory, opt-in monthly-level alternative. It starts
from half the median of the latest 12 eligible monthly changes and damps the
additional mean step by 0.8 each future month. It requires a monthly level card,
Gaussian or bootstrap sampling, and no `--drift`. The default forecast does not
enable it. The card-shaped retrospective proxy and its vintage limitations are
recorded in `../project-evidence/t2-card-trend-review-20260927.md`.

`Dockerfile.monthly-trend-dev` is a Development-only experiment based on the
previously admitted image. It sets `AGENTHON_MONTHLY_TREND_AUTO=1`, which turns
the same trend on only for eligible monthly-level Gaussian/bootstrap cards;
daily and return cards retain the incumbent forecast. The ordinary source and
base image defaults remain unchanged. The two-card fixed-vintage local study
does not establish a general quality gain or a Final-ready candidate.

The Gaussian, bootstrap, reference-walk and online profiles default to the
organizer-injected `QFBENCH_SEED`, falling back to0 when absent; an explicit
`--seed` takes precedence. The M0 control instead uses the published CRC32
unit-ID seed. The actual seed is recorded in the rationale. Repeated runs
reproduce the samples; this does not certify general predictive performance.

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
versions and limits. That September 25 prototype was local at the time;
the later monthly-trend image was published and submitted as Development
950513, with the same 1.0135 score as 947121 on the 71 daily scored cards.
The M0 control and online experiment have not been submitted.

From this directory, run `python backtest.py --source-dir ../track2-forecasting-public/units/t2-F3-election-2024-joint --out <new-results-directory>` in the same isolated environment. The harness freezes its plan before evaluation, fits within each chronological fold, and invokes the pinned official T2 raw metric implementation. The 20 annual rates/FX folds are retained without tuning or model selection. Historical revision vintages are uncertified, monthly/log-return performance is not covered, and no official aggregate is defined. See `../project-evidence/t2-04-run-summary.json` for source hashes, per-fold results and repeat evidence.
