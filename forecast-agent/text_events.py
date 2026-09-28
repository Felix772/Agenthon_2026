"""Cutoff-aware evidence and bounded event interface; no language model or fitted policy."""
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
import re

import numpy as np


def iso_day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Document date must be canonical YYYY-MM-DD')
    return date.fromisoformat(value)


def load_text(directory, asof):
    cutoff = iso_day(asof)
    directory = Path(directory)
    if directory.is_symlink() or directory.is_junction():
        raise ValueError('Linked text directory')
    index = directory / 'corpus_index.json'
    if not index.exists():
        return {}, {'status': 'no_index', 'eligible': 0, 'excluded': []}
    if index.is_symlink() or index.stat().st_size > 8*1024*1024:
        raise ValueError('Invalid corpus index')
    raw = json.loads(index.read_text(encoding='utf-8'))
    if not isinstance(raw, dict) or not isinstance(raw.get('documents'), list):
        raise ValueError('Invalid documents index')
    docs, excluded, seen = {}, [], set()
    for entry in raw['documents']:
        if not isinstance(entry, dict): raise ValueError('Invalid document entry')
        doc_id, filename = entry.get('doc_id'), entry.get('file')
        if not isinstance(doc_id, str) or not doc_id or doc_id in seen:
            raise ValueError('Invalid or duplicate document id')
        seen.add(doc_id)
        if (not isinstance(filename, str) or not re.fullmatch(r'[^\W_][\w.-]*\.txt', filename, flags=re.UNICODE)
                or '..' in filename):
            raise ValueError('Unsafe text filename')
        try: released = iso_day(entry.get('timestamp'))
        except (ValueError, TypeError):
            excluded.append({'doc_id': doc_id, 'reason': 'invalid_or_missing_release_date'})
            continue
        if released > cutoff:
            excluded.append({'doc_id': doc_id, 'reason': 'post_cutoff'})
            continue
        path = directory / filename
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 8*1024*1024:
            raise ValueError('Invalid text document')
        payload = path.read_bytes()
        text = payload.decode('utf-8')
        docs[doc_id] = {'text': text, 'released': released.isoformat(),
                        'sha256': hashlib.sha256(payload).hexdigest()}
    return docs, {'status': 'loaded', 'eligible': len(docs), 'excluded': excluded,
                  'date_policy': 'corpus index timestamp is public release date, not event date'}


def retrieve(docs, query, top_k=5):
    if type(top_k) is not int or top_k < 0: raise ValueError('Invalid top_k')
    terms = set(re.findall(r'\w+', query.casefold()))
    hits = []
    for doc_id, doc in sorted(docs.items()):
        text = doc['text']
        for start in range(0, len(text), 800):
            end = min(start+1000, len(text))
            counts = Counter(re.findall(r'\w+', text[start:end].casefold()))
            score = sum(min(counts[t], 3) for t in terms)
            if score:
                hits.append({'doc_id': doc_id, 'span_start': start, 'span_end': end,
                             'quote': text[start:end], 'release_date': doc['released'], 'score': score})
            if end == len(text): break
    return sorted(hits, key=lambda h: (-h['score'], h['doc_id'], h['span_start']))[:top_k]


def adjust_samples(samples, grid, events, docs, asof):
    """Apply explicit evidence-bound standardized shifts, never infer an event from keywords.

    Policy is uncalibrated and only an interface: aggregate mean shift is capped
    at0.25 baseline predictive standard deviations, scale multiplier at[0.75,1.25].
    Zero spread remains zero; missing events leave every sample unchanged.
    """
    original = np.asarray(samples, dtype=float)
    if original.ndim != 3 or original.shape[1:] != (len(grid.assets), len(grid.horizons)) or not np.isfinite(original).all():
        raise ValueError('Invalid base samples')
    adjusted = original.copy()
    if not isinstance(events, list): raise ValueError('Events must be a list')
    shifts = np.zeros(original.shape[1:])
    scales = np.ones_like(shifts)
    evidence = []
    seen = set()
    for event in events:
        if not isinstance(event, dict): raise ValueError('Invalid event')
        eid = event.get('event_id')
        if not isinstance(eid, str) or not eid or eid in seen: raise ValueError('Duplicate or missing event_id')
        seen.add(eid)
        asset, horizon = event.get('asset'), event.get('horizon')
        if asset not in grid.assets or type(horizon) is not int or horizon not in grid.horizons:
            raise ValueError('Unknown event asset/horizon')
        doc = docs.get(event.get('doc_id'))
        if doc is None or iso_day(doc['released']) > iso_day(asof): raise ValueError('Unavailable event evidence')
        start, end = event.get('span_start'), event.get('span_end')
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(doc['text']):
            raise ValueError('Invalid event offsets')
        if event.get('quote') != doc['text'][start:end]: raise ValueError('Event quote mismatch')
        shift, scale = event.get('mean_shift_sigma'), event.get('vol_multiplier')
        if any(type(v) not in (int,float) or not np.isfinite(v) for v in (shift,scale)) or scale <= 0:
            raise ValueError('Invalid numerical adjustment')
        ai, hi = grid.assets.index(asset), grid.horizons.index(horizon)
        shifts[ai,hi] += np.clip(shift,-0.25,0.25)
        scales[ai,hi] *= np.clip(scale,0.75,1.25)
        evidence.append({'event_id':eid,'asset':asset,'horizon':horizon,'doc_id':event['doc_id'],
                         'sha256':doc['sha256'],'span_start':start,'span_end':end})
    if events:
        shifts = np.clip(shifts,-0.25,0.25)
        scales = np.clip(scales,0.75,1.25)
        mean, spread = original.mean(axis=0), original.std(axis=0)
        adjusted = mean+(original-mean)*scales+shifts*spread
        if not np.isfinite(adjusted).all(): raise ValueError('Nonfinite adjusted samples')
    return adjusted, {'event_count':len(events),'evidence':evidence,
                      'mean_shift_sigma':shifts.tolist(),'vol_multiplier':scales.tolist(),
                      'quality_evidence':'not measured; numerical bounds are uncalibrated',
                      'text_contribution':'none' if not events else 'explicit supplied event adjustments'}
