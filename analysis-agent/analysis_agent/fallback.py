"""Input-only recovery estimates when the House model cannot finish a roster."""
import math
import calendar
import re
import statistics

from .contract import ContractError, _task_contract, build_answer
from .retrieval import calendar_date


def _number(text):
    if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)', text):
        raise ValueError('not a plain numeric observation')
    value = float(text)
    if not math.isfinite(value):
        raise ValueError('nonfinite observation')
    return value


def _tables(text):
    lines = text.splitlines(keepends=True)
    offset = 0
    header, observations = None, []
    for line in lines + ['']:
        cells = [cell.strip() for cell in line.strip().split('|')]
        if header is not None and len(cells) == len(header) and re.fullmatch(r'\d{4}-\d{2}(?:-\d{2})?', cells[0]):
            observations.append((cells, offset, offset + len(line.rstrip('\r\n'))))
        else:
            if header is not None and observations:
                yield header, observations
            header, observations = (cells, []) if len(cells) > 1 else (None, [])
        offset += len(line)


def _series(task, entity, index):
    """Read two documented input structures, without using unit IDs or resolved labels.

    Auction ratios use a nonshared, single-entity history table. Monthly CPI uses
    an explicitly matched entity-name column in a shared MoM-percent table.
    No numeric field is guessed from an unrelated table, level, or currency.
    """
    eid = entity['entity_id']
    _, _, units, _ = _task_contract(task)
    unit = units[eid]
    ratio = unit == 'bid_to_cover_ratio' and 'bid_to_cover' in task['target'].get('name', '')
    monthly = unit == 'mom_pct_change_sa' and 'mom' in task['target'].get('name', '')
    if not (ratio or monthly):
        return None
    candidates = []
    for doc in index.documents.values():
        if not doc.admits(eid):
            continue
        if ratio and (doc.shared or doc.entity_ids != (eid,)):
            continue
        if monthly and 'month-over-month percent changes' not in doc.text.casefold():
            continue
        for header, records in _tables(doc.text):
            column = 'bid_to_cover' if ratio else entity.get('name')
            if column not in header or header.count(column) != 1:
                continue
            column = header.index(column)
            observations = []
            try:
                for cells, start, end in records:
                    day = cells[0]
                    if len(day) == 7:
                        year, month = map(int, day.split('-'))
                        day += f'-{calendar.monthrange(year, month)[1]:02d}'
                    if calendar_date(day) > index.cutoff:
                        raise ValueError('history row is after cutoff')
                    observations.append((cells[0], _number(cells[column]), start, end))
            except (ValueError, ContractError):
                continue
            dates = [observation[0] for observation in observations]
            if len(observations) >= 6 and dates == sorted(set(dates)):
                candidates.append((doc, observations))
    # Conflicting/overlapping series require model interpretation, not an arbitrary choice.
    return candidates[0] if len(candidates) == 1 else None


def baseline_rows(task, index):
    """Median of last three observations with an empirical rolling-residual band.

    The 90% field is the required nominal level; this short-history band has no
    claimed coverage guarantee. It is an input-derived emergency estimate only.
    Classification and unsupported target structures deliberately return no rows.
    """
    kind, _, units, _ = _task_contract(task)
    if kind != 'regression':
        return {}
    result = {}
    for entity in task['entities']:
        series = _series(task, entity, index)
        if series is None:
            continue
        doc, observations = series
        values = [observation[1] for observation in observations]
        point = statistics.median(values[-3:])
        residuals = sorted(values[i] - statistics.median(values[i-3:i]) for i in range(3, len(values)))
        # Conservative observed residual extrema; no fitted or future calibration data.
        lo, hi = min(point, point + residuals[0]), max(point, point + residuals[-1])
        _, _, start, end = observations[-1]
        quote = index.validate_span(doc.doc_id, start, end, entity_id=entity['entity_id'])
        if len(quote.encode('utf-8')) > 350:
            continue
        row = {'entity_id': entity['entity_id'], 'point_forecast': point,
               'interval': {'level': .9, 'lo': lo, 'hi': hi},
               'claims': [{'doc_id': doc.doc_id, 'span_start': start, 'span_end': end, 'claim': quote}]}
        if units[entity['entity_id']] is not None:
            row['unit'] = units[entity['entity_id']]
        build_answer(dict(task, entities=[entity]), [row])
        result[entity['entity_id']] = row
    return result


_CANARY = re.compile(r'(?i)(?:canary|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})')


def _emergency_fact(task, entity, index):
    """Return one exact, eligible, entity-bound excerpt; never invent a citation."""
    eid = entity['entity_id']
    query = ' '.join(str(entity.get(key, '')) for key in ('entity_id', 'name'))
    query += ' ' + str(task['target'].get('name', ''))
    passages = [(hit.passage.doc_id, hit.passage.span_start, hit.passage.span_end)
                for hit in index.search(query, top_k=20, entity_id=eid)]
    passages += [(doc.doc_id, 0, len(doc.text)) for doc in index.documents.values()
                 if doc.admits(eid)]
    fallback = None
    for doc_id, begin, limit in passages:
        doc = index.documents[doc_id]
        start = begin + len(doc.text[begin:limit]) - len(doc.text[begin:limit].lstrip())
        if start >= limit:
            continue
        end = min(limit, start + 240)
        while end > start and len(doc.text[start:end].encode('utf-8')) > 320:
            end -= 1
        quote = doc.text[start:end].rstrip()
        end = start + len(quote)
        if (len(quote) < 20 or len(re.findall(r'[A-Za-z]{2,}', quote)) < 3
                or _CANARY.search(quote)):
            continue
        citation = {'doc_id': doc_id, 'span_start': start, 'span_end': end,
                    'claim': index.validate_span(doc_id, start, end, entity_id=eid)}
        if re.search(r'\d', quote):
            return citation
        if fallback is None:
            fallback = citation
    return fallback


