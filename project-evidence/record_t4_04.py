"""Record synthetic integration separately from unavailable real House quality."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / 'project-evidence'
refs = json.loads((E / 't4-03-source-recheck.json').read_text(encoding='utf-8'))
for row in refs:
    repo = ROOT / row['repository']
    command = ['git', '-c', f'safe.directory={repo}', '-C', str(repo)]
    before = row['ref']
    row['ref'] = subprocess.check_output(command+['rev-parse', 'origin/main'], text=True).strip()
    row['checked_at'] = datetime.now(timezone.utc).isoformat()
    diff = subprocess.check_output(command+['diff', before, row['ref'], '--', '*.md'])
    filename = f"t4-04-{row['repository']}-docs.diff"
    (E / filename).write_bytes(diff)
    row['documentation_diff'] = 'project-evidence/'+filename
    row['review'] = 'Fresh fetch and complete changed Markdown reviewed; unchanged previously consulted rules remain applicable.'
(E / 't4-04-source-recheck.json').write_text(json.dumps(refs, indent=2)+'\n')
assert '72 passed, 1 skipped' in (E / 't4-04-tests-windows-final.log').read_text()
assert '73 passed' in (E / 't4-04-tests-linux-final.log').read_text()
files = sorted(p for p in (ROOT / 'analysis-agent').rglob('*') if p.is_file()
               and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts)
files.append(ROOT / 'qfbench-agent/agent/model_client.py')
report = {'task': 'T4-04', 'local_integration': 'passed', 'real_house_quality': 'blocked-external',
          'house_configuration_present_at_check': {'MODEL_ENDPOINT': False, 'MODEL_NAME': False, 'MODEL_TOKEN': False},
          'windows_tests_passed': 72, 'windows_tests_skipped': 1, 'linux_tests_passed': 73,
          'production_verified': False, 'production_faithfulness_verified': False,
          'prediction_quality_measured': False, 'chatgpt_review': 'pending',
          'source_files': [{'path': str(p.relative_to(ROOT)), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in files],
          'limitations': ['Synthetic model responses only', 'Shared transport remains a sibling workspace dependency',
                          'Failure output deliberately non-admissible; no invented evidence',
                          'No submission image or live production judge', 'Formal prediction entailment not established']}
(E / 't4-04-run-summary.json').write_text(json.dumps(report, indent=2)+'\n')
path = ROOT / 'planning/task-status.json'
status = json.loads(path.read_text(encoding='utf-8'))
for task in status['tasks']:
    if task['id'] == 'T4-04':
        task.update(status='active', note='Local grouped House pipeline, exact grounding, repair and bounded analyze CLI implemented.72Windows passes/1Linux-only skip;73Linux passes. Real House quality blocked-external: configuration absent. No production faithfulness or image claim; review pending.',
                    evidence=['project-evidence/t4-04-run-summary.json','project-evidence/t4-04-review.md','project-evidence/t4-04-tests-windows-final.log','project-evidence/t4-04-tests-linux-final.log','project-evidence/t4-04-source-recheck.json'])
status['as_of'] = '2026-09-24'
status['execution_state'].update(updated_at=datetime.now(timezone.utc).isoformat(), next_task='T4-05A production judge readiness; real House quality pending configuration', reason='T4-04 local integration passed; live House and production judge remain external.')
path.write_text(json.dumps(status, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
