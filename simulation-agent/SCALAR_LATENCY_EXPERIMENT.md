# T3 scalar-latency experiment

## Recorded result (2026-09-28)

The experimental derivative was built from the exact frozen unlocked-queue
parent with a two-file whitelist context. Its immutable local image ID is
`sha256:a12731a118178e10469608ec92172bc7db4b799a2b4ad8cd2b1acf939dd053c9`.
Inside the image, NumPy is 1.26.4, the patched `config.py` SHA-256 is
`fa223ce9a3f37333a625ee1a0bc176980a7610dcfc4fcdb500eb903745c2844c`,
and all five focused equivalence tests passed under a read-only, nonroot,
no-network container run.

The predeclared four-unit alternating screen completed 24/24 strict runs,
with every stable parquet hash and event count equal to the frozen control.
Across those four units, the equal-unit mean of measured per-unit median
Development self-reported rates was **12.53% higher** for the scalar image;
the analogous Docker start-to-exit rate was **5.56% higher**. One of the four
units had a lower Docker rate, so the screen is only positive preliminary
evidence, not a 71-unit score estimate. The independent strict semantic gate
then passed **71/71** scored units (65 singles and six batches), again with
exact stable parquet hash and event-count equality on every unit. See
`project-evidence/t3-scalar-latency-screen-20260928/{plan,records,summary}.json`
and `project-evidence/t3-scalar-latency-semantic-20260928/{plan,records,summary}.json`.

Full 71-unit paired timing against the unlocked-queue parent subsequently
completed **426/426** strict runs. The all-71 self-reported Development proxy
improved by **9.33%** and the Docker start-to-exit proxy by **5.50%**.
See `SCALAR_LATENCY_PAIRED.md` and
`../project-evidence/t3-scalar-latency-review-20260929.md` for the complete
results and Docker-outage caveat. No image has been published or submitted
from this experiment, and no official score is measured.

The frozen unlocked-queue image is the control. The experimental image changes
only its installed ABIDES scalar latency clip. The patch refuses to run unless
`/opt/abides_fork/config.py` has SHA-256
`5cd0ecfd25b999ff44f961111c84b5da718be2a548cfd2b6ea725bcedf8cccbd`.
The frozen control image ID is
`sha256:94bda24a509530e2bf2dfb0f079a0c052b29422c87bc62163aac1701dfe194bc`.
The frozen unlocked-queue 71-unit paired timing finished before this
experiment began, and the host was checked free before its screen and gate.

The current [public T3 rules](https://github.com/Agenthon-2026/track3-simulation-public/blob/main/README.md)
allow internal simulator modifications; they require the same schema, causal
behavior and deterministic outputs. The score is an equal-unit average across
65 singles and six batches. This four-unit screen is only a filter, not a
score estimate.

The following commands reproduce the build and screen from the repository root
in the retained Linux T3 validation environment:

```bash
test "$(docker image inspect simulation-agent:unlocked-queue-dev-20260928 --format '{{.Id}}')" = \
  sha256:94bda24a509530e2bf2dfb0f079a0c052b29422c87bc62163aac1701dfe194bc
docker build --network none --pull=false \
  -f simulation-agent/Dockerfile.unlocked-queue-scalar-clip \
  -t simulation-agent:unlocked-queue-scalar-dev-20260928 simulation-agent
candidate_id="$(docker image inspect simulation-agent:unlocked-queue-scalar-dev-20260928 --format '{{.Id}}')"
docker run --rm --network none --read-only --user 65534:65534 \
  --mount "type=bind,src=$PWD/simulation-agent,dst=/opt/scalar-test,readonly" \
  --entrypoint python "$candidate_id" /opt/scalar-test/test_scalar_latency_clip.py -v
python simulation-agent/run_scalar_latency_experiment.py \
  --stage screen --candidate-id "$candidate_id"
```

The screen uses the current public developer verifier under the strict
4-CPU/16-GiB, no-network, read-only, nonroot, 300-second launcher. Each of the
four chosen units runs once per image for warm-up and twice per image in
alternating order. Every accepted trace must match the frozen unlocked-queue
control's stable parquet hashes and event count. Results are written to
`project-evidence/t3-scalar-latency-screen-20260928/`; compare both reported
Development rate and Docker start-to-exit rate, including per-unit paired
differences. Advance only if the measured benefit is convincing against
variation; no gain is assumed from this patch alone.

The completed semantic gate was run with:

```bash
python simulation-agent/run_scalar_latency_experiment.py \
  --stage semantic --candidate-id "$candidate_id"
```

The semantic stage runs all 71 scored units once, checks the current verifier,
and requires exact stable parquet hash and event-count equality to the frozen
unlocked-queue 71-unit gate. It is resume-safe and stops on a failed attempt.
Its records go to `project-evidence/t3-scalar-latency-semantic-20260928/`.
The complete paired timing supports promoting this derivative as a local
Development candidate; publication and official scoring remain separate.
Neither this plan nor its script uploads or submits an image.
