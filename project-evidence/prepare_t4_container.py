"""Whitelist-only build context and synthetic protocol fixture; no secret copying."""
import hashlib
import json
from pathlib import Path
import shutil
ROOT=Path(__file__).resolve().parents[1]
context=ROOT/'.validation/t4-build-20260924'
context.mkdir(exist_ok=False)
(context/'analysis_agent').mkdir()
for path in sorted((ROOT/'analysis-agent/analysis_agent').glob('*.py')):
    shutil.copyfile(path,context/'analysis_agent'/path.name)
shutil.copyfile(ROOT/'analysis-agent/Dockerfile',context/'Dockerfile')
shutil.copyfile(ROOT/'qfbench-agent/agent/model_client.py',context/'model_client.py')
unit=ROOT/'project-evidence/t4-container-input'
(unit/'corpus').mkdir(parents=True,exist_ok=False)
task={'task_id':'synthetic-container-only','target':{'type':'regression','unit':'USD','name':'revenue'},
      'cutoff_date':'2024-01-01','prompt':'Forecast revenue from supplied synthetic evidence.',
      'entities':[{'entity_id':f'E{i}','name':'Revenue entity'} for i in range(4)]}
(unit/'task.json').write_text(json.dumps(task),encoding='utf-8')
doc={'doc_id':'doc','doc_date':'2024-01-01','text':'Revenue in this synthetic fixture increased to one dollar.'}
payload=json.dumps(doc).encode()
(unit/'corpus/doc.json').write_bytes(payload)
(unit/'manifest.json').write_text(json.dumps({'files':[{'path':'corpus/doc.json','role':'corpus',
    'sha256':hashlib.sha256(payload).hexdigest()}]}),encoding='utf-8')
manifest=[{'path':str(p.relative_to(context)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
          for p in sorted(context.rglob('*')) if p.is_file()]
(ROOT/'project-evidence/t4-container-build-context.json').write_text(json.dumps(manifest,indent=2)+'\n')
print('Prepared whitelisted code-only context and synthetic input')
