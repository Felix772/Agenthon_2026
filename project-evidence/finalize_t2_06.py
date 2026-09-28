"""Summarize completed evidence without rerunning or replacing raw test records."""
from collections import Counter
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / 'project-evidence'
image = json.loads((E / 't2-06-image-contracts-20260925/report.json').read_text())
host = json.loads((E / 't2-06-host-contracts-20260925/report.json').read_text())
probe = json.loads((E / 't2-container-probe-20260925/report.json').read_text())
assert image['all_contracts_passed'] and host['all_contracts_passed'] and probe['passed']
assert image['image_id'] == probe['image_id']
rows = image['records']
assert len(rows) == 104 and len({r['unit'] for r in rows}) == 104
assert {r['unit'] for r in rows} == {r['unit'] for r in host['records']}
assert all(r['draws'] >= r['minimum_draws'] >= 200 for r in rows)
assert all(r['expected_rows'] == len(r['assets']) * len(r['horizons']) * r['draws'] for r in rows)
assert all(r['verdict']['admissible'] and not r['verdict']['scored'] for r in rows)
summary = {
    'date': '2026-09-25', 'task': 'T2-06', 'statistical_branch_local_contracts': 'passed',
    'house_branch': 'blocked-external', 'remote_review': 'pending',
    'image': image['image'], 'local_image_id': image['image_id'], 'source_ref': image['source_ref'],
    'practice': {'passed': 103, 'total': 103}, 'exemplar': {'passed': 1, 'total': 1},
    'host_contracts_passed': 104,
    'family_counts_including_exemplar': dict(Counter(r['family'] for r in rows)),
    'target_counts': dict(Counter(r['target'] for r in rows)),
    'frequency_counts': dict(Counter(r['frequency'] for r in rows)),
    'draw_counts': sorted({r['draws'] for r in rows}),
    'minimum_required_draws': sorted({r['minimum_draws'] for r in rows}),
    'one_percent_expected_tail_draws': sorted({r['draws'] * .01 for r in rows}),
    'expected_rows_min_max': [min(r['expected_rows'] for r in rows), max(r['expected_rows'] for r in rows)],
    'max_output_bytes': max(r['output_bytes'] for r in rows),
    'output_limit_bytes': 64 * 1024**2, 'model_calls': 0,
    'strict_repeated_probe': {'scenarios': 3, 'runs': 6, 'identical_bytes': True},
    'official_single_cell_tests': {'linux_passed': 22, 'windows_passed': 1,
        'windows_failed': 21, 'windows_failure_reason': 'Official tests require POSIX os.O_DIRECTORY'},
    'scoring_policy': 'Official implementation owns single-cell effective weight reallocation; no participant scorer replacement',
    'local_limits': image['local_limits'], 'score': None, 'production_verified': False,
    'input_provenance': 'Raw pinned Git archive; manifest-checked staged card/spec, panels and text only',
    'evidence': ['t2-06-image-contracts-20260925/report.json', 't2-06-host-contracts-20260925/report.json',
        't2-container-probe-20260925/report.json', 't2-06-official-single-cell-tests-linux.log',
        't2-06-official-single-cell-tests.log'],
    'limitations': image['limitations'] + ['Five expected 1% tail draws do not validate tail calibration',
        'Windows host and Linux image use different dependency versions; cross-environment byte identity is not claimed']}
(E / 't2-06-run-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
status_path = ROOT / 'planning/task-status.json'
status = json.loads(status_path.read_text(encoding='utf-8-sig'))
for task in status['tasks']:
    if task['id'] == 'T2-06':
        task['status'] = 'active'
        task['evidence'] = ['project-evidence/t2-06-run-summary.json', 'project-evidence/t2-06-review.md',
                            'project-evidence/t2-06-image-contracts-20260925/report.json']
        task['note'] = ('Statistical branch local acceptance complete: 103/103 practice + 1/1 exemplar '
            'pass official g0–g3 in the same candidate image. 22 official single-cell tests pass on Linux. '
            'No accuracy score or House quality claim; House branch blocked-external. Remote review pending.')
    if task['id'] == 'C05':
        for entry in ['project-evidence/c05-t2-review.md', 'project-evidence/t2-container-probe-20260925/report.json',
                      'project-evidence/t3-c05-runtime-20260925/report.json']:
            if entry not in task['evidence']:
                task['evidence'].append(entry)
        task['note'] = ('T1 historical, T2 current and T4 current strict local probes pass. T3 runtime-only '
            '/tmp image passes six strict single-scenario runs with identical trace and ledger bytes; '
            'batch probe separate. GPU/high-memory, real House and organizer platform remain unverified.')
status['as_of'] = '2026-09-25'
status_path.write_text(json.dumps(status, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
print(json.dumps(summary, indent=2))
