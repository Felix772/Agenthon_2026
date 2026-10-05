"""Evidence-only exact-image reproduction of a historical input-contract mismatch.

No participant code, source repositories, images, credentials or remote submissions change.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT/'track4-analysis-public'
OUT = ROOT/'project-evidence/t14-experiments/R0_T4/legacy-manifest-repro-v1'
IMAGE = 'sha256:58b602ce944abbed0fe318e90a651c55739ff4344d72ddb515309190540d1bef'
REVISIONS = {'legacy':'77d836da4c37362c3f219cae787bf9bc4f4f5d0f',
             'current':'febb5d2fb4cf8adcb6abc5a450cd5ae44c9a53d4'}
UNITS = ['t4-auction-btc-202411-us7','t4-cpicomp-202410-us11']

def save(path, value):
    path.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def git(*args):
    return subprocess.check_output(['git','-C',str(REPO),*args])

def main():
    OUT.mkdir(exist_ok=False)
    sys.path.insert(0,str(ROOT/'.validation/t4-core-20260929-v1'))
    from analysis_agent.retrieval import RetrievalIndex
    from analysis_agent.fallback import baseline_rows
    from analysis_agent.contract import validate_answer
    inputs=[]
    for variant, commit in REVISIONS.items():
        for unit in UNITS:
            prefix='units/'+unit+'/'
            paths=git('ls-tree','-r','--name-only',commit,'--','units/'+unit).decode().splitlines()
            target=OUT/'inputs'/variant/unit
            hashes={}
            for path in paths:
                relative=path[len(prefix):]
                dest=target/relative
                dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes(git('show',commit+':'+path))
                hashes[relative]=digest(dest)
            task=json.loads((target/'task.json').read_text())
            index=RetrievalIndex.load(target/'corpus',task['cutoff_date'])
            manifest=json.loads((target/'manifest.json').read_text())
            corpus=[e for e in manifest['files'] if e.get('role')=='corpus']
            inputs.append({'variant':variant,'revision':commit,'unit':unit,'file_hashes':hashes,
                'roster_size':len(task['entities']),'corpus_entries':len(corpus),
                'entity_marked_entries':sum(bool(e.get('entity_ids')) for e in corpus),
                'shared_entries':sum(e.get('shared') is True for e in corpus),
                'authorized_doc_entity_pairs':sum(d.admits(e['entity_id']) for d in index.documents.values() for e in task['entities']),
                'frozen_v1_fallback_rows':len(baseline_rows(task,index))})
    cases=[('legacy',UNITS[0],2,0),('legacy',UNITS[1],2,0),('current',UNITS[1],0,11)]
    registration={'registered_at_utc':datetime.now(timezone.utc).isoformat(),'image':IMAGE,
        'script_sha256':digest(Path(__file__)),'revisions':REVISIONS,'inputs':inputs,
        'hypothesis':'Historical manifest without explicit ownership cannot authorize frozen-v1 retrieval or fallback, despite identical historical task/corpus data.',
        'expected_cases':[{'variant':v,'unit':u,'exit_code':e,'prediction_rows':n} for v,u,e,n in cases],
        'runtime':{'network':'none','model_credentials':'absent','timeout_seconds':45,'uid':65534,'cpus':2,'memory':'1g'},
        'reused_current_auction_control':'project-evidence/t14-experiments/R0_T4/v1/slow-drip/baseline/answer.json',
        'acceptance':'Legacy cases must exit2 with zero rows and prediction-stage ContractError; current CPI must exit0 with11 schema-valid rows. Observing this reproduces a possible failure mechanism, not the online root cause.',
        'online_input_revision_known':False,'online_scorer_version_known':False}
    save(OUT/'registration.json',registration)
    report={'image':IMAGE,'registration_sha256':digest(OUT/'registration.json'),'cases':[],
        'online_root_cause_proven':False,'scope':'Offline historical/current input differential with the immutable submitted image'}
    try:
        for number,(variant,unit,expected_exit,count) in enumerate(cases):
            inp=OUT/'inputs'/variant/unit
            output=OUT/(variant+'-'+unit)
            output.mkdir()
            name='t4-legacy-repro-'+str(number)
            cmd=['docker','run','--name',name,'--network=none','--read-only','--user','65534:65534',
                '--cap-drop=ALL','--security-opt','no-new-privileges','--cpus','2','--memory','1g','--memory-swap','1g',
                '--pids-limit','256','--ulimit','nproc=256:256','--ulimit','nofile=1024:1024','--ulimit','fsize=67108864:67108864',
                '--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777',
                '--mount',f'type=bind,src={inp},dst=/input,readonly','--mount',f'type=bind,src={output},dst=/output',
                '-e','MODEL_ENDPOINT=','-e','MODEL_NAME=','-e','MODEL_TOKEN=',IMAGE,
                'analyze','--task','/input/task.json','--corpus','/input/corpus/','--out','/output/answer.json',
                '--timeout','45','--diagnostics','/output/diagnostics.json']
            started=time.monotonic()
            try:
                result=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
                wall=time.monotonic()-started
                state=json.loads(subprocess.check_output(['docker','inspect',name,'--format','{{json .State}}'],text=True))
            finally:
                subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=15)
            (output/'container.log').write_text(result.stdout+result.stderr,encoding='utf-8')
            answer=json.loads((output/'answer.json').read_text())
            diag=json.loads((output/'diagnostics.json').read_text())
            if expected_exit==0:
                task=json.loads((inp/'task.json').read_text())
                validate_answer(task,answer)
                index=RetrievalIndex.load(inp/'corpus',task['cutoff_date'])
                for row in answer['entity_predictions']:
                    for claim in row['claims']:
                        index.validate_span(claim['doc_id'],claim['span_start'],claim['span_end'],quote=claim['claim'],entity_id=row['entity_id'])
            duration=(datetime.fromisoformat(state['FinishedAt'].replace('Z','+00:00'))-
                      datetime.fromisoformat(state['StartedAt'].replace('Z','+00:00'))).total_seconds()
            row={'variant':variant,'unit':unit,'exit_code':result.returncode,'rows':len(answer.get('entity_predictions',[])),
                'answer_sha256':digest(output/'answer.json'),'diagnostics':diag,'state':state,
                'container_duration_seconds':duration,'docker_command_wall_seconds':wall,
                'quiet':not result.stdout and not result.stderr,'expected_exit_code':expected_exit}
            row['matches_registered_expectation']=(result.returncode==expected_exit and row['rows']==count and row['quiet']
                and not state['OOMKilled'] and duration<45
                and (expected_exit==0 or (diag['stage']=='prediction' and diag['exception_category']=='ContractError')))
            report['cases'].append(row)
            save(OUT/'report.json',report)
            print(variant,unit,result.returncode,row['rows'],round(duration,3),row['matches_registered_expectation'],flush=True)
        report['inputs_unchanged']=all(digest(OUT/'inputs'/r['variant']/r['unit']/p)==h for r in inputs for p,h in r['file_hashes'].items())
        report['passed']=report['inputs_unchanged'] and all(r['matches_registered_expectation'] for r in report['cases'])
    finally:
        save(OUT/'report.json',report)
    return 0 if report['passed'] else 1

if __name__=='__main__':
    raise SystemExit(main())
