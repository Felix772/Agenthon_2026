"""Track 4 structural rehearsal using public inputs and synthetic model replies.

The replies are deliberately arbitrary. Passing these checks says nothing about
forecast quality or production citation entailment.
"""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'analysis-agent'), str(ROOT / 'track4-analysis-public'),
                str(ROOT / 'Agenthon2026-public/common')]

from analysis_agent import ContractError, write_answer
from analysis_agent.pipeline import analyze
from analysis_agent.retrieval import Document, RetrievalIndex
from qfbench2_common.smoke import run_smoke
from qfbench2_track_analysis.alignment import EntityRoster, align_predictions
from qfbench2_track_analysis.scoring import build_smoke_verifier


class StructuralModel:
    """Returns complete grounded rows with intentionally meaningless predictions."""

    def __init__(self):
        self.calls = 0

    def complete(self, messages, deadline):
        self.calls += 1
        assert self.calls <= 25
        request = json.loads(messages[-1]['content'])
        excerpt_id, excerpt = next(iter(request['excerpts'].items()))
        rows = []
        for entity in request['entities']:
            unit = entity.get('unit', entity.get('units',
                   request['target'].get('unit', request['target'].get('units'))))
            row = {'entity_id': entity['entity_id'], 'supported': True,
                   'point_forecast': 0, 'interval': {'level': 0.9, 'lo': -1, 'hi': 1},
                   'claims': [{'excerpt_id': excerpt_id, 'quote': excerpt['text'],
                               'claim': 'Synthetic structural rehearsal only.'}]}
            if unit is not None:
                row['unit'] = unit
            if request['target']['type'] == 'classification':
                row['label'] = request['target']['labels'][0]
            rows.append(row)
        return json.dumps({'entity_predictions': rows}, ensure_ascii=False)


# This LF-preserving snapshot matches the current upstream unit blobs. The Windows
# Git checkout expands LF to CRLF, which invalidates the corpus byte digests.
PUBLIC_UNITS = sorted(unit for unit in (ROOT / '.validation/track4-20260923/units').iterdir()
                      if (unit / 'task.json').is_file())


@pytest.mark.parametrize('unit', PUBLIC_UNITS, ids=lambda path: path.name)
def test_every_public_unit_structural_smoke(unit, tmp_path):
    task = json.loads((unit / 'task.json').read_text(encoding='utf-8'))
    index = RetrievalIndex.load(unit / 'corpus', task['cutoff_date'])
    client = StructuralModel()
    answer = analyze(task, index, client, time.monotonic() + 60)
    assert len(answer['entity_predictions']) == len(task['entities'])
    assert client.calls == (len(task['entities']) + 2) // 3 <= 25
    output = tmp_path / 'answer.json'
    write_answer(task, answer, output)
    assert output.stat().st_size < 64 * 1024 * 1024
    if sys.platform != 'win32':
        # The official manifest verifier refuses Windows without O_NOFOLLOW.
        verdict = run_smoke(unit, tmp_path, build_smoke_verifier)
        assert verdict.admissible
        assert verdict.score is None  # public inputs have no resolved outcomes


def task(entities, *, kind='ranking', prompt='revenue'):
    value = {'task_id': 'synthetic-generalization', 'target': {'type': kind, 'name': 'revenue'},
             'cutoff_date': '2024-01-01', 'prompt': prompt, 'entities': entities}
    if kind == 'classification':
        value['target']['labels'] = ['rise', 'fall']
    return value


def test_long_text_and_ranking_ties_keep_exact_offsets():
    text = 'unrelated ' * 20000 + 'Beacon revenue increased in the synthetic report.'
    index = RetrievalIndex({'old': Document('old', '2024-01-01', 'synthetic', text)},
                           '2024-01-01', [])
    entities = [{'entity_id': f'B{i}', 'name': 'Beacon', 'feature': None} for i in range(4)]
    client = StructuralModel()
    answer = analyze(task(entities, prompt='Beacon revenue'), index, client,
                     time.monotonic() + 60)
    assert client.calls == 2
    assert {row['point_forecast'] for row in answer['entity_predictions']} == {0}
    assert align_predictions(answer, EntityRoster.from_task(task(entities)),
                             target_type='ranking', interval_level=0.9).count == 4
    for row in answer['entity_predictions']:
        for claim in row['claims']:
            assert claim['span_start'] > 100000
            assert 'Beacon revenue' in index.validate_span(
                claim['doc_id'], claim['span_start'], claim['span_end'])


def test_duplicate_roster_and_no_relevance_fail_without_model_calls():
    client = StructuralModel()
    index = RetrievalIndex({'old': Document('old', '2024-01-01', 'synthetic',
                                           'unrelated passage')}, '2024-01-01', [])
    entities = [{'entity_id': 'X', 'name': 'none'}, {'entity_id': 'X', 'name': 'none'}]
    with pytest.raises(ContractError):
        analyze(task(entities, prompt='absent'), index, client, time.monotonic() + 10)
    with pytest.raises(ContractError, match='no relevant'):
        analyze(task(entities[:1], prompt='absent'), index, client, time.monotonic() + 10)
    assert client.calls == 0


def test_mixed_dates_exclude_future_before_prompting(tmp_path):
    corpus = tmp_path / 'corpus'
    corpus.mkdir()
    entries = []
    for name, day in [('old', '2024-01-01'), ('future', '2024-01-02')]:
        payload = json.dumps({'doc_date': day, 'text': 'Beacon revenue evidence.'}).encode()
        (corpus / f'{name}.json').write_bytes(payload)
        entries.append({'path': f'corpus/{name}.json', 'role': 'corpus',
                        'sha256': hashlib.sha256(payload).hexdigest()})
    (tmp_path / 'manifest.json').write_text(json.dumps({'files': entries}))
    index = RetrievalIndex.load(corpus, '2024-01-01')
    assert list(index.documents) == ['old']
    assert ('future', 'post_cutoff') in index.excluded
    answer = analyze(task([{'entity_id': 'B', 'name': 'Beacon'}], prompt='Beacon revenue'),
                     index, StructuralModel(), time.monotonic() + 10)
    assert {claim['doc_id'] for row in answer['entity_predictions']
            for claim in row['claims']} == {'old'}


def test_roster_over_request_capacity_is_rejected_before_model_calls():
    entities = [{'entity_id': f'E{i}', 'name': 'Beacon'} for i in range(76)]
    index = RetrievalIndex({'old': Document('old', '2024-01-01', 'synthetic',
                                           'Beacon revenue evidence.')}, '2024-01-01', [])
    client = StructuralModel()
    with pytest.raises(ContractError, match='request capacity'):
        analyze(task(entities, prompt='Beacon revenue'), index, client,
                time.monotonic() + 10)
    assert client.calls == 0


def test_oversized_house_response_fails_without_a_prediction():
    module_path = ROOT / 'qfbench-agent/agent/model_client.py'
    spec = importlib.util.spec_from_file_location('synthetic_house_transport', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    client = module.ModelClient(endpoint='http://house.invalid', model='fixture', token='fixture')

    class OversizedOpener:
        def open(self, request, timeout):
            return io.BytesIO(b'x' * 2_000_001)

    client.opener = OversizedOpener()
    index = RetrievalIndex({'old': Document('old', '2024-01-01', 'synthetic',
                                           'Beacon revenue evidence.')}, '2024-01-01', [])
    with pytest.raises(module.ModelError, match='2 MB'):
        analyze(task([{'entity_id': 'B', 'name': 'Beacon'}], prompt='Beacon revenue'),
                index, client, time.monotonic() + 10)
    assert client.requests == 1
