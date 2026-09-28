# Organizer model access — updated 2026-09-26 UTC

Current contract: `MODEL_ENDPOINT` is an origin without a path. Call
`POST $MODEL_ENDPOINT/v1/chat/completions`, use injected `MODEL_NAME`, and send
`Authorization: Bearer $MODEL_TOKEN` through the injected proxy. House model identity:
`nvidia/nemotron-3-super-120b-a12b`, snapshot `rl-030326-fp8`, training cutoff `unpublished`.
Limit: 25 admitted requests per unit and 4,000 output tokens per call; retries can count.
The earlier cumulative input/output token allowance has been withdrawn with no replacement.
Context limits apply separately to each request. Track 1 Development uploads: one per day,
23 total under the 2026-09-25 rule update. Held and cancelled uploads consume an attempt;
an upload the platform marks `Failed` does not.

The current [Development runtime guide](https://github.com/Agenthon-2026/Agenthon2026-public/blob/main/docs/DEVELOPMENT-RUNTIME.md)
still states participant access is held pending deployment and verification. That is distinct
from the published API contract, now implemented and locally tested in this project.
See [House API](https://github.com/Agenthon-2026/Agenthon2026-public/blob/main/docs/HOUSE-MODEL.md)
and [local verification](RUNTIME-TEST-RESULTS.md). Use the official toolkit's team alias and
team-claim pack commands for first-upload preparation; the legacy one-file ZIP helper does
not supply that claim.

## Historical access check — 2026-09-07 (superseded where different above)

The latest organizer reply found on the specific access issue is dated 2026-09-04.
It says the house endpoint was **not deployed**, the audited proxy was deny-all, and
model-quality testing was unavailable. This is not a missing local installation step.
The issue was still open and had no newer reply when checked.

Primary source:
[Shared-toolkit issue #3, organizer reply](https://github.com/Agenthon-2026/Agenthon2026-public/issues/3#issuecomment-5534947498).

## What is known

- Planned base model: `nvidia/nemotron-3-super-120b-a12b`.
- This is not yet a confirmed runtime MODEL_NAME/pinned revision to put in configuration.
- Endpoint/model ID, training cutoff and rate-limit information are to be published together.
- Per-unit token budget: 1,000,000 input and 100,000 output tokens.
- Development submission limits stated in that reply: 20 scored submissions per team,
  at most 5 per day, 12-hour execution limit per submission.
- The Track 1 competition existed but was unpublished at the time of the reply.
- The CodaBench team_id must not be inferred from the website Team ID; organizers said
  they would publish the mapping with the competition page.

## Local runner clarification

The organizer confirmed that qfbench2 smoke only verifies existing output and has no
--agent-image option. Their supported local procedure is to run your image, then execute
the supplied checks offline in finance-bench-sandbox with the unit at /input and the same
output bound at both /app/output and /output. Check reward.json afterward: checks/test.sh
can itself exit 0 even when it records a failing reward. Only the checker writes rewards.

[Track 1 issue #5, organizer instructions](https://github.com/Agenthon-2026/track1-coding-public/issues/5#issuecomment-5553555197).

Our separate agent-run and verifier-run approach is consistent with the clarification.
There is no reason to wait for a new --agent-image release before local development.
Actual house-model testing still requires the endpoint to be deployed and access instructions.

## Where to look next

- [Model-access issue #3](https://github.com/Agenthon-2026/Agenthon2026-public/issues/3).
- [Track 1 submission-route issue #6](https://github.com/Agenthon-2026/track1-coding-public/issues/6)
  was open with no replies when checked.
- [Weekly release announcement](https://github.com/Agenthon-2026/track1-coding-public/issues/3):
  organizers state Wednesdays by 23:59 AoE. This is a release cadence, not a promise that
  endpoint deployment will occur in a particular release.

The linked shared repo's starter-packs/track1/SUBMISSION-DESCRIPTOR.md and
RUNTIME-ENVIRONMENT.md describe injected variables and metadata requirements but provide
no working development endpoint or onboarding command. A reference checkout is stored in
artifacts/organizer-docs; installed toolkit versions and public practice tasks were not changed.
