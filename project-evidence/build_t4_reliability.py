"""Whitelisted derivative build preserves baseline dependencies and all releases."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASE = 'analysis-agent:local-20260924'
BASE_ID = 'sha256:69e4d2efc1c39e5901851cd4c40ff69fa0c3239ca45b929f0542bccaeea1cbae'
TAG = 'analysis-agent:reliability-20260927'
context = ROOT/'.validation/t4-reliability-build-20260927'
context.mkdir(exist_ok=False)
metadata = json.loads(subprocess.check_output(['docker', 'image', 'inspect', BASE]))[0]
assert metadata['Id'] == BASE_ID
(context/'analysis_agent').mkdir()
for path in (ROOT/'analysis-agent/analysis_agent').glob('*.py'):
    shutil.copyfile(path, context/'analysis_agent'/path.name)
shutil.copyfile(ROOT/'qfbench-agent/agent/model_client.py', context/'model_client.py')
(context/'Dockerfile').write_text('FROM '+BASE+'\nCOPY analysis_agent /app/analysis-agent/analysis_agent\nCOPY model_client.py /app/qfbench-agent/agent/model_client.py\n')
manifest = [{'path': p.relative_to(context).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in sorted(context.rglob('*')) if p.is_file()]
record = {'baseline': BASE_ID, 'tag': TAG, 'context': str(context.relative_to(ROOT)), 'files': manifest}
result = subprocess.run(['docker', 'build', '--network=none', '--pull=false', '-t', TAG, str(context)],
                        capture_output=True, text=True, encoding='utf-8', timeout=180)
(ROOT/'project-evidence/t4-reliability-build-20260927.log').write_text(result.stdout+result.stderr)
assert result.returncode == 0, 'build failed; see retained log'
candidate = json.loads(subprocess.check_output(['docker', 'image', 'inspect', TAG]))[0]
assert candidate['RootFS']['Layers'][:len(metadata['RootFS']['Layers'])] == metadata['RootFS']['Layers']
record['image'] = candidate['Id']
code = "import hashlib,json,pathlib; files=list(pathlib.Path('/app/analysis-agent/analysis_agent').glob('*.py'))+[pathlib.Path('/app/qfbench-agent/agent/model_client.py')]; print(json.dumps({str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}))"
actual = json.loads(subprocess.check_output(['docker','run','--rm','--read-only','--network=none',
    '--entrypoint','python',candidate['Id'],'-c',code]))
expected = {('/app/qfbench-agent/agent/model_client.py' if x['path']=='model_client.py' else
             '/app/analysis-agent/'+x['path']): x['sha256'] for x in manifest if x['path']!='Dockerfile'}
assert actual == expected
record.update(source_hashes=actual, source_identity_passed=True, dependency_layers_preserved=True,
              published=False, house_verified=False)
(ROOT/'project-evidence/t4-reliability-build-20260927.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record,indent=2))
