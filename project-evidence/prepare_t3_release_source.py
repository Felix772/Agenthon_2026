"""Stage an unchanged raw official snapshot for manifest-checked release tests."""
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
REF = '1504b37b8472f7f949d4b8f80b7c8b2b71ab0e43'
destination = ROOT / '.validation/t3-release-source-20260925'
destination.mkdir(exist_ok=False)
archive = ROOT / '.validation/t3-release-source-20260925.tar'
with archive.open('xb') as output:
    subprocess.run(['git', '-C', str(ROOT / 'track3-simulation-public'), '-c', 'core.autocrlf=false',
                    'archive', '--format=tar', REF], stdout=output, check=True)
with tarfile.open(archive) as bundle:
    bundle.extractall(destination, filter='data')
refs = {}
for name in ['Agenthon2026-public', 'track1-coding-public', 'track2-forecasting-public',
             'track3-simulation-public', 'track4-analysis-public']:
    refs[name] = subprocess.check_output(['git', '-C', str(ROOT / name), 'rev-parse', 'origin/main'], text=True).strip()
(ROOT / 'project-evidence/t3-06-source-recheck.json').write_text(json.dumps({
    'date': '2026-09-25', 'task': 'T3-06', 'refs': refs,
    'consultation': 'Fresh fetch of all five repos; unchanged from instructions consulted before T3 runtime work',
    'snapshot': str(destination.relative_to(ROOT)), 'raw_git_archive': True,
    'withdrawn_exemplar_included': False}, indent=2) + '\n')
print(destination)
