"""Current public T2 contract gate for the fixed Development trend image."""

from collections import Counter
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import time
import tomllib

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '.validation/t2-current-20260928'
OUT = ROOT / 'project-evidence/t2-monthly-trend-dev-roster-20260928'
IMAGE = 'forecast-agent:monthly-trend-dev-20260928'
EXPECTED_ID = 'sha256:461ad3fb69becc4084744879ba8f9bf53ae7e4de64f49785f14fae3a217aa8a3'
SOURCE_REF = '28a6cae9674f69e63a07a19165a9217e85eacfff'


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    image_id = subprocess.check_output(
        ['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}'], text=True).strip()
    if image_id != EXPECTED_ID:
        raise RuntimeError(f'Candidate image changed: {image_id}')
    retry_errors = sys.argv[1:] == ['--retry-errors']
    if not SOURCE.is_dir() or (OUT.exists() and not retry_errors) or (retry_errors and not OUT.exists()):
        raise RuntimeError('Expected immutable current source and appropriate output directory')
    cards = sorted((SOURCE / 'units').glob('*/card.toml'))
    if len(cards) != 104:
        raise RuntimeError(f'Expected 104 current public cards, found {len(cards)}')
    if not retry_errors:
        OUT.mkdir()
    sys.path.insert(0, str(SOURCE))
    from qfbench2_track_forecasting.scoring import _main as score_cli

    report = json.loads((OUT / 'report.json').read_text()) if retry_errors else {
        'source_ref': SOURCE_REF,
        'image': IMAGE,
        'image_id': image_id,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'practice_denominator': 103,
        'exemplar_denominator': 1,
        'seed': 20260928,
        'factory': 'current official score CLI, admissibility gates only',
        'score': None,
        'limits': {'cpus': 2, 'memory_bytes': 1073741824, 'tmp_bytes': 67108864},
        'records': [],
    }
    common = [
        'docker', 'run', '--rm', '--network', 'none', '--read-only',
        '--user', '65534:65534', '--cap-drop=ALL',
        '--security-opt', 'no-new-privileges', '--cpus', '2',
        '--memory', '1g', '--memory-swap', '1g', '--pids-limit', '256',
        '--ulimit', 'nproc=256:256', '--ulimit', 'nofile=1024:1024',
        '--ulimit', 'fsize=67108864:67108864',
        '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777',
    ]
    for position, card_path in enumerate(cards):
        started = time.perf_counter()
        unit = card_path.parent
        name = f'agenthon-t2-trend-20260928-{position:03}'
        if retry_errors and report['records'][position]['status'] == 'passed':
            continue
        row = {'unit': unit.name, 'is_exemplar': unit.name == 't2-EXAMPLE-ust-curve-1m'}
        if retry_errors:
            if report['records'][position]['unit'] != unit.name:
                raise RuntimeError('Retry roster order changed')
            report['records'][position] = row
        else:
            report['records'].append(row)
        try:
            card = tomllib.loads(card_path.read_text(encoding='utf-8'))
            spec_path = unit / 'forecast_spec.json'
            spec = json.loads(spec_path.read_text()) if spec_path.exists() else {}
            targets = card['targets']
            params = card.get('scoring', {}).get('params', {})
            draws = max(500, int(params.get('n_draws_min', 0)), int(spec.get('n_draws_min', 0)))
            row.update(
                family=card['metadata']['category'],
                frequency=targets.get('target_frequency', card['metadata'].get('target_frequency')),
                target=targets['target_type'],
                cutoff=str(card['provenance']['data_cutoff']),
                draws=draws,
            )
            staged = OUT / ('retry-inputs' if retry_errors else 'inputs') / unit.name
            (staged / 'panels').mkdir(parents=True)
            (staged / 'text').mkdir()
            manifest = json.loads((unit / 'manifest.json').read_text())
            selected = []
            for entry in manifest['files']:
                rel = PurePosixPath(entry['path'])
                if rel.is_absolute() or '..' in rel.parts:
                    raise ValueError('Unsafe manifest path')
                is_panel = entry.get('role') == 'input' and rel.suffix == '.parquet'
                is_text = rel.parts[0] == 'text' and entry.get('role') in (
                    'corpus', 'fixture', 'input', 'metadata')
                is_contract = rel.as_posix() in ('card.toml', 'forecast_spec.json')
                if not (is_panel or is_text or is_contract):
                    continue
                source = unit.joinpath(*rel.parts)
                if source.is_symlink() or not source.is_file() or digest(source) != entry['sha256']:
                    raise ValueError(f'Invalid or changed manifest member: {rel}')
                destination = staged / ('panels/' + rel.name if is_panel else rel.as_posix())
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    raise ValueError('Colliding staged member')
                shutil.copyfile(source, destination)
                selected.append({'path': destination.relative_to(staged).as_posix(),
                                 'sha256': entry['sha256']})
            if not (staged / 'card.toml').is_file() or not list((staged / 'panels').glob('*.parquet')):
                raise ValueError('Required staged input missing')
            if spec and not (staged / 'forecast_spec.json').is_file():
                raise ValueError('Required forecast specification missing')
            row['staged_files'] = selected
            output = OUT / ('retry-outputs' if retry_errors else 'outputs') / unit.name
            output.mkdir(parents=True)
            command = common + [
                '--name', name,
                '--mount', f'type=bind,src={staged},dst=/input,readonly',
                '--mount', f'type=bind,src={output},dst=/output',
                '-e', 'QFBENCH_SEED=20260928', IMAGE, 'forecast',
                '--panels', '/input/panels', '--text', '/input/text',
                '--asof', row['cutoff'], '--out', '/output/forecast.parquet',
                '--n-draws', str(draws),
            ]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=1800)
            row.update(exit_code=completed.returncode,
                       stdout=completed.stdout[-2000:], stderr=completed.stderr[-2000:])
            if completed.returncode:
                raise RuntimeError('Candidate container forecast failed')
            stream = io.StringIO()
            with redirect_stdout(stream):
                code = score_cli(['score', '--card', str(card_path),
                                  '--forecast', str(output / 'forecast.parquet')])
            row['verdict'] = json.loads(stream.getvalue())
            row['files'] = {path.name: {'bytes': path.stat().st_size, 'sha256': digest(path)}
                            for path in output.iterdir()}
            row['output_bytes'] = sum(item['bytes'] for item in row['files'].values())
            rationale = json.loads((output / 'forecast_rationale.md').read_text().split('\n\n', 1)[1])
            row['trend_active'] = 'monthly_trend' in rationale['fit']
            expected_trend = row['frequency'] == 'monthly' and row['target'] == 'level'
            row['status'] = 'passed' if (
                code == 0 and row['verdict']['admissible']
                and row['output_bytes'] <= 64 * 1024**2
                and row['trend_active'] == expected_trend) else 'failed'
        except Exception as exc:
            row.update(status='error', error_type=type(exc).__name__, error=str(exc))
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
        row['elapsed_sec'] = time.perf_counter() - started
        (OUT / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(row['status'].upper(), unit.name, row.get('error', ''), flush=True)
    report['practice_results'] = dict(Counter(
        row['status'] for row in report['records'] if not row['is_exemplar']))
    report['exemplar_results'] = dict(Counter(
        row['status'] for row in report['records'] if row['is_exemplar']))
    report['trend_active_count'] = sum(row.get('trend_active', False) for row in report['records'])
    report['all_contracts_passed'] = all(row['status'] == 'passed' for row in report['records'])
    (OUT / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in (
        'practice_results', 'exemplar_results', 'trend_active_count',
        'all_contracts_passed')}, indent=2), flush=True)
    return 0 if report['all_contracts_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
