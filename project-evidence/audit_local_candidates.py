"""R01 local identity/source audit; no credentials or registry operations."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'project-evidence/r01-local-audit-20260925-v3'
OUT.mkdir(exist_ok=False)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect(tag):
    return json.loads(subprocess.check_output(['docker', 'image', 'inspect', tag]))[0]


specs = [
    ('T1', 'qfbench-agent:house-budget-20260922', 'cfade5cc601048069aca618a9184aa795c65402f31c3ee6607bfca344b18148c',
     {f'/app/agent/{p.name}': p for p in (ROOT / 'qfbench-agent/agent').glob('*.py')}, None),
    ('T2', 'forecast-agent:local-20260925', 'faa9e5cb19d4964f42dd97c4a9622f05c6507dea37656eb15eef672842bf8f6c',
     {f'/app/{p.relative_to(ROOT / ".validation/t2-build-20260925").as_posix()}': p
      for p in (ROOT / '.validation/t2-build-20260925').rglob('*.py')}, '2.4.4'),
    ('T3', 'simulation-agent:runtime-20260925', 'c5724d34e2a8c951e4645b7c9aed1b95e3eabf1bd3dc24c0195055700cdbd45d',
     {'/opt/participant_build/patch_timestamp_cache.py': ROOT / 'simulation-agent/patch_timestamp_cache.py'}, None),
    ('T4', 'analysis-agent:local-20260924', '69e4d2efc1c39e5901851cd4c40ff69fa0c3239ca45b929f0542bccaeea1cbae',
     {**{f'/app/analysis-agent/analysis_agent/{p.name}': p for p in (ROOT / 'analysis-agent/analysis_agent').glob('*.py')},
      '/app/qfbench-agent/agent/model_client.py': ROOT / 'qfbench-agent/agent/model_client.py'}, '2.4.4')]

probe = r'''
import hashlib, importlib.metadata, json, os, pathlib, platform, sys
files = json.loads(sys.argv[1])
versions = {}
for name in ['qfbench2-common', 'numpy', 'pandas', 'pyarrow', 'jsonschema']:
    try: versions[name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError: versions[name] = None
result = {'python': platform.python_version(), 'uid': os.getuid(), 'versions': versions,
          'file_hashes': {p: hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in files},
          'sensitive_filenames': []}
for root in ['/app', '/opt/participant_build']:
    if pathlib.Path(root).exists():
        for path in pathlib.Path(root).rglob('*'):
            if path.is_file() and (path.name in {'.env', '.netrc', '.npmrc', 'id_rsa', 'id_ed25519', 'credentials'}
                                  or path.suffix in {'.pem', '.key'}):
                result['sensitive_filenames'].append(str(path))
if sys.argv[2] == 'T3':
    import abides_core.utils
    result['formatter_module_sha256'] = hashlib.sha256(pathlib.Path(abides_core.utils.__file__).read_bytes()).hexdigest()
print(json.dumps(result))
'''
report = {'created_at': datetime.now(timezone.utc).isoformat(), 'task': 'R01',
          'scope': 'Local immutable identities, participant-source bytes and source recovery; no publication',
          'registry_digest': None, 'records': [], 'refs': {}, 'production_verified': False}
for repo in ['Agenthon2026-public', 'track1-coding-public', 'track2-forecasting-public',
             'track3-simulation-public', 'track4-analysis-public']:
    report['refs'][repo] = subprocess.check_output(['git', '-C', str(ROOT / repo), 'rev-parse', 'origin/main'], text=True).strip()
for track, tag, image_hash, files, toolkit in specs:
    metadata = inspect(tag)
    assert metadata['Id'] == 'sha256:' + image_hash
    config = metadata['Config']
    sensitive_env_names = []
    for item in config.get('Env') or []:
        key, _, value = item.partition('=')
        if value and any(word in key.upper() for word in ['TOKEN', 'SECRET', 'PASSWORD', 'API_KEY', 'TEAM_KEY']):
            sensitive_env_names.append(key)
    row = {'track': track, 'tag': tag, 'local_image_id': metadata['Id'], 'registry_digest': None,
           'architecture': metadata['Architecture'], 'os': metadata['Os'],
           'interface_version': (config.get('Labels') or {}).get('qfbench2.interface_version'),
           'entrypoint': config.get('Entrypoint'), 'cmd': config.get('Cmd'),
           'working_dir': config.get('WorkingDir'), 'default_user': config.get('User'),
           'sensitive_nonempty_image_env_names': sensitive_env_names,
           'status': 'running'}
    report['records'].append(row)
    (OUT / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    result = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--read-only',
        '--user', '65534:65534', '--cap-drop=ALL', '--security-opt', 'no-new-privileges',
        '--memory', '512m', '--memory-swap', '512m', '--pids-limit', '256',
        '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777', '--entrypoint', 'python',
        metadata['Id'], '-c', probe, json.dumps(list(files)), track], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, (track, result.stderr[-1000:])
    runtime = json.loads(result.stdout)
    expected = {path: digest(local) for path, local in files.items()}
    row.update(runtime=runtime, expected_source_hashes=expected,
               source_paths={path: local.relative_to(ROOT).as_posix() for path, local in files.items()})
    row['evaluator_toolkit_version'] = '2.4.3' if track == 'T1' else '2.4.4'
    (OUT / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    assert runtime['file_hashes'] == expected, track
    assert row['architecture'] == 'amd64' and row['os'] == 'linux'
    assert row['interface_version'] == '2.0'
    assert not sensitive_env_names and not runtime['sensitive_filenames']
    assert runtime['uid'] == 65534
    if track != 'T3':
        assert runtime['versions']['qfbench2-common'] == toolkit
        assert runtime['python'].startswith('3.13.')
    else:
        assert runtime['python'].startswith('3.11.')
        assert runtime['formatter_module_sha256'] == '342febd26345bad77936bbe281678631fd332e69de844e98a8a86ea568d6cfba'
        parent = inspect('simulation-agent:format-cache-20260924')
        base_layers = parent['RootFS']['Layers']
        layers = metadata['RootFS']['Layers']
        # BuildKit represents WORKDIR /tmp with an empty 1024-byte tar layer.
        # Verify its exact digest rather than assuming layer lists are identical.
        empty_tar_digest = 'sha256:' + hashlib.sha256(bytes(1024)).hexdigest()
        assert layers == base_layers + [empty_tar_digest]
        row['runtime_parent_layers_preserved'] = True
        row['runtime_extra_layer'] = {'diff_id': empty_tar_digest, 'content': 'empty 1024-byte tar'}
    row['status'] = 'passed'
    (OUT / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print('PASS', track, len(files), 'participant source files', flush=True)

# Explicit source/build-context allowlist, never a whole-workspace or credential backup.
selected = set()
for folder in ['qfbench-agent', 'forecast-agent', 'analysis-agent', 'simulation-agent']:
    root = ROOT / folder
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if any(part in {'.git', '.venv', '__pycache__', 'artifacts', 'output', 'data'} for part in relative.parts):
            continue
        if path.is_file() and (path.suffix in {'.py', '.md', '.toml'} or
                path.name in {'Dockerfile', 'Dockerfile.harness', 'Dockerfile.runtime', 'requirements.txt', '.dockerignore', 'LICENSE'}):
            selected.add(path)
for context in ['t2-build-20260925', 't4-build-20260924', 't3-format-cache-build', 't3-runtime-build-20260925']:
    selected.update(p for p in (ROOT / '.validation' / context).rglob('*') if p.is_file())
manifest = [{'path': p.relative_to(ROOT).as_posix(), 'bytes': p.stat().st_size, 'sha256': digest(p)} for p in sorted(selected)]
archive = OUT / 'participant-source.zip'
with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
    for row in manifest:
        bundle.write(ROOT / row['path'], row['path'])
with zipfile.ZipFile(archive) as bundle:
    assert len(bundle.namelist()) == len(manifest)
    assert all(hashlib.sha256(bundle.read(row['path'])).hexdigest() == row['sha256'] for row in manifest)
(OUT / 'source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
repo = ROOT / 'qfbench-agent'
git = ['git', '-c', f'safe.directory={repo.as_posix()}', '-C', str(repo)]
dirty = subprocess.check_output(git + ['status', '--porcelain', '--untracked-files=all'], text=True)
(OUT / 'qfbench-agent-dirty-files.txt').write_text(dirty)
archived = {row['path'] for row in manifest}
not_archived = [line[3:] for line in dirty.splitlines() if 'qfbench-agent/' + line[3:] not in archived]
report.update(all_local_image_audits_passed=True, source_archive={'path': archive.name,
    'sha256': digest(archive), 'files': len(manifest), 'roundtrip_hashes_verified': True,
    'qfbench_agent_head': subprocess.check_output(git + ['rev-parse', 'HEAD'], text=True).strip(),
    'dirty_files_not_archived': not_archived},
    limitations=['Credential checks cover image environment names and participant app filenames plus whitelist contexts; not an exhaustive base-layer secret scan',
        'Source archive contains participant source and build contexts; official base images and dependency caches are separate pinned dependencies',
        'No release descriptor or registry manifest digest; no publication/upload',
        'T1/T4 real House and T4 judge unavailable; T3 full release report determines its local release gate'])
assert not not_archived
(OUT / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
print('PASS source archive', len(manifest), 'files')
