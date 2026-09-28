# Local release validation

The release candidate is the immutable local image
`sha256:c5724d34e2a8c951e4645b7c9aed1b95e3eabf1bd3dc24c0195055700cdbd45d`.
This is a local Docker image ID, not a published registry manifest digest.
The measured formatter-cache image and the WORKDIR /tmp runtime derivative
have separate identities and separate evidence.

`project-evidence/run_t3_release.py` runs the frozen 71-unit official roster
(65 singles, six batches) under Linux. It stages only manifest-checked scenario
inputs from `.validation/t3-release-source-20260925`, pinned to official commit
1504b37b8472f7f949d4b8f80b7c8b2b71ab0e43. No reference data is mounted into the
candidate. The removed exemplar is excluded. All five upstream repositories
were checked before implementation; see `t3-06-source-recheck.json`.

The local runner enforces read-only root/input, UID65534, network none,
capability dropping, no-new-privileges, 4 CPU / 16 GiB quota with no swap,
pid/nproc256, nofile1024, per-file64MiB, /tmp64MiB noexec and a 1,800-second
deadline. The actual host has about 7.5 GiB memory. A 16 GiB quota does not
demonstrate 16 GiB capacity. No GPU is attached.

The official bounded timer measures host elapsed time and cgroup memory; event
counts come from Parquet footers and must match reported counts. The official
output sanitizer and developer card verifier check every retained output.
The total output cap is checked at64MiB. Card declarations determine required
message ledgers; all emitted ledgers are retained. Raw outputs, sanitized
outputs, actual file hashes, full verdicts and host telemetry stay in the run
directory. Logs are bounded; failed attempts are preserved.

The largest public single and a four-scenario batch repeat twice, with no
warmup discarded. Their actual trace/ledger bytes and host event counts must
repeat. Measured timing/resource fields remain honest and are not forced to
equal across runs. Other units run once. This local convention is frozen before
execution and does not claim to implement the unsettled official Final repeat
contract.

The completed2026-09-25 execution passes all71 units in73 runs; see
`project-evidence/t3-06-run-summary.json`. The first invocation used
`--max-units 2` for the heavy preflight, then resumed the remaining69 units.
For an interrupted run, use the existing environment from the workspace:

```powershell
wsl -d Ubuntu --cd /mnt/c/Users/felix/Desktop/Agenthon_2026 .validation/linux-t234/bin/python project-evidence/run_t3_release.py --resume
```

Resume verifies the unchanged plan and image ID, skips successful units only,
and creates a new numbered attempt for a failed unit. The runner stops on the
first error so the cause is resolved before consuming the rest of the sweep.
`report.json` is the current execution evidence; `completed=false` is not a
release pass. A completed report still describes local, non-rankable developer
validation, not organizer timing or official Final acceptance. Publication and
competition upload require their own approved destination and real identity.
