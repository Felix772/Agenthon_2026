# Simulation candidate

Experimental T3 CPU candidate derived from the verified official ABIDES baseline.
The only simulator-source change caches diagnostic timestamp strings by whole
second. The cache holds at most 4,096 strings; it does not retain orders or RNG
state. Unusual inputs keep the original formatting and exception behavior.
Both `simulate` and `simulate-batch` entrypoints remain available.

The required base image is `track3-abides-baseline:agenthon-local-20260922`, local
ID `sha256:3d534ebbd778dfdea64d2ed5c2844430208465cd670e530893779ad06ae9bf22`.
The build-time script refuses a changed formatter implementation. Build from a
whitelist context containing this Dockerfile and patch_timestamp_cache.py only.
Tests, profiles and public task data do not belong in the image.

The measured image `simulation-agent:format-cache-20260924` has local ID
`sha256:5ffbe1d8ff6cbe723e7cfd2cc34b345d5d24b27940e13cb06f85e5a265467cb7`.
It passes formatter equivalence, 65 standard single-scenario regressions,
six batch developer verifiers and local paired timing/memory gates. Five paired
runs per representative scenario show median elapsed-time reductions of 8.6%,
15.6% and 10.0%. These are local measurements with an extra /work tmpfs; they
are not official Final timing. See `../project-evidence/t3-04-review.md`.

`Dockerfile.runtime` adds `WORKDIR /tmp` to that image, fixing ABIDES ./log
creation under a read-only root. The runtime image
`simulation-agent:runtime-20260925` has local ID
`sha256:c5724d34e2a8c951e4645b7c9aed1b95e3eabf1bd3dc24c0195055700cdbd45d`.
Six representative strict single runs and two four-scenario batch runs pass
without an extra /work tmpfs. Full card-gate validation on this exact image now
passes65 single and6 batch units (73 total runs with two units repeated).
Actual output hashes, ledgers, host event counts, memory and output totals are
retained. This establishes local semantic/runtime acceptance, not official
timing. See `../project-evidence/t3-06-review.md` and `c05-t3-review.md`.
No image is published.
