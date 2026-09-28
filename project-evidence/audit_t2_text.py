"""Audit every current public corpus without model calls or outcome access."""
import json
from pathlib import Path
import sys
import tomllib
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'forecast-agent'))
from text_events import load_text,retrieve
rows=[]
for unit in sorted((ROOT/'track2-forecasting-public/units').iterdir()):
    if not (unit/'card.toml').exists():continue
    row={'unit':unit.name}
    try:
        card=tomllib.loads((unit/'card.toml').read_text(encoding='utf-8'))
        cutoff=str(card['provenance']['data_cutoff'])
        docs,audit=load_text(unit/'text',cutoff)
        hits=retrieve(docs,' '.join(card['targets']['asset_ids'])+' interest rates inflation growth policy')
        for hit in hits:
            assert hit['quote']==docs[hit['doc_id']]['text'][hit['span_start']:hit['span_end']]
        row.update(status='passed',cutoff=cutoff,audit=audit,hit_count=len(hits),
                   documents=[{'doc_id':k,'sha256':v['sha256'],'released':v['released']} for k,v in docs.items()])
    except Exception as exc:
        row.update(status='failed',error_type=type(exc).__name__,reason=str(exc))
    rows.append(row)
report={'scope':'Local staged text bytes; digests identify this checkout, not raw Git blobs',
        'denominator':len(rows),'passed':sum(r['status']=='passed' for r in rows),
        'rows':rows,'model_calls':0,'text_prediction_gain_measured':False}
(ROOT/'project-evidence/t2-05a-public-text-audit.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='rows'}))
raise SystemExit(0 if report['passed']==report['denominator'] else 1)
