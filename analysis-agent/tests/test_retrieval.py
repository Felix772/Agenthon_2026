import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'analysis-agent'))
from analysis_agent import ContractError
from analysis_agent.retrieval import RetrievalIndex, canonical_text
from qfbench2_common.scoring.faithfulness import _doc_text
from conftest import T4_UNITS


def corpus(tmp_path, docs):
    folder = tmp_path / 'corpus'
    folder.mkdir()
    files = []
    for name, doc in docs.items():
        data = json.dumps(doc, ensure_ascii=False).encode('utf-8')
        (folder / (name+'.json')).write_bytes(data)
        files.append({'path': f'corpus/{name}.json', 'role': 'corpus',
                      'sha256': hashlib.sha256(data).hexdigest()})
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'files': files}), encoding='utf-8')
    return folder, manifest


def document(text, day='2024-01-01'):
    return {'text': text, 'doc_date': day}


def test_dates_filter_before_index_and_ignore_unlisted(tmp_path):
    folder, _ = corpus(tmp_path, {'good': document('profit'),
        'future': document('profit profit', '2024-01-02'),
        'undated': {'text': 'profit'}, 'bad': document('profit', '2024-02-30')})
    (folder / 'unlisted.json').write_text(json.dumps(document('profit')))
    index = RetrievalIndex.load(folder, '2024-01-01')
    assert list(index.documents) == ['good']
    assert len(index.excluded) == 3
    assert [h.passage.doc_id for h in index.search('profit')] == ['good']
    for name in ('future', 'undated', 'bad', 'unlisted', '../good'):
        with pytest.raises(ContractError): index.validate_span(name, 0, 1)


@pytest.mark.parametrize('doc', [document('é🙂\r\n  Revenue Straße STRASSE.'),
    {'doc_date': '2024-01-01', 'spans': [{'text': 'one'}, {'text': ''}, {'text': '二🙂  three'}]},
    {'text': 'flat wins', 'spans': [{'text': 'ignored'}], 'doc_date': '2024-01-01'}])
def test_exact_offsets_match_official_text(doc, tmp_path):
    folder, _ = corpus(tmp_path, {'doc': doc})
    index = RetrievalIndex.load(folder, '2024-01-01', chunk_chars=9, overlap=3)
    assert canonical_text(doc) == _doc_text(doc)
    for passage in index.passages:
        assert index.validate_span('doc', passage.span_start, passage.span_end, quote=passage.text) == passage.text
        assert _doc_text(doc)[passage.span_start:passage.span_end] == passage.text


def test_quote_ambiguity_and_no_normalization(tmp_path):
    folder, _ = corpus(tmp_path, {'doc': document('same quote; same quote; café  grows')})
    index = RetrievalIndex.load(folder, '2024-01-01')
    with pytest.raises(ContractError): index.ground_quote('doc', 'same quote')
    assert index.ground_quote('doc', 'same quote', within=(12, 22)).span_start == 12
    with pytest.raises(ContractError): index.ground_quote('doc', 'cafe grows')
    with pytest.raises(ContractError): index.validate_span('doc', 0, 4, quote='SAME')


@pytest.mark.parametrize('bounds', [(-1, 2), (0, 100), (1, 1), (3, 2), (True, 2), (0, 1.0)])
def test_invalid_offsets(bounds, tmp_path):
    folder, _ = corpus(tmp_path, {'doc': document('abc')})
    index = RetrievalIndex.load(folder, '2024-01-01')
    with pytest.raises(ContractError): index.validate_span('doc', *bounds)


@pytest.mark.parametrize('fault', ['digest', 'path', 'duplicate', 'id', 'date', 'missing_manifest'])
def test_input_faults(fault, tmp_path):
    folder, manifest = corpus(tmp_path, {'doc': document('abc')})
    data = json.loads(manifest.read_text())
    if fault == 'digest': (folder / 'doc.json').write_text('{}')
    elif fault == 'path': data['files'][0]['path'] = 'corpus/../../escape.json'
    elif fault == 'duplicate': data['files'].append(data['files'][0])
    elif fault == 'id':
        payload = json.dumps({**document('abc'), 'doc_id': 'other'}).encode()
        (folder / 'doc.json').write_bytes(payload)
        data['files'][0]['sha256'] = hashlib.sha256(payload).hexdigest()
    manifest.write_text(json.dumps(data))
    if fault == 'missing_manifest': manifest.unlink()
    with pytest.raises((ContractError, FileNotFoundError)):
        RetrievalIndex.load(folder, '20240101' if fault == 'date' else '2024-01-01')


def test_bm25_ties_empty_and_no_relevance_fallback(tmp_path):
    folder, _ = corpus(tmp_path, {'b': document('earnings revenue'), 'a': document('earnings revenue')})
    index = RetrievalIndex.load(folder, '2024-01-01')
    assert [h.passage.doc_id for h in index.search('EARNINGS')] == ['a', 'b']
    assert index.search('absent') == []
    assert index.search('') == []
    assert index.search('earnings', top_k=0) == []
    with pytest.raises(ContractError): index.search('earnings', top_k=-1)


def test_all_ineligible_returns_no_hits(tmp_path):
    folder, _ = corpus(tmp_path, {'doc': document('profit', '2025-01-01')})
    assert RetrievalIndex.load(folder, '2024-01-01').search('profit') == []


@pytest.mark.skipif(sys.platform == 'win32', reason='Linux production no-follow check')
def test_symlink_document_refused(tmp_path):
    folder, _ = corpus(tmp_path, {'doc': document('abc')})
    target = tmp_path / 'other.json'
    (folder / 'doc.json').rename(target)
    (folder / 'doc.json').symlink_to(target)
    with pytest.raises(ContractError): RetrievalIndex.load(folder, '2024-01-01')


def test_current_public_corpora_against_official_text():
    for unit in sorted(T4_UNITS.iterdir()):
        task_path = unit / 'task.json'
        if not task_path.exists(): continue
        task = json.loads(task_path.read_text(encoding='utf-8'))
        index = RetrievalIndex.load(unit / 'corpus', task['cutoff_date'])
        assert index.documents
        for doc_id, doc in index.documents.items():
            original = json.loads((unit / 'corpus' / (doc_id+'.json')).read_text(encoding='utf-8'))
            assert doc.text == _doc_text(original)
        for hit in index.search(task.get('prompt', ''), top_k=3):
            p = hit.passage
            assert index.validate_span(p.doc_id, p.span_start, p.span_end, quote=p.text) == p.text
