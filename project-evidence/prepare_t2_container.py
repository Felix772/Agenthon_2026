"""Build a source-only whitelist, preserving exact upstream helper bytes."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]
repo=ROOT/'track2-forecasting-public'
ref='8799596ae68a6ec26f749f46054c39ad2b89512e'
git=['git','-c',f'safe.directory={repo}','-C',str(repo)]
assert subprocess.check_output(git+['rev-parse','origin/main'],text=True).strip()==ref
context=ROOT/'.validation/t2-build-20260925'
context.mkdir(exist_ok=False)
for name in ['Dockerfile','forecast.py','joint_model.py','text_events.py']:
    shutil.copyfile(ROOT/'forecast-agent'/name,context/name)
package=context/'qfbench2_track_forecasting'
package.mkdir()
# Upstream package __init__ imports the scorer. This intentionally minimal
# participant-only initializer exposes helpers without importing evaluator code.
(package/'__init__.py').write_text('"""Pinned Track2 contract helpers only; no scoring API in this participant subset."""\n')
helpers=['grid.py','limits.py','failures.py','horizons.py','targets.py']
for name in helpers:
    (package/name).write_bytes(subprocess.check_output(git+['show',f'{ref}:qfbench2_track_forecasting/{name}']))
(context/'upstream-LICENSE').write_bytes(subprocess.check_output(git+['show',f'{ref}:LICENSE']))
manifest={'upstream_ref':ref,'toolkit':'2.4.4','helper_modules':helpers,
          'initializer':'Participant minimal initializer replaces scorer-importing upstream initializer',
          'excluded':['public unit data','reference outcomes','scoring.py','tests','credentials'],
          'files':[{'path':p.relative_to(context).as_posix(),'bytes':p.stat().st_size,
                    'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(context.rglob('*')) if p.is_file()]}
(ROOT/'project-evidence/t2-container-build-context.json').write_text(json.dumps(manifest,indent=2)+'\n')
print('Prepared',len(manifest['files']),'whitelisted files')
