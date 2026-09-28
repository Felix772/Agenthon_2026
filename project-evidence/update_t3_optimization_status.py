"""Persist measured milestones and pending acceptance conditions."""
from datetime import datetime, timezone
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
E=ROOT/'project-evidence'
path=ROOT/'planning/task-status.json'
status=json.loads(path.read_text(encoding='utf-8'))
for task in status['tasks']:
    if task['id']=='T3-03':
        task.update(status='active',note='Local profiling complete:13 fixed runs over3 public scenarios, all trace/ledger hashes repeat. Three measured optimization candidates documented. Remote review pending.')
        task['evidence']=['project-evidence/t3-03-review.md','project-evidence/t3-03-profile-20260924/profile-summary.json','project-evidence/t3-03-profile-20260924/plan.json']
    elif task['id']=='T3-04':
        task.update(status='active',note='Bounded diagnostic timestamp cache candidate built. Three formatter tests cover20,000 random values plus edges;3/3 small official scenarios pass. Paired timing and full regression must pass before acceptance.')
        task['evidence']=['project-evidence/t3-04-formatter-tests.log','project-evidence/t3-04-build.log','project-evidence/t3-04-small-regression/report.json']
    elif task['id']=='T2-06':
        task.update(status='active',note='Statistical branch full-roster harness prepared:103 practice units plus1 exemplar; no hidden outcomes. Execution and container contract acceptance pending.')
        task['evidence']=['project-evidence/run_t2_roster_contracts.py']
status['as_of']='2026-09-25'
status['execution_state'].update(updated_at=datetime.now(timezone.utc).isoformat(),next_task='T3 paired timing then full regression; T2 image and full-roster contracts',reason='Docker recovered after user-authorized restart; T1 current rule changes consulted. No production, publication or submission claimed.')
path.write_text(json.dumps(status,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
handoff=ROOT/'planning/IMPLEMENTATION-HANDOFF.md'
old=handoff.read_text(encoding='utf-8')
title='## Current execution — 2026-09-25\n'
assert title not in old
new='''
## Current execution — 2026-09-25

T3-03 profiling is locally complete (13 diagnostic runs, three scenarios,
identical trace/ledger bytes across repeats). See t3-03-review.md. The T3-04
candidate only caches repeated diagnostic timestamp formatting; image local ID
sha256:5ffbe1d8ff6cbe723e7cfd2cc34b345d5d24b27940e13cb06f85e5a265467cb7.
Three formatter tests including20,000 random inputs and3/3 small official
regressions pass. Paired timing/full regression are still required. No optimized
candidate is accepted yet. Baseline coverage remains65+6; do not transfer it to
the changed image.

T2 now honors QFBENCH_SEED unless an explicit --seed overrides it. Full Windows
tests pass24+15subtests; the five-helper participant subset passes20+15subtests.
The11-file source-only image context is prepared in .validation/t2-build-20260925,
with hashes/license in t2-container-build-context.json. Image build/runtime and
the103-practice-unit contract report are still pending at this checkpoint.

Docker recovery required temporary endpoint backups and an explicitly authorized
restart of only its WSL instance. No image/volume reset occurred. Details are in
20260925-docker-recovery.md. Current T1 rule changes and refs are recorded in
20260925-rules-update.md. Evidence iterations20–25 were recorded through c2c;
remote review is still pending and the last accepted iteration remains12.

'''
handoff.write_text(old.replace('# Implementation handoff\n','# Implementation handoff\n'+new,1),encoding='utf-8')
print('Status and handoff updated')
