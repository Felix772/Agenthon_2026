"""Two representative restricted runs; local correctness, never ranked timing."""
import argparse
import dataclasses
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[1] if len(SCRIPT.parents) > 1 else SCRIPT.parent
UPSTREAM = ROOT / '.validation/t3-current-20261011/track3-simulation-public-1d7e994761f2e9256dd0c572c3592422c8fd0914'
IMAGE = 'sha256:2c88bb849a19a485a4964fa343ef9a18fd144ce77e76f69394315e2691649ec1'
UNITS = ('t3-as01-base-mix', 't3-gbatch-homog-4')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(destination):
    from qfbench2_track_simulation.limits import stable_paths_for
    from qfbench2_track_simulation.scoring import build_developer_verifier
    from throughput.run_unit import UnitRecord, UnitRun, _host_n_events, _reported_n_events, merge_host_metrics
    rows = []
    for name in UNITS:
        unit = Path('/upstream/units') / name
        output = destination / name / 'output'
        runtime = json.loads((output.parent / 'runtime.json').read_text())
        stable = stable_paths_for(unit)
        assert all((output / p).is_file() for p in stable)
        count = _host_n_events(output, (unit / 'batch.json').exists())
        reported = _reported_n_events(output, (unit / 'batch.json').exists())
        assert count == reported
        wall = runtime['wall_sec']
        run = UnitRun(count / wall, count, reported, wall, None, None, 0)
        record = UnitRecord(name, 'simulate-batch' if (unit / 'batch.json').exists() else 'simulate',
                            IMAGE, [run], False, count / wall)
        merge_host_metrics(record, output.parent / 'host_metrics.json')
        ctx = {'unit_dir': unit, 'output_dir': output}
        verdict = build_developer_verifier(ctx).run(ctx)
        rows.append({'unit': name, 'host_events': count,
                     'stable_sha256': {p: sha(output / p) for p in stable},
                     'verdict': dataclasses.asdict(verdict), 'passed': verdict.admissible})
    report = {'scope': 'Two local resource and semantic smoke tests; no rank or performance claim',
              'image': IMAGE, 'rows': rows, 'passed': all(r['passed'] for r in rows)}
    (destination / 'verdicts.json').write_text(json.dumps(report, indent=2, default=str) + '\n')
    print(json.dumps(report, default=str), flush=True)
    return 0 if report['passed'] else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    if args.verify:
        return verify(args.output)
    dest = args.output.resolve()
    dest.mkdir(parents=True, exist_ok=False)
    info = json.loads(subprocess.check_output(['docker', 'image', 'inspect', IMAGE]))[0]
    assert info['Id'] == IMAGE and info['Os'] == 'linux' and info['Architecture'] == 'amd64'
    assert not info['Config'].get('Volumes')
    assert not any(value.startswith('NVIDIA_VISIBLE_DEVICES=') for value in info['Config'].get('Env', []))
    (dest / 'image.json').write_text(json.dumps({'id': info['Id'], 'config': info['Config']}, indent=2))
    for name in UNITS:
        unit = UPSTREAM / 'units' / name
        run = dest / name
        staged, output = run / 'input', run / 'output'
        staged.mkdir(parents=True)
        output.mkdir()
        batch = (unit / 'batch.json').exists()
        selected = [unit / 'card.toml'] + ([unit / 'batch.json', *sorted((unit / 'scenarios').glob('*.json'))]
                    if batch else [unit / 'scenario.json'])
        manifest = {entry['path']: entry['sha256'] for entry in json.loads((unit / 'manifest.json').read_text())['files']}
        for path in selected:
            relative = path.relative_to(unit)
            if relative.as_posix() in manifest:
                assert sha(path) == manifest[relative.as_posix()]
            target = staged / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        container = 't3-resources-20261011-' + name
        command = ['docker', 'run', '--name', container, '--network=none', '--read-only', '--user=65534:65534',
                   '--cap-drop=ALL', '--security-opt=no-new-privileges', '--cpus=4', '--memory=16g', '--memory-swap=16g',
                   '--pids-limit=256', '--ulimit=nofile=1024:1024', '--ulimit=nproc=256:256',
                   '--ulimit=fsize=268435456:268435456', '--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777',
                   '--mount', f'type=bind,source={staged},target=/input,readonly',
                   '--mount', f'type=bind,source={output},target=/output', '-e', 'QFBENCH_SEED=0',
                   '-e', 'QFBENCH_NETWORK=none', IMAGE]
        command += (['simulate-batch', '--batch-dir', '/input/scenarios', '--out-dir', '/output'] if batch else
                    ['simulate', '--config', '/input/scenario.json', '--out', '/output/trace.parquet'])
        (run / 'command.json').write_text(json.dumps(command, indent=2))
        started = time.monotonic()
        try:
            with (run / 'console.txt').open('wb') as log:
                completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=300)
            info = json.loads(subprocess.check_output(['docker', 'inspect', container]))[0]
            wall = time.monotonic() - started
            runtime = {'exit_code': completed.returncode, 'oom_killed': info['State']['OOMKilled'],
                       'wall_sec': wall, 'timing_scope': 'Local run plus inspect, concurrent work; non-comparative',
                       'host_config': {k: info['HostConfig'][k] for k in ('NanoCpus', 'Memory', 'MemorySwap', 'PidsLimit', 'NetworkMode', 'ReadonlyRootfs')}}
            (run / 'runtime.json').write_text(json.dumps(runtime, indent=2))
            assert completed.returncode == 0 and not runtime['oom_killed']
            assert sum(p.stat().st_size for p in output.rglob('*') if p.is_file()) <= 256 * 1024 ** 2
            for path in output.rglob('*.parquet'):
                old = ROOT / 'project-evidence/t3-notices-20261009-v3' / name / 'output' / path.relative_to(output)
                assert sha(path) == sha(old), f'Stable bytes changed: {path.name}'
            print(name, 'exit=0; all stable parquet bytes unchanged', flush=True)
        finally:
            subprocess.run(['docker', 'rm', '-f', container], capture_output=True, timeout=30)
    command = ['docker', 'run', '--rm', '--network=none', '--read-only', '--user=65534:65534',
               '--cap-drop=ALL', '--security-opt=no-new-privileges', '--entrypoint=python',
               '-e', 'PYTHONPATH=/common:/upstream', '--mount', f'type=bind,source={UPSTREAM},target=/upstream,readonly',
               '--mount', f'type=bind,source={ROOT / "Agenthon2026-public/common"},target=/common,readonly',
               '--mount', f'type=bind,source={Path(__file__).resolve()},target=/verify.py,readonly',
               '--mount', f'type=bind,source={dest},target=/evidence', 't4-e2-test-harness:v251',
               '/verify.py', '--verify', '--output', '/evidence']
    with (dest / 'verifier.log').open('wb') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=120)
    print('Current verifier exit', result.returncode, flush=True)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
