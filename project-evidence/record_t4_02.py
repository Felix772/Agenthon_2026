"""Capture exact source, interface and task-roster provenance for T4-02."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / 'project-evidence'
repos = ['Agenthon2026-public'] + [f'track{i}-{name}-public' for i, name in
                                 [(1, 'coding'), (2, 'forecasting'), (3, 'simulation'), (4, 'analysis')]]
records = []
for repo in repos:
    ref = subprocess.check_output(['git', '-c', f'safe.directory={ROOT / repo}', '-C', str(ROOT / repo),
                                   'rev-parse', 'origin/main'], text=True).strip()
    records.append({'repository': repo, 'ref': ref, 'checked_at': datetime.now(timezone.utc).isoformat(),
                    'review': 'Fresh fetch; complete Markdown changes reviewed against previously consulted docs; applicable T4 task/schema/alignment and instructions read.',
                    'documentation_diff': f'project-evidence/20260923-{repo}-docs.diff'})
(E / '20260923-source-recheck.json').write_text(json.dumps(records, indent=2)+'\n', encoding='utf-8')
source = ROOT / '.validation/track4-20260923'
roster = []
for path in sorted((source / 'units').glob('*/task.json')):
    task = json.loads(path.read_text(encoding='utf-8'))
    roster.append({'task_id': task['task_id'], 'target_type': task['target']['type'],
                   'entity_count': len(task['entities']), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
files = [p for p in (ROOT / 'analysis-agent').rglob('*')
         if p.is_file() and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts]
summary = {'task': 'T4-02', 'local_result': 'passed', 'review': 'pending',
           'windows_tests_passed': 35, 'linux_tests_passed': 35,
           'public_task_shape_count': len(roster), 'public_task_shapes': roster,
           'source_files': [{'path': str(p.relative_to(ROOT)), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(files)],
           'production_verified': False, 'forecast_quality_measured': False,
           'limitations': ['No corpus/date/span grounding yet', 'No House prediction', 'No production NLI judge',
                          'No CLI or participant image yet', 'Units absent from structured task fields remain uncertified']}
for platform in ('windows', 'linux'):
    log = (E / f't4-02-tests-{platform}.log').read_text(encoding='utf-8')
    assert '35 passed' in log, platform
(E / 't4-02-run-summary.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
path = ROOT / 'planning/task-status.json'
status = json.loads(path.read_text(encoding='utf-8'))
for task in status['tasks']:
    if task['id'] == 'T4-02':
        task.update(status='active', note='Local interface complete:35 tests pass on Windows and Linux; official alignment cross-check and all public task shapes tested with synthetic rows. ChatGPT review pending; no prediction-quality or grounding claim.',
                    evidence=['project-evidence/t4-02-run-summary.json', 'project-evidence/t4-02-review.md', 'project-evidence/t4-02-tests-windows.log', 'project-evidence/t4-02-tests-linux.log'])
    if task['id'] == 'T3-02':
        task['note'] += ' 2026-09-23 upstream withdraws exemplar from scored roster; it is no longer an acceptance requirement. Remaining38/65 single scenes still need completion; avoid concurrent heavy runs.'
    if task['id'] == 'T1-02':
        task['note'] += ' 2026-09-23 current roster is86 after withdrawal of t1-polars-api-migration; existing87-task inventory and42-task sample remain historical, not silently rebased.'
status['as_of'] = '2026-09-23'
status['execution_state'].update(next_task='T4-03 traceable retrieval; resume T3 remaining38 sequentially without withdrawn exemplar',
                               reason='T4-02 local interface passed; current five-repository docs reviewed. Pending review13–17 is distinct from local completion.',
                               updated_at=datetime.now(timezone.utc).isoformat())
path.write_text(json.dumps(status, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps({'public_tasks': len(roster), 'types': dict(Counter(r['target_type'] for r in roster)), 'local_tests': 35}))
