# QFBench coding agent

A single-agent generate/execute/repair implementation for Agenthon 2026 Track 1.
See [MODEL-ACCESS.md](MODEL-ACCESS.md) for the current House API contract and access status.

### Local E1 experiment (October 9, 2026)

`AGENT_E1_SEMANTIC` defaults to `0`. This experiment is disabled in production and
does not change the image submitted as Development run `972771`. Set it to `1`
only for an explicit local experiment. After the core solver publishes structurally
valid deliverables, E1 requires at least 155 seconds of remaining work time and two
remaining request slots. It can generate one independent checker through the House
route, then make at most one repair request. The same checker is reused on the repair.
The existing 360-second solve cap and 25-request allowance remain unchanged.

Checks must quote an explicit instruction requirement and compute from runtime
inputs. The checker receives no solver source, cannot write the candidate, and runs
with the existing network, process, ctypes and sealed-input restrictions. An invalid
checker, timeout, insufficient budget or unsuccessful repair keeps the original
candidate. Small original deliverables (up to 8 MiB total, subject to output-space
checks) are preserved before optional work. A private supervisor pipe authenticates
the recovery snapshot; an output marker alone cannot turn a timeout into success.
E1 supervision requires an exclusive CLI process and is disabled if the supervisor
already has children. Do not invoke it in a host that starts unrelated subprocesses
concurrently. The checker's separate result pipe isolates stdout; it is not a
hostile-code boundary or an unforgeable proof that the mathematics is correct.

The checker is model-generated and can be wrong. Its result is not an official
domain verdict. Validation so far uses scripted synthetic responses; it establishes
engineering behavior, not real House quality or a competition score gain. See
`../project-evidence/t1-semantic-e1-20261009/` and
`../verification/t1-semantic-20261009.json` for exact executed and pending tests.

The competition entrypoint remains:

```text
solve --task-dir /input --out /app/output
```

**Current evidence:** API and synthetic Docker-to-official-verifier tests pass under the
published Development restrictions, including non-root execution and the 64 MiB file limit.
The actual House service, audited proxy, GPU and real finance accuracy remain unverified.
See [RUNTIME-TEST-RESULTS.md](RUNTIME-TEST-RESULTS.md). The released control was
submitted as Development run `972771`; its current official result is unavailable.

The original diagnostic skeleton is saved in Git tag `diagnostic-baseline`; the current `solve`
requires model settings and fails clearly if they are missing. It no longer creates diagnostic.json.
Do not keep rerunning the old diagnostic-only commands expecting a finance solution.

## Build

Use PowerShell and Docker Desktop with Linux containers. From the project root, build the
organizer's unchanged base if it is not already available:

```powershell
Set-Location C:\Users\felix\Desktop\Agenthon_2026\track1-coding-public
docker build -t finance-bench-sandbox:latest -f docker/sandbox.Dockerfile .
Set-Location ../qfbench-agent
docker build --build-arg BASE_IMAGE=finance-bench-sandbox:latest -t qfbench-agent:dev .
docker build -t qfbench-harness:dev -f Dockerfile.harness .
```

The agent inherits numpy, pandas, scipy, pyarrow, scikit-learn and other numerical libraries
from the official sandbox. No extra client dependency is needed. Exact runtime package versions
are recorded in `/app/runtime-versions.txt`. Development defaults to
`finance-bench-sandbox:latest`; the release builder requires an immutable
`registry/repository@sha256:...` base reference and targets `linux/amd64`. All dependencies
install at build time. Nothing is installed or downloaded while solving.

## Tests you can run now without model access

From qfbench-agent:

```powershell
$projectPath = (Get-Location).Path
docker run --rm --network=none --read-only --user=65534:65534 --cap-drop=ALL --security-opt=no-new-privileges --pids-limit=256 --tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64m --ulimit=nofile=1024:1024 --ulimit=nproc=256:256 --ulimit=fsize=67108864:67108864 --cpus=16 --memory=128g --memory-swap=128g -e PYTHONPATH=/workspace --mount "type=bind,source=$projectPath,target=/workspace,readonly" --entrypoint python qfbench-agent:dev -m unittest discover -s /workspace/tests -v
..\track1-coding-public\.venv\Scripts\python.exe tools/selftest.py
```

The first command tests the client, repair loop, input preservation, output validation and
execution restrictions in Linux. The second creates a new synthetic CSV task under artifacts,
starts a scripted mock server on a temporary **internal** Docker network, executes the actual
agent image, and runs the unchanged official verifier offline. It deletes the helper container
and network afterward and preserves logs, output, verdict and summary under artifacts/selftest-*.
The mock has no intelligence and contains no public finance answers. It is excluded from the
submitted image along with all tests and development tools.

## Real model access and evaluation

Obtain these from organizers: the route origin (scheme/host/port without a path), MODEL_NAME,
MODEL_TOKEN and development network/proxy configuration. The client appends
`/v1/chat/completions` and sends Bearer authentication, temperature 0, QFBENCH_SEED and max_tokens.
Compatibility with the actual house endpoint still needs a real request. Do not substitute
a vendor endpoint or invent a model identifier. Only the injected House bearer is read;
no credential is embedded in the image or passed to generated code.

