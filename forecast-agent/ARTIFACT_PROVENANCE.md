# Artifact provenance

The participant implementation contains numerical code and lexical retrieval of supplied text. No pretrained neural checkpoints, stored unit answers, fitted model file, precomputed retrieval index or external market dataset is packaged. Each run estimates its mean/covariance or empirical innovation distribution only from that unit's supplied panel observations, after rejecting observations after the supplied as-of date. The model is not fitted across units. Text retrieval does not change default samples.

Contract dependencies were initially checked against official Track2 source4a14af5c48500091de1db54d972b3f6c7bd3ad18. The current candidate binds helper bytes to8799596ae68a6ec26f749f46054c39ad2b89512e and toolkit v2.4.4 (68bc878a740eb0040e70ddf0feab98405dca1d6d). The numerical contract helpers are unchanged across those refs. The candidate copies only grid, limits, failures, horizons and targets modules, with the upstream MIT license; a minimal initializer avoids importing the scorer. No official generator, scoring implementation or task data is copied into the image. Original host development versions remain in project-evidence/c02-freeze-t234.txt and c02-linux-freeze-t234.txt; Docker dependencies are separately pinned in the participant Dockerfile.

The run rationale records fitting interval, transformation, window, seed, mean/covariance treatment and horizon mapping. Fixed defaults have not been selected against hidden targets. Subsequent calibration/backtesting must keep its own cutoff and fold provenance. The historical local prototype `forecast-agent:local-20260925` passed all 104 public-input contracts; its local ID was sha256:faa9e5cb19d4964f42dd97c4a9622f05c6507dea37656eb15eef672842bf8f6c. The monthly-trend image was subsequently published and submitted to Development as 950513 (image digest sha256:461ad3fb69becc4084744879ba8f9bf53ae7e4de64f49785f14fae3a217aa8a3). That submission scored 1.0135 over 71 daily cards, unchanged from 947121 because its monthly branch covered none of them. The current M0 daily control is unsubmitted, and the online selector remains an inactive local experiment. Any future packaged learned artifacts must be disclosed according to the official artifact policy.

The optional daily reference control and unit-local online selector are pure
numerical code, not packaged fitted parameters or an answer table. They use
only the current unit's cutoff-bounded panel at inference time. Their method
was reviewed against the current public Track 2 source ref
28a6cae9674f69e63a07a19165a9217e85eacfff and its published M0 guide.
The selector's historical folds, labels and fitting data all come from the
current unit's input and precede that unit's as-of date. No selected parameter
or historical answer from one unit is carried into another. The pinball
calculation follows the public `qfbench2_track_forecasting.tail.tail_pinball`
formula from that ref and is checked against it on synthetic samples.
An exploratory global proxy selection from later revised snapshots was
discarded as ineligible for earlier cards; none of its selected settings are
used in the participant image.

The separate `Dockerfile.m0-control` builds from the admitted September 25
participant image, copies only forecast, joint-model and M0 reference-model
source, and enables the published unit-id CRC32 seed on daily cards. It does
not package the online selector, data, answer files or derived parameters.
The 71 scored public inputs have one matching panel file per target asset;
the exact-draw limitation from unpublished sealed cell order remains. The
published M0 specification reviewed for this profile is Track 2 source ref
28a6cae9674f69e63a07a19165a9217e85eacfff, `docs/M0-BASELINE.md`.

The unsubmitted M0 control image ID is
sha256:273582dca87206f49ea5edde63016a227f8c7374b386b2ab9d78b548d2708ff1.
Its strict local Docker gate completed all 71 scored Development card inputs
under 2 CPU, 1 GiB, non-root, read-only, no-network and 64 MiB output limits.
Each output passed current public g0-g3 and matched the corresponding host
forecast by draw/asset/horizon within a maximum absolute difference of
2.842170943040401e-14. The report retains one failed host-scorer attempt
caused by missing `jsonschema`; resuming under pinned Python 3.13 passed that
unit and all others. The audit used no sealed realized outcomes or reference
scales, so this evidence measures admissibility and draw parity, not the
image's official normalized score. See
`../project-evidence/t2-m0-control-docker-review-20260928.md`.
