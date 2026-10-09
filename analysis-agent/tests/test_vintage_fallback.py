"""Revision-level fallback keeps the supplied scale without inferring release stages."""
import json
import math
import time

import pytest

from conftest import T4_UNITS
from analysis_agent.fallback import emergency_rows
from analysis_agent.pipeline import analyze
from analysis_agent.retrieval import Document, RetrievalIndex


def fixture(*, anchor=1000.0, direction=1, prefix='', reorder=False):
    entity = {'entity_id': 'unseen-row', 'series_id': 'SERIES_X',
              'series_name': 'Synthetic revision series', 'units': 'thousands of units, SA',
              'ref_month': '2024-07', 'latest_precutoff_estimate': anchor,
              'latest_precutoff_vintage': '2024-09-06', 'resolving_release_date': '2024-10-04'}
    task = {'task_id': 'unseen-task', 'target': {'name': 'next_estimate_revision_direction',
            'type': 'classification', 'labels': ['up', 'down']}, 'cutoff_date': '2024-09-30',
            'prompt': 'Give a point forecast of the revised value in the original units '
                      'and a 90% interval. Also predict its revision direction.',
            'entities': [entity]}
    header = ['reference_month', 'as_of_2024-07-05', 'as_of_2024-08-02', 'as_of_2024-09-06']
    records = [['2024-05', '100', str(100+direction*4), str(100+direction*7)],
               ['2024-06', '--', '200', str(200+direction*6)],
               ['2024-07', '--', '--', '1000']]
    if reorder:
        order = [2, 0, 3, 1]
        header = [header[i] for i in order]
        records = [[record[i] for i in order] for record in reversed(records)]
    text = prefix + ('Series SERIES_X. Units: thousands of units, seasonally adjusted. '
                     'The latest published estimate describes the supplied reference month.\n')
    text += '\n'.join(' | '.join(row) for row in [header, *records])
    doc = Document('frozen-series', '2024-09-06', 'synthetic-only', text,
                   entity_ids=(entity['entity_id'],))
    return task, RetrievalIndex({doc.doc_id: doc}, task['cutoff_date'], [])


def row_for(task, index):
    return emergency_rows(task, index)[task['entities'][0]['entity_id']]


def test_revision_level_uses_latest_input_estimate_in_original_units():
    task, index = fixture()
    row = row_for(task, index)
    assert row['point_forecast'] == 1000.0
    assert row['unit'] == 'thousands of units, SA'
    assert row['interval'] == {'level': .9, 'lo': 500.0, 'hi': 1500.0}
    assert row['label'] == 'up'  # Existing default; no claim of historical direction skill.


@pytest.mark.parametrize('anchor', [-100.0, 0.0, .1, 1e100])
def test_revision_anchor_band_handles_negative_zero_and_large_finite_values(anchor):
    task, index = fixture(anchor=anchor)
    row = row_for(task, index)
    assert row['point_forecast'] == anchor
    assert all(math.isfinite(row['interval'][key]) for key in ('lo', 'hi'))
    assert row['interval']['lo'] <= anchor <= row['interval']['hi']
    assert row['interval']['hi'] > row['interval']['lo']


@pytest.mark.parametrize('anchor', [10**400, 1.7e308, math.inf, -math.inf, math.nan, True, '1000'])
def test_invalid_or_unbounded_anchor_keeps_previous_generic_fallback(anchor):
    task, index = fixture(anchor=anchor)
    row = row_for(task, index)
    assert 'point_forecast' not in row
    assert row['interval'] == {'level': .9, 'lo': 0.0, 'hi': 1.0}


@pytest.mark.parametrize('field,value', [
    ('series_id', ''), ('ref_month', '2024-13'), ('ref_month', '2024-10'),
    ('latest_precutoff_vintage', 'invalid'), ('latest_precutoff_vintage', '2024-10-01'),
    ('resolving_release_date', '2024-09-30'), ('resolving_release_date', 'invalid'),
])
def test_ambiguous_or_inconsistent_revision_fields_keep_previous_path(field, value):
    task, index = fixture()
    task['entities'][0][field] = value
    row = row_for(task, index)
    assert 'point_forecast' not in row
    assert row['interval'] == {'level': .9, 'lo': 0.0, 'hi': 1.0}


@pytest.mark.parametrize('field', ['series_id', 'ref_month', 'latest_precutoff_vintage',
                                   'resolving_release_date', 'latest_precutoff_estimate', 'units'])
def test_missing_revision_contract_does_not_invent_scale(field):
    task, index = fixture()
    task['entities'][0].pop(field)
    row = row_for(task, index)
    assert 'point_forecast' not in row


