"""Optional reason guards compared with the external official participant helper."""
from copy import deepcopy
import json
import time
import subprocess
from unittest.mock import patch

import pytest

from analysis_agent.cli import main, valid_checkpoint
from analysis_agent.contract import write_answer
from analysis_agent.pipeline import analyze
from analysis_agent.reasons import compact_bytes, projected_answer, reason_findings
from analysis_agent.retrieval import Document, RetrievalIndex
from baselines.guardrails_example.citation_rail import CorpusDoc, check_submitted_reasons
from test_pipeline import FakeClient, setup
from test_reliability import ModelError
from test_recovery import public_input


def fixture():
    task, index = setup(n=1)
    answer = analyze(task, index, FakeClient(), time.monotonic()+30)
    text = index.documents['doc'].text
    reason = {'reason_id': 'r1', 'premise': text,
              'mechanism': 'An increase in revenue contributes to a higher next-period level.',
              'answer_implication': 'This supports the submitted revenue forecast for E0.',
              'scope': {'entities': ['E0']},
              'citations': [{'doc_id': 'doc', 'span_start': 0, 'span_end': len(text)}]}
    answer['submitted_reasons'] = [reason]
    return task, index, answer


def official(answer, index):
    corpus = {key: CorpusDoc(doc.doc_id, doc.text, doc.doc_date) for key, doc in index.documents.items()}
    return {finding.code for finding in check_submitted_reasons(answer, corpus, index.cutoff.isoformat())}


def test_valid_reason_passes_both_guards():
    task, index, answer = fixture()
    assert reason_findings(task, answer, index) == []
    assert official(answer, index) == set()


def test_three_distinct_reasons_are_legal():
    task, index, answer = fixture()
    reason = answer['submitted_reasons'][0]
    answer['submitted_reasons'] = [{**deepcopy(reason), 'reason_id': f'r{i}',
        'mechanism': reason['mechanism'] + f' Distinct synthetic channel {i}.'} for i in range(1, 4)]
    assert not reason_findings(task, answer, index) and not official(answer, index)


def test_duplicate_normalization_includes_nfc():
    task, index, answer = fixture()
    answer['submitted_reasons'][0]['mechanism'] = 'The café has growing revenue.'
    duplicate = deepcopy(answer['submitted_reasons'][0])
    duplicate['mechanism'] = 'The cafe\u0301 has growing revenue.'
    answer['submitted_reasons'].append(duplicate)
    assert 'duplicate_reason' in reason_findings(task, answer, index)
    assert 'duplicate_reason' in official(answer, index)


@pytest.mark.parametrize('case', ['empty', 'four', 'missing', 'type', 'task', 'unknown', 'span',
                                 'deny', 'duplicate', 'scope'])
def test_invalid_reason_blocks_rejected_by_official_and_participant(case):
    task, index, answer = fixture()
    reason = answer['submitted_reasons'][0]
    if case == 'empty': answer['submitted_reasons'] = []
    elif case == 'four': answer['submitted_reasons'] *= 4
    elif case == 'missing': reason.pop('mechanism')
    elif case == 'type': reason['premise'] = 1
    elif case == 'task': reason['citations'][0]['doc_id'] = 'task'
    elif case == 'unknown': reason['citations'][0]['doc_id'] = 'other'
    elif case == 'span': reason['citations'][0]['span_end'] = 10000
    elif case == 'deny': reason['mechanism'] += ' leaderboard'
    elif case == 'scope': reason['scope'] = 'E0'
    elif case == 'duplicate':
        duplicate = deepcopy(reason)
        duplicate['reason_id'] = 'different'
        for field in ('premise', 'mechanism', 'answer_implication'):
            duplicate[field] = '\u200b' + duplicate[field].upper().replace(' ', '  ')
        answer['submitted_reasons'].append(duplicate)
    assert reason_findings(task, answer, index)
    assert official(answer, index)


