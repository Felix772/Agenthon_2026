"""Synthetic network-isolated runtime probe; always clean up only owned resources."""
import json
import argparse
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'analysis-agent'))
from analysis_agent.contract import validate_answer
from analysis_agent.retrieval import RetrievalIndex
E=ROOT/'project-evidence'
parser=argparse.ArgumentParser()
parser.add_argument('--run-id',default='t4-container-probe')
parser.add_argument('--image',default='analysis-agent:local-20260924')
args=parser.parse_args()
if not all(c.isalnum() or c=='-' for c in args.run_id):raise ValueError('Invalid run ID')
image=args.image
network=args.run_id+'-network'
mock=args.run_id+'-mock'
candidate=args.run_id+'-candidate'
output=E/(args.run_id+'-output')
output.mkdir(exist_ok=False)
record={'scope':'Synthetic internal-network protocol + container runtime, not House or organizer platform',
        'production_verified':False,'image':image,'commands':[]}
def run(args,check=True,timeout=90):
    result=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    # All arguments here are fixed public/synthetic strings, never real credentials.
    record['commands'].append({'argv':args,'exit_code':result.returncode,'stdout':result.stdout[-5000:],'stderr':result.stderr[-2000:]})
    if check and result.returncode:raise RuntimeError('Probe command failed: '+args[1])
    return result
created=False
try:
    record['image_id']=run(['docker','image','inspect',image,'--format','{{.Id}}']).stdout.strip()
    run(['docker','network','create','--internal',network]);created=True
    run(['docker','run','-d','--name',mock,'--network',network,'--network-alias','mock',
         '--read-only','--cap-drop=ALL','--security-opt','no-new-privileges','--memory','256m',
         '--mount',f'type=bind,src={E / "t4_container_mock.py"},dst=/probe/mock.py,readonly',
         '--entrypoint','python',image,'/probe/mock.py'])
    run(['docker','exec',mock,'python','-c','import urllib.request; print(urllib.request.urlopen("http://localhost:8080").status)'])
    common=['docker','run','--rm','--read-only','--user','65534:65534','--cap-drop=ALL',
            '--security-opt','no-new-privileges','--cpus','2','--memory','1g','--memory-swap','1g','--pids-limit','256',
            '--ulimit','nproc=256:256',
            '--ulimit','nofile=1024:1024','--ulimit','fsize=67108864:67108864',
            '--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777']
    probe='import os,json,resource,importlib.metadata; from pathlib import Path; refused=[]\nfor name in ["/forbidden","/input/forbidden"]:\n try: Path(name).write_text("x")\n except OSError: refused.append(name)\nprint(json.dumps({"uid":os.getuid(),"write_refused":refused,"tmp_mount":[x for x in Path("/proc/mounts").read_text().splitlines() if " /tmp " in x],"limits":{n:resource.getrlimit(getattr(resource,n)) for n in ["RLIMIT_NOFILE","RLIMIT_NPROC","RLIMIT_FSIZE"]},"status":[x for x in Path("/proc/self/status").read_text().splitlines() if x.startswith(("CapEff:","NoNewPrivs:"))],"memory_max":Path("/sys/fs/cgroup/memory.max").read_text().strip(),"swap_max":Path("/sys/fs/cgroup/memory.swap.max").read_text().strip(),"pids_max":Path("/sys/fs/cgroup/pids.max").read_text().strip(),"toolkit_version":importlib.metadata.version("qfbench2-common")}))'
    runtime=run(common+['--network','none','--mount',f'type=bind,src={E / "t4-container-input"},dst=/input,readonly','--entrypoint','python',image,'-c',probe])
    record['runtime']=json.loads(runtime.stdout)
    rt=record['runtime']
    assert rt['uid']==65534 and rt['write_refused']==['/forbidden','/input/forbidden']
    assert rt['limits']=={'RLIMIT_NOFILE':[1024,1024],'RLIMIT_NPROC':[256,256],'RLIMIT_FSIZE':[67108864,67108864]}
    assert rt['memory_max']=='1073741824' and rt['swap_max']=='0' and rt['pids_max']=='256'
    assert any('noexec' in line and 'nosuid' in line and 'nodev' in line and 'size=65536k' in line for line in rt['tmp_mount'])
    assert all(line.split()[-1]==('1' if line.startswith('NoNewPrivs') else '0000000000000000') for line in rt['status'])
    run(common+['--name',candidate,'--network',network,
        '--mount',f'type=bind,src={E / "t4-container-input"},dst=/input,readonly',
        '--mount',f'type=bind,src={output},dst=/output',
        '-e','MODEL_ENDPOINT=http://mock:8080','-e','MODEL_NAME=synthetic-test',
        '-e','MODEL_TOKEN=synthetic-not-a-credential',image,'analyze','--task','/input/task.json',
        '--corpus','/input/corpus','--out','/output/answer.json','--timeout','30'],timeout=45)
    task=json.loads((E/'t4-container-input/task.json').read_text())
    answer=json.loads((output/'answer.json').read_text())
    validate_answer(task,answer)
    index=RetrievalIndex.load(E/'t4-container-input/corpus',task['cutoff_date'])
    for row in answer['entity_predictions']:
        for cite in row['claims']:index.validate_span(cite['doc_id'],cite['span_start'],cite['span_end'])
    record.update(passed=True,entity_count=len(answer['entity_predictions']),
                  output_bytes=(output/'answer.json').stat().st_size)
except Exception as exc:
    record.update(passed=False,error_type=type(exc).__name__,error=str(exc))
finally:
    if created:
        run(['docker','rm','-f',candidate],check=False)
        run(['docker','rm','-f',mock],check=False)
        run(['docker','network','rm',network],check=False)
    (E/(args.run_id+'.json')).write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({k:v for k,v in record.items() if k!='commands'},indent=2))
raise SystemExit(0 if record.get('passed') else 1)
