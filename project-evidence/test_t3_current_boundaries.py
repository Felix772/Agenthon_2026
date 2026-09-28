"""Synthetic checks of the isolated official T3 update, never participant performance."""
import json
import math
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parents[1] / '.validation' / 'track3-current-update'
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(SOURCE / 'tests'))

import pytest
from qfbench2_track_simulation import domain, host_metrics, scoring
import test_scoring_gate as fixtures


@pytest.mark.parametrize('markets', [1, 8])
@pytest.mark.parametrize('position,accepted', [('below', True), ('at', True), ('above', False)])
def test_exact_development_boundary(markets, position, accepted):
    ceiling = domain.DEV_PLAUSIBILITY_CEILING_PER_MARKET * markets
    rate = {'below': math.nextafter(ceiling, -math.inf), 'at': ceiling,
            'above': math.nextafter(ceiling, math.inf)}[position]
    reason = host_metrics.implausible_self_report(None, 'synthetic', rate, markets,
        ceiling_per_market=domain.DEV_PLAUSIBILITY_CEILING_PER_MARKET)
    assert (reason is None) is accepted
    assert domain.MAX_PER_MARKET_EVENTS_PER_SEC == 1e7
    assert domain.DEV_PLAUSIBILITY_CEILING_PER_MARKET == 1e9


def test_final_host_rate_ignores_claim_above_development_ceiling(tmp_path):
    ctx = fixtures.official_ctx(tmp_path)
    path = Path(ctx['output_dir']) / 'events.json'
    payload = json.loads(path.read_text())
    payload['events_per_sec'] = 1e11
    payload['wall_clock_sec'] = payload['n_events'] / 1e11
    path.write_text(json.dumps(payload))
    verdict = scoring.build_verifier(ctx).run(ctx)
    assert verdict.admissible, verdict.gate_results
    assert verdict.detail['rankable'] is True
    assert verdict.detail['profile'] == 'official'
    assert verdict.score == 400.0


def test_development_rejects_large_consistent_self_report(tmp_path):
    ctx = fixtures.developer_ctx(tmp_path)
    path = Path(ctx['output_dir']) / 'events.json'
    payload = json.loads(path.read_text())
    payload['events_per_sec'] = 1e11
    payload['wall_clock_sec'] = payload['n_events'] / 1e11
    path.write_text(json.dumps(payload))
    verdict = scoring.build_developer_verifier(ctx).run(ctx)
    assert not verdict.admissible
    # The shared verifier returns before score metadata on a failed gate.
    # Successful developer verdicts carry rankable=False (official suite).
    assert verdict.score is None
    assert any('plausibility ceiling' in str(gate.detail)
               for gate in verdict.gate_results.values() if not gate.passed)
