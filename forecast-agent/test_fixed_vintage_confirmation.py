"""Guard the predeclared, dated-label confirmation against silent retuning."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from fixed_vintage_confirmation import freeze_plan, run_confirmation, validate_plan


def frozen_plan():
    path = (Path(__file__).resolve().parents[1] / 'project-evidence' /
            't2-fixed-vintage-confirmation-plan-20260927-v2.json')
    return json.loads(path.read_text(encoding='utf-8'))


def test_fixed_labels_and_unresolved_cards_cannot_change():
    plan = frozen_plan()
    validate_plan(plan)
    changed = deepcopy(plan)
    next(c for c in changed['cases'] if c['unit_id'] == 't2-F4-covid-nfp-2020')['label']['value'] = 131045.0
    with pytest.raises(ValueError, match='label or card changed'):
        validate_plan(changed)
    changed = deepcopy(plan)
    next(c for c in changed['cases'] if c['unit_id'] == 't2-F1-sahm-watch-2024')['status'] = 'fixed_label_confirmation'
    with pytest.raises(ValueError, match='unresolved card changed'):
        validate_plan(changed)


def test_plan_round_trips_to_the_same_frozen_specification():
    plan = frozen_plan()
    root = Path(__file__).resolve().parents[1]
    expected = freeze_plan(Path(plan['source_repo']),
                           root / 'project-evidence' / 't2-card-trend-plan-20260927-v5.json')
    assert plan == expected


def test_hidden_score_weight_change_is_rejected_before_sampling(monkeypatch):
    changed = frozen_plan()
    next(c for c in changed['cases'] if c['unit_id'] == 't2-F4-cpi-vintage-2022')['weights_effective'][0] = 1.0
    monkeypatch.setattr('fixed_vintage_confirmation.joint_samples',
                        lambda *args, **kwargs: pytest.fail('sampling began before plan validation'))
    with pytest.raises(ValueError, match='plan contents changed'):
        run_confirmation(changed)
