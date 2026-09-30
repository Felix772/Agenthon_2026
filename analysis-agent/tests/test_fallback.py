"""Lenient (default) mode: a failed group or a missing model never refuses the unit."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'analysis-agent'), str(ROOT / 'track4-analysis-public')]
from analysis_agent.contract import task_table, validate_answer
from analysis_agent.pipeline import analyze, fallback_answer
from analysis_agent.retrieval import Document, RetrievalIndex
from qfbench2_track_analysis.corpus import task_table_text
from test_pipeline import FakeClient, setup

PUBLIC_UNITS = sorted(unit for unit in (ROOT / '.validation/track4-20260923/units').iterdir()
                      if (unit / 'task.json').is_file())


def with_features(task):
    for i, entity in enumerate(task['entities']):
        entity['prior_value'] = 10 + i
    return task


class FailingSecondGroup(FakeClient):
    def complete(self, messages, deadline):
        request = json.loads(messages[-1]['content'])
        if 'predictions' in request:
            self.calls += 1
            return 'not json'
        if any(e['entity_id'] == 'E3' for e in request['entities']):
            self.calls += 1
            return 'not json'
        return super().complete(messages, deadline)


def test_failed_group_gets_last_resort_rows_not_a_refusal():
    task, index = setup(n=4)
    with_features(task)
    client = FailingSecondGroup()
    answer = analyze(task, index, client, time.monotonic() + 60)
    rows = {r['entity_id']: r for r in answer['entity_predictions']}
    assert list(rows) == ['E0', 'E1', 'E2', 'E3']
    assert rows['E3']['point_forecast'] == 1.0  # median of the model-made rows
    assert 'submitted_reasons' not in answer  # invalid reasons are omitted, never emitted
    assert '1 entities used the last-resort rule' in answer['evidence_trace']
    validate_answer(task, answer)


def test_partial_group_repairs_only_missing_entities():
    task, index = setup(n=3)
    client = FakeClient('missing')
    answer = analyze(task, index, client, time.monotonic() + 60, reasons=False)
    assert client.calls == 2
    assert len(answer['entity_predictions']) == 3


def test_claims_are_verbatim_and_task_rows_are_cited():
    task, index = setup(n=2)
    with_features(task)
    answer = analyze(task, index, FakeClient(), time.monotonic() + 60, reasons=False)
    text, ranges = task_table(task)
    assert (text, ranges) == task_table_text(task)  # mirrors the official definition
    for row in answer['entity_predictions']:
        corpus = [c for c in row['claims'] if c['doc_id'] != 'task']
        table = [c for c in row['claims'] if c['doc_id'] == 'task']
        for claim in corpus:
            quoted = index.validate_span(claim['doc_id'], claim['span_start'], claim['span_end'])
            assert claim['claim'] == 'Revenue entity: ' + quoted
        (claim,) = table
        start, end = ranges[row['entity_id']]
        assert start <= claim['span_start'] < claim['span_end'] <= end
        assert claim['claim'].endswith(text[claim['span_start']:claim['span_end']])


def test_documents_not_labelled_for_the_entity_are_never_offered_or_cited():
    docs = {'mine': Document('mine', '2024-01-01', 's', 'Revenue for A rose 5% in the year.', ('A',)),
            'other': Document('other', '2024-01-01', 's', 'Revenue for B fell 9% in the year.', ('B',))}
    index = RetrievalIndex(docs, '2024-01-01', [])
    assert index.labelled
    assert {h.passage.doc_id for h in index.search('revenue', entity_id='A')} == {'mine'}
    assert not index.citable_for('other', 'A')


@pytest.mark.parametrize('unit', PUBLIC_UNITS, ids=lambda path: path.name)
def test_model_free_fallback_is_admissible_with_no_false_claims(unit):
    task = json.loads((unit / 'task.json').read_text(encoding='utf-8'))
    index = RetrievalIndex.load(unit / 'corpus', task['cutoff_date'])
    answer = fallback_answer(task, index)
    validate_answer(task, answer)
    from baselines.guardrails_example.citation_rail import check_claim_rules
    findings = [f for f in check_claim_rules(answer, unit, token_counter=None)
                if f.code != 'claim_tokens_unchecked']
    assert findings == []


def test_cli_without_model_writes_admissible_fallback(tmp_path):
    unit = PUBLIC_UNITS[0]
    output = tmp_path / 'answer.json'
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'analysis-agent'))
    for key in ('MODEL_ENDPOINT', 'MODEL_NAME', 'MODEL_TOKEN'):
        env.pop(key, None)
    result = subprocess.run([sys.executable, '-m', 'analysis_agent.cli', 'analyze',
        '--task', str(unit / 'task.json'), '--corpus', str(unit / 'corpus'), '--out', str(output),
        '--timeout', '60'], env=env, capture_output=True, timeout=90)
    assert result.returncode == 0
    task = json.loads((unit / 'task.json').read_text(encoding='utf-8'))
    validate_answer(task, json.loads(output.read_text(encoding='utf-8')))