def test_local_url_rail_remains_stricter_than_scorer_522():
    task, index, answer = fixture()
    text = 'Revenue appears at https://public.example/revenue in the source.'
    index = RetrievalIndex({'doc': Document('doc', '2024-01-01', 'synthetic', text, shared=True)}, '2024-01-01', [])
    reason = answer['submitted_reasons'][0]
    reason['premise'] = text
    reason['citations'][0]['span_end'] = len(text)
    assert not reason_findings(task, answer, index) and not official(answer, index)
    reason['premise'] = 'The source says: ' + text
    assert 'deny_list' in reason_findings(task, answer, index)
    # 5.2.2 masks valid URLs; the existing opt-in local rail still omits this block.
    assert not official(answer, index)


@pytest.mark.parametrize('field,limit,code', [('reasons', 6500, 'cap_reason_bytes'),
                                           ('answer', 3000, 'cap_answer_bytes')])
def test_multibyte_compact_cap_boundary_matches_official(field, limit, code):
    task, index, answer = fixture()
    if field == 'reasons':
        reason = answer['submitted_reasons'][0]
        reason['mechanism'] = '\u4e2d' * 1900 + '\n"\\'
        projection = [{key: reason[key] for key in ('reason_id', 'premise', 'mechanism', 'answer_implication')}]
        extra = limit - compact_bytes(projection)
        reason['mechanism'] += 'x' * extra
        assert compact_bytes([{key: reason[key] for key in projection[0]}]) == limit
        assert not reason_findings(task, answer, index) and not official(answer, index)
        reason['mechanism'] += 'x'
    else:
        # Arbitrary optional string labels on regression rows are in the official helper's conservative projection.
        row = answer['entity_predictions'][0]
        row['label'] = 'x' * (limit - compact_bytes(projected_answer(answer)) - len(',"label":""'))
        assert compact_bytes(projected_answer(answer)) == limit
        assert not reason_findings(task, answer, index) and not official(answer, index)
        row['label'] += 'x'
    assert code in reason_findings(task, answer, index)
    assert code in official(answer, index)


def test_citation_character_cap_boundary():
    task, _, answer = fixture()
    text = 'x' * 8001
    index = RetrievalIndex({'doc': Document('doc', '2024-01-01', 'synthetic', text, shared=True)}, '2024-01-01', [])
    citation = answer['submitted_reasons'][0]['citations'][0]
    citation['span_end'] = 8000
    assert not reason_findings(task, answer, index) and not official(answer, index)
    citation['span_end'] = 8001
    assert 'cap_citation_chars' in reason_findings(task, answer, index)
    assert 'cap_citation_chars' in official(answer, index)


def test_uri_masking_is_counted_in_evidence_bytes():
    task, index, answer = fixture()
    text = 'https://' + 'x' * 7492
    index = RetrievalIndex({'doc': Document('doc', '2024-01-01', 'synthetic', text, shared=True)}, '2024-01-01', [])
    reason = answer['submitted_reasons'][0]
    reason['citations'] = [{'doc_id': 'doc', 'span_start': 0, 'span_end': len(text)}] * 3
    assert 'cap_evidence_bytes' in reason_findings(task, answer, index)
    assert 'cap_evidence_bytes' in official(answer, index)


def test_evidence_compact_byte_boundary_matches_official():
    task, _, answer = fixture()
    index = RetrievalIndex({'doc': Document('doc', '2024-01-01', 'synthetic', 'x' * 8000, shared=True)}, '2024-01-01', [])
    citations = [{'doc_id': 'doc', 'span_start': 0, 'span_end': 8000} for _ in range(5)]
    citations.append({'doc_id': 'doc', 'span_start': 0, 'span_end': 1})
    answer['submitted_reasons'][0]['citations'] = citations
    for _ in range(4):
        size = compact_bytes([{**citation, 'trusted_text': 'x' * citation['span_end'], 'reason_id': 'r1'}
                              for citation in citations])
        if size == 46500:
            break
        citations[-1]['span_end'] += 46500 - size
    assert size == 46500 and 0 < citations[-1]['span_end'] <= 8000
    assert not reason_findings(task, answer, index) and not official(answer, index)
    citations[-1]['span_end'] += 1
    assert 'cap_evidence_bytes' in reason_findings(task, answer, index)
    assert 'cap_evidence_bytes' in official(answer, index)


