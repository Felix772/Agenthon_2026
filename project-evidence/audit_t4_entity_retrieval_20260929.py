"""Read-only algorithm comparison over pinned public Git blobs; no model or outcomes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'analysis-agent'))
from analysis_agent.retrieval import Document, RetrievalIndex, canonical_text

REVISION = 'febb5d2fb4cf8adcb6abc5a450cd5ae44c9a53d4'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    repo = ROOT / 'track4-analysis-public'
    git = ['git', '-C', str(repo)]

    def blob(path):
        return subprocess.check_output(git + ['show', REVISION + ':' + path])

    paths = subprocess.check_output(git + ['ls-tree', '-r', '--name-only', REVISION,
                                          '--', 'units']).decode().splitlines()
    units = sorted({p.split('/')[1] for p in paths if p.endswith('/task.json')})
    report = {'scope': 'Lexical retrieval eligibility; no House, predictions or production judge',
              'revision': REVISION, 'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'retrieval_sha256': hashlib.sha256((ROOT / 'analysis-agent/analysis_agent/retrieval.py').read_bytes()).hexdigest(),
              'units': []}
    for unit in units:
        task = json.loads(blob(f'units/{unit}/task.json'))
        manifest = json.loads(blob(f'units/{unit}/manifest.json'))
        docs = {}
        for entry in manifest['files']:
            if entry.get('role') != 'corpus' or entry['path'] == 'corpus/manifest.json':
                continue
            payload = blob(f"units/{unit}/{entry['path']}")
            assert hashlib.sha256(payload).hexdigest() == entry['sha256']
            doc = json.loads(payload)
            assert doc['doc_date'] <= task['cutoff_date']
            docs[doc['doc_id']] = Document(doc['doc_id'], doc['doc_date'], entry['sha256'],
                canonical_text(doc), entity_ids=tuple(entry['entity_ids']) if 'entity_ids' in entry else None,
                shared=entry.get('shared') is True)
        index = RetrievalIndex(docs, task['cutoff_date'], [])
        result = {'unit': unit, 'entities': len(task['entities']), 'baseline_hits': 0,
                  'baseline_wrong_entity': 0, 'candidate_hits': 0, 'candidate_wrong_entity': 0,
                  'baseline_no_eligible_top3': [], 'candidate_no_eligible_top3': [],
                  'baseline_eligible_hit_lost': 0}
        for entity in task['entities']:
            eid = entity['entity_id']
            query = ' '.join(str(entity.get(k, '')) for k in ('entity_id', 'name'))
            query += ' ' + task.get('prompt', '') + ' ' + task['target'].get('name', '')
            baseline = index.search(query, top_k=3)
            candidate = index.search(query, top_k=3, entity_id=eid)
            eligible = lambda hit: docs[hit.passage.doc_id].admits(eid)
            identity = lambda hit: (hit.passage.doc_id, hit.passage.span_start, hit.passage.span_end)
            for name, hits in [('baseline', baseline), ('candidate', candidate)]:
                result[name + '_hits'] += len(hits)
                result[name + '_wrong_entity'] += sum(not eligible(hit) for hit in hits)
                if not any(eligible(hit) for hit in hits):
                    result[name + '_no_eligible_top3'].append(eid)
            old = {identity(hit) for hit in baseline if eligible(hit)}
            new = {identity(hit) for hit in candidate}
            result['baseline_eligible_hit_lost'] += len(old - new)
        report['units'].append(result)
    count_keys = ['entities', 'baseline_hits', 'baseline_wrong_entity', 'candidate_hits',
                  'candidate_wrong_entity', 'baseline_eligible_hit_lost']
    report['totals'] = {key: sum(row[key] for row in report['units']) for key in count_keys}
    report['passed'] = (report['totals']['baseline_wrong_entity'] == 61
                        and report['totals']['baseline_hits'] == 234
                        and report['totals']['candidate_wrong_entity'] == 0
                        and report['totals']['baseline_eligible_hit_lost'] == 0
                        and not any(row['candidate_no_eligible_top3'] for row in report['units']))
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], **report['totals']}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
