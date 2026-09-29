"""Run a resumable, strict local Docker gate on the 71 scored public T2 cards.

This checks only participant output admissibility and host-draw parity. It never
reads sealed outcomes or normalization scales, publishes an image, or submits.
Run with no arguments for a fresh report; use --resume after an interruption.
"""

import argparse
from contextlib import redirect_stdout
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import time
import tomllib
import uuid
import zlib

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '.validation/t2-current-20260928'
TOOLKIT = ROOT / '.validation/toolkit-v2.4.4/common'
sys.path[:0] = [str(SOURCE), str(TOOLKIT)]
import qfbench2_common  # noqa: E402
from qfbench2_track_forecasting.scoring import _main as score_main  # noqa: E402

ROSTER = ROOT / 'project-evidence/t2-codabench-scored-950513-20260928.csv'
STAGED = ROOT / 'project-evidence/t2-monthly-trend-dev-roster-20260928'
HOST = ROOT / 'project-evidence/t2-m0-control-scored-roster-20260928'
OUT = ROOT / 'project-evidence/t2-m0-control-scored-docker-gate-v2-20260928'
IMAGE = 'forecast-agent:m0-control-20260928'
IMAGE_ID = 'sha256:273582dca87206f49ea5edde63016a227f8c7374b386b2ab9d78b548d2708ff1'
SOURCE_REF = '28a6cae9674f69e63a07a19165a9217e85eacfff'
ROSTER_SHA256 = '81cd50edc7606c04ce7da1b3256312e1cd39f6aa7a57abf52b68d789c2c5a530'
FORECAST_SHA256 = '9c110dbd6f5c90ced18ace2671e9ddede2a7b7e62dde99c4b7a4aff5082d4e65'
REFERENCE_SHA256 = '5c204c4636ea9b9fe05866c6e74cf6964ba5ab1035a0d43eb66d8114fbda0be0'
HOST_REPORT_SHA256 = 'adbb6b4ce9f109a1ce4d4678fd8ebcf04bb32d9d9f2fc8ac76e5ea1f790c8736'
STAGED_REPORT_SHA256 = '991a36cc638b6b2ceb329eca6deb124ac0c130d8b368264489b70f323a95be7a'
TOOLKIT_COMMON_SHA256 = '938bad75902bb578fa57d08b6799cc0f0bde7d77cbbfa4d818c87bc69a24d143'
REQUIRED_OUTPUTS = ('forecast.parquet', 'forecast_meta.json', 'forecast_rationale.md')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def toolkit_digest():
    """Pin the 98 Python/JSON files used by the shared v2.4.4 toolkit."""
    package = TOOLKIT / 'qfbench2_common'
    files = sorted((path for path in package.rglob('*')
                    if path.is_file() and path.suffix in ('.py', '.json')),
                   key=lambda path: path.relative_to(package).as_posix())
    if len(files) != 98 or any(path.is_symlink() for path in files):
        raise ValueError('Shared toolkit source tree differs from frozen v2.4.4')
    sha = hashlib.sha256()
    for path in files:
        sha.update(path.relative_to(package).as_posix().encode())
        sha.update(b'\0')
        sha.update(path.read_bytes())
        sha.update(b'\0')
    return sha.hexdigest()


def save(report):
    temporary = OUT / 'report.json.tmp'
    temporary.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    temporary.replace(OUT / 'report.json')


def keyed(path):
    frame = pd.read_parquet(path).sort_values(['draw', 'asset', 'horizon']).reset_index(drop=True)
    if not np.isfinite(frame.value.to_numpy(dtype=float)).all():
        raise ValueError(f'Non-finite draw values in {path}')
    return frame


def outputs_and_hashes(output):
    files = list(output.rglob('*'))
    if any(path.is_symlink() for path in files):
        raise ValueError(f'Output contains a symlink: {output}')
    file_paths = [path for path in files if path.is_file()]
    if (len(file_paths) != len(REQUIRED_OUTPUTS)
            or {path.name for path in file_paths} != set(REQUIRED_OUTPUTS)
            or any(path.parent != output for path in file_paths)):
        raise ValueError(f'Output file set differs from required deliverables: {output}')
    total = sum(path.stat().st_size for path in file_paths)
    if total > 64 * 1024**2:
        raise ValueError(f'Output exceeds 64 MiB: {output}')
    return total, {path.name: digest(path) for path in file_paths}


def final_staged(unit, prior):
    retry = STAGED / 'retry-inputs' / unit
    staged = retry if retry.is_dir() else STAGED / 'inputs' / unit
    for member in prior['staged_files']:
        path = staged / member['path']
        if path.is_symlink() or not path.is_file() or digest(path) != member['sha256']:
            raise ValueError(f'Staged input changed from manifest-verified run: {unit} {member["path"]}')
    source_card = SOURCE / 'units' / unit / 'card.toml'
    if digest(source_card) != digest(staged / 'card.toml'):
        raise ValueError(f'Staged and current public cards differ: {unit}')
    return staged, source_card


