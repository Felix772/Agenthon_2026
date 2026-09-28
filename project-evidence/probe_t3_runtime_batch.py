"""Repeat a representative batch under C05 limits; retain actual bytes."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / 'project-evidence'
OUT = E / 't3-c05-runtime-batch-20260925-v2'
SOURCE = ROOT / '.validation/track3-current-update/units/t3-gbatch-homog-4'
IMAGE = 'simulation-agent:runtime-20260925'
REF = '1504b37b8472f7f949d4b8f80b7c8b2b71ab0e43'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUT.mkdir(exist_ok=False)
    staged = OUT / 'input/scenarios'
    staged.mkdir(parents=True)
    def blob(relative):
        return subprocess.check_output(['git', '-C', str(ROOT / 'track3-simulation-public'),
            'show', f'{REF}:units/{SOURCE.name}/{relative}'])
    manifest = {f['path']: f for f in json.loads(blob('manifest.json'))['files']}
    inputs = []
    for source in sorted((SOURCE / 'scenarios').glob('*.json')):
        relative = source.relative_to(SOURCE).as_posix()
        raw = blob(relative)
        assert hashlib.sha256(raw).hexdigest() == manifest[relative]['sha256']
        (staged / source.name).write_bytes(raw)
        inputs.append({'path': relative, 'sha256': digest(staged / source.name)})
    report = {'image': IMAGE, 'image_id': subprocess.check_output(
        ['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}'], text=True).strip(),
        'unit': SOURCE.name, 'source_ref': REF, 'inputs': inputs, 'extra_work_tmpfs': False,
        'official_platform': False, 'records': []}
    path = OUT / 'report.json'
    baseline = E / 't3-02-batches-root/outputs' / SOURCE.name
    expected = {p.relative_to(baseline).as_posix(): digest(p) for p in baseline.rglob('*.parquet')}
    assert len(expected) == 8
    signatures = []
    for repeat in [1, 2]:
        output = OUT / str(repeat)
        output.mkdir()
        name = f'agenthon-t3-runtime-batch-20260925-{repeat}'
        command = ['docker', 'run', '--name', name, '--network', 'none', '--read-only',
            '--user', '65534:65534', '--cap-drop=ALL', '--security-opt', 'no-new-privileges',
            '--cpus', '4', '--memory', '1g', '--memory-swap', '1g', '--pids-limit', '256',
            '--ulimit', 'nofile=1024:1024', '--ulimit', 'nproc=256:256',
            '--ulimit', 'fsize=67108864:67108864',
            '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777',
            '--mount', f'type=bind,src={staged.parent},dst=/input,readonly',
            '--mount', f'type=bind,src={output},dst=/output', IMAGE,
            'simulate-batch', '--batch-dir', '/input/scenarios', '--out-dir', '/output']
        row = {'repeat': repeat, 'command': command, 'status': 'running'}
        report['records'].append(row)
        path.write_text(json.dumps(report, indent=2) + '\n')
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=300)
            row.update(exit_code=result.returncode, stdout=result.stdout[-5000:], stderr=result.stderr[-5000:])
            assert result.returncode == 0
            actual = {p.relative_to(output).as_posix(): digest(p) for p in output.rglob('*.parquet')}
            assert actual == expected
            counts = {}
            for sub in sorted(output.glob('sub_*')):
                counts[sub.name] = json.loads((sub / 'events.json').read_text())['n_events']
                assert counts[sub.name] == json.loads((baseline / sub.name / 'events.json').read_text())['n_events']
            aggregate = json.loads((output / 'batch_events.json').read_text())
            assert aggregate['total_events'] == sum(counts.values())
            row.update(hashes=actual, event_counts=counts, aggregate=aggregate,
                       output_bytes=sum(p.stat().st_size for p in output.rglob('*') if p.is_file()))
            assert row['output_bytes'] <= 64 * 1024**2
            signatures.append((actual, counts))
            row['status'] = 'passed'
            print('PASS batch repeat', repeat, flush=True)
        finally:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
            path.write_text(json.dumps(report, indent=2) + '\n')
    assert signatures[0] == signatures[1]
    report.update(passed=True, repeats=2, actual_trace_and_ledger_bytes_identical=True,
                  full_roster_this_image=False, timing_comparison=False)
    path.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