def test_future_reason_document_is_rejected():
    task, index, answer = fixture()
    doc = index.documents['doc']
    future = Document(doc.doc_id, '2025-01-01', doc.sha256, doc.text, shared=True)
    index = RetrievalIndex({'doc': future}, '2024-01-01', [])
    assert 'reason_citation' in reason_findings(task, answer, index)
    assert 'reason_citation' in official(answer, index)


def test_reason_citation_must_belong_to_a_named_entity():
    task, index, answer = fixture()
    old = index.documents['doc']
    peer = Document('doc', old.doc_date, old.sha256, old.text, entity_ids=('OTHER',))
    assert 'reason_citation' in reason_findings(task, answer, RetrievalIndex({'doc': peer}, old.doc_date, []))


class ReasonClient(FakeClient):
    def __init__(self, bad=None):
        super().__init__()
        self.bad = bad
        self.reason_calls = 0
        self.snapshots = []

    def complete(self, messages, deadline, *, max_sends=1):
        request = json.loads(messages[-1]['content'])
        if request.get('mode') != 'submitted_reasons':
            return super().complete(messages, deadline, max_sends=max_sends)
        self.calls += 1
        self.reason_calls += 1
        assert self.snapshots and max_sends == 1
        self.frozen = deepcopy(request['final_answer'])
        key, excerpt = next(iter(request['excerpts'].items()))
        entity = excerpt['entity_ids'][0]
        reason = {'reason_id': 'model-id', 'premise': excerpt['text'],
                  'mechanism': 'Revenue growth supports a higher next-period level.',
                  'answer_implication': 'This supports the frozen forecast for ' + entity + '.',
                  'citations': [{'excerpt_id': key, 'quote': excerpt['text']}]}
        reasons = [reason]
        if self.bad == 'transport': raise ModelError('fixture', category='transport')
        if self.bad == 'json': return 'invalid'
        if self.bad == 'empty': reasons = []
        if self.bad == 'missing': reason.pop('mechanism')
        if self.bad == 'task': reason['citations'][0]['excerpt_id'] = 'task'
        if self.bad == 'deny': reason['mechanism'] += ' leaderboard'
        if self.bad == 'duplicate': reasons *= 2
        if self.bad == 'second_invalid': reasons.append({'reason_id': 'bad'})
        value = {'submitted_reasons': reasons}
        if self.bad == 'overwrite': value['entity_predictions'] = [{'entity_id': 'E0', 'point_forecast': 999}]
        return json.dumps(value)


def test_reasons_default_off_has_no_extra_send():
    task, index = setup(n=1)
    client = ReasonClient()
    answer = analyze(task, index, client, time.monotonic()+30, checkpoint=client.snapshots.append)
    assert client.calls == 1 and client.reason_calls == 0
    assert 'submitted_reasons' not in answer


def test_opt_in_reason_is_after_checkpoint_and_never_changes_predictions():
    task, index = setup(n=1)
    client = ReasonClient()
    state = {}
    answer = analyze(task, index, client, time.monotonic()+30, checkpoint=client.snapshots.append,
                     enable_reasons=True, state=state)
    assert client.calls == 2 and client.reason_calls == 1
    assert state['reasons_status'] == 'included'
    assert 'submitted_reasons' not in client.snapshots[0]
    assert answer['entity_predictions'] == client.snapshots[0]['entity_predictions']
    assert projected_answer(answer) == client.frozen
    assert not official(answer, index)


