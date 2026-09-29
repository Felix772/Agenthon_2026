# T3 scalar paired benchmark: Docker interruption and one-time recovery

The retained `records.json` is an intact prefix of 150 passed runs in the frozen
426-run schedule: all six runs for each of the first 25 units. The next key is
`t3-gb-horizon-240s|unlocked|0`, a discarded warm-up for unit 26. Its attempt
`1/attempt.json` still says `running`; `1/raw/` is empty and `1/container.cid`
exists. Docker reports that exact container exited with code 255 at
2026-09-29T14:04:31Z after the daemon interruption. There is no completed
participant output to score or append to `records.json`.

After Docker recovered, the retained Linux validation environment reported the
same CPU model/count, memory, GPU name/count, kernel, Docker version and full
node fingerprint as the frozen plan. Both image tags still resolve to their
planned immutable image IDs. The runner's `--recover-interrupted-151` option
requires the exact 150-run passed prefix and inspects attempt 1 and its exited
container before it can launch this key as attempt 2. Attempt 1 and the first
150 records remain untouched. Any other attempt collision still stops the run.

Co-residency changed across the interruption. The frozen plan recorded
`quantlab-donchian-paper-freqtrade-1` running and `claude-app-1` restarting;
after Docker recovered, the former was exited (255) and only `claude-app-1`
appeared in `docker ps`, still restarting. This local host is not the official
otherwise-idle timing instance. The first 25 units form complete within-unit
pairs, and unit 26 begins with a fresh warm-up, but the phase split and changing
host load limit interpretation of the aggregate timing. This experiment is a
nonrankable Development/Final proxy, not a CodaBench result.

The official shared and Track 1–4 repository HEADs were rechecked on
2026-09-29 and remained at the frozen refs in `plan.json`. The shared and track
READMEs, agent and contribution rules, Track 3 submission contract, throughput,
stable-repeat and Development runtime guidance were consulted before changing
the local runner. No benchmark run, image publication or submission was made as
part of this recovery preparation.
