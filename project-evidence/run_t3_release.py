"""Strict, non-rankable same-image release validation with official card gates.

Run in Linux with the existing evaluation environment. Every output, verdict and
failed attempt is retained. Resume skips only successful frozen-plan units.
"""
import argparse
from collections import Counter
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tomllib
import traceback

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '.validation/t3-release-source-20260925'
OUT = ROOT / 'project-evidence/t3-06-release-20260925'
IMAGE = 'simulation-agent:runtime-20260925'
IMAGE_ID = 'sha256:c5724d34e2a8c951e4645b7c9aed1b95e3eabf1bd3dc24c0195055700cdbd45d'
sys.path.insert(0, str(SOURCE))
from throughput import node_fingerprint
from throughput.run_unit import (UnitRecord, UnitRun, _host_n_events, _reported_n_events,
                                 is_batch_unit, merge_host_metrics, retain_output)
from throughput.timer import timed_container_run
from qfbench2_track_simulation.scoring import build_developer_verifier
from qfbench2_track_simulation.limits import requires_message_ledger, stable_paths_for


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, indent=2, default=str) + '\n')


def main():
    global OUT, IMAGE, IMAGE_ID
    parser = argparse.ArgumentParser()
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--max-units', type=int)
    parser.add_argument('--image', help='New immutable candidate; requires a fresh output directory')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--units', help='Comma-separated bounded semantic roster; default is all71')
    args = parser.parse_args()
    if args.image:
        if not args.out: parser.error('--image requires --out to preserve release evidence')
        IMAGE = args.image
        IMAGE_ID = subprocess.check_output(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}'], text=True).strip()
    if args.out: OUT = args.out.resolve()
    assert os.name == 'posix', 'Official sanitation requires Linux'
    actual = subprocess.check_output(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}'], text=True).strip()
    assert actual == IMAGE_ID
    units = sorted(p for p in (SOURCE / 'units').iterdir() if (p / 'card.toml').exists())
    assert len(units) == 71 and sum(is_batch_unit(p) for p in units) == 6
    if args.units:
        if not args.out: parser.error('--units requires a fresh --out')
        selected = set(args.units.split(','))
        assert selected <= {p.name for p in units}, 'Unknown unit in requested roster'
        units = [p for p in units if p.name in selected]
    # Prioritize the largest observed trace to expose output/tmpfs limits early.
    large_id = '9b855004-0d35-5ed1-945f-5d5d299b64d8'
    largest = [p for p in units if (p / 'scenario.json').exists()
               and json.loads((p / 'scenario.json').read_text()).get('scenario_id') == large_id]
    assert len(largest) == (1 if not args.units or 't3-gb-mega-throughput' in selected else 0)
    priority = [p.name for p in largest] + (['t3-gbatch-homog-4'] if any(p.name=='t3-gbatch-homog-4' for p in units) else [])
    units.sort(key=lambda p: (priority.index(p.name) if p.name in priority else 2, p.name))
    plan = {'image': IMAGE, 'image_id': IMAGE_ID, 'source_ref': '1504b37b8472f7f949d4b8f80b7c8b2b71ab0e43',
            'roster': [p.name for p in units], 'repeat_units': priority, 'repeats_other_units': 1,
            'warmup_discarded': False, 'quota': {'cpus': 4, 'memory_bytes': 16 * 1024**3, 'swap': False},
            'output_limit_bytes': 64 * 1024**2, 'unit_deadline_sec': 1800,
            'stable_repeat_policy': 'Actual trace/ledger bytes and host event counts; honest timing retained separately',
            'rankable': False, 'official_platform': False}
    fingerprint = node_fingerprint.collect().to_dict()
    if args.resume:
        assert json.loads((OUT / 'plan.json').read_text()) == plan
        report = json.loads((OUT / 'report.json').read_text())
    else:
        OUT.mkdir(exist_ok=False)
        write(OUT / 'plan.json', plan)
        report = {'plan': plan, 'node_fingerprint': fingerprint, 'records': [], 'completed': False}
    completed = {r['unit'] for r in report['records'] if r['status'] == 'passed'}
    remaining = [p for p in units if p.name not in completed]
    if args.max_units:
        remaining = remaining[:args.max_units]
    for unit in remaining:
        row = {'unit': unit.name, 'batch': is_batch_unit(unit), 'status': 'running',
               'requires_message_ledger': requires_message_ledger(tomllib.loads((unit / 'card.toml').read_text())),
               'runs': [], 'started_at': time.time()}
        attempt = 1 + sum(r['unit'] == unit.name for r in report['records'])
        report['records'].append(row)
        write(OUT / 'report.json', report)
        print('START', unit.name, 'attempt', attempt, flush=True)
        try:
            work = OUT / 'attempts' / unit.name / str(attempt)
            work.mkdir(parents=True, exist_ok=False)
            input_dir = work / 'input'
            input_dir.mkdir()
            manifest = {f['path']: f for f in json.loads((unit / 'manifest.json').read_text())['files']}
            inputs = sorted((unit / 'scenarios').glob('*.json')) if row['batch'] else [unit / 'scenario.json']
            row['inputs'] = []
            for source in inputs:
                relative = source.relative_to(unit).as_posix()
                assert digest(source) == manifest[relative]['sha256'], relative
                target = input_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                row['inputs'].append({'path': relative, 'sha256': digest(target)})
            signatures = []
            repeats = 2 if unit.name in priority else 1
            for repeat in range(1, repeats + 1):
                base = work / str(repeat)
                raw = base / 'raw'
                raw.mkdir(parents=True)
                raw.chmod(0o777)
                output = base / 'accepted' / unit.name
                cid = base / 'container.cid'
                verb = (['simulate-batch', '--batch-dir', '/input/scenarios', '--out-dir', '/output']
                        if row['batch'] else ['simulate', '--config', '/input/scenario.json', '--out', '/output/trace.parquet'])
                command = ['docker', 'run', '--rm', '--cidfile', str(cid), '--network', 'none',
                    '--read-only', '--user', '65534:65534', '--cap-drop=ALL', '--security-opt', 'no-new-privileges',
                    '--cpus', '4', '--memory', '16g', '--memory-swap', '16g', '--pids-limit', '256',
                    '--ulimit', 'nofile=1024:1024', '--ulimit', 'nproc=256:256', '--ulimit', 'fsize=67108864:67108864',
                    '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777',
                    '--mount', f'type=bind,src={input_dir},dst=/input,readonly',
                    '--mount', f'type=bind,src={raw},dst=/output', IMAGE_ID, *verb]
                run = {'repeat': repeat, 'command': command}
                row['runs'].append(run)
                proc, wall, gpu, peak = timed_container_run(command, cidfile=cid, gpus=None, timeout_sec=1800)
                run.update(exit_code=proc.returncode, host_wall_clock_sec=wall, host_peak_memory_bytes=peak,
                           host_gpu_seconds=gpu, stdout=proc.stdout[-5000:].decode(errors='replace'),
                           stderr=proc.stderr[-5000:].decode(errors='replace'))
                write(OUT / 'report.json', report)
                assert proc.returncode == 0, run['stderr']
                assert not output.exists()
                retain_output(raw, output, unit)
                files = {p.relative_to(output).as_posix(): {'bytes': p.stat().st_size, 'sha256': digest(p)}
                         for p in output.rglob('*') if p.is_file()}
                count = _host_n_events(output, row['batch'])
                assert count == _reported_n_events(output, row['batch'])
                run.update(files=files, host_n_events=count, output_bytes=sum(p['bytes'] for p in files.values()))
                assert run['output_bytes'] <= plan['output_limit_bytes'], 'Total output exceeds 64 MiB'
                measurement = UnitRun(count / wall, count, count, wall, gpu, peak, 0)
                record = UnitRecord(unit=unit.name, verb=verb[0], image=IMAGE_ID, warmup_discarded=False,
                                    runs=[measurement], median_events_per_sec=count / wall,
                                    median_host_gpu_seconds=gpu, median_host_peak_memory_bytes=peak,
                                    node_fingerprint=fingerprint)
                merge_host_metrics(record, output.parent / 'host_metrics.json')
                ctx = {'unit_dir': unit, 'output_dir': output}
                verdict = build_developer_verifier(ctx).run(ctx)
                run['verdict'] = dataclasses.asdict(verdict)
                assert verdict.admissible, run['verdict']
                stable = {path: files[path]['sha256'] for path in stable_paths_for(unit) if path in files}
                assert stable
                signatures.append((stable, count))
                run['status'] = 'passed'
                write(OUT / 'report.json', report)
            assert all(s == signatures[0] for s in signatures)
            row.update(status='passed', repeats_identical=True)
        except Exception as exc:
            row.update(status='error', error_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc())
        row['finished_at'] = time.time()
        write(OUT / 'report.json', report)
        print(row['status'].upper(), unit.name, flush=True)
        # Preserve evidence and resolve a failed release gate before continuing.
        if row['status'] != 'passed':
            return 1
    passed = {r['unit'] for r in report['records'] if r['status'] == 'passed'}
    report.update(completed=len(passed) == len(units), passed_units=len(passed), total_units=len(units),
                  attempt_status_counts=dict(Counter(r['status'] for r in report['records'])))
    write(OUT / 'report.json', report)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
