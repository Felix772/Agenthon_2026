"""Manifest-bound lexical retrieval with offsets in unchanged canonical corpus text."""
from collections import Counter
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType

from .contract import ContractError

MAX_DOC_BYTES = 8 * 1024 * 1024
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')


def calendar_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ContractError('date must be YYYY-MM-DD')
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError('invalid calendar date') from exc


def _read(path):
    path = Path(path).absolute()
    for component in (path, *path.parents):
        if component.is_symlink() or component.is_junction():
            raise ContractError('linked corpus paths are not allowed')
    if not stat.S_ISREG(path.stat().st_mode):
        raise ContractError('corpus input must be a regular file')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ContractError('corpus input must be regular')
        payload = stream.read(MAX_DOC_BYTES + 1)
    if len(payload) > MAX_DOC_BYTES:
        raise ContractError('corpus input exceeds byte limit')
    return payload


def canonical_text(doc):
    """Same flat-text precedence and one-space span join as the official scorer."""
    if isinstance(doc.get('text'), str):
        return doc['text']
    spans = doc.get('spans')
    if not isinstance(spans, list):
        raise ContractError('document has no text or spans')
    parts = [s.get('text', '') for s in spans if isinstance(s, dict)]
    if not all(isinstance(s, str) for s in parts):
        raise ContractError('non-text span')
    return ' '.join(parts)


def tokens(text):
    # Case folding happens only in the search index, never in citation text.
    return re.findall(r'\w+', text.casefold(), flags=re.UNICODE)


@dataclass(frozen=True)
class Document:
    doc_id: str
    doc_date: str
    sha256: str
    text: str
    entity_ids: tuple[str, ...] | None = None
    shared: bool = False

    def admits(self, entity_id):
        """Only the trusted manifest can grant an entity permission to cite a document."""
        return self.shared or (self.entity_ids is not None and entity_id in self.entity_ids)


@dataclass(frozen=True)
class Passage:
    doc_id: str
    span_start: int
    span_end: int
    text: str


@dataclass(frozen=True)
class Hit:
    passage: Passage
    score: float


