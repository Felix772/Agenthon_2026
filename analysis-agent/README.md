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

Use Python 3.13 and toolkit 2.4.4. Tests use synthetic predictions, and invoke
the separately pinned official Track 4 alignment code as an independent check.

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

The reader assumes the organizer's read-only input mount. It rejects existing
symlinks/junctions and uses no-follow file opens where available; it is not a
general sandbox for concurrently mutated, hostile directory trees.

### Prediction integration (T4-04 local implementation)

From this directory, run `python -m analysis_agent.cli analyze --task PATH --corpus DIR --out PATH`.
The worker loads the existing `../qfbench-agent/agent/model_client.py` transport.
That workspace dependency must be explicitly bundled when an image is built.
Configure only organizer-provided MODEL_ENDPOINT, MODEL_NAME and MODEL_TOKEN.

Three entities share each prompt. The shared client enforces25 requests and4000
output tokens per call, including conservative retry accounting. Invalid model
JSON, roster, units or quotes trigger one repair with broader retrieval. Quotes
must occur in the excerpts actually supplied; model offsets are never trusted.
A model's support assertion is not a production entailment verdict.

The parent process kills its worker on timeout (default580s, maximum600s).
Missing configuration, no evidence, invalid predictions after repair or timeout
return exit2 and a structured `notes.status=unavailable` result. That record
contains no fabricated predictions and deliberately does not satisfy the answer
schema; it is never admissible. Only a complete validated answer returns exit0.
Raw exceptions and model responses are not logged. Live quality remains blocked
until House access and the official production judge are available.

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
