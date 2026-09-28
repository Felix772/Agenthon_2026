"""Five alternating pairs per fixed scenario; never report profiled time as speedup."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import statistics
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]
E=ROOT/'project-evidence'
OUT=E/'t3-04-paired-timing'
small_report=json.loads((E/'t3-04-small-regression/report.json').read_text())
assert small_report['total_scenarios']==small_report['passed']==3
assert small_report['failed']==small_report['errored']==0
OUT.mkdir(exist_ok=True)
if any(OUT.iterdir()):raise RuntimeError('Refuse to overwrite existing timing evidence')
images={'baseline':'track3-abides-baseline:agenthon-local-20260922','candidate':'simulation-agent:format-cache-20260924'}
ids={key:subprocess.check_output(['docker','image','inspect',value,'--format','{{.Id}}'],text=True).strip() for key,value in images.items()}
scenarios=['as01_base_mix','as03_mm_liquidity_heavy','gb_base_30agent_30s']
plan={'created_at':datetime.now(timezone.utc).isoformat(),'images':images,'image_ids':ids,'scenarios':scenarios,
      'pairs_each':5,'order':'baseline,candidate on odd pairs; reversed on even pairs',
      'warmup':0,'cpus':4,'memory_bytes':3*1024**3,'concurrency':1,'production_timing_verified':False,
      'acceptance':'Each scenario median host speedup >=3%, paired percentile bootstrap95% lower bound>0; candidate RSS<=115% baseline maximum and <3GiB; all output hashes identical',
      'bootstrap':{'replicates':10000,'seed':20260924,'scope':'Conditional exploratory interval from only five pairs; not an organizer score or population guarantee'}}
(OUT/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
records=[]
for scenario in scenarios:
    input_dir=E/'t3-03-profile-20260924'/scenario/'input'
    for pair in range(1,6):
        order=['baseline','candidate'] if pair%2 else ['candidate','baseline']
        for key in order:
            dest=OUT/scenario/f'pair-{pair}-{key}'
            output=dest/'output';diag=dest/'diagnostic'
            output.mkdir(parents=True);diag.mkdir()
            name=f'agenthon-t3-pair-{scenario.replace("_","-")}-{pair}-{key}'
            cmd=['docker','run','--name',name,'--network','none','--read-only','--user','65534:65534',
                 '--cap-drop=ALL','--security-opt','no-new-privileges','--cpus','4','--memory','3g','--memory-swap','3g',
                 '--pids-limit','256','--ulimit','nofile=1024:1024','--ulimit','nproc=256:256','--ulimit','fsize=67108864:67108864',
                 '--tmpfs','/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777','--tmpfs','/work:rw,noexec,nosuid,nodev,size=64m,mode=1777',
                 '--mount',f'type=bind,src={input_dir},dst=/input,readonly','--mount',f'type=bind,src={output},dst=/output',
                 '--mount',f'type=bind,src={diag},dst=/diagnostic',
                 '--mount',f'type=bind,src={E / "t3_profile_worker.py"},dst=/diagnostic_code/worker.py,readonly',
                 images[key],'python','/diagnostic_code/worker.py','--mode','timing']
            record={'scenario':scenario,'pair':pair,'variant':key,'command':cmd}
            try:
                start=time.perf_counter()
                with (dest/'run.log').open('w') as log:
                    result=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=120)
                record.update(host_wall_sec=time.perf_counter()-start,exit_code=result.returncode)
                if result.returncode:raise RuntimeError('Run failed')
                record['measurements']=json.loads((diag/'measurements.json').read_text())
                record['hashes']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*.parquet')}
                print(f'{scenario} pair{pair} {key}: {record["host_wall_sec"]:.3f}s',flush=True)
            except Exception as exc:
                record['error']=str(exc)
                raise
            finally:
                subprocess.run(['docker','rm','-f',name],capture_output=True)
                records.append(record)
                (OUT/'runs.json').write_text(json.dumps(records,indent=2)+'\n')
summary={'image_ids':ids,'scenarios':[],'production_timing_verified':False,'all_local_timing_gates':True}
rng=random.Random(20260924)
for scenario in scenarios:
    rows=[r for r in records if r['scenario']==scenario]
    assert len({json.dumps(r['hashes'],sort_keys=True) for r in rows})==1
    assert len({r['measurements']['events']['n_events'] for r in rows})==1
    pairs=[]
    for pair in range(1,6):
        b=next(r for r in rows if r['pair']==pair and r['variant']=='baseline')
        c=next(r for r in rows if r['pair']==pair and r['variant']=='candidate')
        pairs.append(1-c['host_wall_sec']/b['host_wall_sec'])
    estimates=sorted(statistics.median(rng.choices(pairs,k=5)) for _ in range(10000))
    peaks={key:max(r['measurements']['process_peak_rss_bytes'] for r in rows if r['variant']==key) for key in images}
    row={'scenario':scenario,'paired_host_speedup_fraction':pairs,'median_speedup_fraction':statistics.median(pairs),
         'bootstrap95':[estimates[249],estimates[9749]],'trace_and_ledger_identical':True,'peak_rss_bytes':peaks}
    row['local_timing_gate']=row['median_speedup_fraction']>=.03 and row['bootstrap95'][0]>0 and peaks['candidate']<=1.15*peaks['baseline'] and peaks['candidate']<3*1024**3
    summary['all_local_timing_gates'] &= row['local_timing_gate']
    summary['scenarios'].append(row)
(OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2),flush=True)
