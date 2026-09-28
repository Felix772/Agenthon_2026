"""Validate all public forecast contracts against one immutable local candidate."""
from collections import Counter
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
E=ROOT/'project-evidence'
source=ROOT/'.validation/t2-current-20260925'
host=E/'t2-06-host-contracts-20260925'
OUT=E/'t2-06-image-contracts-20260925'
probe=json.loads((E/'t2-container-probe-20260925/report.json').read_text())
assert probe['passed'] and probe['runs']==6
image='forecast-agent:local-20260925'
image_id=subprocess.check_output(['docker','image','inspect',image,'--format','{{.Id}}'],text=True).strip()
assert image_id==probe['image_id']
OUT.mkdir(exist_ok=False)
sys.path.insert(0,str(source))
from qfbench2_track_forecasting.scoring import _main as score_cli
roster=json.loads((host/'report.json').read_text())
assert roster['all_contracts_passed']
report={'image':image,'image_id':image_id,'source_ref':roster['source_ref'],
        'created_at':datetime.now(timezone.utc).isoformat(),'practice_denominator':103,'exemplar_denominator':1,
        'host_input_manifest':'../t2-06-host-contracts-20260925/report.json','seed':20260925,
        'model_calls':0,'factory':'official score CLI gates-only','score':None,'production_verified':False,
        'local_limits':{'cpus':2,'memory_bytes':1073741824,'swap':False,'deadline_sec':1800,'pids':256},'records':[]}
common=['docker','run','--network','none','--read-only','--user','65534:65534','--cap-drop=ALL',
        '--security-opt','no-new-privileges','--cpus','2','--memory','1g','--memory-swap','1g',
        '--pids-limit','256','--ulimit','nproc=256:256','--ulimit','nofile=1024:1024',
        '--ulimit','fsize=67108864:67108864','--tmpfs','/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777']
for position,row in enumerate(roster['records']):
    unit=row['unit'];output=OUT/'outputs'/unit;output.mkdir(parents=True)
    name=f'agenthon-t2-roster-20260925-{position:03}'
    record={k:row[k] for k in ['unit','is_exemplar','family','assets','horizons','target','frequency','cutoff','minimum_draws','draws','expected_rows']}
    report['records'].append(record)
    command=common+['--name',name,'--mount',f'type=bind,src={host / "inputs" / unit},dst=/input,readonly',
                   '--mount',f'type=bind,src={output},dst=/output','-e','QFBENCH_SEED=20260925',image,
                   'forecast','--panels','/input/panels','--text','/input/text','--asof',row['cutoff'],
                   '--out','/output/forecast.parquet','--n-draws',str(row['draws'])]
    started=time.perf_counter()
    try:
        completed=subprocess.run(command,capture_output=True,text=True,timeout=1800)
        record.update(exit_code=completed.returncode,stdout=completed.stdout[-5000:],stderr=completed.stderr[-5000:])
        if completed.returncode:raise RuntimeError('Container forecast failed')
        stream=io.StringIO()
        with redirect_stdout(stream):
            code=score_cli(['score','--card',str(source/'units'/unit/'card.toml'),'--forecast',str(output/'forecast.parquet')])
        record['verdict']=json.loads(stream.getvalue())
        record['files']={p.name:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in output.iterdir()}
        record['output_bytes']=sum(f['bytes'] for f in record['files'].values())
        assert record['output_bytes']<=64*1024**2
        record['status']='passed' if code==0 and record['verdict']['admissible'] else 'failed'
    except Exception as exc:
        record.update(status='error',error_type=type(exc).__name__,error=str(exc))
    finally:
        # Only the fixed container name created by this exact iteration is removed.
        subprocess.run(['docker','rm','-f',name],capture_output=True)
    record['elapsed_sec']=time.perf_counter()-started
    (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(record['status'].upper(),unit,record.get('error',''),flush=True)
report['practice_results']=dict(Counter(r['status'] for r in report['records'] if not r['is_exemplar']))
report['exemplar_results']=dict(Counter(r['status'] for r in report['records'] if r['is_exemplar']))
report['all_contracts_passed']=all(r['status']=='passed' for r in report['records'])
report['limitations']=['No realized outcomes, ranking or accuracy score','House branch not exercised; numerical model calls none',
                      'Local2CPU/1GiB workload acceptance, not official platform/GPU/128GiB capacity',
                      'Concurrent T3 correctness regression: elapsed values are diagnostic, not a performance comparison']
(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:report[k] for k in ['image_id','practice_results','exemplar_results','all_contracts_passed']},indent=2),flush=True)
raise SystemExit(0 if report['all_contracts_passed'] else 1)
