"""Strict participant output assembly against the mounted task's complete roster."""
from copy import deepcopy
import importlib.resources
import json
import math
import os
from pathlib import Path
import tempfile
import unicodedata

from jsonschema import Draft202012Validator


class ContractError(ValueError):
    """Invalid task or prediction; never silently omit an entity."""


def _require(condition, message):
    if not condition:
        raise ContractError(message)


def _finite_tree(value):
    if isinstance(value, float):
        _require(math.isfinite(value), 'nonfinite number')
    elif isinstance(value, dict):
        for item in value.values():
            _finite_tree(item)
    elif isinstance(value, list):
        for item in value:
            _finite_tree(item)


def _number(value, name):
    _require(type(value) in (int, float), f'{name} must be a number, not bool/string')
    if isinstance(value, float):
        _require(math.isfinite(value), f'{name} must be finite')


def _unit(mapping):
    values = [mapping[k] for k in ('unit', 'units') if k in mapping]
    for value in values:
        _require(isinstance(value, str) and bool(value.strip()), 'invalid unit declaration')
    _require(len(set(values)) <= 1, 'conflicting unit/units declarations')
    return values[0] if values else None


def _task_contract(task):
    _require(isinstance(task, dict), 'task must be an object')
    _require(isinstance(task.get('task_id'), str) and bool(task['task_id']), 'missing task_id')
    target = task.get('target')
    _require(isinstance(target, dict), 'missing target object')
    kind = target.get('type')
    _require(kind in ('classification', 'regression', 'ranking'), 'unsupported target type')
    _require(task.get('target_type', kind) == kind, 'conflicting task target types')
    _require(task.get('interval_level', 0.9) == 0.9, 'interval level must be 0.9')
    entities = task.get('entities')
    _require(isinstance(entities, list) and bool(entities), 'empty entity roster')
    ids, units = [], {}
    for entity in entities:
        _require(isinstance(entity, dict), 'entity must be an object')
        eid = entity.get('entity_id')
        _require(isinstance(eid, str) and bool(eid), 'invalid entity_id')
        _require(unicodedata.normalize('NFC', eid) == eid, 'entity_id must be NFC')
        _require(eid not in units, 'duplicate task entity')
        ids.append(eid)
        units[eid] = _unit(entity) or _unit(target)
    labels = target.get('labels')
    if labels is not None:
        _require(kind == 'classification', 'labels belong only to classification')
        _require(isinstance(labels, list) and len(labels) >= 2, 'invalid label vocabulary')
        _require(all(isinstance(x, str) and x and x == x.strip() for x in labels), 'invalid label')
        _require(len(set(labels)) == len(labels), 'duplicate label vocabulary')
    return kind, ids, units, labels


def task_table(task):
    """The text a `doc_id: "task"` citation indexes, and each entity row's [start, end).

    Mirrors qfbench2_track_analysis.corpus.task_table_text (scorer 5.2.x): one line per
    `entities` row in task.json order, `json.dumps(row, ensure_ascii=False,
    separators=(", ", ": "))`, joined by a single newline."""
    lines, ranges, offset = [], {}, 0
    for row in task['entities']:
        line = json.dumps(row, ensure_ascii=False, separators=(', ', ': '))
        ranges[row['entity_id']] = (offset, offset + len(line))
        lines.append(line)
        offset += len(line) + 1
    return '\n'.join(lines), ranges


def validate_answer(task, answer):
    """Validate structure and task binding, not citation truth or prediction quality."""
    kind, ids, _, labels = _task_contract(task)
    _finite_tree(answer)
    schema = json.loads((importlib.resources.files('qfbench2_common') /
                         'schemas/analysis.schema.json').read_text(encoding='utf-8'))
    errors = list(Draft202012Validator(schema).iter_errors(answer))
    _require(not errors, 'answer violates official analysis schema')
    _require(answer['task_id'] == task['task_id'], 'task_id mismatch')
    _require(answer.get('target_type') == kind, 'target_type mismatch or missing')
    rows = answer['entity_predictions']
    row_ids = [r['entity_id'] for r in rows]
    _require(len(row_ids) == len(set(row_ids)), 'duplicate prediction entity')
    _require(set(row_ids) == set(ids), 'prediction roster differs from task')
    ranks = []
    for row in rows:
        if kind == 'classification':
            _require(isinstance(row.get('label'), str) and bool(row['label']), 'missing class label')
            _require(labels is None or row['label'] in labels, 'label outside vocabulary')
        if kind != 'classification':
            _require('point_forecast' in row, 'missing point_forecast')
        if 'point_forecast' in row:
            _number(row['point_forecast'], 'point_forecast')
        interval = row['interval']
        for key in ('level', 'lo', 'hi'):
            _number(interval[key], 'interval.' + key)
        _require(interval['lo'] <= interval['hi'], 'reversed interval')
        for claim in row['claims']:
            _require(bool(claim['doc_id'].strip()) and bool(claim['claim'].strip()), 'empty citation')
            _require(claim['span_end'] > claim['span_start'], 'empty or reversed citation span')
        if 'rank' in row:
            _require(type(row['rank']) is int, 'rank must be an integer')
            ranks.append(row['rank'])
    if ranks:
        _require(sorted(ranks) == list(range(1, len(ids)+1)), 'rank must be complete permutation')
    return answer


def build_answer(task, predictions, *, evidence_trace=''):
    """Accept internal rows with explicit numeric units, emit official fields in roster order.

    Internal `unit`/`units` acknowledges the task unit for the point and both bounds.
    There is no implicit percent/bps/currency conversion or inference from prose.
    If the task supplies no structured unit, no unit is invented or certified.
    """
    kind, ids, units, _ = _task_contract(task)
    rows = deepcopy(list(predictions))
    _require(all(isinstance(r, dict) for r in rows), 'prediction must be an object')
    by_id = {}
    for row in rows:
        eid = row.get('entity_id')
        _require(isinstance(eid, str) and eid in units, 'unknown prediction entity')
        _require(eid not in by_id, 'duplicate prediction entity')
        supplied = _unit(row)
        _require(units[eid] is None or supplied == units[eid], 'prediction unit mismatch or missing')
        row.pop('unit', None)
        row.pop('units', None)
        by_id[eid] = row
    _require(set(by_id) == set(ids), 'missing prediction entity')
    answer = {'task_id': task['task_id'], 'schema_version': '3', 'target_type': kind,
              'entity_predictions': [by_id[eid] for eid in ids], 'evidence_trace': evidence_trace}
    return validate_answer(task, answer)


def write_answer(task, answer, path):
    """Validate before touching the destination, then publish complete JSON atomically."""
    validate_answer(task, answer)
    data = json.dumps(answer, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.answer-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
