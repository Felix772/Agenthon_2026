"""Evaluate the six public batch units and exemplar with official developer tools.

This records local correctness only. A single run and concurrent local activity
cannot establish comparable performance or organizer Final timing.
"""
import dataclasses
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '.validation/track3-current-update'
sys.path.insert(0, str(SOURCE))
from throughput.run_unit import run_unit, merge_host_metrics
from qfbench2_track_simulation.scoring import build_developer_verifier


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination', default='project-evidence/t3-02-extra')
    parser.add_argument('--batches-only', action='store_true')
    parser.add_argument('--image', default='track3-abides-baseline:agenthon-local-20260922')
    args = parser.parse_args()
    image = args.image
    destination = ROOT / args.destination
    destination.mkdir(exist_ok=False)
    units = sorted((SOURCE / 'units').glob('t3-gbatch-*'))
    if not args.batches_only:
        units.append(SOURCE / 'units/t3-EXAMPLE-vectorized-matching')
    records = []
    report = {'factory': 'build_developer_verifier', 'rankable': False,
              'production_verified': False, 'denominator': len(units),
              'roster': [u.name for u in units], 'records': records,
              'image': image, 'runs': 1, 'discard_warmup': False,
              'limitations': ['No performance comparison; local concurrent workloads',
                              '16GiB quota does not imply actual host capacity']}
    report_path = destination / 'report.json'
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    for unit in units:
        row = {'unit': unit.name, 'status': 'running', 'started_at_unix': time.time()}
        records.append(row)
        print('START', unit.name, flush=True)
        try:
            output = destination / 'outputs' / unit.name
            run = run_unit(image, unit, runs=1, discard_warmup=False,
                           cpus='4', memory='16g', gpus=None,
                           keep_output=output, timeout_sec=1800,
                           run_as_host_user=False)
            row['run'] = run.to_dict()
            merge_host_metrics(run, output.parent / 'host_metrics.json')
            ctx = {'unit_dir': unit, 'output_dir': output}
            verdict = build_developer_verifier(ctx).run(ctx)
            row['verdict'] = dataclasses.asdict(verdict)
            row['status'] = 'passed' if verdict.admissible else 'failed'
        except Exception as exc:
            import traceback
            row['traceback'] = traceback.format_exc()
            row['status'] = 'error'
            row['error_type'] = type(exc).__name__
            row['error'] = str(exc)
        row['finished_at_unix'] = time.time()
        report_path.write_text(json.dumps(report, indent=2, default=str) + '\n')
        print(row['status'].upper(), unit.name, flush=True)
    return 0 if all(row['status'] == 'passed' for row in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