Set MODEL_ENDPOINT, MODEL_NAME, MODEL_TOKEN and proxy variables as directed by organizers.
Set QFBENCH_SEED for reproducible experiments. Then run, from this folder:

```powershell
# QFBENCH_DEV_NETWORK must name the development network configured per organizer instructions.
# Setting a variable does not create the audited proxy/network.
if (-not $env:MODEL_ENDPOINT -or -not $env:MODEL_NAME -or -not $env:MODEL_TOKEN -or -not $env:QFBENCH_DEV_NETWORK) { throw 'Configure organizer endpoint, model, bearer and development network first.' }
$env:QFBENCH_NETWORK = 'restricted'
$env:QFBENCH_SEED = '0'
..\track1-coding-public\.venv\Scripts\python.exe tools/evaluate.py --unit t1-EXAMPLE-bs-greeks-pde --network $env:QFBENCH_DEV_NETWORK
```

To run selected tasks, repeat `--unit <directory-name>`. To run the public suite once each:

```powershell
..\track1-coding-public\.venv\Scripts\python.exe tools/evaluate.py --all --network $env:QFBENCH_DEV_NETWORK
```

The suite consumes model time/budget. Start with the exemplar, then several different formats
and categories. The evaluator stages inputs without checks/reference directories, uses a fresh
output and container per task, enforces card CPU/memory/agent timeout, then launches the official
verifier offline. Only that verifier receives the complete unit at `/input`, checker files at
`/tests`, task data at `/app/data`, and the limited top-level `/app/<file>` data mounts used by
legacy public checkers. It pins each run to the inspected local image ID and records failures in
the denominator. It does not provide GPU access to this CPU/inference agent. It is a local
development launcher, not a replacement implementation of competition scoring or an official
submission runner.

Results are under artifacts/eval-*/:

- environment.json: image IDs, model, public commit, task count.
- results.jsonl and summary.json: per-task result and one-run success rate.
- each task: staged input, agent.log, output/.agent/run.json, verifier.log and verdict.json.
- model usage appears in the agent report; reservations are retained for failed requests or
  responses without usage statistics. The organizer's proxy remains the authoritative meter.

An agent exit of 0 means declared files exist and pass basic format checks. The status is
`completed_unverified`, deliberately not "task solved". Only the official verifier establishes
financial correctness. A nonzero agent exit, timeout or verifier failure stays a failed attempt.

## Manual invocation and standalone verification

Use a fresh output directory each time. For model solves, pass organizer configuration and a
reachable restricted network; `--network=none` cannot reach a model. The exact image invocation is:

```powershell
$publicPath = (Resolve-Path ../track1-coding-public).Path
$taskPath = Join-Path $publicPath 'units/t1-EXAMPLE-bs-greeks-pde'
$runId = Get-Date -Format yyyyMMdd-HHmmss
New-Item -ItemType Directory -Force "artifacts/manual-$runId" | Out-Null
$outPath = (Resolve-Path "artifacts/manual-$runId").Path
docker run --rm --read-only --cap-drop=ALL --security-opt=no-new-privileges --network $env:QFBENCH_DEV_NETWORK -e MODEL_ENDPOINT -e MODEL_NAME -e MODEL_TOKEN -e HTTP_PROXY -e HTTPS_PROXY -e NO_PROXY -e QFBENCH_SEED -e QFBENCH_NETWORK --mount "type=bind,source=$taskPath,target=/input,readonly" --mount "type=bind,source=$outPath,target=/app/output" --mount "type=bind,source=$outPath,target=/output" qfbench-agent:dev solve --task-dir /input --out /app/output
docker run --rm --network=none --mount "type=bind,source=$publicPath,target=/public,readonly" --mount "type=bind,source=$outPath,target=/app/output,readonly" qfbench-harness:dev smoke units/t1-EXAMPLE-bs-greeks-pde /app/output --track coding
```

Prefer evaluate.py for routine runs: it removes checks from agent input and enforces task limits.
The manual mounts above reproduce the historical structural command; the task reader excludes
checks and the worker blocks ordinary Python access, but the evaluator supplies stronger separation.
From Linux, local `python -m agent solve --task-dir <path> --out <path>` uses the same CLI;
generated code execution is intentionally refused on native Windows. Use the image.

Native Windows `qfbench2 smoke` in toolkit 2.3.1 fails POSIX manifest checks. The pinned CLI
also lacks the README's `--agent-image` option and does not generate a mock solve. The Linux
verifier image and our separate launcher work around local development issues without patching
the public repository or shared scoring code. Confirm the official submission path with organizers.

## Implementation

