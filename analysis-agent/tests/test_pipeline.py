import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'analysis-agent'))
from analysis_agent import ContractError
from analysis_agent.pipeline import analyze
from analysis_agent.retrieval import Document, RetrievalIndex
from analysis_agent.cli import house_client


def setup(kind='regression', n=4):
    task = {'task_id': 'synthetic', 'target': {'type': kind, 'name': 'revenue', 'unit': 'USD'},
            'cutoff_date': '2024-01-01', 'prompt': 'Forecast revenue',
            'entities': [{'entity_id': f'E{i}', 'name': 'Revenue entity'} for i in range(n)]}
    if kind == 'classification': task['target']['labels'] = ['up', 'down']
    doc = Document('doc', '2024-01-01', 'synthetic-only', 'Revenue increased in the supplied synthetic example.')
    return task, RetrievalIndex({'doc': doc}, '2024-01-01', [])


class FakeClient:
    def __init__(self, fault=None, permanent=False):
        self.calls = 0
        self.fault = fault
        self.permanent = permanent

    def complete(self, messages, deadline):
        self.calls += 1
        assert self.calls <= 25
        request = json.loads(messages[-1]['content'])
        key = next(iter(request['excerpts']))
        quote = request['excerpts'][key]['text']
        rows = [{'entity_id': e['entity_id'], 'supported': True, 'point_forecast': 1,
                 'unit': 'USD', 'interval': {'level': 0.9, 'lo': 0, 'hi': 2},
                 'claims': [{'excerpt_id': key, 'quote': quote, 'claim': 'Synthetic support, not real prediction.'}]}
                for e in request['entities']]
        if request['target']['type'] == 'classification':
            for row in rows: row['label'] = 'up'
        if self.calls == 1 or self.permanent:
            if self.fault == 'json': return 'not json'
            if self.fault == 'missing': rows.pop()
            if self.fault == 'quote': rows[0]['claims'][0]['quote'] = 'invented quote'
            if self.fault == 'excerpt': rows[0]['claims'][0]['excerpt_id'] = 'unknown'
            if self.fault == 'unit': rows[0]['unit'] = 'bps'
            if self.fault == 'unsupported': rows[0]['supported'] = False
            if self.fault == 'nan': rows[0]['point_forecast'] = float('nan')
        return json.dumps({'entity_predictions': rows})


@pytest.mark.parametrize('kind', ['classification', 'regression', 'ranking'])
def test_all_types_multigroup(kind):
    task, index = setup(kind, 30)
    client = FakeClient()
    answer = analyze(task, index, client, time.monotonic()+30, strict=True)
    assert client.calls == 10
    assert len(answer['entity_predictions']) == 30
    assert answer['target_type'] == kind
    for row in answer['entity_predictions']:
        for claim in row['claims']:
            assert index.validate_span(claim['doc_id'], claim['span_start'], claim['span_end'])


@pytest.mark.parametrize('fault', ['json', 'missing', 'quote', 'excerpt', 'unit', 'unsupported', 'nan'])
def test_repair_retrieves_and_revalidates(fault):
    task, index = setup(n=2)
    client = FakeClient(fault)
    assert len(analyze(task, index, client, time.monotonic()+30, strict=True)['entity_predictions']) == 2
    assert client.calls == 2


@pytest.mark.parametrize('fault', ['missing', 'quote', 'unsupported'])
def test_permanent_error_never_yields_partial_answer(fault):
    task, index = setup()
    client = FakeClient(fault, permanent=True)
    with pytest.raises(ContractError): analyze(task, index, client, time.monotonic()+30, strict=True)
    assert client.calls == 2


def test_no_evidence_and_deadline_do_not_call_model():
    task, index = setup()
    client = FakeClient()
    with pytest.raises(ContractError): analyze(task, index, client, time.monotonic()-1, strict=True)
    with pytest.raises(ContractError): analyze(task, RetrievalIndex({}, task['cutoff_date'], []), client, time.monotonic()+30, strict=True)
    assert client.calls == 0


def test_shared_client_requires_real_configuration(monkeypatch):
    for key in ('MODEL_ENDPOINT', 'MODEL_NAME', 'MODEL_TOKEN'): monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError): house_client()


def test_mismatched_cutoff_rejected_before_model():
    task, index = setup()
    task['cutoff_date'] = '2023-12-31'
    client = FakeClient()
    with pytest.raises(ContractError): analyze(task, index, client, time.monotonic()+30)
    assert client.calls == 0


def test_cli_unavailable_is_explicit_nonadmissible_output(tmp_path):
    task, _ = setup()
    task_path = tmp_path / 'task.json'
    task_path.write_text(json.dumps(task))
    output = tmp_path / 'answer.json'
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'analysis-agent'))
    for key in ('MODEL_ENDPOINT', 'MODEL_NAME', 'MODEL_TOKEN'): env.pop(key, None)
    result = subprocess.run([sys.executable, '-m', 'analysis_agent.cli', 'analyze',
        '--task', str(task_path), '--corpus', str(tmp_path / 'corpus'), '--out', str(output),
        '--timeout', '20'], env=env, capture_output=True, timeout=25)
    assert result.returncode == 2
    answer = json.loads(output.read_text(encoding='utf-8'))
    assert answer['entity_predictions'] == []
    assert answer['notes']['schema_valid'] is False
    assert len(answer['notes']['unresolved_entity_ids']) == 4
    assert result.stdout == result.stderr == b''
