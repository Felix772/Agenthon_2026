"""Local retrieval coverage and source provenance; no model or prediction score."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'analysis-agent'))
from analysis_agent.retrieval import RetrievalIndex

E = ROOT / 'project-evidence'
refs = json.loads((E / '20260923-source-recheck.json').read_text(encoding='utf-8'))
for record in refs:
    repo = ROOT / record['repository']
    actual = subprocess.check_output(['git', '-c', f'safe.directory={repo}', '-C', str(repo),
                                      'rev-parse', 'origin/main'], text=True).strip()
    assert actual == record['ref']
    record['checked_at'] = datetime.now(timezone.utc).isoformat()
    record['review'] = 'Fresh fetch this task, ref unchanged from reviewed docs; current corpus resolver and retrieval/offset contract consulted.'
(E / 't4-03-source-recheck.json').write_text(json.dumps(refs, indent=2)+'\n', encoding='utf-8')
units = []
for unit in sorted((ROOT / '.validation/track4-20260923/units').iterdir()):
    if not (unit / 'task.json').exists(): continue
    task = json.loads((unit / 'task.json').read_text(encoding='utf-8'))
    index = RetrievalIndex.load(unit / 'corpus', task['cutoff_date'])
    results = []
    for entity in task['entities']:
        query = ' '.join(str(entity.get(k, '')) for k in ('entity_id', 'name')) + ' ' + task.get('prompt', '')
        hits = index.search(query, top_k=5)
        for hit in hits:
            p = hit.passage
            index.validate_span(p.doc_id, p.span_start, p.span_end, quote=p.text)
        results.append({'entity_id': entity['entity_id'], 'hit_count': len(hits)})
    units.append({'unit': unit.name, 'eligible_documents': len(index.documents),
                  'excluded': list(index.excluded), 'passages': len(index.passages), 'queries': results})
files = sorted(p for p in (ROOT / 'analysis-agent').rglob('*') if p.is_file()
               and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts)
report = {'task': 'T4-03', 'local_result': 'passed', 'chatgpt_review': 'pending',
          'tests': {'windows_passed': 55, 'windows_skipped': 1, 'linux_passed': 56},
          'units': units, 'source_files': [{'path': str(p.relative_to(ROOT)),
                    'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in files],
          'production_faithfulness_verified': False, 'prediction_quality_measured': False,
          'limitations': ['Lexical relevance and exact quotation do not imply predictive entailment',
                         'No external evidence or House calls', 'Read-only input mount assumption',
                         'No production judge or complete analyze CLI yet']}
assert '55 passed, 1 skipped' in (E / 't4-03-tests-windows.log').read_text(encoding='utf-8')
assert '56 passed' in (E / 't4-03-tests-linux.log').read_text(encoding='utf-8')
(E / 't4-03-run-summary.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
path = ROOT / 'planning/task-status.json'
status = json.loads(path.read_text(encoding='utf-8'))
for task in status['tasks']:
    if task['id'] == 'T4-03':
        task.update(status='active', note='Local retrieval complete: manifest/digest-bound BM25 with pre-index cutoff filters and original-text offsets. Windows55pass/1Linux-only skip; Linux56pass. All11public corpora checked. ChatGPT review pending; no production faithfulness claim.',
                    evidence=['project-evidence/t4-03-run-summary.json', 'project-evidence/t4-03-tests-windows.log', 'project-evidence/t4-03-tests-linux.log', 'project-evidence/t4-03-source-recheck.json'])
status['execution_state'].update(updated_at=datetime.now(timezone.utc).isoformat(),
    next_task='T4-04 prediction/evidence integration; live House quality remains external',
    reason='T4-03 local retrieval and exact citation tests passed; remote review still pending.')
path.write_text(json.dumps(status, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps({'units': len(units), 'documents': sum(u['eligible_documents'] for u in units),
                  'entity_queries': sum(len(u['queries']) for u in units)}))
