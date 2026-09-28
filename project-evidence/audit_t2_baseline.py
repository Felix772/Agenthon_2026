"""Audit the official offline forecast run without inventing forecast scores."""
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import tomllib
import numpy as np
import pandas as pd
from evidence_audit import audit_output

root = Path(__file__).resolve().parents[1]
evidence = root / 'project-evidence'
unit = root / 'track2-forecasting-public/units/t2-EXAMPLE-ust-curve-1m'
staged = root / '.validation/t2-exemplar-staged'
output = evidence / 't2-01-output'
card = tomllib.loads((unit / 'card.toml').read_text())
meta = json.loads((output / 'forecast_meta.json').read_text())
forecast = pd.read_parquet(output / 'forecast.parquet')
grid = {(asset, horizon) for asset in card['targets']['asset_ids'] for horizon in card['targets']['horizons']}
assert set(forecast.columns) == {'draw', 'asset', 'horizon', 'value'}
assert not forecast.duplicated(['draw', 'asset', 'horizon']).any()
assert np.isfinite(forecast['value']).all()
assert forecast['draw'].nunique() == meta['n_draws'] == 500
assert all(set(zip(group.asset, group.horizon)) == grid for _, group in forecast.groupby('draw'))
assert meta['target'] == card['targets']['target_type']
assert (output / 'forecast_rationale.md').read_text().strip()
hash_file = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
mapping = []
for path in sorted(staged.rglob('*')):
    if path.is_file():
        rel = path.relative_to(staged)
        source = unit / (rel.name if rel.parts[0] == 'panels' else rel)
        assert hash_file(path) == hash_file(source)
        mapping.append({'staged': rel.as_posix(), 'source': source.relative_to(unit).as_posix(),
                        'sha256': hash_file(path), 'bytes': path.stat().st_size})
panel = pd.read_parquet(staged / 'panels/rates_daily.parquet')
assert pd.to_datetime(panel.date).max() <= pd.Timestamp(meta['asof'])
verdict = json.loads((evidence / 't2-01-verdict.json').read_text())
assert verdict['admissible'] and not verdict['scored']
audit = audit_output(output, expected=['forecast.parquet', 'forecast_meta.json', 'forecast_rationale.md'])
assert audit['passed'] and len(audit['files']) == 3
summary = {'run_id': 't2-official-baseline-20260921', 'track': 'T2', 'task_id': 'T2-01',
    'recorded_at': datetime.now(timezone.utc).isoformat(), 'source_ref': '4a14af5c48500091de1db54d972b3f6c7bd3ad18',
    'toolkit_version': version('qfbench2-common'), 'image_id': None, 'registry_digest': None,
    'execution': 'official CLI in C02 Linux Python 3.13 environment; no image built',
    'unit_count': 1, 'attempt_count': 1, 'seed': 0, 'draws': 500, 'rows': len(forecast),
    'grid_cells': len(grid), 'staged_input_mapping': mapping, 'output_audit': audit,
    'official_verdict': verdict, 'score': None, 'unscorable_reason': 'public exemplar has no realized targets or normalization reference',
    'model_requests': 0, 'model_revision': None, 'credentials_used': False,
    'method': 'unmodified official joint Gaussian random walk; text unused',
    'limitations': ['host CLI only; C05 candidate-container probe pending',
        'upstream Dockerfile pins toolkit v2.4.2, differing from README v2.4.4; left unchanged',
        'scorer runpy warning retained in t2-01-scorer.log', 'no forecast-quality or platform claim']}
(evidence / 't2-01-run-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps({key: summary[key] for key in ('track', 'draws', 'rows', 'grid_cells', 'toolkit_version', 'score')}))
print('All grid, finite-value, staged byte-identity, cutoff and output artifact checks passed.')
