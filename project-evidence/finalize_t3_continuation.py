"""Preserve the full denominator for an intentionally interrupted local run."""
import json
from pathlib import Path
import re
ROOT = Path(__file__).resolve().parents[1]
E = ROOT / 'project-evidence'
source = ROOT / '.validation/track3-current-update'
passed = re.findall(r'\[PASS\] ([\w-]+)', (E / 't3-02-single-regression.log').read_text())
roster = []
for path in sorted((source / 'regression_suite/scenarios').rglob('*.json')):
    task = json.loads(path.read_text())
    if 'scenario_id' in task:
        sid = task['scenario_id']
        roster.append({'scenario_id': sid, 'source': str(path.relative_to(ROOT)),
                       'status': 'passed' if sid in passed else 'incomplete'})
batch = json.loads((E / 't3-02-batches-root/report.json').read_text())
report = {'full_regression_complete': False, 'all_pass': False,
          'single_denominator': len(roster), 'single_passed': len(passed),
          'single_incomplete': len(roster)-len(passed), 'single_roster': roster,
          'batch_denominator': 6, 'batch_passed': sum(r['status']=='passed' for r in batch['records']),
          'semantic_boundary_tests_passed': 59,
          'exemplar': {'passed': False, 'exit_code': 137, 'docker_oom_confirmed': True},
          'interruption': 'After exemplar Docker OOM and WSL connection failure, terminated this run parent/worker and its remaining container to avoid further local resource pressure. Other containers untouched.',
          'production_verified': False, 'semantic_pass': None, 'local_performance_pass': None,
          'review': 'pending', 'actual_host_memory_gib_approx': 7.5,
          'limitations': ['Concurrency and other local workloads confound timing',
                          '16 GiB per-container quota exceeds actual host RAM',
                          '65-scenario suite has no completed official aggregate report',
                          'Original batch permission failures preserved; six reruns with root evaluator pass unchanged official baseline and verifier']}
assert len(roster) == 65, len(roster)
assert set(passed) <= {r['scenario_id'] for r in roster}
(E / 't3-02-run-summary.json').write_text(json.dumps(report, indent=2) + '\n')
status_path = ROOT / 'planning/task-status.json'
status = json.loads(status_path.read_text(encoding='utf-8'))
for task in status['tasks']:
    if task['id'] == 'T3-02':
        task['note'] = f"Partial local result: {len(passed)}/65 single scenarios pass; remainder incomplete after host resource pressure. Six batches pass root-evaluator rerun; 59 semantic boundary tests pass. Exemplar exits137 with Docker OOM. Full semantic acceptance and profiling blocked pending adequate isolated resources; no performance claim."
        task['evidence'] += ['project-evidence/t3-02-run-summary.json', 'project-evidence/t3-02-batches-root/report.json', 'project-evidence/t3-02-retained-verification.json', 'project-evidence/t3-02-docker-oom.jsonl']
status['execution_state'].update(status='local-increment-complete-review-pending', next_task='Review local increments13–16; T3 needs adequate isolated resources; independent T4-02 remains available', reason='House budget correction and T4 baseline completed locally. T3 full semantic run incomplete after confirmed exemplar OOM. ChatGPT connection remains unverified; authorized evidence recording is separate from review.')
status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='single_roster'}, indent=2))
