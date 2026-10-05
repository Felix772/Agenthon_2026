"""Small, explicit history adapters; absence of a supported series means no fallback."""
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