def _bounded_feature(value):
    if type(value) not in (int, float):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    # Leave room for the interval width; a finite point can overflow its bounds.
    return number if math.isfinite(number) and math.isfinite(number * 1.5) else None


def _revision_anchor(task, entity, labels, unit):
    """Recognize an explicit revision-level contract; do not infer release stages.

    A vintage table can contain multiple releases in a month, truncated early
    vintages, or an annual update. Those columns alone do not identify comparable
    revision stages. This bounded recovery path therefore uses only the supplied
    latest estimate. Its band is nominal and uncalibrated, and its direction
    remains the generic default. The reason is available to local audit callers.
    """
    target_words = re.findall(r'[a-z]+', str(task['target'].get('name', '')).casefold())
    if 'revision' not in target_words or set(labels or ()) != {'up', 'down'}:
        return None, 'not_revision_level_contract'
    # A revision classifier may ask for the revised level OR its change. Only
    # the explicit level instruction admits the latest-estimate anchor.
    prompt = task.get('prompt')
    if not isinstance(prompt, str):
        return None, 'revised_level_not_explicit'
    level_requests = [sentence for sentence in re.split(r'[.!?;\n]', prompt.casefold())
        if re.search(r'\bpoint[ _-]+forecast\s+(?:of|for)\s+(?:the\s+)?revised\s+'
                     r'(?:value|level|estimate)\b', sentence)]
    if not level_requests or any(re.search(r'\b(?:delta|change|difference|minus|growth|percentage)\b',
                                          sentence) for sentence in level_requests):
        return None, 'revised_level_not_explicit'
    if not isinstance(unit, str) or not unit.strip():
        return None, 'missing_original_units'
    series = entity.get('series_id')
    month = entity.get('ref_month')
    if (not isinstance(series, str) or not series.strip() or not isinstance(month, str)
            or not re.fullmatch(r'\d{4}-\d{2}', month)):
        return None, 'missing_revision_identity'
    anchor = _bounded_feature(entity.get('latest_precutoff_estimate'))
    if anchor is None:
        return None, 'invalid_or_unbounded_latest_estimate'
    try:
        reference = calendar_date(month + '-01')
        vintage = calendar_date(entity.get('latest_precutoff_vintage'))
        cutoff = calendar_date(task.get('cutoff_date'))
        resolving = calendar_date(entity.get('resolving_release_date'))
    except (ContractError, ValueError):
        return None, 'invalid_revision_dates'
    if not reference <= vintage <= cutoff < resolving:
        return None, 'inconsistent_revision_dates'
    return anchor, 'input_persistence_only_revision_stage_not_inferred'


def _emergency_prediction(task, entity, kind, labels, unit):
    """Conservative, outcome-free forecast; model rows always replace this estimate."""
    row = {'entity_id': entity['entity_id']}
    target_name = str(task['target'].get('name', '')).casefold()
    if unit is not None:
        row['unit'] = unit
    if kind == 'classification':
        if not labels:
            return None
        row['label'] = next((value for value in ('no_event', 'inline', 'up')
                             if value in labels), labels[0])
        row['interval'] = {'level': .9, 'lo': 0.0, 'hi': 1.0}
        anchor, _ = _revision_anchor(task, entity, labels, unit)
        if anchor is not None:
            width = max(1.0, abs(anchor) * .5)
            row['point_forecast'] = anchor
            row['interval'] = {'level': .9, 'lo': anchor - width, 'hi': anchor + width}
            return row
        consensus = _bounded_feature(entity.get('consensus_eps'))
        if 'eps' in target_name and consensus is not None:
            # EPS classification can still score a numeric EPS interval. The
            # mounted consensus supplies the scale; this band is uncalibrated.
            width = max(1.0, abs(consensus) * .5)
            row['point_forecast'] = consensus
            row['interval'] = {'level': .9, 'lo': consensus - width, 'hi': consensus + width}
        return row
    point = 0.0
    if kind == 'ranking':
        candidates = []
        for key, value in entity.items():
            if not any(word in key.casefold() for word in ('change', 'growth', 'momentum')):
                continue
            candidate = _bounded_feature(value)
            if candidate is not None:
                candidates.append((key, candidate))
        if candidates:
            point = candidates[0][1]
    elif kind != 'regression':
        return None
    unit_name = (unit or '').casefold()
    width = (100.0 if 'bps' in unit_name or 'basis_point' in unit_name else
             20.0 if 'pct' in unit_name or 'percent' in unit_name or 'pct' in target_name else
             max(1.0, abs(point) * .5))
    row['point_forecast'] = point
    row['interval'] = {'level': .9, 'lo': point - width, 'hi': point + width}
    return row


def emergency_rows(task, index, *, missing=None):
    """Cover unresolved rows only when a real pre-cutoff citation is available."""
    kind, _, units, labels = _task_contract(task)
    needed = set(missing) if missing is not None else {e['entity_id'] for e in task['entities']}
    rows = {}
    for entity in task['entities']:
        eid = entity['entity_id']
        if eid not in needed:
            continue
        citation = _emergency_fact(task, entity, index)
        row = _emergency_prediction(task, entity, kind, labels, units[eid])
        if citation is None or row is None:
            continue
        row['claims'] = [citation]
        build_answer(dict(task, entities=[entity]), [row])
        rows[eid] = row
    return rows
