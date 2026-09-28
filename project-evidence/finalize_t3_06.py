"""Accept only a complete same-image strict release report and real file hashes."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / 'project-evidence'
OUT = E / 't3-06-release-20260925'
SOURCE = ROOT / '.validation/t3-release-source-20260925'
report = json.loads((OUT / 'report.json').read_text())
assert report['completed'] and report['passed_units'] == report['total_units'] == 71
plan = report['plan']
assert len(plan['roster']) == len(set(plan['roster'])) == 71
passed = {r['unit']: r for r in report['records'] if r['status'] == 'passed'}
assert set(passed) == set(plan['roster'])
records = []
for unit, row in passed.items():
    candidates = list((OUT / 'attempts' / unit).glob('*'))
    # Fresh attempt directories retain older failed evidence if any.
    attempt = max(candidates, key=lambda p: int(p.name))
    repeats = 2 if unit in plan['repeat_units'] else 1
    assert len(row['runs']) == repeats and row['repeats_identical']
    signatures = []
    for run in row['runs']:
        assert run['status'] == 'passed' and run['exit_code'] == 0
        assert run['verdict']['admissible']
        assert all(g['passed'] for g in run['verdict']['gate_results'].values())
        output = attempt / str(run['repeat']) / 'accepted' / unit
        files = {}
        for path in output.rglob('*'):
            if path.is_file():
                with path.open('rb') as stream:
                    sha = hashlib.file_digest(stream, 'sha256').hexdigest()
                files[path.relative_to(output).as_posix()] = {'bytes': path.stat().st_size, 'sha256': sha}
        assert files == run['files']
        assert sum(f['bytes'] for f in files.values()) == run['output_bytes'] <= plan['output_limit_bytes']
        stable = {p: f['sha256'] for p, f in files.items() if p.endswith('.parquet')}
        signatures.append((stable, run['host_n_events']))
        if row['batch']:
            measured = E / 't3-04-batches-root/outputs' / unit
            expected = {}
            for path in measured.rglob('*.parquet'):
                with path.open('rb') as stream:
                    expected[path.relative_to(measured).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
            assert stable == expected
        else:
            scenario = json.loads((SOURCE / 'units' / unit / 'scenario.json').read_text())
            measured = E / 't3-04-full-regression' / scenario['scenario_id']
            with (measured / 'trace.parquet').open('rb') as stream:
                expected_trace = hashlib.file_digest(stream, 'sha256').hexdigest()
            assert files['trace.parquet']['sha256'] == expected_trace
        assert run['host_peak_memory_bytes'] is not None
        records.append({'unit': unit, 'repeat': run['repeat'], 'batch': row['batch'],
            'requires_message_ledger': row['requires_message_ledger'], 'actual_file_hashes_rechecked': True,
            'host_n_events': run['host_n_events'], 'output_bytes': run['output_bytes'],
            'host_wall_clock_sec': run['host_wall_clock_sec'], 'host_peak_memory_bytes': run['host_peak_memory_bytes'],
            'actual_trace_matches_measured_candidate': True,
            'actual_ledger_matches_measured_candidate': True if row['batch'] else None})
    assert all(signature == signatures[0] for signature in signatures)
summary = {'created_at': datetime.now(timezone.utc).isoformat(), 'task': 'T3-06',
    'source_type': 'fresh', 'test_kind': 'local developer semantic/runtime validation',
    'image': plan['image'], 'local_image_id': plan['image_id'], 'registry_digest': None,
    'source_ref': plan['source_ref'], 'toolkit_version': '2.4.4', 'factory': 'build_developer_verifier',
    'single_passed': 65, 'single_total': 65, 'batch_passed': 6, 'batch_total': 6,
    'runs': len(records), 'repeat_units': plan['repeat_units'], 'repeat_policy': plan['stable_repeat_policy'],
    'all_repeated_actual_trace_and_ledger_bytes_identical': True,
    'all_actual_file_hashes_rechecked': True, 'all_host_event_counts_match_output': True,
    'single_cards_requiring_message_ledger': sum(r['requires_message_ledger'] and not r['batch'] for r in passed.values()),
    'semantic_pass': True, 'strict_local_runtime_pass': True,
    'local_performance_pass': None, 'production_timing_verified': False, 'production_verified': False,
    'performance_evidence_for_parent_only': 't3-04-paired-timing/summary.json',
    'max_output_bytes': max(r['output_bytes'] for r in records),
    'max_host_peak_memory_bytes': max(r['host_peak_memory_bytes'] for r in records),
    'max_host_wall_clock_sec': max(r['host_wall_clock_sec'] for r in records),
    'node_fingerprint': report['node_fingerprint'], 'quota': plan['quota'],
    'attempt_status_counts': dict(Counter(r['status'] for r in report['records'])),
    'records': records, 'remote_review': 'pending',
    'limitations': ['Non-rankable local developer evidence, not organizer production timing',
        'Two predeclared units repeat twice; other units run once, not an official Final repeat count',
        'Actual trace/ledger bytes repeat; volatile timing JSON remains honest and may differ',
        '16GiB quota exceeds about7.5GiB actual host memory; no16GiB capacity certificate',
        'No GPU attached; no B200 or GPU throughput claim',
        'Performance speedup evidence belongs to parent5ff image; no paired timing repeated on runtime c572 image',
        'No registry push or competition upload']}
(E / 't3-06-run-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps({k: v for k, v in summary.items() if k not in ['records', 'node_fingerprint']}, indent=2))
