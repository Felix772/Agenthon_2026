"""Whitelist one experimental patch onto the immutable published image."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
ROOT=Path(__file__).resolve().parents[1]
E=ROOT/'project-evidence'
BASE='sha256:c5724d34e2a8c951e4645b7c9aed1b95e3eabf1bd3dc24c0195055700cdbd45d'
def inspect(x):return json.loads(subprocess.check_output(['docker','image','inspect',x],text=True))[0]
context=ROOT/'.validation/t3-log-copy-20260927';context.mkdir(exist_ok=False)
shutil.copyfile(ROOT/'simulation-agent/patch_log_copy.py',context/'patch_log_copy.py')
(context/'Dockerfile').write_text('FROM simulation-agent:runtime-20260925\nCOPY patch_log_copy.py /tmp/patch_log_copy.py\nRUN python /tmp/patch_log_copy.py && rm /tmp/patch_log_copy.py\n')
base=inspect('simulation-agent:runtime-20260925');assert base['Id']==BASE
tag='simulation-agent:log-copy-20260927'
with (E/'t3-log-copy-build-20260927.log').open('w') as log:
    subprocess.run(['docker','build','--network','none','--pull=false','-t',tag,str(context)],stdout=log,stderr=subprocess.STDOUT,check=True)
candidate=inspect(tag)
assert candidate['Config']==base['Config']
assert candidate['RootFS']['Layers'][:len(base['RootFS']['Layers'])]==base['RootFS']['Layers']
code='''import hashlib,importlib.metadata,json,pathlib,inspect
import abides_core.utils,abides_markets,abides_fork.simulate
roots=[pathlib.Path(inspect.getfile(m)).parent for m in [abides_core.utils,abides_markets,abides_fork.simulate]]
print(json.dumps(dict(files={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for root in roots for p in root.rglob('*.py')},packages=sorted([(d.metadata['Name'],d.version) for d in importlib.metadata.distributions()]))))'''
actual=json.loads(subprocess.check_output(['docker','run','--rm','--network','none','--read-only','--entrypoint','python',candidate['Id'],'-c',code],text=True))
original=json.loads((E/'t3-exact-full-source-audit-20260927.json').read_text())['baseline']
assert original['packages']==actual['packages'] and original['files'].keys()==actual['files'].keys()
delta=[p for p in actual['files'] if actual['files'][p]!=original['files'][p]]
assert len(delta)==1 and delta[0].endswith('/abides_core/agent.py')
result={'baseline':BASE,'candidate':candidate['Id'],'source_audit':actual,'changed_sources':delta,
        'config_identical':True,'dependencies_identical':True,'base_layers_preserved':True,
        'context':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in context.iterdir()}}
(E/'t3-log-copy-build-20260927.json').write_text(json.dumps(result,indent=2)+'\n')
print(candidate['Id']);print('One source changed; dependencies and runtime configuration identical.')
