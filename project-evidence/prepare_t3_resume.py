"""Stage only incomplete original scenarios; preserve original denominator and hashes."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
repo = ROOT / 'track3-simulation-public'
command = ['git', '-c', f'safe.directory={repo}', '-C', str(repo)]
ref = subprocess.check_output(command+['rev-parse','origin/main'],text=True).strip()
delta = subprocess.check_output(command+['diff','e9b770da',ref,'--','regression_suite/run_regression.py',
    'regression_suite/scenarios','qfbench2_track_simulation','baselines/abides_baseline'],text=True)
if delta.strip():
    raise RuntimeError('Relevant source changed; revalidate before resuming')
old = json.loads((ROOT/'project-evidence/t3-02-run-summary.json').read_text())
destination = ROOT/'.validation/t3-resume-20260924'
destination.mkdir(exist_ok=False)
rows=[]
for row in old['single_roster']:
    if row['status'] == 'passed': continue
    source = ROOT / row['source'].replace('\\','/')
    target = destination / source.name
    shutil.copyfile(source,target)
    rows.append({'scenario_id':row['scenario_id'],'file':target.name,
                 'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
assert len(rows)==38
(ROOT/'project-evidence/t3-resume-roster.json').write_text(json.dumps({
    'official_ref':ref,'source_delta_empty':True,'previous_passed':27,'resumed':rows,
    'full_denominator':65,'concurrency':1,'exemplar_excluded_by_official_withdrawal':True},indent=2)+'\n')
print('Prepared38 unchanged scenarios')
