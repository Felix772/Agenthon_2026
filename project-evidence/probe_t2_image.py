"""Three real public input contracts, each repeated inside the restricted candidate."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
E=ROOT/'project-evidence'
OUT=E/'t2-container-probe-20260925'
OUT.mkdir(exist_ok=False)
roster=E/'t2-06-host-contracts-20260925'
records=json.loads((roster/'report.json').read_text())['records']
selected=[next(r for r in records if r['status']=='passed' and r['frequency']=='monthly'),
          next(r for r in records if r['status']=='passed' and r['target']=='log_return'),
          next(r for r in records if r['status']=='passed' and r['frequency']=='daily' and r['target']=='level' and not r['is_exemplar'])]
image='forecast-agent:local-20260925'
report={'scope':'Local real-public-input contract and runtime checks; no answers or House',
        'image':image,'records':[],'production_verified':False,'score':None}
inspect=json.loads(subprocess.check_output(['docker','image','inspect',image],text=True))[0]
report.update(image_id=inspect['Id'],architecture=inspect['Architecture'],os=inspect['Os'],labels=inspect['Config']['Labels'])
assert report['architecture']=='amd64' and report['os']=='linux' and report['labels']['qfbench2.interface_version']=='2.0'
common=['docker','run','--rm','--network','none','--read-only','--user','65534:65534',
        '--cap-drop=ALL','--security-opt','no-new-privileges','--cpus','2','--memory','1g','--memory-swap','1g',
        '--pids-limit','256','--ulimit','nproc=256:256','--ulimit','nofile=1024:1024','--ulimit','fsize=67108864:67108864',
        '--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777']
runtime_code='''import os,json,resource,importlib.metadata as m
from pathlib import Path
refused=[]
for p in ['/root-write-test','/input/write-test']:
 try: Path(p).write_text('x')
 except OSError: refused.append(p)
print(json.dumps({'uid':os.getuid(),'refused':refused,
 'limits':{n:resource.getrlimit(getattr(resource,n)) for n in ['RLIMIT_NOFILE','RLIMIT_NPROC','RLIMIT_FSIZE']},
 'swap_max':Path('/sys/fs/cgroup/memory.swap.max').read_text().strip(),
 'memory_max':Path('/sys/fs/cgroup/memory.max').read_text().strip(),
 'pids_max':Path('/sys/fs/cgroup/pids.max').read_text().strip(),
 'tmp':[s for s in Path('/proc/mounts').read_text().splitlines() if ' /tmp ' in s],
 'versions':{p:m.version(p) for p in ['qfbench2-common','numpy','pandas','pyarrow','jsonschema']}}))'''
runtime_cmd=common+['--mount',f'type=bind,src={roster / "inputs" / selected[0]["unit"]},dst=/input,readonly',
                    '--entrypoint','python',image,'-c',runtime_code]
runtime=subprocess.run(runtime_cmd,capture_output=True,text=True,timeout=30)
assert runtime.returncode==0,runtime.stderr
rt=report['runtime']=json.loads(runtime.stdout)
assert rt['uid']==65534 and rt['refused']==['/root-write-test','/input/write-test']
assert rt['swap_max']=='0' and rt['memory_max']=='1073741824' and rt['pids_max']=='256'
assert rt['limits']=={'RLIMIT_NOFILE':[1024,1024],'RLIMIT_NPROC':[256,256],'RLIMIT_FSIZE':[67108864,67108864]}
assert any(all(x in line for x in ['noexec','nosuid','nodev','size=65536k']) for line in rt['tmp'])
sys.path.insert(0,str(ROOT/'.validation/t2-current-20260925'))
sys.path.insert(0,str(ROOT/'forecast-agent'))
from forecast import validate_outputs
from qfbench2_track_forecasting.grid import GridSpec
for row in selected:
    hashes=[]
    for repeat in [1,2]:
        output=OUT/row['unit']/str(repeat)
        output.mkdir(parents=True)
        cmd=common+['--mount',f'type=bind,src={roster / "inputs" / row["unit"]},dst=/input,readonly',
                    '--mount',f'type=bind,src={output},dst=/output','-e','QFBENCH_SEED=20260925',image,
                    'forecast','--panels','/input/panels','--text','/input/text','--asof',row['cutoff'],
                    '--out','/output/forecast.parquet','--n-draws',str(row['draws'])]
        run=subprocess.run(cmd,capture_output=True,text=True,timeout=180)
        entry={'unit':row['unit'],'repeat':repeat,'command':cmd,'exit_code':run.returncode,'stdout':run.stdout,'stderr':run.stderr}
        report['records'].append(entry)
        (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        assert run.returncode==0
        validate_outputs(output,GridSpec(tuple(row['assets']),tuple(row['horizons'])),row['draws'],row['target'])
        rationale=json.loads((output/'forecast_rationale.md').read_text().split('\n\n',1)[1])
        assert rationale['seed']==20260925
        entry['files']={p.name:{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in output.iterdir()}
        hashes.append(entry['files'])
        print('PASS',row['unit'],repeat,flush=True)
    assert hashes[0]==hashes[1]
report.update(passed=True,public_cases=3,runs=6,repeat_identical=True)
(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k not in ['records']},indent=2))