@pytest.mark.parametrize('bad', ['transport', 'json', 'empty', 'missing', 'task', 'deny', 'duplicate', 'second_invalid'])
def test_optional_failure_rolls_back_the_entire_block(bad):
    task, index = setup(n=1)
    client = ReasonClient(bad)
    answer = analyze(task, index, client, time.monotonic()+30, checkpoint=client.snapshots.append, enable_reasons=True)
    assert client.calls == 2 and client.reason_calls == 1
    assert answer == client.snapshots[0]
    assert 'submitted_reasons' not in answer


def test_reason_response_cannot_overwrite_final_prediction_rows():
    task, index = setup(n=1)
    client = ReasonClient('overwrite')
    answer = analyze(task, index, client, time.monotonic()+30,
                     checkpoint=client.snapshots.append, enable_reasons=True)
    assert answer['entity_predictions'] == client.snapshots[0]['entity_predictions']
    assert answer['entity_predictions'][0]['point_forecast'] == 1


def test_optional_step_can_use_send_25_without_send_26():
    task, index = setup(n=1)
    client = ReasonClient()
    client.sends = 23
    state = {}
    answer = analyze(task, index, client, time.monotonic()+30, checkpoint=client.snapshots.append,
                     enable_reasons=True, state=state)
    assert client.calls == 2 and client.sends + client.calls == 25
    assert client.reason_calls == 1 and state['reasons_status'] == 'included'
    assert 'submitted_reasons' in answer


@pytest.mark.parametrize('enabled', [False, True])
def test_cli_reason_flag_is_explicit_and_off_by_default(tmp_path, enabled):
    unit, task, index = public_input()
    answer = analyze(task, index, None, time.monotonic()+30)
    output = tmp_path/'answer.json'
    seen = []
    def work(*args, enable_reasons, **kwargs):
        seen.append(enable_reasons)
        return answer
    with patch('analysis_agent.cli.house_client', return_value=None), \
            patch('analysis_agent.cli.analyze', side_effect=work):
        status = main(['analyze', '--worker', '--task', str(unit/'task.json'),
                       '--corpus', str(unit/'corpus'), '--out', str(output)] + (['--reasons'] if enabled else []))
    assert status == 0 and seen == [enabled]


def test_slow_optional_worker_keeps_complete_base_and_reason_flag(tmp_path):
    unit, task, index = public_input()
    answer = analyze(task, index, None, time.monotonic()+30)
    output = tmp_path/'answer.json'
    def run(command, **kwargs):
        if '--validate-only' in command:
            assert valid_checkpoint(unit/'task.json', unit/'corpus', output)
            return subprocess.CompletedProcess(command, 0)
        assert '--reasons' in command
        # A2 publishes before the optional request; the watchdog stops that request.
        write_answer(task, answer, output)
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])
    with patch('analysis_agent.cli.subprocess.run', side_effect=run):
        status = main(['analyze', '--task', str(unit/'task.json'), '--corpus', str(unit/'corpus'),
                       '--out', str(output), '--timeout', '30', '--reasons'])
    assert status == 0 and json.loads(output.read_text()) == answer


@pytest.mark.parametrize('cause', ['budget', 'time'])
def test_optional_request_does_not_use_missing_budget_or_write_reserve(cause):
    task, index = setup(n=1)
    client = ReasonClient()
    if cause == 'budget': client.sends = 24
    state = {}
    answer = analyze(task, index, client, time.monotonic() + (5 if cause == 'time' else 30),
                     checkpoint=client.snapshots.append, enable_reasons=True, state=state)
    assert client.calls == 1 and client.reason_calls == 0
    assert state['reasons_status'] == 'skipped_' + cause
    assert 'submitted_reasons' not in answer


def test_large_projected_answer_omits_reasons_without_dropping_rows():
    task, index = setup(n=30)
    for entity in task['entities']:
        entity['entity_id'] += 'X' * 120
    client, state = ReasonClient(), {}
    answer = analyze(task, index, client, time.monotonic()+30,
                     checkpoint=client.snapshots.append, enable_reasons=True, state=state)
    assert len(answer['entity_predictions']) == 30
    assert client.calls == 10 and client.reason_calls == 0
    assert state['reasons_status'] == 'skipped_answer_size'
