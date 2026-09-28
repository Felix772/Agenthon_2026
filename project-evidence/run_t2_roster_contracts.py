"""Full public statistical branch: staged inputs and official gates, no answer scoring."""
from collections import Counter
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import time
import tomllib

ROOT=Path(__file__).resolve().parents[1]
E=ROOT/'project-evidence'
OUT=E/'t2-06-host-contracts-20260925'
SOURCE=ROOT/'.validation/t2-current-20260925'
ref='8799596ae68a6ec26f749f46054c39ad2b89512e'
repo=ROOT/'track2-forecasting-public'
git=['git','-c',f'safe.directory={repo}','-c','core.autocrlf=false','-C',str(repo)]
assert subprocess.check_output(git+['rev-parse','origin/main'],text=True).strip()==ref
OUT.mkdir(exist_ok=False)
SOURCE.mkdir(exist_ok=False)
archive=ROOT/'.validation/t2-current-20260925.tar'
subprocess.run(git+['archive','--format=tar',f'--output={archive}',ref],check=True)
with tarfile.open(archive) as tar:tar.extractall(SOURCE,filter='data')
sys.path.insert(0,str(SOURCE))
sys.path.insert(0,str(ROOT/'forecast-agent'))
from forecast import main as forecast
from qfbench2_track_forecasting.scoring import _main as official_score_cli

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
units=sorted((SOURCE/'units').glob('*/card.toml'))
assert len(units)==104
practice=[p for p in units if p.parent.name!='t2-EXAMPLE-ust-curve-1m']
assert len(practice)==103
report={'source_ref':ref,'created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'Windows host numerical branch and official gates only; C05 image readiness remains separate',
        'practice_denominator':103,'exemplar_denominator':1,'seed':20260925,'method':'gaussian',
        'house_calls':0,'image_id':None,'scored':False,'production_verified':False,
        'source_hashes':{p.name:digest(p) for p in (ROOT/'forecast-agent').glob('*.py')},'records':[]}
(OUT/'plan.json').write_text(json.dumps({k:v for k,v in report.items() if k!='records'},indent=2)+'\n')
for card_path in units:
    unit=card_path.parent
    start=time.perf_counter()
    row={'unit':unit.name,'is_exemplar':unit.name=='t2-EXAMPLE-ust-curve-1m','status':'running'}
    report['records'].append(row)
    try:
        card=tomllib.loads(card_path.read_text(encoding='utf-8'))
        spec_path=unit/'forecast_spec.json'
        spec=json.loads(spec_path.read_text()) if spec_path.exists() else {}
        targets=card['targets']
        params=card.get('scoring',{}).get('params',{})
        minimum=max(200,int(params.get('n_draws_min',0)),int(spec.get('n_draws_min',0)))
        draws=max(500,minimum)
        row.update(family=card['metadata']['category'],assets=targets['asset_ids'],horizons=targets['horizons'],
                   target=targets['target_type'],frequency=targets.get('target_frequency',card['metadata'].get('target_frequency')),
                   cutoff=str(card['provenance']['data_cutoff']),minimum_draws=minimum,draws=draws,
                   expected_rows=draws*len(targets['asset_ids'])*len(targets['horizons']),
                   declared_score_components=params.get('weights'),
                   expected_one_percent_tail_draws=draws*.01)
        staged=OUT/'inputs'/unit.name
        (staged/'panels').mkdir(parents=True)
        (staged/'text').mkdir()
        manifest=json.loads((unit/'manifest.json').read_text())
        selected=[]
        for entry in manifest['files']:
            rel=PurePosixPath(entry['path'])
            if rel.is_absolute() or '..' in rel.parts:raise ValueError('Unsafe manifest path')
            is_panel=entry.get('role')=='input' and rel.suffix=='.parquet'
            is_text=rel.parts[0]=='text' and entry.get('role') in ('corpus','fixture','input','metadata')
            is_contract=rel.as_posix() in ('card.toml','forecast_spec.json')
            if not (is_panel or is_text or is_contract):continue
            source=unit/rel
            if source.is_symlink() or not source.is_file():raise ValueError('Invalid input member')
            actual=digest(source)
            if actual!=entry['sha256']:raise ValueError('Upstream raw-byte hash mismatch: '+str(rel))
            target=staged/('panels/'+rel.name if is_panel else str(rel))
            target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():raise ValueError('Colliding staged input')
            shutil.copyfile(source,target)
            selected.append({'source':str(rel),'staged':target.relative_to(staged).as_posix(),'sha256':actual})
        if not (staged/'card.toml').exists() or not any((staged/'panels').glob('*.parquet')):
            raise ValueError('Missing required staged inputs')
        if spec and not (staged/'forecast_spec.json').exists():raise ValueError('Missing staged specification')
        if (unit/'text/corpus_index.json').exists() and not (staged/'text/corpus_index.json').exists():
            raise ValueError('Corpus index not declared/staged')
        row['staged_files']=selected
        row['text_files']=len(list((staged/'text').glob('*.txt')))
        output=OUT/'outputs'/unit.name
        stream=io.StringIO()
        with redirect_stdout(stream):
            code=forecast(['forecast','--panels',str(staged/'panels'),'--text',str(staged/'text'),
                           '--asof',row['cutoff'],'--out',str(output/'forecast.parquet'),
                           '--seed','20260925','--n-draws',str(draws)])
        assert code==0
        row['forecast_log']=stream.getvalue()
        stream=io.StringIO()
        with redirect_stdout(stream):
            code=official_score_cli(['score','--card',str(card_path),'--forecast',str(output/'forecast.parquet')])
        row['official_verdict']=json.loads(stream.getvalue())
        row['output_files']={p.name:{'sha256':digest(p),'bytes':p.stat().st_size} for p in output.iterdir()}
        row['output_bytes']=sum(p['bytes'] for p in row['output_files'].values())
        row['status']='passed' if code==0 and row['official_verdict']['admissible'] else 'failed'
    except Exception as exc:
        row.update(status='error',error_type=type(exc).__name__,error=str(exc))
    row['elapsed_sec']=time.perf_counter()-start
    (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(row['status'].upper(),unit.name,row.get('error',''),flush=True)
report['practice_results']=dict(Counter(r['status'] for r in report['records'] if not r['is_exemplar']))
report['exemplar_results']=dict(Counter(r['status'] for r in report['records'] if r['is_exemplar']))
report['families']=dict(Counter(r.get('family','unknown') for r in report['records'] if not r['is_exemplar']))
report['targets']=dict(Counter(r.get('target','unknown') for r in report['records'] if not r['is_exemplar']))
report['all_contracts_passed']=all(r['status']=='passed' for r in report['records'])
report['limitations']=['Host runs only; container/API branch not certified by this run',
 'No realized outcomes or official normalization bundle, so no accuracy score',
 '500 draws imply only5 expected samples in each1% tail; this is not tail-quality validation',
 'Default text adjustment is none; lexical audit does not establish forecast value',
 'Host elapsed time is recorded without enforcing a per-unit container deadline']
(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:report[k] for k in ['practice_results','exemplar_results','families','targets','all_contracts_passed']},indent=2))
