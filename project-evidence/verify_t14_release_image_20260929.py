"""Bind a release reference to an already frozen candidate; never build or push."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def run(args):
    return subprocess.check_output(args, text=True, encoding='utf-8', timeout=120)

def inspect(image):
    return json.loads(run(['docker', 'image', 'inspect', image]))[0]

PROBE = '''
import hashlib,importlib.metadata,importlib.machinery,json,os,pathlib
files={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in pathlib.Path('/app').rglob('*') if p.is_file()}
loader_matches={str(p):importlib.machinery.SourceFileLoader(p.stem,str(p)).get_code(p.stem)==compile(p.read_bytes(),str(p),'exec') for p in pathlib.Path('/app').rglob('*.py')}
print(json.dumps({'uid':os.getuid(),'files':files,'loader_matches_source':loader_matches,'dependencies':{d.metadata['Name'].lower():d.version for d in importlib.metadata.distributions()}}))
'''

def probe(image):
    return json.loads(run(['docker','run','--rm','--network=none','--read-only',
        '--user=65534:65534','--cap-drop=ALL','--security-opt=no-new-privileges',
        '--memory=512m','--memory-swap=512m','--pids-limit=256',
        '--entrypoint=python',image,'-B','-c',PROBE]))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track',choices=['t1','t4'],required=True)
    parser.add_argument('--image',required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if args.report.exists():
        raise RuntimeError('Refusing to overwrite audit evidence')
    record_path=ROOT/f'project-evidence/{args.track}-core-build-20260929-v1.json'
    record=json.loads(record_path.read_text())
    metadata=inspect(args.image)
    assert metadata['Id']==record['image_id'], 'Reference does not identify the frozen image'
    baseline=inspect(record['baseline_image_id'])
    assert metadata['RootFS']['Layers'][:len(baseline['RootFS']['Layers'])]==baseline['RootFS']['Layers']
    config=metadata['Config']
    assert metadata['Os']=='linux' and metadata['Architecture']=='amd64'
    assert config['Labels']['qfbench2.interface_version']=='2.0'
    expected_entrypoint=['python','-m','agent' if args.track=='t1' else 'analysis_agent.cli']
    assert config['Entrypoint']==expected_entrypoint
    sensitive_names=[item.split('=',1)[0] for item in config.get('Env',[]) if item.partition('=')[2]
        and any(word in item.split('=',1)[0].upper() for word in ['TOKEN','SECRET','PASSWORD','API_KEY','TEAM_KEY'])]
    assert not sensitive_names, 'Image has a nonempty sensitive environment setting'
    runtime=probe(record['image_id'])
    assert runtime['uid']==65534 and runtime['dependencies']==record['dependencies']
    assert runtime['loader_matches_source'] and all(runtime['loader_matches_source'].values()), 'Python loader would execute code different from current source'
    expected_files=dict(record['image_source_hashes'])
    if args.track=='t1':
        base_files=probe(record['baseline_image_id'])['files']
        for name in ['/app/requirements.txt','/app/runtime-versions.txt']:
            expected_files[name]=base_files[name]
        # These nine cache files were already in the published baseline. Require their
        # exact prior hashes and independently require loader code to match new source.
        for name in ['__init__','__main__','cli','execution','model_client','prompts','solver','task_reader','workspace']:
            path=f'/app/agent/__pycache__/{name}.cpython-313.pyc'
            expected_files[path]=base_files[path]
    assert runtime['files']==expected_files, 'Unexpected app files or changed source bytes'
    context=ROOT/record['context']
    actual_context={p.relative_to(context).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in context.rglob('*') if p.is_file()}
    assert actual_context==record['context_files'], 'Frozen build context changed'
    result={'checked_at_utc':datetime.now(timezone.utc).isoformat(),'track':args.track,
        'reference':args.image,'image_id':metadata['Id'],'platform':'linux/amd64',
        'entrypoint':config['Entrypoint'],'cmd':config.get('Cmd'),'working_dir':config['WorkingDir'],
        'default_user':config.get('User'),'runtime_uid':runtime['uid'],'interface_version':'2.0',
        'source_build_record':record_path.relative_to(ROOT).as_posix(),
        'source_build_record_sha256':hashlib.sha256(record_path.read_bytes()).hexdigest(),
        'app_file_hashes':runtime['files'],'dependencies':runtime['dependencies'],
        'python_loader_code_matches_current_source':runtime['loader_matches_source'],
        'baseline_python_cache_files_bound':9 if args.track=='t1' else 0,
        'frozen_context_hashes_match':True,'baseline_layers_preserved':True,
        'app_exact_allowlist_pass':True,'source_identity_pass':True,'sensitive_image_environment_names':[],
        'a3_included':False if args.track=='t4' else None,
        'exclusion_scope':'Entire /app exact file/hash allowlist and frozen context; inherited OS/dependency layers unchanged. Not an exhaustive audit of all base-layer files.',
        'quality_or_house_execution_verified':False,'passed':True}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    with args.report.open('x',encoding='utf-8') as output:
        json.dump(result,output,indent=2); output.write('\n')
    print(json.dumps({'track':args.track,'image':metadata['Id'],'app_files':len(expected_files),'passed':True,'report':str(args.report)}))

if __name__=='__main__':
    main()