class RetrievalIndex:
    def __init__(self, documents, cutoff, excluded, *, chunk_chars=1200, overlap=200):
        if type(chunk_chars) is not int or type(overlap) is not int or not 0 <= overlap < chunk_chars:
            raise ContractError('invalid chunk size or overlap')
        self.cutoff = calendar_date(cutoff)
        self.documents = MappingProxyType(dict(documents))
        self.excluded = tuple(excluded)
        passages = []
        for doc_id, doc in sorted(self.documents.items()):
            for start in range(0, len(doc.text), chunk_chars-overlap):
                end = min(start+chunk_chars, len(doc.text))
                text = doc.text[start:end]
                if text.strip():
                    passages.append(Passage(doc_id, start, end, text))
                if end == len(doc.text):
                    break
        self.passages = tuple(passages)
        self._counts = [Counter(tokens(p.text)) for p in self.passages]
        self._lengths = [sum(c.values()) for c in self._counts]
        self._average = sum(self._lengths)/len(self._lengths) if self._lengths else 1
        self._df = Counter(t for count in self._counts for t in count)

    @classmethod
    def load(cls, corpus_dir, cutoff, *, manifest_path=None, **chunk_options):
        limit = calendar_date(cutoff)
        corpus = Path(corpus_dir)
        if manifest_path is None:
            manifest_path = corpus.parent / 'manifest.json'
            if not manifest_path.exists():
                manifest_path = corpus / 'manifest.json'
        manifest = json.loads(_read(manifest_path))
        if not isinstance(manifest, dict) or not isinstance(manifest.get('files'), list):
            raise ContractError('manifest has no files array')
        documents, seen, excluded = {}, set(), []
        for entry in manifest['files']:
            if not isinstance(entry, dict) or entry.get('role') != 'corpus':
                continue
            relative = entry.get('path')
            if relative == 'corpus/manifest.json':
                continue
            if not isinstance(relative, str) or not relative.startswith('corpus/') or not relative.endswith('.json'):
                raise ContractError('invalid manifest corpus path')
            doc_id = relative[len('corpus/'):-len('.json')]
            if not ID.fullmatch(doc_id) or '..' in doc_id or doc_id in seen:
                raise ContractError('invalid or duplicate manifest document id')
            seen.add(doc_id)
            digest = entry.get('sha256')
            if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
                raise ContractError('invalid manifest digest')
            entity_ids = entry.get('entity_ids')
            if entity_ids is not None:
                if (not isinstance(entity_ids, list)
                        or not all(isinstance(eid, str) and eid for eid in entity_ids)
                        or len(entity_ids) != len(set(entity_ids))):
                    raise ContractError('invalid manifest entity ownership')
                entity_ids = tuple(entity_ids)
            raw_shared = entry.get('shared')
            if raw_shared is not None and raw_shared is not True:
                raise ContractError('manifest shared flag must be true when present')
            shared = raw_shared is True
            if shared and entity_ids:
                raise ContractError('manifest document is both shared and entity-labelled')
            payload = _read(corpus / (doc_id + '.json'))
            if hashlib.sha256(payload).hexdigest() != digest:
                raise ContractError('corpus digest mismatch')
            doc = json.loads(payload)
            if not isinstance(doc, dict) or doc.get('doc_id', doc_id) != doc_id:
                raise ContractError('document id differs from manifest')
            try:
                doc_date = calendar_date(doc.get('doc_date'))
            except ContractError:
                excluded.append((doc_id, 'missing_or_invalid_date'))
                continue
            if doc_date > limit:
                excluded.append((doc_id, 'post_cutoff'))
                continue
            documents[doc_id] = Document(doc_id, doc['doc_date'], digest, canonical_text(doc),
                                         entity_ids=entity_ids, shared=shared)
        if not seen:
            raise ContractError('manifest declares no citable documents')
        return cls(documents, cutoff, excluded, **chunk_options)

    def search(self, query, *, top_k=5, entity_id=None):
        if not isinstance(query, str) or type(top_k) is not int or top_k < 0:
            raise ContractError('invalid query or top_k')
        if entity_id is not None and (not isinstance(entity_id, str) or not entity_id):
            raise ContractError('invalid retrieval entity')
        terms = set(tokens(query))
        results = []
        for passage, count, length in zip(self.passages, self._counts, self._lengths):
            if entity_id is not None and not self.documents[passage.doc_id].admits(entity_id):
                continue
            score = 0.0
            for term in sorted(terms):
                frequency = count[term]
                if frequency:
                    idf = math.log(1+(len(self.passages)-self._df[term]+0.5)/(self._df[term]+0.5))
                    score += idf*frequency*2.5/(frequency+1.5*(0.25+0.75*length/self._average))
            if score > 0:
                results.append(Hit(passage, score))
        results.sort(key=lambda h: (-h.score, h.passage.doc_id, h.passage.span_start))
        return results[:top_k]

    def validate_span(self, doc_id, start, end, *, quote=None, entity_id=None):
        doc = self.documents.get(doc_id) if isinstance(doc_id, str) else None
        if doc is None or calendar_date(doc.doc_date) > self.cutoff:
            raise ContractError('citation document unavailable or embargoed')
        if entity_id is not None and not doc.admits(entity_id):
            raise ContractError('citation document does not belong to entity')
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(doc.text):
            raise ContractError('citation offsets out of bounds')
        actual = doc.text[start:end]
        if quote is not None and quote != actual:
            raise ContractError('citation quote differs from original text')
        return actual

    def ground_quote(self, doc_id, quote, *, within=None, entity_id=None):
        if not isinstance(quote, str) or not quote:
            raise ContractError('empty quote')
        doc = self.documents.get(doc_id)
        if doc is None:
            raise ContractError('unknown document')
        start, end = within if within is not None else (0, len(doc.text))
        self.validate_span(doc_id, start, end, entity_id=entity_id)
        found = doc.text.find(quote, start, end)
        if found < 0:
            raise ContractError('quote not found verbatim')
        if doc.text.find(quote, found+1, end) >= 0:
            raise ContractError('ambiguous quote; constrain to retrieved passage')
        return Passage(doc_id, found, found+len(quote), quote)
