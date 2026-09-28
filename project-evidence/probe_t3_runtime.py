"""Runtime-only image layer: strict mounts, then compare actual output bytes."""
import hashlib
import json
from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parents[1]
E=ROOT/'project-evidence'
OUT=E/'t3-c05-runtime-20260925'
OUT.mkdir(exist_ok=False)
image='simulation-agent:runtime-20260925'
report={'image':image,'image_id':subprocess.check_output(['docker','image','inspect',image,'--format','{{.Id}}'],text=True).strip(),
        'scope':'Runtime-only /tmp working directory; source unchanged from cache candidate',
        'extra_work_tmpfs':False,'production_verified':False,'records':[]}
common=['docker','run','--rm','--network','none','--read-only','--user','65534:65534','--cap-drop=ALL',
        '--security-opt','no-new-privileges','--cpus','4','--memory','1g','--memory-swap','1g',
        '--pids-limit','256','--ulimit','nofile=1024:1024','--ulimit','nproc=256:256','--ulimit','fsize=67108864:67108864',
        '--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777']
for scenario in ['as01_base_mix','as03_mm_liquidity_heavy','gb_base_30agent_30s']:
    hashes=[]
    for repeat in [1,2]:
        output=OUT/scenario/str(repeat);output.mkdir(parents=True)
        source=E/'t3-03-profile-20260924'/scenario/'input'
        cmd=common+['--mount',f'type=bind,src={source},dst=/input,readonly',
                    '--mount',f'type=bind,src={output},dst=/output',image,'simulate',
                    '--config','/input/scenario.json','--out','/output/trace.parquet']
        result=subprocess.run(cmd,capture_output=True,text=True,timeout=180)
        row={'scenario':scenario,'repeat':repeat,'command':cmd,'exit_code':result.returncode,
             'stdout':result.stdout[-5000:],'stderr':result.stderr[-5000:]}
        report['records'].append(row)
        (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        assert result.returncode==0
        actual={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*.parquet')}
        expected_dir=E/'t3-04-paired-timing'/scenario/'pair-1-candidate/output'
        expected={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in expected_dir.glob('*.parquet')}
        assert actual==expected
        events=json.loads((output/'events.json').read_text())
        row.update(trace_and_ledger_match_measured_candidate=True,hashes=actual,n_events=events['n_events'],
                   output_bytes=sum(p.stat().st_size for p in output.iterdir()))
        assert row['output_bytes']<=64*1024**2
        hashes.append((actual,events['n_events']))
        print('PASS',scenario,repeat,flush=True)
    assert hashes[0]==hashes[1]
report.update(passed=True,representative_scenarios=3,runs=6,repeat_identical=True,
              full_roster_this_image=False,official_platform=False)
(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='records'},indent=2))
