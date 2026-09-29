# T2 M0 control: scored-roster Docker gate

The unsubmitted M0 daily control image completed the strict local gate on all
71 cards listed in the Development scored roster. All 71 produced admissible
outputs under the current public g0-g3 scorer, with keyed draw parity to the
cutoff-only host audit. This is not an official platform quality score.

- Frozen image: `sha256:273582dca87206f49ea5edde63016a227f8c7374b386b2ab9d78b548d2708ff1`;
  admitted parent: `sha256:faa9e5cb19d4964f42dd97c4a9622f05c6507dea37656eb15eef672842bf8f6c`.
- Official Track 2 source: `28a6cae9674f69e63a07a19165a9217e85eacfff`;
  pinned shared toolkit v2.4.4 package-tree SHA256:
  `938bad75902bb578fa57d08b6799cc0f0bde7d77cbbfa4d818c87bc69a24d143`.
  The exact public scorer source hash is frozen in the local gate report.
- Roster SHA256: `81cd50edc7606c04ce7da1b3256312e1cd39f6aa7a57abf52b68d789c2c5a530`.
  The runner used the manifest-verified final retry input when one existed.
- Runtime gate: 2 CPU, 1 GiB, UID/GID 65534, read-only root, no network,
  dropped capabilities, 64 MiB noexec temporary filesystem and 64 MiB output
  ceiling. Result: 71/71 passed; maximum keyed draw absolute error versus
  host M0 audit `2.842170943040401e-14`; largest output 110,495 bytes.
- The report has 72 attempt records. Its first attempt for
  `t2-F1-ai-mom-2024` reached the host public scorer but could not run schema
  validation because that host Python lacked `jsonschema`. The failure remains
  recorded. Resuming with the pinned Python 3.13 environment passed the unit
  and completed all 71; this was a host validation-environment issue, not a
  scored forecast rejection.

Detailed local evidence is in
`t2-m0-control-scored-docker-gate-v2-20260928/report.json` (SHA256
`f989487fe00d699c9cb56325248b3cfe62b93b6f0ffe1605f9da804b06d00482`).
No sealed outcomes or reference scales were read. The organizer's normalized
score remains unmeasured for this candidate. The
[current official M0 specification, §6](https://github.com/Agenthon-2026/track2-forecasting-public/blob/28a6cae9674f69e63a07a19165a9217e85eacfff/docs/M0-BASELINE.md)
warns that the Development scoring image may predate the refreshed pinball-tail
scorer; even exact M0 draw parity does not guarantee a 1.0 score on the
currently deployed leaderboard. Published M0 also notes a few sealed cell-order
exceptions, and unseen cards with an asset in multiple panel files need a
separate parity review before Final use. The frozen M0 control image excludes
`online_model.py`, so manual `--method online-ensemble` invocation is
intentionally unavailable in that image.
