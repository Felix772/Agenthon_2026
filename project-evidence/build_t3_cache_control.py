"""Audit immutable release, build a diagnostic derivative disabling only fmt_ts cache."""
import hashlib
import json
from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parents[1]
E=ROOT/'project-evidence'
BASE='sha256:c5724d34e2a8c951e4645b7c9aed1b95e3eabf1bd3dc24c0195055700cdbd45d'
def inspect(x):return json.loads(subprocess.check_output(['docker','image','inspect',x],text=True))[0]
def audit(image):
    code='''import hashlib,importlib.metadata,json,pathlib,platform,inspect
import abides_core.utils,abides_markets.orders,abides_fork.simulate
roots=[pathlib.Path(inspect.getfile(m)).parent for m in [abides_core.utils,abides_markets.orders,abides_fork.simulate]]
files={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for root in roots for p in root.rglob('*.py')}
assert files
print(json.dumps(dict(files=files,python=platform.python_version(),packages=sorted([(d.metadata['Name'],d.version) for d in importlib.metadata.distributions()]))))'''
    return json.loads(subprocess.check_output(['docker','run','--rm','--network','none','--read-only','--entrypoint','python',image,'-c',code],text=True))
base=inspect(BASE); assert base['Id']==BASE
original=audit(BASE)
context=ROOT/'.validation/t3-cache-control-20260927-v2';context.mkdir(exist_ok=False)
patch='''from pathlib import Path
import inspect
import abides_core.utils
p=Path(inspect.getfile(abides_core.utils));s=p.read_text()
needle='    if (isinstance(timestamp, _FmtIntegral) and not isinstance(timestamp, bool)'
assert s.count(needle)==1
s=s.replace(needle,'    if (False and isinstance(timestamp, _FmtIntegral) and not isinstance(timestamp, bool)')
p.write_text(s)
'''
(context/'disable_cache.py').write_text(patch)
(context/'Dockerfile').write_text('FROM simulation-agent:runtime-20260925\nCOPY disable_cache.py /tmp/disable_cache.py\nRUN python /tmp/disable_cache.py && rm /tmp/disable_cache.py\n')
assert inspect('simulation-agent:runtime-20260925')['Id']==BASE
tag='simulation-agent:cache-control-20260927-v2'
with (E/'t3-cache-control-build-20260927-v2.log').open('w') as log:
    subprocess.run(['docker','build','--network','none','--pull=false','-t',tag,str(context)],stdout=log,stderr=subprocess.STDOUT,check=True)
control=inspect(tag);changed=audit(control['Id'])
assert original['packages']==changed['packages'] and original['python']==changed['python']
delta=[p for p in original['files'] if original['files'][p]!=changed['files'][p]]
assert len(delta)==1 and delta[0].endswith('/abides_core/utils.py'),delta
assert original['files'].keys()==changed['files'].keys()
assert base['Config']==control['Config']
assert control['RootFS']['Layers'][:len(base['RootFS']['Layers'])]==base['RootFS']['Layers']
result={'baseline_id':BASE,'control_id':control['Id'],'baseline':original,'control':changed,'changed_sources':delta,
        'config_identical':True,'dependencies_identical':True,'base_layers_preserved':True,
        'context':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in context.iterdir()}}
(E/'t3-exact-source-audit-20260927.json').write_text(json.dumps(result,indent=2)+'\n')
print(control['Id']);print('audited source files',len(original['files']));print('changed',delta)
