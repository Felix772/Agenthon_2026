"""Run the frozen T4 image against scorer-5.2.1 public units and a fake House route."""

import argparse
from collections import Counter
import hashlib
import importlib.metadata
import importlib.resources
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'project-evidence'
UPSTREAM = ROOT / 'track4-analysis-public'
PINNED_UPSTREAM = 'fe313cee2865fbfbe47b65a8fcf7b830a40ea141'
FIXTURE = ROOT / '.validation/t4-origin-5.2.1-20260930/units'


def command(argv, **options):
    return subprocess.run(argv, capture_output=True, text=True, encoding='utf-8',
                          timeout=110, **options)


def check_fixture():
    git = ['git', '-c', f'safe.directory={UPSTREAM.as_posix()}', '-C', str(UPSTREAM)]
    observed = command(git + ['rev-parse', 'fe313ce'], check=True).stdout.strip()
    if observed != PINNED_UPSTREAM:
        raise RuntimeError('scorer-5.2.1 source identity changed')
    unit_changes = command(git + ['diff', '--name-only', observed + '..HEAD', '--', 'units'], check=True)
    if unit_changes.stdout.strip():
        raise RuntimeError('working-tree unit roster differs from scorer-5.2.1 release')
    paths = command(git + ['ls-tree', '-r', '--name-only', observed, '--', 'units'],
                    check=True).stdout.splitlines()
    local = list(FIXTURE.rglob('*'))
    if any(path.is_symlink() for path in local):
        raise RuntimeError('fixture contains a symbolic link')
    if {path.relative_to(FIXTURE.parent).as_posix() for path in local if path.is_file()} != set(paths):
        raise RuntimeError('fixture inventory differs from raw Git tree')
    for path in paths:
        raw = subprocess.run(git + ['show', f'{observed}:{path}'], capture_output=True,
                             check=True, timeout=15).stdout
        if (FIXTURE.parent / path).read_bytes() != raw:
            raise RuntimeError(f'fixture differs from raw Git blob: {path}')
    return len(paths)


