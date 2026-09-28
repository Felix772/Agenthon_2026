from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'analysis-agent'))
sys.path.insert(0, str(ROOT / '.validation/track4-20260923'))
from analysis_agent import ContractError, build_answer, validate_answer, write_answer
from qfbench2_track_analysis.alignment import EntityRoster, align_predictions


def fixture(kind='regression'):
    task = {'task_id': 'synthetic-unit', 'target': {'type': kind},
            'interval_level': 0.9, 'entities': [{'entity_id': 'A', 'unit': 'bps'},
                                             {'entity_id': 'B', 'unit': 'bps'}]}
    if kind == 'classification':
        task['target']['labels'] = ['up', 'down']
    rows = [{'entity_id': eid, 'unit': 'bps', 'point_forecast': 2.5,
             'interval': {'level': 0.9, 'lo': -1, 'hi': 5},
             'claims': [{'doc_id': 'synthetic-document', 'span_start': 0, 'span_end': 12,
                         'claim': 'Synthetic contract fixture only.'}]}
            for eid in ('B', 'A')]
    if kind == 'classification':
        for row in rows:
            row['label'] = 'up'
    if kind == 'ranking':
        for rank, row in enumerate(rows, 1):
            row['rank'] = rank
    return task, rows


@pytest.mark.parametrize('kind', ['classification', 'regression', 'ranking'])
def test_roundtrip_and_official_alignment(kind, tmp_path):
    task, rows = fixture(kind)
    original = deepcopy(rows)
    answer = build_answer(task, rows)
    assert rows == original
    assert [r['entity_id'] for r in answer['entity_predictions']] == ['A', 'B']
    assert all('unit' not in r for r in answer['entity_predictions'])
    aligned = align_predictions(answer, EntityRoster.from_task(task), target_type=kind, interval_level=0.9)
    assert aligned.count == 2
    path = tmp_path / 'answer.json'
    write_answer(task, answer, path)
    assert json.loads(path.read_text(encoding='utf-8')) == answer
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('bad', ['missing', 'duplicate', 'extra', 'unit', 'missing_unit',
                                'reverse', 'level', 'no_claim', 'span', 'no_point', 'bool',
                                'string', 'nan', 'inf', 'nested_nan'])
def test_rejects_bad_predictions(bad):
    task, rows = fixture()
    if bad == 'missing': rows.pop()
    elif bad == 'duplicate': rows.append(deepcopy(rows[0]))
    elif bad == 'extra': rows[0]['entity_id'] = 'EXTRA'
    elif bad == 'unit': rows[0]['unit'] = 'percent'
    elif bad == 'missing_unit': rows[0].pop('unit')
    elif bad == 'reverse': rows[0]['interval']['lo'] = 6
    elif bad == 'level': rows[0]['interval']['level'] = 0.95
    elif bad == 'no_claim': rows[0]['claims'] = []
    elif bad == 'span': rows[0]['claims'][0]['span_end'] = 0
    elif bad == 'no_point': rows[0].pop('point_forecast')
    elif bad == 'bool': rows[0]['point_forecast'] = True
    elif bad == 'string': rows[0]['point_forecast'] = '2.5'
    elif bad == 'nan': rows[0]['point_forecast'] = float('nan')
    elif bad == 'inf': rows[0]['interval']['hi'] = float('inf')
    elif bad == 'nested_nan': rows[0]['extra'] = {'nested': [float('nan')]}
    with pytest.raises(ContractError): build_answer(task, rows)


@pytest.mark.parametrize('bad', ['kind', 'conflict', 'duplicate_id', 'nfc', 'level', 'labels'])
def test_rejects_bad_tasks(bad):
    task, rows = fixture('classification')
    if bad == 'kind': task['target']['type'] = 'unknown'
    elif bad == 'conflict': task['target_type'] = 'ranking'
    elif bad == 'duplicate_id': task['entities'][1]['entity_id'] = 'A'
    elif bad == 'nfc': task['entities'][0]['entity_id'] = 'e\u0301'
    elif bad == 'level': task['interval_level'] = 0.8
    elif bad == 'labels': task['target']['labels'] = ['up', 'up']
    with pytest.raises(ContractError): build_answer(task, rows)


@pytest.mark.parametrize('ranks', [(1, 1), (1, None), (0, 1), (True, 2), (1.5, 2)])
def test_invalid_ranks(ranks):
    task, rows = fixture('ranking')
    for row, rank in zip(rows, ranks):
        if rank is None: row.pop('rank')
        else: row['rank'] = rank
    with pytest.raises(ContractError): build_answer(task, rows)


def test_unknown_label_and_missing_label():
    task, rows = fixture('classification')
    for value in ('unknown', ''):
        rows[0]['label'] = value
        with pytest.raises(ContractError): build_answer(task, rows)


def test_optional_class_point_and_optional_ranks():
    for kind in ('classification', 'ranking'):
        task, rows = fixture(kind)
        for row in rows:
            row.pop('point_forecast' if kind == 'classification' else 'rank')
        answer = build_answer(task, rows)
        assert align_predictions(answer, EntityRoster.from_task(task), target_type=kind, interval_level=0.9).count == 2


def test_unit_fallback_override_and_no_invention():
    task, rows = fixture()
    task['target']['unit'] = 'USD'
    task['entities'][0].pop('unit')
    rows[1]['unit'] = 'USD'
    build_answer(task, rows)
    task['target'].pop('unit')
    task['entities'][1].pop('unit')
    for row in rows: row.pop('unit')
    build_answer(task, rows)


@pytest.mark.parametrize('field,value', [('task_id', 'wrong'), ('target_type', 'ranking')])
def test_bad_binding_preserves_output(field, value, tmp_path):
    task, rows = fixture()
    answer = build_answer(task, rows)
    path = tmp_path / 'answer.json'
    path.write_text('original')
    answer[field] = value
    with pytest.raises(ContractError): write_answer(task, answer, path)
    assert path.read_text() == 'original'


def test_all_public_task_shapes_without_outcomes():
    paths = sorted((ROOT / '.validation/track4-20260923/units').glob('*/task.json'))
    assert paths
    for path in paths:
        task = json.loads(path.read_text(encoding='utf-8'))
        kind = task['target']['type']
        rows = []
        for entity in task['entities']:
            row = deepcopy(fixture()[1][0])
            row['entity_id'] = entity['entity_id']
            unit = entity.get('unit', entity.get('units', task['target'].get('unit', task['target'].get('units'))))
            if unit is None: row.pop('unit')
            else: row['unit'] = unit
            if kind == 'classification': row['label'] = task['target']['labels'][0]
            rows.append(row)
        answer = build_answer(task, rows)
        assert align_predictions(answer, EntityRoster.from_task(task), target_type=kind, interval_level=0.9).count == len(rows)
