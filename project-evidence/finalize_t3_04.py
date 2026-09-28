"""Audit measured candidate coverage and actual retained output against baseline."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / 'project-evidence'


def read(path):
    return json.loads(path.read_text())


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


single = read(E / 't3-04-full-regression/report.json')
batch = read(E / 't3-04-batches-root/report.json')
timing = read(E / 't3-04-paired-timing/summary.json')
baseline = read(E / 't3-02-completed-summary.json')
assert (single['passed'], single['failed'], single['errored']) == (65, 0, 0)
assert len(batch['records']) == 6 and all(r['status'] == 'passed' for r in batch['records'])
assert all(s['local_timing_gate'] and s['trace_and_ledger_identical'] for s in timing['scenarios'])
assert {r['scenario_id'] for r in single['results']} == {r['scenario_id'] for r in baseline['coverage']}
records = []
for row in single['results']:
    sid = row['scenario_id']
    candidate = E / 't3-04-full-regression' / sid
    sources = [p / sid for p in [E / 't3-02-single-regression', E / 't3-resume-20260924']
               if (p / sid / 'trace.parquet').exists()]
    assert len(sources) == 1, (sid, sources)
    old = sources[0]
    trace_hash = digest(candidate / 'trace.parquet')
    assert trace_hash == digest(old / 'trace.parquet'), sid
    events = read(candidate / 'events.json')
    previous = read(old / 'events.json')
    assert events['n_events'] == previous['n_events']
    # Standard regression does not retain actual message_trace.parquet.
    claimed_ledger_matches = events.get('message_trace_sha256') == previous.get('message_trace_sha256')
    assert claimed_ledger_matches
    records.append({'scenario_id': sid, 'trace_sha256': trace_hash,
                    'trace_bytes': (candidate / 'trace.parquet').stat().st_size,
                    'n_events': events['n_events'], 'actual_trace_matches_baseline': True,
                    'self_reported_ledger_hash_matches_baseline': claimed_ledger_matches,
                    'actual_ledger_retained': False, 'baseline': old.relative_to(E).as_posix()})
batch_records = []
for row in batch['records']:
    unit = row['unit']
    candidate = E / 't3-04-batches-root/outputs' / unit
    old = E / 't3-02-batches-root/outputs' / unit
    actual = {p.relative_to(candidate).as_posix(): digest(p) for p in candidate.rglob('*.parquet')}
    expected = {p.relative_to(old).as_posix(): digest(p) for p in old.rglob('*.parquet')}
    assert actual == expected, unit
    count = 0
    for sub in candidate.glob('*/events.json'):
        new_events, old_events = read(sub), read(old / sub.relative_to(candidate))
        assert new_events['n_events'] == old_events['n_events']
        count += new_events['n_events']
    total = sum(p.stat().st_size for p in candidate.rglob('*') if p.is_file())
    assert total <= 64 * 1024**2
    batch_records.append({'unit': unit, 'actual_trace_and_ledger_match_baseline': True,
                          'files': actual, 'n_events': count, 'output_bytes': total,
                          'official_developer_gates_passed': True})
summary = {'date': '2026-09-25', 'task': 'T3-04', 'change': 'Bounded 4096-entry diagnostic whole-second formatter cache',
    'image': single['candidate_image'], 'image_id': timing['image_ids']['candidate'],
    'baseline_image_id': timing['image_ids']['baseline'],
    'local_standard_regression_and_timing_passed': True,
    'formatter_tests': {'tests': 3, 'random_input_equivalence_cases': 20000},
    'single_passed': 65, 'single_total': 65, 'batch_passed': 6, 'batch_total': 6,
    'singles': records, 'batches': batch_records, 'paired_timing': timing['scenarios'],
    'performance_repeats': 'Five alternating baseline/candidate pairs per each of three scenarios; 30 runs',
    'scoring_profile': 'developer', 'rankable': False, 'official_platform': False,
    'remote_review': 'pending', 'release_accepted': False,
    'limitations': ['Standard 65-case runner retains trace but not actual message ledger and is not full card-gate release validation',
        'Actual ledger equivalence verified for three paired scenarios and all six batches',
        'Timing is local to this host, limited five-pair sample, and used an extra writable /work tmpfs',
        'Runtime-only /tmp derivative c5724d34 has a different image ID; T3-06 release validation is separate',
        'Six original batch attempts failed during root-owned temporary directory cleanup; root-host rerun retained separately',
        'No GPU/B200, real 16GiB capacity, production telemetry or Final ranking claimed']}
write_path = E / 't3-04-run-summary.json'
write_path.write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps({k: v for k, v in summary.items() if k not in ['singles', 'batches', 'paired_timing']}, indent=2))
