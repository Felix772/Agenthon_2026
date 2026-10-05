"""Registered core-v1 real ENTRYPOINT short-budget slow-drip test. No builds or uploads."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time
from run_t4_core_shapes_20260929 import command, check_fixture, output_tree_bytes

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'project-evidence/t14-experiments/R0_T4/v1/slow-drip'
UNIT = ROOT/'.validation/t4-origin-5.2.0-20260929/units/t4-auction-btc-202411-us7'
IMAGE = 'sha256:58b602ce944abbed0fe318e90a651c55739ff4344d72ddb515309190540d1bef'
NAME = 't4-r0-v1-slow-20260929'

def main():
    sys.path.insert(0, str(ROOT/'analysis-agent'))
    from analysis_agent.contract import validate_answer
    from analysis_agent.retrieval import RetrievalIndex
    check_fixture()
    OUT.mkdir(exist_ok=False)
    task = json.loads((UNIT/'task.json').read_text(encoding='utf-8'))
    index = RetrievalIndex.load(UNIT/'corpus', task['cutoff_date'])
    paths = [Path(__file__), ROOT/'project-evidence/t4_r0_slow_proxy_20260929.py',
             OUT.parent/'preregistration.json', ROOT/'analysis-agent/analysis_agent/contract.py',
             ROOT/'analysis-agent/analysis_agent/retrieval.py']
    def hashes():
        return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    report = {'image': IMAGE, 'identity_hashes': hashes(), 'cases': [], 'passed': False}
    base = None
    common = ['docker','run','--read-only','--user','65534:65534','--cap-drop=ALL',
        '--security-opt','no-new-privileges','--cpus','2','--memory','1g','--memory-swap','1g',
        '--pids-limit','256','--ulimit','nproc=256:256','--ulimit','nofile=1024:1024',
        '--ulimit','fsize=67108864:67108864','--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777']
    network, proxy = NAME+'-net', NAME+'-proxy'
    created = False
    try:
        command(['docker','network','create','--internal',network], check=True)
        created = True
        command(['docker','run','-d','--name',proxy,'--network',network,'--network-alias','proxy',
            '--read-only','--memory','256m','--mount',
            f'type=bind,src={paths[1]},dst=/proxy.py,readonly','--entrypoint','python',IMAGE,'/proxy.py'], check=True)
        for _ in range(20):
            ready = command(['docker','exec',proxy,'python','-c',
                'import urllib.request; urllib.request.urlopen("http://localhost:8080")'])
            if ready.returncode == 0:
                break
            time.sleep(.2)
        else:
            raise RuntimeError('proxy unavailable')
        for case in ('baseline','slow'):
            folder, name = OUT/case, NAME+'-'+case
            folder.mkdir()
            env = ['-e','MODEL_ENDPOINT=','-e','MODEL_NAME=','-e','MODEL_TOKEN='] if case == 'baseline' else [
                '-e','MODEL_ENDPOINT=http://house.invalid','-e','MODEL_NAME=slow',
                '-e','MODEL_TOKEN=synthetic-not-a-credential','-e','HTTP_PROXY=http://proxy:8080',
                '-e','HTTPS_PROXY=http://proxy:8080','-e','NO_PROXY=','-e','no_proxy=','-e','QFBENCH_SEED=0']
            argv = common + ['--name',name,'--network',network,'--mount',f'type=bind,src={UNIT},dst=/input,readonly',
                '--mount',f'type=bind,src={folder},dst=/output'] + env + [IMAGE,'analyze',
                '--task','/input/task.json','--corpus','/input/corpus','--out','/output/answer.json',
                '--timeout','45','--diagnostics','/output/diagnostics.json']
            started = time.monotonic()
            try:
                result = command(argv)
                elapsed = time.monotonic()-started
                state = json.loads(command(['docker','inspect',name,'--format','{{json .State}}'], check=True).stdout)
            finally:
                command(['docker','rm','-f',name])
            answer = json.loads((folder/'answer.json').read_text(encoding='utf-8'))
            validate_answer(task, answer)
            for row in answer['entity_predictions']:
                for claim in row['claims']:
                    index.validate_span(claim['doc_id'],claim['span_start'],claim['span_end'],
                                        quote=claim['claim'],entity_id=row['entity_id'])
            if case == 'baseline':
                base = answer
            diagnostics = json.loads((folder/'diagnostics.json').read_text(encoding='utf-8'))
            duration = (datetime.fromisoformat(state['FinishedAt'].replace('Z','+00:00'))-
                        datetime.fromisoformat(state['StartedAt'].replace('Z','+00:00'))).total_seconds()
            row = {'case':case,'started_at':state['StartedAt'],'finished_at':state['FinishedAt'],
                'container_process_duration_sec':duration,'docker_command_wall_sec':elapsed,
                'exit_code':result.returncode,'oom_killed':state['OOMKilled'],'diagnostics':diagnostics,
                'complete_rows':len(answer['entity_predictions']),'entire_answer_equals_baseline':answer==base,
                'answer_sha256':hashlib.sha256((folder/'answer.json').read_bytes()).hexdigest(),
                'quiet':not result.stdout and not result.stderr,'output_tree_bytes':output_tree_bytes(folder)}
            row['passed'] = (result.returncode == 0 and not state['OOMKilled'] and row['quiet']
                and 0 <= duration <= 45 and len(answer['entity_predictions']) == 7
                and answer == base and 'submitted_reasons' not in answer and row['output_tree_bytes'] <= 64*1024*1024)
            if case == 'slow':
                row['passed'] &= diagnostics['stage'] == 'watchdog' and 40 <= diagnostics['elapsed_sec'] < 45
            report['cases'].append(row)
            print(case, round(duration,3), row['passed'], flush=True)
        # Allow the next scheduled write to observe closure; no participant work remains.
        time.sleep(.3)
        stats = json.loads(command(['docker','exec',proxy,'python','-c',
            'import urllib.request; print(urllib.request.urlopen("http://localhost:8080").read().decode())'],check=True).stdout)
        report['proxy_stats'] = stats
        report['identity_unchanged'] = hashes() == report['identity_hashes']
        report['passed'] = (all(row['passed'] for row in report['cases']) and len(report['cases']) == 2
            and report['identity_unchanged'] and stats['sends'] == 1 and stats['shape_ok']
            and stats['drip_chunks'] > 10 and stats['client_closed'] and not stats['natural_end']
            and stats['client_closed_elapsed_sec'] < 70)
    finally:
        if created:
            command(['docker','rm','-f',proxy])
            command(['docker','network','rm',network])
        (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return 0 if report['passed'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