def freeze():
    if (sys.version_info < (3, 13)
            or not Path(qfbench2_common.__file__).resolve().is_relative_to(TOOLKIT.resolve())
            or toolkit_digest() != TOOLKIT_COMMON_SHA256):
        raise ValueError('Pinned shared toolkit v2.4.4 is unavailable or changed')
    with ROSTER.open(newline='') as file:
        units = [row['unit'] for row in csv.DictReader(file)]
    if len(units) != 71 or len(set(units)) != 71 or digest(ROSTER) != ROSTER_SHA256:
        raise ValueError('Scored roster differs from frozen 71-card ID-only roster')
    if digest(HOST / 'report.json') != HOST_REPORT_SHA256:
        raise ValueError('Frozen host M0 audit report changed')
    if digest(STAGED / 'report.json') != STAGED_REPORT_SHA256:
        raise ValueError('Frozen manifest-verified staging report changed')
    image_id = subprocess.check_output(
        ['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}'], text=True).strip()
    if image_id != IMAGE_ID:
        raise ValueError(f'M0 image tag differs from frozen image ID: {image_id}')
    env = json.loads(subprocess.check_output(
        ['docker', 'image', 'inspect', IMAGE, '--format', '{{json .Config.Env}}'],
        text=True))
    if not {'AGENTHON_DAILY_M0_CONTROL=1', 'AGENTHON_MONTHLY_TREND_AUTO=1'} <= set(env):
        raise ValueError('Image control environment differs from frozen profile')
    host = json.loads((HOST / 'report.json').read_text())
    if (host.get('count') != 71 or host.get('m0_spec_ref') != SOURCE_REF
            or host.get('forecast_source_sha256') != FORECAST_SHA256
            or host.get('reference_source_sha256') != REFERENCE_SHA256
            or {row['unit'] for row in host['records']} != set(units)):
        raise ValueError('Host M0 audit does not match frozen scored roster or source')
    staged_report = json.loads((STAGED / 'report.json').read_text())
    prior = {row['unit']: row for row in staged_report['records'] if row['status'] == 'passed'}
    if not set(units) <= set(prior):
        raise ValueError('Manifest-verified staged input missing for scored unit')
    host_forecast_sha256 = {}
    for unit in units:
        path = HOST / 'outputs' / unit / 'forecast.parquet'
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Frozen host forecast missing or linked: {unit}')
        host_forecast_sha256[unit] = digest(path)
    return units, prior, {row['unit']: row for row in host['records']}, {
        'image': IMAGE, 'image_id': IMAGE_ID, 'source_ref': SOURCE_REF,
        'roster_sha256': ROSTER_SHA256, 'script_sha256': digest(Path(__file__)),
        'host_report_sha256': HOST_REPORT_SHA256,
        'host_forecast_sha256': host_forecast_sha256,
        'staged_report_sha256': STAGED_REPORT_SHA256,
        'toolkit_common_sha256': TOOLKIT_COMMON_SHA256,
        'official_scorer_sha256': digest(SOURCE / 'qfbench2_track_forecasting/scoring.py'),
        'seed_injected': 123456789,
        'limits': {'cpus': 2, 'memory_bytes': 1073741824,
                   'output_bytes': 64*1024**2, 'tmp_bytes': 64*1024**2},
    }


def validate_resume(report, frozen, units):
    for key, value in frozen.items():
        if report.get(key) != value:
            raise ValueError(f'Frozen gate assumption changed: {key}')
    passed = 0
    for row in report.get('records', []):
        if passed >= len(units) or row.get('unit') != units[passed]:
            raise ValueError('Resume record order differs from scored roster')
        if row.get('status') == 'passed':
            output = OUT / row['output_relative']
            total, hashes = outputs_and_hashes(output)
            expected_seed = zlib.crc32(row['unit'].encode('utf-8')) & 0x7FFFFFFF
            if (total != row['output_bytes'] or hashes != row['file_sha256']
                    or row.get('seed') != expected_seed or not row.get('admissible')
                    or row.get('max_abs_draw_error_vs_host', 1.) > 1e-9):
                raise ValueError(f'Previously passed output changed: {row["unit"]}')
            passed += 1
        elif row.get('status') != 'failed':
            raise ValueError('Unknown resume record status')
    return passed


def next_output(unit):
    attempt = 1
    while True:
        output = OUT / 'outputs' / unit / f'attempt-{attempt:03}'
        if not output.exists():
            output.mkdir(parents=True)
            return output, attempt
        attempt += 1