```text
agent/
  cli.py, __main__.py    competition argument parsing and exit status
  task_reader.py        required instruction/card, bounded data previews, no checks
  model_client.py       organizer HTTP client, timeouts, retries, token accounting
  prompts.py            task-independent code-generation instructions
  solver.py             bounded generate/execute/repair loop
  execution.py          Linux child process, deadline, process-group cleanup, bounded logs
  worker.py             generated Python execution guardrails and seed initialization
  workspace.py          isolated attempts, safe paths, format checks, deliverable publication
tools/
  evaluate.py           local one-run-per-task Docker launcher
  verify.py             serialize the unchanged official verifier's verdict
  selftest.py           full synthetic integration test
  mock_endpoint.py      scripted test endpoint (never shipped)
  submission.py         seal/validate actual submission metadata (never submits)
tests/
  test_agent.py, mock_server.py
```

The card's `[agent].timeout_sec` sets the overall deadline; no universal 1800-second timeout
is assumed. A watchdog bounds the whole solve even if a model server trickles bytes. Each
execution gets at most 40% of the task budget and must fit within the remaining time.
Default is three generate/execute attempts, configurable with AGENT_MAX_ATTEMPTS (1–5).
Each model request gets at most one retry for transient network/429/5xx failures. The client
tracks cumulative tokens or reservations as evidence, with no per-unit token cap,
and allows at most 25 sends (including retries) and 4,000 output tokens per call. Explicit 401/403
pre-admission refusals restore reservations; uncertain failures keep them. Generated code
cannot make model calls. The evaluator rejects complete output trees larger than 64 MiB.

All generated code, logs and scratch files stay under --out/.agent/attempt-N; only validated
declared deliverables are copied to --out. Old attempt files remain for debugging. JSON and
Parquet readability and Python syntax are checked, but semantic checks remain task-specific.
There are no task-ID branches, stored finance answers, multiple agents, model weights or LoRA.

Worker audit hooks catch normal Python attempts to write outside its output, read checks/card
metadata, create reward files, launch subprocesses or use networking. They are **guardrails, not
a security sandbox for hostile Python/native code**. The Docker read-only filesystem, staged
input, network policy and resource limits are the actual containment. This version is limited
to single-process Python solutions; native binaries, arbitrary subprocess workflows and legacy
task file layouts may require later improvements. Do not expose host Docker sockets or secrets
to generated code. Model prompts and reports omit card canaries; the full card is not sent.

## Submission preparation

The image carries `qfbench2.interface_version=2.0`. Before finalization, get a real exemplar pass,
measure varied tasks, confirm architecture/registry requirements, and test an official development
submission. Freeze the image by its published registry digest and retain the source/configuration.

This repository is licensed under Apache-2.0; see LICENSE. Use the organizer's actual submission.json example and fill in real competition/team IDs,
phase, image registry/repository/digest, category api, license and model disclosures. Do not
invent IDs, training cutoffs, revisions, unknown fields, or a public registry destination.
This helper delegates alias and pack to the official toolkit. Pack derives team_id, seals the
descriptor and writes both submission.json and team-claim.json. It asks for the Team Key without
echo; noninteractive use requires a private key-only file via --team-key-file. Never pass the key
itself on a command line. Run from the isolated Linux toolkit environment for key-file permissions:

```powershell
python tools/submission.py alias --team-number N
python tools/submission.py pack --descriptor submission-body.json --team-number N --out submission.zip
```

For descriptor-only validation, `python tools/submission.py submission-body.json --out submission.json`
retains the official C5 parser and sealing behavior. The old `--zip` option is rejected before
writing files because it omitted the team claim. Use official per-track fixtures and genuine
metadata for real preparation; synthetic fixture digests do not identify a published image.

For the actual release image, first obtain the organizer's immutable sandbox digest. The release
builder refuses mutable tags, builds `linux/amd64`, checks the required interface label, and writes
an evidence record. It does not push an image or contact a registry:

```powershell
..\track1-coding-public\.venv\Scripts\python.exe tools/build_release.py --base registry.example/organizer/finance-bench-sandbox@sha256:<64-lowercase-hex> --image qfbench-agent:release --record artifacts/release-build.json
```

No image push, account registration, external message or final submission is performed by these
tools. MODEL_ENDPOINT access, measured finance results and actual submission identifiers remain
needed before final submission. The historical initial setup record is SETUP-RESULTS.md.

## Official toolkit environment

The sibling track1-coding-public/.venv already holds Python 3.13 and editable Track 1 tooling.
To recreate it on Windows with Python 3.13 installed:

```powershell
Set-Location ../track1-coding-public
& "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install "qfbench2-common @ git+https://github.com/Agenthon-2026/Agenthon2026-public.git@v2.4.3#subdirectory=common"
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\qfbench2.exe --help
```

Official references: [Track 1 README](https://github.com/Agenthon-2026/track1-coding-public),
[model and packaging contract](https://github.com/Agenthon-2026/track1-coding-public/blob/main/baselines/README.md),
[submission contract](https://github.com/Agenthon-2026/track1-coding-public/blob/main/SUBMISSION_CLI.md).
