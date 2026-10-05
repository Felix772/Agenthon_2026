"""Manifest labels bind both retrieval and final citations, even for identical text."""
import json
import time

import pytest

from analysis_agent.contract import ContractError
from analysis_agent.pipeline import analyze, parse_rows
from analysis_agent.retrieval import Document, RetrievalIndex
from test_retrieval import corpus, document
from test_pipeline import setup


def labelled_index(tmp_path, labels):
    folder, manifest = corpus(tmp_path, {
        key: {**document('Revenue increased.'), 'entity_ids': ['forged'], 'shared': True}
        for key in labels})
    value = json.loads(manifest.read_text())
    for entry in value['files']:
        key = entry['path'].removeprefix('corpus/').removesuffix('.json')
        entry.update(labels[key])
    manifest.write_text(json.dumps(value))
    return RetrievalIndex.load(folder, '2024-01-01')


def test_only_trusted_manifest_can_grant_citation_ownership(tmp_path):
    index = labelled_index(tmp_path, {'own': {'entity_ids': ['A']},
        'peer': {'entity_ids': ['B']}, 'market': {'shared': True},
        'empty': {'entity_ids': []}, 'unlabelled': {}, 'null_shared': {'shared': None}})
    assert {h.passage.doc_id for h in index.search('Revenue', entity_id='A')} == {'own', 'market'}
    assert {h.passage.doc_id for h in index.search('Revenue', entity_id='forged')} == {'market'}
    for key in ('peer', 'empty', 'unlabelled', 'null_shared'):
        with pytest.raises(ContractError, match='belong'):
            index.ground_quote(key, 'Revenue increased.', entity_id='A')
    assert index.ground_quote('own', 'Revenue increased.', entity_id='A').span_start == 0


@pytest.mark.parametrize('metadata', [{'entity_ids': 'A'}, {'entity_ids': [1]},
    {'entity_ids': ['A', 'A']}, {'entity_ids': ['']}, {'shared': False},
    {'shared': 1}, {'shared': True, 'entity_ids': ['A']}])
def test_malformed_manifest_ownership_is_not_silently_shared(tmp_path, metadata):
    with pytest.raises(ContractError):
        labelled_index(tmp_path, {'doc': metadata})


def test_model_cannot_cross_cite_another_group_entity():
    task, _ = setup(n=2)
    index = RetrievalIndex({key: Document(key, '2024-01-01', 'synthetic',
        'Revenue increased.', entity_ids=(entity,)) for key, entity in [('own', 'E0'), ('peer', 'E1')]},
        '2024-01-01', [])
    excerpts = {'e0': index.search('Revenue', entity_id='E0')[0].passage,
                'e1': index.search('Revenue', entity_id='E1')[0].passage}
    rows = [{'entity_id': e['entity_id'], 'point_forecast': 1, 'unit': 'USD',
             'interval': {'level': .9, 'lo': 0, 'hi': 2}, 'supported': True,
             'claims': [{'excerpt_id': 'e1', 'quote': 'Revenue increased.',
                         'claim': 'Revenue increased.'}]} for e in task['entities']]
    with pytest.raises(ContractError, match='ownership'):
        parse_rows(json.dumps({'entity_predictions': rows}), task, task['entities'], excerpts, index)


def test_no_eligible_document_is_not_replaced_with_a_peer():
    task, _ = setup(n=1)
    index = RetrievalIndex({'peer': Document('peer', '2024-01-01', 'synthetic',
        'Revenue increased.', entity_ids=('OTHER',))}, '2024-01-01', [])

    class NeverCalled:
        def complete(self, *args, **kwargs):
            pytest.fail('Model must not receive a peer-only evidence set.')

    with pytest.raises(ContractError, match='no relevant'):
        analyze(task, index, NeverCalled(), time.monotonic() + 10)
