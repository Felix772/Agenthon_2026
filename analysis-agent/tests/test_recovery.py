"""Causal checks for preserving valid work, actual-send caps, and honest fallback."""
from copy import deepcopy
import json
import subprocess
import time
from unittest.mock import patch

import pytest

from conftest import T4_UNITS
from analysis_agent.cli import main, valid_checkpoint
from analysis_agent.contract import ContractError, write_answer
from analysis_agent.fallback import baseline_rows
from analysis_agent.pipeline import analyze
from analysis_agent.retrieval import Document, RetrievalIndex
from test_pipeline import FakeClient, setup
from test_reliability import ModelError


def public_input(name='t4-auction-btc-202411-us7'):
    unit = T4_UNITS / name
    task = json.loads((unit / 'task.json').read_text(encoding='utf-8'))
    return unit, task, RetrievalIndex.load(unit / 'corpus', task['cutoff_date'])


def test_partial_repair_only_requests_invalid_rows_and_retains_values():
    task, index = setup(n=3)

    class Repair(FakeClient):
        def complete(self, messages, deadline, *, max_sends=1):
            request = json.loads(messages[-1]['content'])
            self.last_request = request
            value = json.loads(super().complete(messages, deadline, max_sends=max_sends))
            if self.calls == 1:
                value['entity_predictions'][0]['point_forecast'] = 137
                value['entity_predictions'][1]['unit'] = 'wrong'
            else:
                assert [row['entity_id'] for row in request['entities']] == ['E1']
                assert request['row_errors'] == {'E1': 'invalid_unit'}
            return json.dumps(value)

    client = Repair()
    answer = analyze(task, index, client, time.monotonic()+10)
    assert client.calls == 2
    assert answer['entity_predictions'][0]['point_forecast'] == 137
    assert len(answer['entity_predictions']) == 3


def test_legacy_supported_false_is_not_an_admission_gate():
    task, index = setup(n=1)
    client = FakeClient('unsupported', permanent=True)
    answer = analyze(task, index, client, time.monotonic()+10)
    assert client.calls == 1
    claim = answer['entity_predictions'][0]['claims'][0]
    assert claim['claim'] == index.validate_span(claim['doc_id'], claim['span_start'], claim['span_end'])


def test_public_fallback_coverage_is_explicit_and_not_all_families():
    coverage = {}
    for unit in sorted(T4_UNITS.iterdir()):
        if not (unit / 'task.json').is_file():
            continue
        _, task, index = public_input(unit.name)
        coverage[unit.name] = len(baseline_rows(task, index))
    assert {name: rows for name, rows in coverage.items() if rows} == {
        't4-auction-btc-202411-us7': 7, 't4-cpicomp-202410-us11': 11}


@pytest.mark.parametrize('name', ['t4-auction-btc-202411-us7', 't4-cpicomp-202410-us11'])
def test_no_client_returns_complete_input_history_answer(name):
    _, task, index = public_input(name)
    snapshots = []
    answer = analyze(task, index, None, time.monotonic()+10, checkpoint=snapshots.append)
    assert len(answer['entity_predictions']) == len(task['entities'])
    assert snapshots and snapshots[0] == answer
    assert 'Input-history fallback rows:' in answer['evidence_trace']
    for row in answer['entity_predictions']:
        assert row['interval']['lo'] <= row['point_forecast'] <= row['interval']['hi']


def test_baseline_depends_on_inputs_not_unit_id_or_ticker():
    _, task, index = public_input()
    expected = baseline_rows(task, index)
    renamed = deepcopy(task)
    renamed['task_id'] = 'unseen-unit'
    aliases = {entity['entity_id']: f'unseen-{i}' for i, entity in enumerate(task['entities'])}
    for entity in renamed['entities']:
        entity['entity_id'] = aliases[entity['entity_id']]
    docs = {key: Document(doc.doc_id, doc.doc_date, doc.sha256, doc.text,
            tuple(aliases[eid] for eid in doc.entity_ids) if doc.entity_ids else None, doc.shared)
            for key, doc in index.documents.items()}
    result = baseline_rows(renamed, RetrievalIndex(docs, renamed['cutoff_date'], []))
    assert {eid: row['point_forecast'] for eid, row in result.items()} == {
        aliases[eid]: row['point_forecast'] for eid, row in expected.items()}
    renamed['entities'][0]['unit'] = 'USD'
    assert renamed['entities'][0]['entity_id'] not in baseline_rows(renamed, RetrievalIndex(docs, renamed['cutoff_date'], []))


def test_baseline_honors_target_units_alias():
    _, task, index = public_input()
    original = baseline_rows(task, index)
    task['target']['units'] = 'bid_to_cover_ratio'
    for entity in task['entities']:
        entity.pop('unit')
    assert baseline_rows(task, index) == original


@pytest.mark.parametrize('terminal', [False, True])
def test_transport_failure_preserves_preexisting_complete_baseline(terminal):
    _, task, index = public_input()
    snapshots = []

    class Unavailable:
        sends = 0
        def complete(self, messages, deadline, *, max_sends=1):
            assert max_sends == 1 and snapshots
            self.sends += 1
            raise ModelError('synthetic', category='http_permanent' if terminal else 'transport', terminal=terminal)

    client = Unavailable()
    answer = analyze(task, index, client, time.monotonic()+10, checkpoint=snapshots.append)
    assert len(answer['entity_predictions']) == 7
    assert client.sends == (1 if terminal else 6)


