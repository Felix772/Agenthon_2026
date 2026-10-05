"""Frozen four-case optional-reason test of the real immutable-image entrypoint."""
import argparse
from datetime import datetime
import hashlib
import importlib.metadata
import importlib.resources
import json
from pathlib import Path
import re
import subprocess
import sys
import time

from run_t4_core_shapes_20260929 import check_fixture, command, output_tree_bytes, PINNED_UPSTREAM

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'project-evidence'
SOURCE = ROOT / '.validation/t4-origin-5.2.0-20260929'
UNIT = SOURCE / 'units/t4-EXAMPLE-eps-beat'
CASES = ('defaultoff', 'valid', 'invalid', 'slow')
INVOCATION_SECONDS = 45


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    if not re.fullmatch('[a-z0-9-]{1,48}', args.run_id):
        parser.error('run-id must be a short lowercase Docker-safe name')
    sys.path[:0] = [str(ROOT/'analysis-agent'), str(SOURCE)]
    from analysis_agent.contract import validate_answer
    from analysis_agent.retrieval import RetrievalIndex
    from baselines.guardrails_example.citation_rail import CorpusDoc, check_submitted_reasons

    identity_paths = [Path(__file__), EVIDENCE/'t4_a3_proxy_20260929.py',
        EVIDENCE/'run_t4_core_shapes_20260929.py', SOURCE/'baselines/guardrails_example/citation_rail.py',
        ROOT/'analysis-agent/analysis_agent/contract.py', ROOT/'analysis-agent/analysis_agent/retrieval.py',
        Path(str(importlib.resources.files('qfbench2_common')/'schemas/analysis.schema.json'))]
    def identities():
        return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in identity_paths}
    hashes = identities()
    blob_count = check_fixture()
    metadata = json.loads(command(['docker', 'image', 'inspect', args.image], check=True).stdout)[0]
    image = metadata['Id']
    if metadata['Os'] != 'linux' or metadata['Architecture'] != 'amd64':
        raise RuntimeError('candidate is not linux/amd64')
    if importlib.metadata.version('qfbench2-common') != '2.5.0':
        raise RuntimeError('external validator requires toolkit 2.5.0')
    task = json.loads((UNIT/'task.json').read_text(encoding='utf-8'))
    index = RetrievalIndex.load(UNIT/'corpus', task['cutoff_date'])
    corpus = {key: CorpusDoc(doc.doc_id, doc.text, doc.doc_date) for key, doc in index.documents.items()}
    out = EVIDENCE/args.run_id
    out.mkdir(exist_ok=False)
    network, proxy = args.run_id+'-net', args.run_id+'-proxy'
    report = {'scope': 'Synthetic A3 format, budget, immutable predictions, and watchdog recovery only',
              'upstream_revision': PINNED_UPSTREAM, 'raw_git_blob_count': blob_count,
              'image': image, 'validator_toolkit_version': importlib.metadata.version('qfbench2-common'),
              'input_unit': UNIT.name, 'identity_hashes': hashes,
              'invocation_timeout_seconds': INVOCATION_SECONDS,
              'expected_worker_cutoff_seconds': 40.5,
              'expected_normal_sends': {'defaultoff': 1, 'valid': 2, 'invalid': 2, 'slow': 2},
              'cases': [], 'passed': False}
    common = ['docker', 'run', '--read-only', '--user', '65534:65534', '--cap-drop=ALL',
              '--security-opt', 'no-new-privileges', '--cpus', '2', '--memory', '1g',
              '--memory-swap', '1g', '--pids-limit', '256', '--ulimit', 'nproc=256:256',
              '--ulimit', 'nofile=1024:1024', '--ulimit', 'fsize=67108864:67108864',
              '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777']
    created, base = False, None
    try:
        command(['docker', 'network', 'create', '--internal', network], check=True)
        created = True
        command(['docker', 'run', '-d', '--name', proxy, '--network', network, '--network-alias', 'proxy',
            '--read-only', '--memory', '256m', '--mount',
            f'type=bind,src={EVIDENCE / "t4_a3_proxy_20260929.py"},dst=/proxy.py,readonly',
            '--entrypoint', 'python', image, '/proxy.py'], check=True)
        for _ in range(20):
            ready = command(['docker', 'exec', proxy, 'python', '-c',
                'import urllib.request; urllib.request.urlopen("http://localhost:8080")'])
            if ready.returncode == 0:
                break
            time.sleep(.2)
        else:
            raise RuntimeError('synthetic proxy did not start')
        for case in CASES:
            name, case_out = args.run_id+'-'+case, out/case
            case_out.mkdir()
            argv = common + ['--name', name, '--network', network,
                '--mount', f'type=bind,src={UNIT},dst=/input,readonly',
                '--mount', f'type=bind,src={case_out},dst=/output',
                '-e', 'MODEL_ENDPOINT=http://house.invalid', '-e', 'MODEL_NAME='+case,
                '-e', 'MODEL_TOKEN=synthetic-not-a-credential',
                '-e', 'HTTP_PROXY=http://proxy:8080', '-e', 'HTTPS_PROXY=http://proxy:8080',
                '-e', 'NO_PROXY=', '-e', 'no_proxy=', '-e', 'QFBENCH_SEED=0', image,
                'analyze', '--task', '/input/task.json', '--corpus', '/input/corpus',
                '--out', '/output/answer.json', '--timeout', str(INVOCATION_SECONDS),
                '--diagnostics', '/output/diagnostics.json']
            if case != 'defaultoff':
                argv.append('--reasons')
            started = time.monotonic()
            try:
                result = command(argv)
                elapsed = time.monotonic()-started
                state = json.loads(command(['docker', 'inspect', name, '--format', '{{json .State}}'], check=True).stdout)
            finally:
                command(['docker', 'rm', '-f', name])
            path = case_out/'answer.json'
            answer = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            validate_answer(task, answer)
            for prediction in answer['entity_predictions']:
                for claim in prediction['claims']:
                    index.validate_span(claim['doc_id'], claim['span_start'], claim['span_end'],
                                        quote=claim['claim'], entity_id=prediction['entity_id'])
            findings = [finding.code for finding in check_submitted_reasons(answer, corpus, task['cutoff_date'])]
            without_reasons = {key: value for key, value in answer.items() if key != 'submitted_reasons'}
            if case == 'defaultoff':
                base = without_reasons
            diagnostics = json.loads((case_out/'diagnostics.json').read_text(encoding='utf-8'))
            container_duration = (datetime.fromisoformat(state['FinishedAt'].replace('Z', '+00:00'))
                                  - datetime.fromisoformat(state['StartedAt'].replace('Z', '+00:00'))).total_seconds()
            row = {'case': case, 'exit_code': result.returncode, 'elapsed_container_wall_sec': elapsed,
                   'container_process_duration_sec': container_duration,
                   'participant_elapsed_sec': diagnostics['elapsed_sec'], 'diagnostics': diagnostics,
                   'oom_killed': state['OOMKilled'], 'quiet': not result.stdout and not result.stderr,
                   'output_tree_bytes': output_tree_bytes(case_out), 'official_reason_findings': findings,
                   'reasons_present': 'submitted_reasons' in answer, 'base_unchanged': without_reasons == base}
            row['passed'] = (result.returncode == 0 and not state['OOMKilled'] and row['quiet']
                and row['base_unchanged'] and not findings and row['reasons_present'] == (case == 'valid')
                and row['output_tree_bytes'] <= 64*1024*1024 and elapsed < 60
                and 0 <= container_duration <= INVOCATION_SECONDS
                and diagnostics['elapsed_sec'] < INVOCATION_SECONDS)
            if case == 'slow':
                row['passed'] &= diagnostics['stage'] == 'watchdog' and 40 <= diagnostics['elapsed_sec'] < 45
            report['cases'].append(row)
            (out/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
            print(case, result.returncode, round(elapsed, 2), row['reasons_present'], flush=True)
        stats = json.loads(command(['docker', 'exec', proxy, 'python', '-c',
            'import urllib.request; print(urllib.request.urlopen("http://localhost:8080").read().decode())'], check=True).stdout)
        report['proxy_stats'] = stats
        report['total_sends'] = sum(stat.get('sends', 0) for stat in stats.values())
        report['proxy_case_keys_ok'] = set(stats) == set(CASES)
        expected_projection = [{'entity_id': row['entity_id'], **{key: row[key] for key in
            ('label', 'point_forecast', 'interval', 'label_probs') if key in row}} for row in base['entity_predictions']]
        for row in report['cases']:
            stat, case = stats.get(row['case'], {}), row['case']
            row['ledger_ok'] = (stat.get('sends') == report['expected_normal_sends'][case]
                and stat.get('prediction_sends') == 1 and stat.get('reason_sends') == (0 if case == 'defaultoff' else 1)
                and stat.get('entity_requests') == [entity['entity_id'] for entity in task['entities']]
                and stat.get('shape_ok') is True)
            row['frozen_answer_ok'] = case == 'defaultoff' or stat.get('frozen_answer') == expected_projection
            row['passed'] &= row['ledger_ok'] and row['frozen_answer_ok']
            if case == 'slow':
                row['passed'] &= (stat.get('drip_chunks', 0) > 10
                    and stat.get('client_closed') is True
                    and stat.get('client_closed_elapsed_sec', 70) < 70
                    and stat.get('drip_completed_naturally') is False)
        report['identity_unchanged'] = hashes == identities()
        report['passed'] = (len(report['cases']) == 4 and report['identity_unchanged']
                            and report['proxy_case_keys_ok'] and report['total_sends'] == 7
                            and all(row['passed'] for row in report['cases']))
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        raise
    finally:
        if created:
            command(['docker', 'rm', '-f', proxy])
            command(['docker', 'network', 'rm', network])
        (out/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
