## Executive summary (read this first)

This is the participant-side Track 4 analysis pipeline. It retrieves frozen evidence, requests grouped House predictions, grounds exact quotations and validates complete answers for classification, regression and ranking. It does not score predictions or access resolved outcomes. The local `analyze` entry point and candidate image pass synthetic interface/runtime checks. Real House quality, production faithfulness and organizer-platform execution remain unverified.

Call `build_answer(task, predictions)` and then `write_answer(task, answer, path)`.
Each internal prediction uses official row fields plus `unit` when the entity or
target declares `unit`/`units`. The value acknowledges the same unit for the point
and both interval bounds; incompatible or missing units fail without conversion.
Entity-level units override target-level units. Unstructured unit descriptions
are not inferred. Unit metadata is internal and removed from the emitted answer.

Rows are emitted in task roster order. Missing, extra and repeated entities fail;
no row is silently dropped. All task types require 90% intervals and citation
fields. Ranking uses numeric point forecasts; optional ranks must cover 1..N,
and tied point forecasts remain legal. Numeric strings, booleans, NaN and infinity
are rejected. Serialization validates first and replaces the output atomically.

`validate_answer` checks output structure and task binding. It cannot recover
the internal unit acknowledgement from already serialized JSON, verify corpus
offsets or dates, or establish production faithfulness. Those belong to the
assembly, retrieval and official judge stages respectively.

The latest local validation uses Python 3.13, toolkit 2.6.0 and Track 4 scorer 5.2.2. Tests use synthetic
predictions and an external pinned evaluator; set `QFBENCH_T4_SOURCE` to its source
directory and, if stored separately, `QFBENCH_T4_UNITS` to its LF-preserving units.
The old September 23 evaluator is not a substitute for the current contract.
The current test default is the LF-preserving snapshot at
`.validation/t4-e2-scorer-5.2.2-20261001` in the workspace root.
`build_answer(..., submitted_reasons=...)` accepts the optional 1–3 reason block
and validates it with the current official schema. Omitting it remains valid.

### Traceable retrieval (T4-03)

`RetrievalIndex.load(corpus_dir, cutoff, manifest_path=...)` loads only declared
corpus documents, verifies SHA-256 bytes and rejects linked or malformed inputs.
The default manifest is the unit-level manifest, falling back to the corpus
manifest when absent. Undated, invalid-date and post-cutoff documents are excluded
before indexing, with reasons retained in `excluded`. No external corpus is used.

Search uses deterministic BM25 over overlapping character windows. Only tokens
are case-folded; text and character offsets are unchanged. Flat text takes
precedence; span arrays use the official single-space join convention.
`validate_span` checks identity, bounds, cutoff and optional verbatim quote.
`ground_quote` refuses ambiguous occurrences unless a narrower range identifies
one. No match returns no hits, and missing evidence remains a downstream failure
to handle rather than a fabricated citation. Matching words or quotes does not
establish support for a prediction; production faithfulness still needs the judge.

The index retains citation ownership from the trusted manifest. Pipeline search
and final quotation grounding both require the citing entity to appear in the
document's `entity_ids`, or the document to be marked `shared: true`. Missing and
empty ownership lists grant no citation permission; document-body metadata does
not override the manifest. Unfiltered `search` remains available for offline
retrieval comparisons and is not used by the prediction pipeline.

Scorer 5.2.2 applies false-claim penalties and an optional reasoning bonus. The
old 80% prediction-entailment admission rule is not the current scoring contract.
Schema and manifest checks do not verify neural contradiction or reason quality.

The reader assumes the organizer's read-only input mount. It rejects existing
symlinks/junctions and uses no-follow file opens where available; it is not a
general sandbox for concurrently mutated, hostile directory trees.

### Prediction integration (T4-04 local implementation)

From this directory, run `python -m analysis_agent.cli analyze --task PATH --corpus DIR --out PATH`.
The worker loads the existing `../qfbench-agent/agent/model_client.py` transport.
That workspace dependency must be explicitly bundled when an image is built.
Configure only organizer-provided MODEL_ENDPOINT, MODEL_NAME and MODEL_TOKEN.

Three entities share each initial prompt. Each call uses exactly one transport
attempt; initial calls cover all groups before one targeted repair per group.
Valid rows are retained, and only unresolved rows enter repair prompts. The
shared actual-send ledger caps all calls at 25, including refusals and retries;
each response is capped at 4,000 output tokens. The legacy `supported` flag is
ignored. A claim is a relevant exact quote, capped conservatively at 350 UTF-8
bytes, and its entity ownership is rechecked. Model offsets are never trusted.
The production judge's token count is an external check, not a local guarantee.

`fallback.py` recognizes two explicit historical table structures: bid-to-cover
ratio histories owned by one entity, and CPI MoM-percent tables with an exact
entity-name column. It uses the median of the latest three values and the observed
rolling-median residual range for a nominal 90% band. This short-history band has
no coverage guarantee and is an emergency estimate, not a measured quality gain.
On current public inputs this provides 18 rows across 2 of 11 complete units.
Other shapes use conservative generic emergency estimates when every row has
an eligible exact quote. These predictions are uncalibrated. The adapters use
target semantics, units, and actual data; they contain no unit IDs,
entity-specific answers, resolved labels, or future observations.

