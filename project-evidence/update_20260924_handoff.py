"""Update status from completed evidence; no acceptance or production inference."""
from datetime import datetime, timezone
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
E=ROOT/'project-evidence'
probe=json.loads((E/'t4-container-probe-strict.json').read_text())
assert probe['passed']
path=ROOT/'planning/task-status.json'
status=json.loads(path.read_text(encoding='utf-8'))
for task in status['tasks']:
    if task['id']=='C05':
        task.update(status='active',note='T1 historical and T4 current strict local probes pass. T4 four-entity synthetic end-to-end passes. T2/T3 full candidate readiness, platform/GPU/high-memory capacity remain unverified; no publication.')
        task['evidence'] += [p for p in ['project-evidence/t4-container-probe-strict.json','project-evidence/c05-t4-review.md','project-evidence/t4-container-build-context.json'] if p not in task['evidence']]
    if task['id']=='T3-03':
        task.update(status='active',note='Fixed 3-scenario serial local CPU/memory profile underway;3 timed repeats and1 CPU-profile each,1 additional Python-allocation run. No Final timing claim.')
status['execution_state'].update(updated_at=datetime.now(timezone.utc).isoformat(),next_task='T3-03 profiling; T2 candidate image and full-roster contracts; real House/judge remain external')
path.write_text(json.dumps(status,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
handoff=ROOT/'planning/IMPLEMENTATION-HANDOFF.md'
old=handoff.read_text(encoding='utf-8')
heading='## Latest local continuation — 2026-09-24\n'
assert heading not in old
content='''
## Latest local continuation — 2026-09-24

T3-02 local semantic coverage is complete:65/65 singles (27 earlier +38 resumed)
and6/6 batches, same baseline image and unchanged relevant official code. The
merged coverage, provenance and limits are in t3-02-completed-summary.json.
There is no single new65-scenario official report, speedup or Final timing claim.
The withdrawn exemplar's OOM and interrupted historical report remain preserved.
T3-03 now runs fixed local profiling separately from unprofiled timing.

T2-05A implements cutoff-aware lexical retrieval and evidence-bound numerical
event adjustments. Default predictions remain unchanged and House calls remain0.
Final Windows/Linux tests each pass23 tests plus15 subtests. All104 local text
directories pass after a Unicode filename fix; initial4 failures are retained.
See t2-05a-review.md and t2-05a-run-summary.json. No text quality gain is claimed.

T1 current inventory is86 units, all staging/contracts verified with zero solve
attempts. Historical87-unit evidence remains separate. See t1-roster-20260924/.

T4-05A readiness correctly refuses a missing organizer judge spec/cache.
42 official stub tests pass,1 real-transformers dependency test skipped.
Production judge model IDs/revisions are unknown, not filled from diagnostic
defaults. Real House and production faithfulness remain blocked-external.

C05: analysis-agent:local-20260924 builds from a source-only whitelist and passes
the strict local probe plus four-entity synthetic analyze flow. Image ID:
sha256:69e4d2efc1c39e5901851cd4c40ff69fa0c3239ca45b929f0542bccaeea1cbae.
See c05-t4-review.md and t4-container-probe-strict.json. No registry push,
competition upload, official proxy/GPU test or large-memory capacity is claimed.

All five official repositories were fetched again before new implementation
tasks; unchanged refs are recorded in20260924-continuation-source-recheck.json.
Remote ChatGPT review remains pending; locally recording evidence is not review.
The original42-task plan is not complete. Later dated updates supersede earlier
status statements below without deleting historical failures or limitations.

'''
handoff.write_text(old.replace('# Implementation handoff\n','# Implementation handoff\n'+content,1),encoding='utf-8')
runtime=E/'runtime-readiness.md'
runtime.write_text(runtime.read_text(encoding='utf-8')+'\n## T4 local candidate — 2026-09-24\n\n'+(E/'c05-t4-review.md').read_text().split('## Executive summary\n\n',1)[1],encoding='utf-8')
plan=ROOT/'planning/AGENTHON-FOUR-TRACK-EXECUTION-PLAN.md'
text=plan.read_text(encoding='utf-8')
text=text.replace('当前公开套件包含 65 个单场景、6 个 batch unit 和 exemplar，共 72 units。具体 roster 在执行时重新枚举。','2026-09-24 当前公开套件包含 65 个单场景和 6 个 batch unit，共 71 units。官方已撤回 exemplar 并移至 examples；历史 72-unit 记录保留。具体 roster 在执行时重新枚举。')
text=text.replace('当前快照有 87 个公开 units；执行时重新枚举当前 unit','2026-09-24 当前快照有 86 个公开 units（官方撤回 t1-polars-api-migration；历史 87-unit 清单保留）；执行时重新枚举当前 unit')
text=text.replace('T3 AGENTS.md 仍写 v2.4.3，与 README/SUBMISSION_CLI 冲突；在 source-conflicts 中记录并核对当前官方说明，受影响步骤未解决前不猜版本。','T3 AGENTS.md 的旧 v2.4.3 冲突已由上游修正为 v2.4.4（2026-09-24 复核）；历史冲突记录保留。')
plan.write_text(text,encoding='utf-8')
print('Updated handoff, runtime evidence and task state')