def test_target_unit_alias_is_the_same_scale():
    task, index = fixture()
    task['target']['unit'] = task['entities'][0].pop('units')
    assert row_for(task, index)['point_forecast'] == 1000.0


def test_different_classification_target_does_not_activate_revision_anchor():
    task, index = fixture()
    task['target']['name'] = 'earnings_direction'
    assert 'point_forecast' not in row_for(task, index)


@pytest.mark.parametrize('prompt', [
    'Predict the revision direction and give a 90% interval.',
    'Give a point forecast of the change in value, relative to the latest estimate.',
    'Give a point forecast of the revised value minus the latest estimate, and a 90% interval.',
    'Give a point forecast of the revised value as a percentage change and a 90% interval.',
    None,
])
def test_delta_or_ambiguous_numeric_request_does_not_use_a_level_anchor(prompt):
    task, index = fixture()
    task['prompt'] = prompt
    row = row_for(task, index)
    assert 'point_forecast' not in row
    assert row['interval'] == {'level': .9, 'lo': 0.0, 'hi': 1.0}


@pytest.mark.parametrize('direction', [-1, 1])
@pytest.mark.parametrize('reorder', [False, True])
def test_sparse_positive_negative_and_reordered_histories_do_not_imply_a_stage(direction, reorder):
    task, index = fixture(direction=direction, reorder=reorder,
                          prefix='Unrelated preamble with 2024 dates.\n'*45)
    row = row_for(task, index)
    assert row['point_forecast'] == 1000.0
    assert row['label'] == 'up'
    assert row['interval'] == {'level': .9, 'lo': 500.0, 'hi': 1500.0}
    for claim in row['claims']:
        assert index.validate_span(claim['doc_id'], claim['span_start'], claim['span_end'],
                                   entity_id=row['entity_id']) == claim['claim']


def test_missing_history_still_has_explicit_input_scale():
    task, index = fixture()
    doc = index.documents['frozen-series']
    text = 'Series SERIES_X has a published estimate. No comparable revision history is supplied.'
    doc = Document(doc.doc_id, doc.doc_date, doc.sha256, text, doc.entity_ids)
    index = RetrievalIndex({doc.doc_id: doc}, task['cutoff_date'], [])
    assert row_for(task, index)['point_forecast'] == 1000.0


def test_postcutoff_table_content_cannot_change_anchor_prediction():
    task, index = fixture()
    baseline = row_for(task, index)
    doc = index.documents['frozen-series']
    changed = Document(doc.doc_id, doc.doc_date, doc.sha256,
        doc.text + '\nFuture column: as_of_2024-10-04 | 999999999999999999999999999\n', doc.entity_ids)
    # A poisoned future column is never parsed as an observed revision by this candidate.
    changed_index = RetrievalIndex({changed.doc_id: changed}, task['cutoff_date'], [])
    candidate = row_for(task, changed_index)
    assert {k: candidate[k] for k in ('label', 'point_forecast', 'interval')} == {
        k: baseline[k] for k in ('label', 'point_forecast', 'interval')}


def test_all_public_revision_rows_use_their_declared_level_scale():
    unit = T4_UNITS / 't4-macrorev-20240930-us6'
    task = json.loads((unit/'task.json').read_text(encoding='utf-8'))
    index = RetrievalIndex.load(unit/'corpus', task['cutoff_date'])
    rows = emergency_rows(task, index)
    assert len(rows) == len(task['entities']) == 12
    for entity in task['entities']:
        row = rows[entity['entity_id']]
        anchor = entity['latest_precutoff_estimate']
        assert row['point_forecast'] == anchor
        assert row['unit'] == entity['units']
        assert row['interval']['lo'] <= anchor <= row['interval']['hi']


def test_complete_house_answer_replaces_revision_anchor_and_checkpoint_survives():
    task, index = fixture()
    snapshots = []

    class House:
        def complete(self, messages, deadline, *, max_sends=1):
            assert snapshots and snapshots[-1]['entity_predictions'][0]['point_forecast'] == 1000.0
            request = json.loads(messages[-1]['content'])
            key = next(iter(request['excerpts']))
            passage = request['excerpts'][key]['text']
            quote = passage[:100]
            return json.dumps({'entity_predictions': [{
                'entity_id': task['entities'][0]['entity_id'], 'unit': task['entities'][0]['units'],
                'label': 'down', 'point_forecast': 997, 'interval': {'level': .9, 'lo': 990, 'hi': 1001},
                'claims': [{'excerpt_id': key, 'quote': quote}]}]})

    answer = analyze(task, index, House(), time.monotonic()+10,
                     checkpoint=snapshots.append, allow_emergency=True)
    assert answer['entity_predictions'][0]['point_forecast'] == 997
    assert answer['entity_predictions'][0]['label'] == 'down'
    assert snapshots[-1] == answer
