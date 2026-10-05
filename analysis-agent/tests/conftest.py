"""Use an explicitly current, external evaluator; never import the stale working checkout."""
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
T4_SOURCE = Path(os.environ.get('QFBENCH_T4_SOURCE', ROOT / '.validation/t4-e2-scorer-5.2.2-20261001'))
T4_UNITS = Path(os.environ.get('QFBENCH_T4_UNITS', T4_SOURCE / 'units'))
sys.path[:0] = [str(ROOT / 'analysis-agent'), str(T4_SOURCE)]


def pytest_sessionstart(session):
    if not (T4_SOURCE / 'qfbench2_track_analysis/scoring.py').is_file():
        raise pytest.UsageError('Set QFBENCH_T4_SOURCE to the pinned Track 4 5.2.2 evaluator snapshot.')
    from qfbench2_track_analysis.scoring import SCORER_VERSION
    if SCORER_VERSION != '5.2.2':
        raise pytest.UsageError('Track 4 tests require scorer 5.2.2, not an older evaluator.')
    if not list(T4_UNITS.glob('*/task.json')):
        raise pytest.UsageError('Set QFBENCH_T4_UNITS to the current LF-preserving public fixtures.')