def output_tree_bytes(directory):
    paths = list(directory.rglob('*'))
    if any(path.is_symlink() for path in paths):
        raise RuntimeError('output tree contains a symbolic link')
    return sum(path.stat().st_size for path in paths if path.is_file())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    # This frozen fixture intentionally tests the current 5.2.0 contract only.
    args.thinking = args.baseline = False
    if not re.fullmatch('[a-z0-9-]{1,48}', args.run_id):
        parser.error('run-id must be a short lowercase Docker-safe name')
    sys.path.insert(0, str(ROOT / '.validation/t4-core-20260929-v1'))
    from analysis_agent.contract import validate_answer
    from analysis_agent.retrieval import RetrievalIndex

    identity_paths = [Path(__file__), EVIDENCE / 't4_core_shape_proxy_20260929.py',
                      ROOT / '.validation/t4-core-20260929-v1/analysis_agent/contract.py',
                      ROOT / '.validation/t4-core-20260929-v1/analysis_agent/retrieval.py',
                      Path(str(importlib.resources.files('qfbench2_common') /
                               'schemas/analysis.schema.json'))]
    def identities():
        return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in identity_paths}
    validator_identity = identities()

    blob_count = check_fixture()
    metadata = json.loads(command(['docker', 'image', 'inspect', args.image], check=True).stdout)[0]
    image = metadata['Id']
    out = EVIDENCE / args.run_id
    out.mkdir(exist_ok=False)
    network, mock = args.run_id + '-net', args.run_id + '-proxy'
    report = {
        'scope': 'Synthetic interface and exact public scorer-5.2.1 unit shapes; no House/NLI/quality result',
        'upstream_revision': PINNED_UPSTREAM,
        'raw_git_blob_count': blob_count,
        'image': image,
        'validator_and_harness_hashes': validator_identity,
        'validator_toolkit_version': importlib.metadata.version('qfbench2-common'),
        'baseline': args.baseline,
        'thinking_mode': args.thinking,
        'resource_scope': 'Conservative local probe: 2 CPUs / 1 GiB, below the official CPU/RAM allocation',
        'harness_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in [Path(__file__), EVIDENCE / 't4_core_shape_proxy_20260929.py']},
        'units': [],
    }
    common = ['docker', 'run', '--read-only', '--user', '65534:65534', '--cap-drop=ALL',
              '--security-opt', 'no-new-privileges', '--cpus', '2', '--memory', '1g',
              '--memory-swap', '1g', '--pids-limit', '256', '--ulimit', 'nproc=256:256',
              '--ulimit', 'nofile=1024:1024', '--ulimit', 'fsize=67108864:67108864',
              '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777']
    created = False
    try:
        probe = ('import os,json,resource; from pathlib import Path; refused=[]\n'
                 'for name in ["/forbidden","/input/forbidden"]:\n'
                 ' try: Path(name).write_text("x")\n'
                 ' except OSError: refused.append(name)\n'
                 'print(json.dumps({"uid":os.getuid(),"write_refused":refused,'
                 '"tmp_mount":[x for x in Path("/proc/mounts").read_text().splitlines() if " /tmp " in x],'
                 '"limits":{n:resource.getrlimit(getattr(resource,n)) for n in ["RLIMIT_NOFILE","RLIMIT_NPROC","RLIMIT_FSIZE"]},'
                 '"status":[x for x in Path("/proc/self/status").read_text().splitlines() if x.startswith(("CapEff:","NoNewPrivs:"))],'
                 '"memory_max":Path("/sys/fs/cgroup/memory.max").read_text().strip(),'
                 '"swap_max":Path("/sys/fs/cgroup/memory.swap.max").read_text().strip(),'
                 '"pids_max":Path("/sys/fs/cgroup/pids.max").read_text().strip()}))')
        runtime = command(common + ['--rm', '--network=none', '--mount',
            f'type=bind,src={FIXTURE},dst=/input,readonly', '--entrypoint=python', image, '-c', probe], check=True)
        rt = json.loads(runtime.stdout)
        assert rt['uid'] == 65534 and rt['write_refused'] == ['/forbidden', '/input/forbidden']
        assert rt['limits'] == {'RLIMIT_NOFILE': [1024, 1024], 'RLIMIT_NPROC': [256, 256],
                                'RLIMIT_FSIZE': [67108864, 67108864]}
        assert rt['memory_max'] == '1073741824' and rt['swap_max'] == '0' and rt['pids_max'] == '256'
        assert any(all(flag in line for flag in ('noexec', 'nosuid', 'nodev', 'size=65536k'))
                   for line in rt['tmp_mount'])
        assert all(line.split()[-1] == ('1' if line.startswith('NoNewPrivs') else '0000000000000000')
                   for line in rt['status'])
        report['runtime_probe'] = rt
        command(['docker', 'network', 'create', '--internal', network], check=True)
        created = True
        command(['docker', 'run', '-d', '--name', mock, '--network', network,
                 '--network-alias', 'proxy', '--read-only', '--memory', '256m',
                 '--mount', f'type=bind,src={EVIDENCE / "t4_core_shape_proxy_20260929.py"},dst=/proxy.py,readonly',
                 '--entrypoint', 'python', image, '/proxy.py'], check=True)
        for _ in range(20):
            ready = command(['docker', 'exec', mock, 'python', '-c',
                             'import urllib.request; urllib.request.urlopen("http://localhost:8080")'])
            if ready.returncode == 0:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError('synthetic proxy did not start')
        for unit in sorted(FIXTURE.iterdir()):
            if not unit.is_dir():
                continue
            task = json.loads((unit / 'task.json').read_text(encoding='utf-8'))
            index = RetrievalIndex.load(unit / 'corpus', task['cutoff_date'])
            name = args.run_id + '-' + unit.name.lower().replace('_', '-')
            case_out = out / unit.name
            case_out.mkdir()
            model = ('thinking-' if args.thinking else 'bare-') + unit.name
            argv = common + ['--name', name, '--network', network,
                '--mount', f'type=bind,src={unit},dst=/input,readonly',
                '--mount', f'type=bind,src={case_out},dst=/output',
                '-e', 'MODEL_ENDPOINT=http://house.invalid', '-e', 'MODEL_NAME='+model,
                '-e', 'MODEL_TOKEN=synthetic-not-a-credential',
                '-e', 'HTTP_PROXY=http://proxy:8080', '-e', 'HTTPS_PROXY=http://proxy:8080',
                '-e', 'NO_PROXY=', '-e', 'no_proxy=', '-e', 'QFBENCH_SEED=0',
                image, 'analyze', '--task', '/input/task.json', '--corpus', '/input/corpus',
                '--out', '/output/answer.json', '--timeout', '90']
            start = time.monotonic()
            try:
                result = command(argv)
                elapsed = time.monotonic() - start
                state = json.loads(command(['docker', 'inspect', name,
                    '--format', '{{json .State}}'], check=True).stdout)
            finally:
                command(['docker', 'rm', '-f', name])
            answer_path = case_out / 'answer.json'
            answer = json.loads(answer_path.read_text(encoding='utf-8')) if answer_path.exists() else {}
            expect_success = not (args.baseline and args.thinking)
            row = {'unit': unit.name, 'target_type': task['target']['type'],
                   'roster_size': len(task['entities']),
                   'roster_ids': [entity['entity_id'] for entity in task['entities']],
                   'eligible_corpus_docs': len(index.documents),
                   'exit_code': result.returncode, 'elapsed_sec': elapsed,
                   'oom_killed': state['OOMKilled'], 'quiet': not result.stdout and not result.stderr,
                   'output_bytes': answer_path.stat().st_size if answer_path.exists() else 0,
                   'output_tree_bytes': output_tree_bytes(case_out),
                   'prediction_count': len(answer.get('entity_predictions', [])),
                   'expected_success': expect_success}
            if expect_success:
                validate_answer(task, answer)
                for prediction in answer['entity_predictions']:
                    for claim in prediction['claims']:
                        index.validate_span(claim['doc_id'], claim['span_start'], claim['span_end'],
                                            quote=claim['claim'], entity_id=prediction['entity_id'])
            row['passed'] = (result.returncode == (0 if expect_success else 2)
                             and row['prediction_count'] == (len(task['entities']) if expect_success else 0)
                             and row['quiet'] and not row['oom_killed'] and elapsed < 100
                             and row['output_tree_bytes'] <= 64 * 1024 * 1024)
            report['units'].append(row)
            (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
            print(unit.name, result.returncode, row['prediction_count'], round(elapsed, 2), flush=True)
        stats = json.loads(command(['docker', 'exec', mock, 'python', '-c',
            'import urllib.request; print(urllib.request.urlopen("http://localhost:8080").read().decode())'],
            check=True).stdout)
        report['proxy_stats'] = stats
        for row in report['units']:
            model = ('thinking-' if args.thinking else 'bare-') + row['unit']
            stat = stats.get(model, {})
            expected_sends = (2 if args.baseline and args.thinking
                              else math.ceil(row['roster_size'] / 3))
            row['send_count_ok'] = stat.get('sends') == expected_sends
            row['entity_requests_ok'] = Counter(stat.get('entity_requests', [])) == Counter(row['roster_ids'])
            row['passed'] &= (row['send_count_ok'] and row['entity_requests_ok']
                              and 0 < stat.get('sends', 0) <= 25 and stat.get('shape_ok') is True)
        report['total_sends'] = sum(value.get('sends', 0) for value in stats.values())
        report['validator_identity_unchanged'] = identities() == validator_identity
        report['passed'] = (len(report['units']) == 11 and report['total_sends'] == 29
                            and report['validator_identity_unchanged']
                            and all(x['passed'] for x in report['units']))
    finally:
        if created:
            command(['docker', 'rm', '-f', mock])
            command(['docker', 'network', 'rm', network])
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    raise SystemExit(0 if report.get('passed') else 1)


if __name__ == '__main__':
    main()
