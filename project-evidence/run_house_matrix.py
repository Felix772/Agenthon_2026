"""Run unchanged image entrypoints via an isolated synthetic proxy; never House."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / 'project-evidence'
CASES = ['bare', 'fenced', 'separated', 'thinking_default', 'prefixed', 'empty',
         'truncated', 'malformed', 'nonobject', 'http401', 'http403', 'http429',
         'http500', 'http503', 'recover429', 'recover503', 'slow', 'deadline',
         'missing', 'duplicate', 'unit', 'quote', 'unsupported', 'repair']
SUPPORTED = {'bare', 'fenced', 'separated', 'thinking_default', 'recover429', 'recover503', 'repair'}


def run(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', timeout=60, **kw)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--baseline', action='store_true')
    parser.add_argument('--diagnostics', action='store_true')
    args = parser.parse_args()
    assert all(c.isalnum() or c == '-' for c in args.run_id)
    out = E / args.run_id
    out.mkdir(exist_ok=False)
    network, mock = args.run_id + '-net', args.run_id + '-proxy'
    metadata = json.loads(run(['docker', 'image', 'inspect', args.image], check=True).stdout)[0]
    image = metadata['Id']
    record = {'image': image, 'repo_digests': metadata.get('RepoDigests'),
              'scope': 'Synthetic isolated protocol only; platform crash root cause unproven',
              'cases': [], 'baseline': args.baseline}
    record['fixture_hashes'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [Path(__file__), E/'house_response_fixture.py']}
    common = ['docker', 'run', '--read-only', '--user', '65534:65534', '--cap-drop=ALL',
              '--security-opt', 'no-new-privileges', '--cpus', '2', '--memory', '1g',
              '--memory-swap', '1g', '--pids-limit', '256', '--ulimit', 'nproc=256:256',
              '--ulimit', 'nofile=1024:1024', '--ulimit', 'fsize=67108864:67108864',
              '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777']
    created = False
    try:
        run(['docker', 'network', 'create', '--internal', network], check=True); created = True
        run(['docker', 'run', '-d', '--name', mock, '--network', network,
             '--network-alias', 'proxy', '--read-only', '--memory', '256m',
             '--mount', f'type=bind,src={E/"house_response_fixture.py"},dst=/fixture.py,readonly',
             '--entrypoint', 'python', image, '/fixture.py'], check=True)
        for _ in range(20):
            ready = run(['docker', 'exec', mock, 'python', '-c',
                         'import urllib.request; urllib.request.urlopen("http://localhost:8080")'])
            if not ready.returncode: break
            time.sleep(.2)
        else: raise RuntimeError('synthetic proxy not ready')
        for case in CASES:
            case_out = out / case; case_out.mkdir()
            timeout = 2 if case == 'slow' else 1 if case == 'deadline' else 15
            name = args.run_id + '-' + case
            argv = common + ['--name', name, '--network', network,
                '--mount', f'type=bind,src={E/"t4-container-input"},dst=/input,readonly',
                '--mount', f'type=bind,src={case_out},dst=/output',
                '-e', 'MODEL_ENDPOINT=http://house.invalid', '-e', 'MODEL_NAME='+case,
                '-e', 'MODEL_TOKEN=synthetic-not-a-credential', '-e', 'HTTP_PROXY=http://proxy:8080',
                '-e', 'HTTPS_PROXY=http://proxy:8080', '-e', 'NO_PROXY=', '-e', 'no_proxy=',
                '-e', 'QFBENCH_SEED=0', image, 'analyze', '--task', '/input/task.json',
                '--corpus', '/input/corpus', '--out', '/output/answer.json', '--timeout', str(timeout)]
            if args.diagnostics: argv += ['--diagnostics', '/output/diagnostics.json']
            start = time.monotonic()
            try:
                result = run(argv)
                elapsed = time.monotonic() - start
                state = json.loads(run(['docker', 'inspect', name, '--format', '{{json .State}}'], check=True).stdout)
            finally:
                run(['docker', 'rm', '-f', name])
            answer_path = case_out/'answer.json'
            answer = json.loads(answer_path.read_text(encoding='utf-8')) if answer_path.exists() else {}
            expected_success = case in SUPPORTED and not (args.baseline and case in {'fenced', 'thinking_default'})
            row = {'case': case, 'exit_code': result.returncode, 'elapsed_sec': elapsed,
                   'oom_killed': state['OOMKilled'], 'output_bytes': answer_path.stat().st_size if answer_path.exists() else 0,
                   'entity_count': len(answer.get('entity_predictions', [])),
                   'quiet': not result.stdout and not result.stderr, 'expected_success': expected_success}
            if expected_success:
                # Validate with source implementation outside the candidate process.
                import sys
                sys.path.insert(0, str(ROOT/'analysis-agent'))
                from analysis_agent.contract import validate_answer
                from analysis_agent.retrieval import RetrievalIndex
                task = json.loads((E/'t4-container-input/task.json').read_text())
                validate_answer(task, answer)
                index = RetrievalIndex.load(E/'t4-container-input/corpus', task['cutoff_date'])
                for entity in answer['entity_predictions']:
                    for cite in entity['claims']:
                        index.validate_span(cite['doc_id'], cite['span_start'], cite['span_end'])
            row['passed'] = (result.returncode == (0 if expected_success else 2) and
                row['entity_count'] == (4 if expected_success else 0) and row['quiet'] and
                not row['oom_killed'] and elapsed < timeout + 5)
            record['cases'].append(row)
            (out/'report.json').write_text(json.dumps(record, indent=2)+'\n')
            print(case, result.returncode, row['entity_count'], round(elapsed, 3), flush=True)
        record['proxy_stats'] = json.loads(run(['docker', 'exec', mock, 'python', '-c',
            'import urllib.request; print(urllib.request.urlopen("http://localhost:8080").read().decode())'], check=True).stdout)
        for row in record['cases']:
            case = row['case']; stat = record['proxy_stats'].get(case, {'sends': 0, 'shape_ok': True})
            expected_sends = (0 if case == 'deadline' else 1 if case in
                {'empty', 'truncated', 'malformed', 'nonobject', 'http401', 'http403', 'slow'} else
                3 if case in {'recover429', 'recover503', 'repair'} else 2)
            row['send_count_ok'] = stat['sends'] == expected_sends
            row['passed'] &= row['send_count_ok'] and stat['shape_ok']
        record['passed'] = all(row['passed'] for row in record['cases'])
    finally:
        if created:
            run(['docker', 'rm', '-f', mock]); run(['docker', 'network', 'rm', network])
        (out/'report.json').write_text(json.dumps(record, indent=2)+'\n')
    raise SystemExit(0 if record.get('passed') else 1)


if __name__ == '__main__': main()