def run_one(unit, prior, host, expected_host_sha256):
    started = time.perf_counter()
    row = {'unit': unit}
    try:
        staged, card_path = final_staged(unit, prior)
        if digest(HOST / 'outputs' / unit / 'forecast.parquet') != expected_host_sha256:
            raise ValueError(f'Frozen host forecast changed: {unit}')
        card = tomllib.loads(card_path.read_text(encoding='utf-8'))
        cutoff = str(card['provenance']['data_cutoff'])
        if cutoff != host['asof']:
            raise ValueError(f'Host and card cutoffs differ: {unit}')
        output, attempt = next_output(unit)
        row.update(attempt=attempt, output_relative=output.relative_to(OUT).as_posix())
        name = f'agenthon-t2-m0-full-{uuid.uuid4().hex[:12]}'
        command = [
            'docker', 'run', '--rm', '--name', name,
            '--network', 'none', '--read-only', '--user', '65534:65534',
            '--cap-drop=ALL', '--security-opt', 'no-new-privileges',
            '--cpus', '2', '--memory', '1g', '--memory-swap', '1g',
            '--pids-limit', '256', '--ulimit', 'nproc=256:256',
            '--ulimit', 'nofile=1024:1024', '--ulimit', 'fsize=67108864:67108864',
            '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777',
            '--mount', f'type=bind,src={staged},dst=/input,readonly',
            '--mount', f'type=bind,src={output},dst=/output',
            '-e', 'QFBENCH_SEED=123456789', IMAGE, 'forecast',
            '--panels', '/input/panels', '--text', '/input/text',
            '--asof', cutoff, '--out', '/output/forecast.parquet', '--n-draws', '500',
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, text=True)
            raise
        row['exit_code'] = completed.returncode
        row['stdout_tail'] = completed.stdout[-2000:]
        row['stderr_tail'] = completed.stderr[-2000:]
        if completed.returncode:
            raise RuntimeError(f'Container exited {completed.returncode}')
        score_stream = io.StringIO()
        with redirect_stdout(score_stream):
            code = score_main(['score', '--card', str(card_path),
                               '--forecast', str(output / 'forecast.parquet')])
        verdict = json.loads(score_stream.getvalue())
        if code or not verdict.get('admissible'):
            raise RuntimeError(f'Official public g0–g3 rejected output: {verdict}')
        rationale = json.loads((output / 'forecast_rationale.md').read_text().split('\n\n', 1)[1])
        expected_seed = zlib.crc32(unit.encode('utf-8')) & 0x7FFFFFFF
        if (rationale.get('method') != 'm0-control' or rationale.get('seed') != expected_seed
                or rationale.get('fit', {}).get('seed') != expected_seed):
            raise ValueError('M0 route did not use the card-ID CRC32 seed')
        actual = keyed(output / 'forecast.parquet')
        control = keyed(HOST / 'outputs' / unit / 'forecast.parquet')
        if not actual[['draw', 'asset', 'horizon']].equals(control[['draw', 'asset', 'horizon']]):
            raise ValueError('Container and host forecast keys differ')
        max_error = float(np.max(np.abs(actual.value.to_numpy()-control.value.to_numpy())))
        if max_error > 1e-9:
            raise ValueError(f'Container and host draw mismatch: {max_error}')
        output_bytes, hashes = outputs_and_hashes(output)
        row.update(status='passed', admissible=True, seed=expected_seed,
                   max_abs_draw_error_vs_host=max_error, output_bytes=output_bytes,
                   file_sha256=hashes, cells=host['cells'])
    except Exception as exc:
        row.update(status='failed', error_type=type(exc).__name__, error=str(exc))
    row['elapsed_sec'] = time.perf_counter() - started
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume', action='store_true', help='continue a frozen partial report')
    args = parser.parse_args()
    units, prior, host, frozen = freeze()
    if args.resume:
        if not (OUT / 'report.json').is_file():
            raise ValueError('Resume requires an existing report.json')
        report = json.loads((OUT / 'report.json').read_text())
        position = validate_resume(report, frozen, units)
    else:
        if OUT.exists():
            raise ValueError('Fresh gate refuses to overwrite an existing output directory')
        OUT.mkdir()
        report = dict(frozen, created_at=datetime.now(timezone.utc).isoformat(),
                      records=[], complete=False)
        save(report)
        position = 0
    for unit in units[position:]:
        row = run_one(unit, prior[unit], host[unit], frozen['host_forecast_sha256'][unit])
        report['records'].append(row)
        report['passed_count'] = sum(r['status'] == 'passed' for r in report['records'])
        save(report)
        print(row['status'].upper(), unit, f'{row["elapsed_sec"]:.2f}s', row.get('error', ''), flush=True)
        if row['status'] != 'passed':
            return 1
    report['complete'] = True
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    save(report)
    print(json.dumps({'complete': True, 'passed': report['passed_count'],
                      'image_id': IMAGE_ID}, indent=2), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
