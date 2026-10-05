"""Real shared transport plus pipeline; every response is synthetic."""
import io
import json
from pathlib import Path
import sys
import time
from unittest.mock import Mock, patch
import urllib.error

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'analysis-agent'), str(ROOT/'qfbench-agent'), str(ROOT/'project-evidence')]
from agent.model_client import ModelClient, ModelError
from analysis_agent.pipeline import analyze
from analysis_agent.contract import ContractError
from house_response_fixture import envelope
from test_pipeline import setup


def client_for(case):
    client = ModelClient('http://house.invalid', 'synthetic', 'synthetic-token')
    client.opener = Mock()
    def respond(request, **kwargs):
        return io.BytesIO(json.dumps(envelope(json.loads(request.data), case, client.requests)).encode())
    client.opener.open.side_effect = respond
    return client


@pytest.mark.parametrize('case', ['bare', 'fenced', 'separated', 'thinking_default', 'repair'])
def test_supported_response_shapes(case):
    task, index = setup()
    client = client_for(case)
    answer = analyze(task, index, client, time.monotonic()+10)
    assert len(answer['entity_predictions']) == 4
    assert client.requests == (3 if case == 'repair' else 2)


@pytest.mark.parametrize('case', ['prefixed', 'missing', 'duplicate', 'unit', 'quote'])
def test_invalid_rows_are_bounded_and_fail_closed(case):
    task, index = setup()
    client = client_for(case)
    with pytest.raises(ContractError): analyze(task, index, client, time.monotonic()+10)
    assert client.requests == 4


@pytest.mark.parametrize('case', ['empty', 'truncated', 'malformed', 'nonobject'])
def test_transport_failure_uses_only_one_send_per_scheduled_attempt(case):
    task, index = setup()
    client = client_for(case)
    with pytest.raises(ModelError): analyze(task, index, client, time.monotonic()+10)
    assert client.requests == 4


@pytest.mark.parametrize('status', [401, 403, 429, 500, 503])
def test_pipeline_cannot_multiply_transport_retries(status):
    task, index = setup()
    client = client_for('bare')
    client.opener.open.side_effect = urllib.error.HTTPError('http://house.invalid', status, 'synthetic', {}, None)
    with patch('agent.model_client.time.sleep'), pytest.raises(ModelError):
        analyze(task, index, client, time.monotonic()+10)
    assert client.opener.open.call_count == (1 if status in (401, 403) else 4)
    assert client.requests == (0 if status in (401, 403) else 4)


def test_repairs_share_global_budget_across_groups():
    task, index = setup(n=75)
    client = client_for('repair')
    with pytest.raises(ContractError, match='incomplete'):
        analyze(task, index, client, time.monotonic()+30)
    assert client.requests == client.opener.open.call_count == 25


@pytest.mark.parametrize('prefix,suffix', [('prose ', ''), ('', ' trailing'),
    ('```python\n', '\n```'), ('```json\n', '\n```\n```json\n{}\n```')])
def test_fence_support_does_not_scan_for_json(prefix, suffix):
    task, index = setup()
    client = client_for('bare')
    original = client.opener.open.side_effect
    def wrapped(request, **kwargs):
        value = json.load(original(request, **kwargs))
        value['choices'][0]['message']['content'] = prefix + value['choices'][0]['message']['content'] + suffix
        return io.BytesIO(json.dumps(value).encode())
    client.opener.open.side_effect = wrapped
    with pytest.raises(ContractError): analyze(task, index, client, time.monotonic()+10)
    assert client.requests == 4
