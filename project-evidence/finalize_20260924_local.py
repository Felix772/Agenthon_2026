"""Consolidate verified local increments without replacing historical evidence."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parents[1]
E=ROOT/'project-evidence'
def read(name):return json.loads((E/name).read_text(encoding='utf-8'))
def write(name,data):(E/name).write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
refs=read('t4-04-source-recheck.json')
for row in refs:
    repo=ROOT/row['repository']
    ref=subprocess.check_output(['git','-c',f'safe.directory={repo}','-C',str(repo),'rev-parse','origin/main'],text=True).strip()
    assert ref==row['ref']
    row['checked_at']=datetime.now(timezone.utc).isoformat()
    row['review']='Repeated fresh fetches before T4-05A, T2-05A, T1 inventory and C05 image changes; docs unchanged from prior complete review.'
write('20260924-continuation-source-recheck.json',refs)
old=read('t3-02-run-summary.json')
resumed=read('t3-resume-20260924/report.json')
batch=read('t3-02-batches-root/report.json')
previous={r['scenario_id'] for r in old['single_roster'] if r['status']=='passed'}
current={r['scenario_id'] for r in resumed['results']}
assert len(previous)==27 and len(current)==38 and not previous&current
assert previous|current=={r['scenario_id'] for r in old['single_roster']}
assert resumed['passed']==38 and resumed['failed']==resumed['errored']==0
assert all(r['status']=='passed' for r in batch['records']) and len(batch['records'])==6
image=subprocess.check_output(['docker','image','inspect','track3-abides-baseline:agenthon-local-20260922','--format','{{.Id}}'],text=True).strip()
assert image=='sha256:3d534ebbd778dfdea64d2ed5c2844430208465cd670e530893779ad06ae9bf22'
write('t3-02-completed-summary.json',{'local_semantic_pass':True,'single_denominator':65,'single_passed':65,
    'batch_denominator':6,'batch_passed':6,'image_id':image,'image':resumed['candidate_image'],
    'source_ref':'1504b37b8472f7f949d4b8f80b7c8b2b71ab0e43','relevant_source_unchanged':True,
    'coverage':[{'scenario_id':sid,'evidence':'t3-02-single-regression.log' if sid in previous else 't3-resume-20260924/report.json'} for sid in sorted(previous|current)],
    'merged_across_runs':True,'official_single_run_65_report':False,'local_performance_pass':None,'production_verified':False,
    'limitations':['Historical27 and resumed38 have identical relevant official source and image; timing not comparable across runs',
                   'Official stylized_facts divide/log warnings retained; no thresholds altered','No organizer trusted timing or GPU test',
                   'Exemplar removed by organizer, not by outcome selection']} )
assert '23 passed' in (E/'t2-05a-tests-windows-final.log').read_text()
assert '23 passed' in (E/'t2-05a-tests-linux-final.log').read_text()
audit=read('t2-05a-public-text-audit.json')
assert audit['passed']==audit['denominator']==104
write('t2-05a-run-summary.json',{'local_result':'passed','windows_tests':23,'linux_tests':23,'subtests_each':15,
    'public_text_directories':104,'passed':104,'first_audit':{'passed':100,'failed':4,'cause':'ASCII-only filename guard; fixed for valid Unicode single components'},
    'model_calls':0,'quality_gain_measured':False,'default_prediction_adjustment':'none',
    'source_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'forecast-agent').glob('*.py'))},
    'review':'pending','next':'T2-05B requires House; event adjustment bounds are uncalibrated'})
status_path=ROOT/'planning/task-status.json'
status=json.loads(status_path.read_text(encoding='utf-8'))
changes={
 'T3-02':('active','Local correctness complete:65/65 singles across27historical+38resumed runs,6/6 batches and59boundary tests; same image/relevant source. Review pending. No performance or production timing claim.',['t3-02-completed-summary.json','t3-resume-20260924/report.json','t3-resume-roster.json']),
 'T4-05A':('blocked-external','Readiness and42official synthetic tests pass;1real-transformers test skipped. Organizer judge spec/cache absent, no production model identity guessed.',['t4-05a-readiness.json','t4-05a-official-tests.log','t4-05a-review.md']),
 'T2-05A':('active','Local interface complete:23tests plus15subtests pass per Windows/Linux;104text directories pass after Unicode path fix. Default samples unchanged; no House or text quality gain. Review pending.',['t2-05a-run-summary.json','t2-05a-public-text-audit.json','t2-05a-review.md']),
 'T1-02':('passed','Original increment accepted previously. Fresh86-unit inventory:all86staging/contracts verified; old87record retained. Current increment review pending; no solve attempts.',['t1-roster-20260924/t1-02-run-summary.json','t1-roster-20260924/public-suite-manifest.json','t1-roster-20260924/stratified-sample.json'])}
for task in status['tasks']:
    if task['id'] in changes:
        state,note,files=changes[task['id']]
        task.update(status=state,note=note)
        task['evidence']+=['project-evidence/'+f for f in files if 'project-evidence/'+f not in task['evidence']]
status['validation_dimensions']['T3']['semantic_pass']=True
status['execution_state'].update(updated_at=datetime.now(timezone.utc).isoformat(),next_task='C05 T4 container probe; T3-03 profiling; real House/judge remain external',reason='T3 local correctness completed;T2 text interface passed;T1 current roster refreshed;T4 judge correctly blocked.')
status_path.write_text(json.dumps(status,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
