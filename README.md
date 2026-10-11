# Agenthon 2026 participant code

This repository contains the current working source for the four Agenthon 2026 tracks. It is a source repository, not an official submission or score report.

| Directory | Purpose |
| --- | --- |
| `qfbench-agent/` | Track 1 coding agent and tests |
| `forecast-agent/` | Track 2 forecasting agent and local evaluation |
| `simulation-agent/` | Track 3 simulator packaging, batch candidate, and tests |
| `analysis-agent/` | Track 4 analysis agent and tests |
| `project-evidence/*.py` | Local audit and benchmark scripts; generated results stay local |

The organizer's public repositories are the authoritative source for rules and contracts: [shared toolkit](https://github.com/Agenthon-2026/Agenthon2026-public), [Track 1](https://github.com/Agenthon-2026/track1-coding-public), [Track 2](https://github.com/Agenthon-2026/track2-forecasting-public), [Track 3](https://github.com/Agenthon-2026/track3-simulation-public), and [Track 4](https://github.com/Agenthon-2026/track4-analysis-public). Consult the current versions of all five before changing code, as required by [AGENTS.md](AGENTS.md).

Organizer checkouts, local validation environments, benchmark outputs, and submission archives are excluded from this repository. Distinguish current source, locally tested candidate images and uploaded images using the versioned records in `verification/`; they are not interchangeable.

## Current review — 2026-10-11

All five upstream repositories and their current submission documentation were
rechecked. The new limits and current validation evidence are recorded in
`verification/four-track-20261011.json`. Track 4 now uses scorer 5.3.0. Track 2
Final ranking uses future UST/G10 tasks with data through October 23, and its
image must remain available through November 13. Track 1 has acknowledged
upstream instruction/checker inconsistencies awaiting organizer fixes.

Official results for submissions 972771, 972754, 972772 and 972773 remain unknown.
Local contract and container checks do not establish a score or rank.
