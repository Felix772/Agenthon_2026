"""Run an immutable T4 image against all exact public unit shapes and a fake House route."""

import argparse
import hashlib
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
PINNED_UPSTREAM = '7b2bce1d80d96f5d5667d7f67bfaa945fa5d1491'
FIXTURE = ROOT / '.validation/t4-public-origin-main-20260927/units'


def command(argv, **options):
    return subprocess.run(argv, capture_output=True, text=True, encoding='utf-8',
                          timeout=70, **options)


def check_fixture():
    git = ['git', '-c', f'safe.directory={UPSTREAM.as_posix()}', '-C', str(UPSTREAM)]
    observed = command(git + ['rev-parse', 'origin/main'], check=True).stdout.strip()
    if observed != PINNED_UPSTREAM:
        raise RuntimeError('upstream revision changed; refresh the fixture and review')
    paths = command(git + ['ls-tree', '-r', '--name-only', observed, '--', 'units'],
                    check=True).stdout.splitlines()
    for path in paths:
        raw = subprocess.run(git + ['show', f'{observed}:{path}'], capture_output=True,
                             check=True, timeout=15).stdout
        if (ROOT / '.validation/t4-public-origin-main-20260927' / path).read_bytes() != raw:
            raise RuntimeError(f'fixture differs from raw Git blob: {path}')
    return len(paths)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--thinking', action='store_true')
    parser.add_argument('--baseline', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch('[a-z0-9-]{1,48}', args.run_id):
        parser.error('run-id must be a short lowercase Docker-safe name')
    sys.path.insert(0, str(ROOT / 'analysis-agent'))
    from analysis_agent.contract import validate_answer
    from analysis_agent.retrieval import RetrievalIndex

    blob_count = check_fixture()
    metadata = json.loads(command(['docker', 'image', 'inspect', args.image], check=True).stdout)[0]
    image = metadata['Id']
    out = EVIDENCE / args.run_id
    out.mkdir(exist_ok=False)
    network, mock = args.run_id + '-net', args.run_id + '-proxy'
    report = {
        'scope': 'Synthetic interface and exact public unit shapes; no House/NLI/quality result',
        'upstream_revision': PINNED_UPSTREAM,
        'raw_git_blob_count': blob_count,
        'image': image,
        'baseline': args.baseline,
        'thinking_mode': args.thinking,
        'harness_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in [Path(__file__), EVIDENCE / 't4_public_shape_proxy.py']},
        'units': [],
    }
    common = ['docker', 'run', '--read-only', '--user', '65534:65534', '--cap-drop=ALL',
              '--security-opt', 'no-new-privileges', '--cpus', '2', '--memory', '1g',
              '--memory-swap', '1g', '--pids-limit', '256', '--ulimit', 'nproc=256:256',
              '--ulimit', 'nofile=1024:1024', '--ulimit', 'fsize=67108864:67108864',
              '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777']
    created = False
    try:
        command(['docker', 'network', 'create', '--internal', network], check=True)
        created = True
        command(['docker', 'run', '-d', '--name', mock, '--network', network,
                 '--network-alias', 'proxy', '--read-only', '--memory', '256m',
                 '--mount', f'type=bind,src={EVIDENCE / "t4_public_shape_proxy.py"},dst=/proxy.py,readonly',
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
                '--out', '/output/answer.json', '--timeout', '45']
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
                   'roster_size': len(task['entities']), 'eligible_corpus_docs': len(index.documents),
                   'exit_code': result.returncode, 'elapsed_sec': elapsed,
                   'oom_killed': state['OOMKilled'], 'quiet': not result.stdout and not result.stderr,
                   'output_bytes': answer_path.stat().st_size if answer_path.exists() else 0,
                   'prediction_count': len(answer.get('entity_predictions', [])),
                   'expected_success': expect_success}
            if expect_success:
                validate_answer(task, answer)
                for prediction in answer['entity_predictions']:
                    for claim in prediction['claims']:
                        index.validate_span(claim['doc_id'], claim['span_start'], claim['span_end'])
            row['passed'] = (result.returncode == (0 if expect_success else 2)
                             and row['prediction_count'] == (len(task['entities']) if expect_success else 0)
                             and row['quiet'] and not row['oom_killed'] and elapsed < 50)
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
            row['passed'] &= row['send_count_ok'] and stat.get('shape_ok') is True
        report['passed'] = len(report['units']) == 11 and all(x['passed'] for x in report['units'])
    finally:
        if created:
            command(['docker', 'rm', '-f', mock])
            command(['docker', 'network', 'rm', network])
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    raise SystemExit(0 if report.get('passed') else 1)


if __name__ == '__main__':
    main()
