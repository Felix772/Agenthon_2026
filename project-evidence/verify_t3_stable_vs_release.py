"""Compare fresh instrumented runs with independently retained release artifacts."""
import argparse
import json
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('experiment',type=Path);a=p.parse_args()
root=Path(__file__).resolve().parents[1]
old=json.loads((root/'project-evidence/t3-06-release-20260925/report.json').read_text())
reference={r['unit']:r['runs'][0] for r in old['records'] if r['status']=='passed'}
rows=json.loads((a.experiment/'runs.json').read_text());checks=[]
for r in rows:
    target=reference[r['unit']]
    stable={p:v['sha256'] for p,v in target['files'].items() if p.endswith('.parquet')}
    assert r['stable']==stable,(r['unit'],r['pair'],r['variant'])
    assert r['event_count']==target['host_n_events']
    checks.append({'unit':r['unit'],'pair':r['pair'],'variant':r['variant'],'matched_release':True})
(a.experiment/'release-identity-check.json').write_text(json.dumps({'checks':checks,'all_match':True},indent=2)+'\n')
print('Release byte/count identity:',len(checks),'/',len(rows))