For an explicit revision classifier that requests a point forecast of the revised
level, the recovery estimate uses the finite `latest_precutoff_estimate` in its
declared original units. It requires a valid reference month and the ordering
`reference <= latest vintage <= cutoff < resolving release`. An ambiguous or
change-only request keeps the previous generic path. The nominal 90% band is
`anchor +/- max(1, abs(anchor)/2)`; no coverage calibration is claimed. The
direction default remains unchanged, and valid House rows still replace it.

The October 9 source candidate passes 143 focused/existing tests (one skip),
all 11 public units in the released Linux runtime with a read-only source overlay,
and official scorer 5.2.2 smoke checks. Ten public outputs are unchanged; only
12 macro-revision points and intervals change. A separate practice diagnosis
finds better raw interval scores on all 10 rows with exact archived outcomes;
two rows lack exact outcomes and are excluded. This is in-sample evidence, not
an official composite or a Final performance claim. See `ARTIFACT_PROVENANCE.md`.

A complete input-derived answer is saved atomically before House enhancement.
Each subsequent complete answer is checkpointed; valid model rows replace their
fallbacks. A missing or permanently failing House route can preserve a supported
baseline. Without enough evidence for every row, failure remains explicit.

The parent process kills its worker on timeout (default 580s, maximum 600s).
At the normal timeout, model work ends 25 seconds early for final validation and
writing. The parent revalidates a complete checkpoint, including corpus integrity
and exact entity-bound citations, before preserving it after a worker failure or
timeout; a slow response cannot replace it with an unavailable record. When no
complete validated answer exists, failure returns exit 2 and a structured
`notes.status=unavailable` result. That record
contains no fabricated predictions and deliberately does not satisfy the answer
schema; it is never admissible. Only a complete validated answer returns exit0.
Raw exceptions and model responses are not logged. Live quality remains blocked
until House access and the official production judge are available.

### Local exit-2 investigation (T4-E2-v1)

The local E2 candidate accepts a single complete CRLF JSON fence and, when sends
are scarce, fills missing rows before spending calls on complete history rows.
It adds opt-in stage and count diagnostics without model text or entity IDs.
Unsupported or ungrounded answers still fail closed. The paired frozen permfix
and E2 runs cover 43 common fault cases, 11 public shapes, external scorer 5.2.2
smoke, and the existing cross-UID output gate. The candidate also exercises the
preexisting optional-reasons rollback. Linux tests pass 191/191.
See [evidence and source boundaries](../project-evidence/t14-experiments/T4-E2-v1/README.md).
These synthetic checks do not identify the seven official exit-2 causes; no
configured House credentials were available and the candidate remains local.

### Optional submitted reasons (A3, disabled by default)

Pass `--reasons` to the CLI, or `enable_reasons=True` to `analyze`, to opt in.
Candidate v1 does not select this feature. Once all predictions are fixed and a
complete answer is saved, the agent may spend one remaining actual send on 1–3
reasons. It never changes prediction rows during this step. It first supplies the
actual cited facts and then broader retrieved context, with at most 32,000 bytes
of excerpt text. Task values needed for the explanation must appear in its premise.

The separate `reasons.py` module grounds model quotes back to corpus offsets and
checks the current schema, named entities, ownership, dates, normalized duplicate
content, deny list, URI masking, and compact UTF-8 size limits. Empty prose and
uncited reasons are also rejected. It keeps a 10% margin when checking whether
the projected answer is small enough to start the optional request. A failure,
insufficient budget/time, or oversized answer leaves the saved base unchanged;
the whole optional block is omitted. Opt-in diagnostics record only an allowlisted
reason status. A permanently unavailable route does not receive an optional retry.

External tests compare these deterministic checks with the official
`check_submitted_reasons` helper from the pinned evaluator. Neither these checks
nor synthetic reasons establish factual entailment, causal strength, or agreement
between free-form prose and numeric predictions. Those quality properties remain
subject to independent review and the official reasoning judge.

### Local candidate image

`Dockerfile` expects a dedicated build context with `analysis_agent/*.py` and the
shared transport copied as `model_client.py`. Do not use the entire workspace
as build context. The recorded context is `.validation/t4-build-20260924` at the
workspace root; `project-evidence/t4-container-build-context.json` binds source
hashes. From the workspace root:

```powershell
docker build --platform linux/amd64 -t analysis-agent:local-20260924 .validation/t4-build-20260924
```

The image defaults to UID 65534 and accepts `analyze --task /input/task.json
--corpus /input/corpus --out /output/answer.json`. Its entrypoint consumes the
leading verb. The strict local runtime probe and synthetic four-entity answer
are recorded in `project-evidence/t4-container-probe-strict.json`. The local
image ID is `sha256:69e4d2efc1c39e5901851cd4c40ff69fa0c3239ca45b929f0542bccaeea1cbae`;
it is not a registry digest or a published release. Organizer credentials belong
in injected environment variables, never in the build context.
