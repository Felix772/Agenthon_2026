"""Build an allowlisted local candidate; never push, package or submit it."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASES = {
    't1': ('qfbench-agent:dev', 'sha256:cfade5cc601048069aca618a9184aa795c65402f31c3ee6607bfca344b18148c'),
    't4': ('analysis-agent:local-20260924', 'sha256:69e4d2efc1c39e5901851cd4c40ff69fa0c3239ca45b929f0542bccaeea1cbae'),
}
WHEEL = ROOT / '.validation/t4-v250-wheel-20260929/qfbench2_common-2.5.0-py3-none-any.whl'
WHEEL_HASH = '92d4f1701e8b5409007927c2534832df93214fd45fa41c1a272419ffd6142828'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(argv, timeout=120):
    return subprocess.run(argv, capture_output=True, text=True, encoding='utf-8',
                          check=True, timeout=timeout).stdout.strip()


def inspect(image):
    return json.loads(run(['docker', 'image', 'inspect', image]))[0]


def image_python(image, code):
    return run(['docker', 'run', '--rm', '--network=none', '--read-only',
                '--user=65534:65534', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                '--entrypoint=python', image, '-c', code])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track', choices=BASES, required=True)
    parser.add_argument('--suffix', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9-]{1,35}', args.suffix):
        parser.error('suffix must be lowercase alphanumeric/hyphens')
    base_tag, base_id = BASES[args.track]
    base = inspect(base_tag)
    if base['Id'] != base_id:
        raise RuntimeError('Existing baseline tag no longer has its recorded identity')
    context = ROOT / f'.validation/{args.track}-core-{args.suffix}'
    context.mkdir(exist_ok=False)
    tag = f'agenthon-{args.track}:core-{args.suffix}'
    evidence = ROOT / f'project-evidence/{args.track}-core-build-{args.suffix}'
    if evidence.with_suffix('.json').exists():
        raise RuntimeError('Refusing to overwrite a previous build record')
    module = 'agent' if args.track == 't1' else 'analysis_agent'
    source_dir = ROOT / ('qfbench-agent' if args.track == 't1' else 'analysis-agent') / module
    (context / module).mkdir()
    source_hashes = {}
    for source in sorted(source_dir.glob('*.py')):
        shutil.copyfile(source, context / module / source.name)
        source_hashes[source.relative_to(ROOT).as_posix()] = digest(source)
    # A local tag is used only after exact ID verification, and verified again below.
    dockerfile = f'FROM {base_tag}\n'
    if args.track == 't1':
        dockerfile += 'COPY agent /app/agent\n'
    else:
        if digest(WHEEL) != WHEEL_HASH:
            raise RuntimeError('Toolkit wheel identity changed')
        shutil.copyfile(WHEEL, context / WHEEL.name)
        source = ROOT / 'qfbench-agent/agent/model_client.py'
        shutil.copyfile(source, context / 'model_client.py')
        source_hashes[source.relative_to(ROOT).as_posix()] = digest(source)
        dockerfile += (
            'USER 0\n'
            f'COPY {WHEEL.name} /tmp/{WHEEL.name}\n'
            f'RUN python -m pip install --no-deps --no-cache-dir /tmp/{WHEEL.name} && rm /tmp/{WHEEL.name}\n'
            'COPY analysis_agent /app/analysis-agent/analysis_agent\n'
            'COPY model_client.py /app/qfbench-agent/agent/model_client.py\n'
            'USER 65534:65534\n'
        )
    (context / 'Dockerfile').write_text(dockerfile, encoding='utf-8', newline='\n')
    record = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'track': args.track, 'tag': tag, 'baseline_image_id': base_id,
        'context': context.relative_to(ROOT).as_posix(),
        'source_hashes': source_hashes,
        'context_files': {p.relative_to(context).as_posix(): digest(p)
                          for p in sorted(context.rglob('*')) if p.is_file()},
        'published': False, 'uploaded': False, 'house_verified': False,
        'scope': 'Local candidate build and identity checks only; runtime gates separate',
    }
    command = ['docker', 'build', '--platform=linux/amd64', '--network=none',
               '--pull=false', '--tag', tag, str(context)]
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=300)
    evidence.with_suffix('.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    record['build_exit_code'] = result.returncode
    evidence.with_suffix('.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    if result.returncode:
        raise RuntimeError('Candidate build failed; retained build log')
    candidate = inspect(tag)
    assert inspect(base_tag)['Id'] == base_id
    assert candidate['RootFS']['Layers'][:len(base['RootFS']['Layers'])] == base['RootFS']['Layers']
    assert candidate['Os'] == 'linux' and candidate['Architecture'] == 'amd64'
    assert candidate['Config']['Labels']['qfbench2.interface_version'] == '2.0'
    paths = ['/app/agent'] if args.track == 't1' else ['/app/analysis-agent/analysis_agent']
    code = ('import pathlib,hashlib,json; '
            f'files=[p for d in {paths!r} for p in pathlib.Path(d).glob("*.py")]; ')
    if args.track == 't4':
        code += 'files.append(pathlib.Path("/app/qfbench-agent/agent/model_client.py")); '
    code += 'print(json.dumps({str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}))'
    actual = json.loads(image_python(candidate['Id'], code))
    expected = {}
    for source, source_hash in source_hashes.items():
        if args.track == 't1':
            target = '/app/agent/' + Path(source).name
        elif source.startswith('qfbench-agent/'):
            target = '/app/qfbench-agent/agent/model_client.py'
        else:
            target = '/app/analysis-agent/analysis_agent/' + Path(source).name
        expected[target] = source_hash
    assert actual == expected, 'Candidate source files differ from snapshot'
    expected_module_paths = {path for path in source_hashes
                             if path.startswith(source_dir.relative_to(ROOT).as_posix() + '/')}
    assert {path.relative_to(ROOT).as_posix() for path in source_dir.glob('*.py')} == expected_module_paths
    assert all(digest(ROOT / path) == value for path, value in source_hashes.items()), 'Source changed during build'
    version_code = 'import importlib.metadata,json; print(json.dumps({d.metadata["Name"].lower():d.version for d in importlib.metadata.distributions()}))'
    before = json.loads(image_python(base_id, version_code))
    after = json.loads(image_python(candidate['Id'], version_code))
    changes = {key: [before.get(key), after.get(key)] for key in before.keys() | after.keys()
               if before.get(key) != after.get(key)}
    if args.track == 't1':
        assert not changes, 'Unexpected T1 dependency change'
    else:
        assert changes == {'qfbench2-common': ['2.4.4', '2.5.0']}, changes
    record.update(image_id=candidate['Id'], source_identity_passed=True,
                  image_source_hashes=actual, dependencies=after, dependency_changes=changes,
                  platform='linux/amd64', interface_version='2.0',
                  source_git_head=run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']))
    evidence.with_suffix('.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'image': record['image_id'], 'tag': tag, 'source_files': len(actual),
                      'dependency_changes': changes, 'published': False}))


if __name__ == '__main__':
    main()