def test_later_groups_receive_initial_slot_before_repairs():
    task, index = setup(n=30)

    class Faults(FakeClient):
        sends = 14
        requests = 0
        def __init__(self):
            super().__init__('missing', permanent=True)
            self.rosters = []
        def complete(self, messages, deadline, *, max_sends=1):
            self.rosters.append([entity['entity_id'] for entity in json.loads(messages[-1]['content'])['entities']])
            self.sends += 1
            return super().complete(messages, deadline, max_sends=max_sends)

    client = Faults()
    with pytest.raises(ContractError, match='incomplete'):
        analyze(task, index, client, time.monotonic()+10)
    assert client.sends == 25 and client.calls == 11
    assert client.rosters[:10] == [[f'E{i}', f'E{i+1}', f'E{i+2}'] for i in range(0,30,3)]
    assert client.rosters[10] == ['E2']


@pytest.mark.parametrize('worker_result', ['timeout', 'nonzero'])
def test_parent_preserves_revalidated_checkpoint_after_worker_failure(tmp_path, worker_result):
    unit, task, index = public_input()
    answer = analyze(task, index, None, time.monotonic()+10)
    output = tmp_path / 'answer.json'
    before = []
    def worker(command, **kwargs):
        if '--validate-only' in command:
            assert valid_checkpoint(unit/'task.json', unit/'corpus', output)
            return subprocess.CompletedProcess(command, 0)
        write_answer(task, answer, output)
        before.append(output.read_bytes())
        if worker_result == 'timeout':
            raise subprocess.TimeoutExpired(command, 1)
        return subprocess.CompletedProcess(command, 2)
    with patch('analysis_agent.cli.subprocess.run', side_effect=worker):
        status = main(['analyze', '--task', str(unit/'task.json'), '--corpus', str(unit/'corpus'),
                       '--out', str(output), '--timeout', '30'])
    assert status == 0 and output.read_bytes() == before[0]


def test_prior_invocation_answer_cannot_mask_missing_worker_output(tmp_path):
    unit, task, index = public_input()
    output = tmp_path/'answer.json'
    write_answer(task, analyze(task, index, None, time.monotonic()+10), output)
    with patch('analysis_agent.cli.subprocess.run', return_value=subprocess.CompletedProcess([], 2)) as run:
        status = main(['analyze', '--task', str(unit/'task.json'), '--corpus', str(unit/'corpus'),
                       '--out', str(output), '--timeout', '30'])
    assert status == 2 and run.call_count == 1
    assert json.loads(output.read_text())['notes']['schema_valid'] is False


@pytest.mark.parametrize('timeout', [580, 600])
def test_worker_and_validation_share_total_deadline(tmp_path, timeout):
    unit, task, index = public_input()
    output = tmp_path/'answer.json'
    answer = analyze(task, index, None, time.monotonic()+10)
    clock, limits = [100.0], []
    def run(command, **kwargs):
        limits.append(kwargs['timeout'])
        if '--validate-only' not in command:
            write_answer(task, answer, output)
            clock[0] += kwargs['timeout']
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        clock[0] += kwargs['timeout']
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])
    with patch('analysis_agent.cli.time.monotonic', side_effect=lambda: clock[0]), \
            patch('analysis_agent.cli.subprocess.run', side_effect=run):
        status = main(['analyze', '--task', str(unit/'task.json'), '--corpus', str(unit/'corpus'),
                       '--out', str(output), '--timeout', str(timeout)])
    assert status == 2
    assert limits == [timeout - 25, 24.75]
    assert clock[0] < 100 + timeout


def test_worker_without_configuration_can_publish_input_baseline(tmp_path):
    unit, task, _ = public_input()
    output = tmp_path/'answer.json'
    with patch('analysis_agent.cli.house_client', side_effect=ModelError('missing config')):
        status = main(['analyze', '--worker', '--task', str(unit/'task.json'),
                       '--corpus', str(unit/'corpus'), '--out', str(output), '--timeout', '30'])
    assert status == 0
    assert valid_checkpoint(unit/'task.json', unit/'corpus', output)


def test_checkpoint_with_corrupt_citation_is_not_kept(tmp_path):
    unit, task, index = public_input()
    answer = analyze(task, index, None, time.monotonic()+10)
    answer['entity_predictions'][0]['claims'][0]['span_end'] = 999999
    output = tmp_path/'answer.json'
    output.write_text(json.dumps(answer), encoding='utf-8')
    assert not valid_checkpoint(unit/'task.json', unit/'corpus', output)


def test_work_deadline_stops_new_calls_but_retains_input_answer():
    _, task, index = public_input()
    snapshots = []

    class Expired:
        sends = 0
        def complete(self, messages, deadline, *, max_sends=1):
            self.sends += 1
            raise ModelError('expired', category='deadline', terminal=True)

    client = Expired()
    answer = analyze(task, index, client, time.monotonic()+10, checkpoint=snapshots.append)
    assert client.sends == 1 and len(answer['entity_predictions']) == 7
