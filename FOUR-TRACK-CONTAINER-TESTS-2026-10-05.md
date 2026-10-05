# Four-track container checks — 2026-10-05

The local Docker Desktop daemon ran the participant images with the published
Development isolation settings: UID 65534, a read-only root and `/input`, no
network except a synthetic local House route for T1 and T4, dropped capabilities,
no new privileges, a 64 MiB no-exec `/tmp`, and the PID, file-descriptor and
per-file limits. T1, T2 and T3 used their published CPU and memory quotas. The
T4 public-shape harness used a tighter 2 CPU / 1 GiB quota. The host has about
7.47 GiB of physical memory, so these runs do not prove behavior on a host with
the full 16 or 128 GiB capacity.

| Track | Image | Local result |
| --- | --- | --- |
| T1 | New current-source test image `sha256:aff9e85f8cd6430e0562b921cc32a95bdef97655e85d61150f9881121d69adc5` | 78/78 applicable image tests pass. The real container entrypoint completes one restricted synthetic solve; its 15 output-tree entries are safe and readable by another UID. That harness uses the earlier local shared-tree checker. |
| T2 | Submitted monthly-trend image `sha256:461ad3fb69becc4084744879ba8f9bf53ae7e4de64f49785f14fae3a217aa8a3` | One daily and one monthly public card exit 0 and pass public g0–g3 with `qfbench2-common 2.6.0`. No realized outcomes were supplied. |
| T3 | Frozen scalar candidate `sha256:a12731a118178e10469608ec92172bc7db4b799a2b4ad8cd2b1acf939dd053c9` | One single and one four-scenario batch unit exit 0. The Linux developer verifier with toolkit 2.6.0 accepts both through g0–g3. Event counts were independently read from Parquet footers and the elapsed time was measured by the host; these rates are not ranked scores. |
| T4 | Frozen E2 candidate `sha256:0caea21f7c34364741b11c1613377866c44ca830ec7f5425963f9ae1a94c4b69` | 11/11 public shapes exit 0. The Linux public scorer 5.2.2 with toolkit 2.6.0 accepts all 11; each score is null because no real outcome or production judge was supplied. |

The exact toolkit 2.6.0 installation resolves to official commit
`bd01548e34d21fd660d88fd06157078fb25ece4e`. The test scripts, commands,
verdicts and failed setup attempts are retained locally under
`project-evidence/formal-container-20261005/`, which the repository ignores as
run evidence. No test container was left running. The Ubuntu Docker socket's
temporary group change was restored to `root:root 660`.

These checks do not clear the release gates: T1 still lacks a real House-model
Rule 9 solve and the planned public domain-checker gate; T2's proposed quality
change did not meet the 2% promotion threshold; T3's exact image still lacks a
complete redistribution notice and SBOM audit; and T4 has no real House or
production-judge quality result. No image was pushed or platform submission made.

Before the next code change, recheck the current README and applicable
AGENTS.md, CONTRIBUTING.md, SUBMISSION_CLI.md, and linked rules in all five
official repositories: [shared toolkit](https://github.com/Agenthon-2026/Agenthon2026-public),
[Track 1](https://github.com/Agenthon-2026/track1-coding-public),
[Track 2](https://github.com/Agenthon-2026/track2-forecasting-public),
[Track 3](https://github.com/Agenthon-2026/track3-simulation-public), and
[Track 4](https://github.com/Agenthon-2026/track4-analysis-public).
