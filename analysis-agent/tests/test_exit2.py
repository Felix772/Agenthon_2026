"""Reproduce recoverable exit-2 mechanisms without weakening evidence admission."""
from copy import deepcopy
import json
import time

import pytest

from analysis_agent.contract import ContractError
from analysis_agent.fallback import baseline_rows
from analysis_agent.pipeline import analyze
from analysis_agent.retrieval import Document, RetrievalIndex
from test_pipeline import FakeClient, setup
from test_recovery import public_input


def test_complete_crlf_json_fence_has_same_rows_as_bare_json():
    task, index = setup()
    class Fenced(FakeClient):
        def complete(self, *args, **kwargs):
            return '```json\r\n'+super().complete(*args, **kwargs)+'\r\n```'
    control = analyze(task, index, FakeClient(), time.monotonic()+20)
    assert analyze(task, index, Fenced(), time.monotonic()+20) == control


def test_single_remaining_send_fills_late_missing_row_before_enhancement():
    _, task, index = public_input()
    task = deepcopy(task)
    task['entities'][-1]['unit'] = 'USD'
    baseline = baseline_rows(task, index)
    last = task['entities'][-1]['entity_id']
    assert len(baseline) == len(task['entities'])-1 and last not in baseline
    class LastRow:
        sends = 24
        def complete(self, messages, deadline, *, max_sends):
            request = json.loads(messages[-1]['content'])
            assert [row['entity_id'] for row in request['entities']] == [last]
            self.sends += 1
            assert self.sends == 25 and max_sends == 1
            key, excerpt = next((key, item) for key, item in request['excerpts'].items()
                                if last in item['entity_ids'])
            return json.dumps({'entity_predictions': [{'entity_id': last, 'unit': 'USD',
                'point_forecast': 1, 'interval': {'level': .9, 'lo': 0, 'hi': 2},
                'claims': [{'excerpt_id': key, 'quote': excerpt['text'][:150]}]}]})
    client, snapshots = LastRow(), []
    answer = analyze(task, index, client, time.monotonic()+20, checkpoint=snapshots.append)
    assert client.sends == 25 and snapshots and len(answer['entity_predictions']) == 7
    for row in answer['entity_predictions'][:-1]:
        assert row['point_forecast'] == baseline[row['entity_id']]['point_forecast']


def test_short_supported_history_is_not_an_emergency_prediction():
    task = {'task_id': 'short-history', 'target': {'type': 'regression',
            'name': 'bid_to_cover', 'unit': 'bid_to_cover_ratio'}, 'cutoff_date': '2024-06-01',
            'entities': [{'entity_id': 'SYNTHETIC', 'name': 'Auction'}]}
    doc = Document('history', '2024-05-31', 'synthetic',
        'date | bid_to_cover\n2024-01-01 | 2.1\n2024-02-01 | 2.2\n2024-03-01 | 2.3\n'
        '2024-04-01 | 2.4\n2024-05-01 | 2.5\n', entity_ids=('SYNTHETIC',))
    index = RetrievalIndex({'history': doc}, task['cutoff_date'], [])
    assert baseline_rows(task, index) == {}
    snapshots = []
    with pytest.raises(ContractError):
        analyze(task, index, None, time.monotonic()+20, checkpoint=snapshots.append)
    assert snapshots == []
