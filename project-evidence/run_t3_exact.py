"""Immutable-image serial profiler / paired timing; retain all attempts."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]
E=ROOT/'project-evidence'
SOURCE=ROOT/'.validation/t3-release-source-20260925'
BASE='sha256:c5724d34e2a8c951e4645b7c9aed1b95e3eabf1bd3dc24c0195055700cdbd45d'
ROSTER=['t3-eq-deterministic-baseline','t3-gb-pop-128-agents','t3-gbatch-dense-3','t3-cancelmodify-lifecycle','t3-as01-base-mix']
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,d):p.write_text(json.dumps(d,indent=2)+'\n')
def inspect(image):return json.loads(subprocess.check_output(['docker','image','inspect',image],text=True))[0]
def run(out,other=None):
    out=out.resolve()
    out.mkdir(exist_ok=False)
    images={'baseline':BASE}
    if other:images['comparison']=inspect(other)['Id']
    assert inspect(BASE)['Id']==BASE
    plan={'created':datetime.now(timezone.utc).isoformat(),'images':images,'roster':ROSTER,
        'mode':'paired' if other else 'profile','warmup_each':1 if other else 0,'measured_pairs':5 if other else 0,
        'order':'baseline/comparison odd; comparison/baseline even','concurrency':1,'cpus':4,'memory_bytes':4*1024**3,
        'aggregate':'sum of five case median host walls; paired bootstrap resamples the five pair indices jointly',
        'gate':'at least5% aggregate reduction; bootstrap95 lower>0; no case median slowdown>2%; RSS<=115%; stable parquet hashes/counts identical',
        'rankable':False,'official_timing':False,'worker_sha256':sha(E/'t3_exact_worker.py')}
    write(out/'plan.json',plan); records=[]
    for unit in ROSTER:
        source=SOURCE/'units'/unit
        inp=out/unit/'input'; inp.mkdir(parents=True)
        manifest={x['path']:x for x in json.loads((source/'manifest.json').read_text())['files']}
        files=list((source/'scenarios').glob('*.json')) if (source/'scenarios').exists() else [source/'scenario.json']
        for f in files:
            rel=f.relative_to(source); assert sha(f)==manifest[rel.as_posix()]['sha256']
            dest=inp/rel; dest.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(f,dest)
        schedule=([(i,k,False) for i in range(6) for k in (['baseline','comparison'] if i%2 else ['comparison','baseline'])]
            if other else [(0,'baseline',False),(1,'baseline',True)])
        for pair,variant,cpu in schedule:
            dest=out/unit/f'{pair}-{variant}'
            output=dest/'output'; diag=dest/'diagnostic'; output.mkdir(parents=True); diag.mkdir()
            name=f't3-exact-{out.name}-{unit}-{pair}-{variant}'
            cmd=['docker','run','--name',name,'--network','none','--read-only','--user','65534:65534',
                '--cap-drop=ALL','--security-opt','no-new-privileges','--cpus','4','--memory','4g','--memory-swap','4g',
                '--pids-limit','256','--ulimit','nofile=1024:1024','--ulimit','nproc=256:256','--ulimit','fsize=67108864:67108864',
                '--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777',
                '--mount',f'type=bind,src={inp},dst=/input,readonly','--mount',f'type=bind,src={output},dst=/output',
                '--mount',f'type=bind,src={diag},dst=/diagnostic',
                '--mount',f'type=bind,src={E/"t3_exact_worker.py"},dst=/diagnostic_code/worker.py,readonly',
                images[variant],'python','/diagnostic_code/worker.py']+(['--cpu'] if cpu else [])
            row={'unit':unit,'pair':pair,'variant':variant,'cpu_profile':cpu,'warmup':bool(other and pair==0),'command':cmd,
                 'inputs':{f.relative_to(inp).as_posix():sha(f) for f in inp.rglob('*.json')}}
            print('START',unit,pair,variant,'cpu' if cpu else 'timing',flush=True)
            try:
                started=time.perf_counter()
                with (dest/'run.log').open('w') as log:proc=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=900)
                row.update(exit_code=proc.returncode,host_wall_sec=time.perf_counter()-started)
                state=json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0]['State']
                row['container_state']=state
                assert proc.returncode==0 and not state['OOMKilled']
                row['measurements']=json.loads((diag/'measurements.json').read_text())
                row['files']={f.relative_to(output).as_posix():{'sha256':sha(f),'bytes':f.stat().st_size} for f in output.rglob('*') if f.is_file()}
                row['stable']={p:v['sha256'] for p,v in row['files'].items() if p.endswith('.parquet')}
                row['output_bytes']=sum(v['bytes'] for v in row['files'].values()); assert row['output_bytes']<=64*1024**2
                ev=row['measurements']['events']; row['event_count']=ev.get('total_events',ev.get('n_events'))
                first=next((r for r in records if r['unit']==unit),None)
                if first:assert row['stable']==first['stable'] and row['event_count']==first['event_count']
                row['status']='passed'
            except Exception as exc:
                row.update(status='failed',error_type=type(exc).__name__,error=str(exc)); raise
            finally:
                subprocess.run(['docker','rm','-f',name],capture_output=True)
                records.append(row); write(out/'runs.json',records)
            print('PASS',unit,pair,variant,round(row['host_wall_sec'],3),flush=True)
    summary={'images':images,'runs':len(records),'stable_outputs_equal':True,'rankable':False}
    if other:
        cases=[]; pair_values=[]
        for unit in ROSTER:
            rs=[r for r in records if r['unit']==unit and not r['warmup']]
            b=[next(r for r in rs if r['pair']==i and r['variant']=='baseline') for i in range(1,6)]
            c=[next(r for r in rs if r['pair']==i and r['variant']=='comparison') for i in range(1,6)]
            bs=[r['host_wall_sec'] for r in b]; cs=[r['host_wall_sec'] for r in c]; pair_values.append((bs,cs))
            cases.append({'unit':unit,'baseline_median':statistics.median(bs),'comparison_median':statistics.median(cs),
                'paired_speedups':[1-y/x for x,y in zip(bs,cs)],'median_reduction':1-statistics.median(cs)/statistics.median(bs),
                'rss_ratio':max(r['measurements']['process_peak_rss_bytes'] for r in c)/max(r['measurements']['process_peak_rss_bytes'] for r in b)})
        reduction=1-sum(r['comparison_median'] for r in cases)/sum(r['baseline_median'] for r in cases)
        rng=random.Random(20260927); boot=[]
        for _ in range(10000):
            indices=rng.choices(range(5),k=5)
            boot.append(1-sum(statistics.median([c[i] for i in indices]) for b,c in pair_values)/sum(statistics.median([b[i] for i in indices]) for b,c in pair_values))
        boot.sort(); ci=[boot[249],boot[9749]]
        summary.update(cases=cases,aggregate_reduction=reduction,bootstrap95=ci,
            local_gate=reduction>=.05 and ci[0]>0 and all(r['median_reduction']>=-.02 and r['rss_ratio']<=1.15 for r in cases))
    write(out/'summary.json',summary);print(json.dumps(summary,indent=2),flush=True)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--comparison');a=p.parse_args();run(a.out,a.comparison)
