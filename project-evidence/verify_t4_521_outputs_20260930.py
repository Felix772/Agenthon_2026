"""Official scorer-5.2.1 smoke verification of saved exact-image outputs; no quality score."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

SCRIPT_PATH = Path(__file__).resolve()
ROOT = SCRIPT_PATH.parents[1] if len(SCRIPT_PATH.parents) > 1 else SCRIPT_PATH.parent
sys.path.insert(0, os.environ.get('T4_SCORER_DIR', str(ROOT / '.validation/t4-scorer-5.2.1-20260930')))

from qfbench2_common.smoke import run_smoke
from qfbench2_track_analysis.scoring import build_smoke_verifier, SCORER_VERSION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--units', type=Path, required=True)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    assert SCORER_VERSION == '5.2.1'
    report = json.loads((args.results / 'report.json').read_text(encoding='utf-8'))
    assert report['passed'] and len(report['units']) == 11
    rows = []
    for unit in report['units']:
        directory = args.results / unit['unit']
        path = directory / 'answer.json'
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        verdict = run_smoke(args.units / unit['unit'], directory, build_smoke_verifier)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
        row = {'unit': unit['unit'], 'answer_sha256': before,
               'admissible': bool(verdict.admissible), 'score': verdict.score}
        rows.append(row)
        assert row['admissible'] and row['score'] is None, row
    result = {'image': report['image'], 'scorer': SCORER_VERSION,
              'passed': True, 'units': rows,
              'scope': 'Official public smoke only; prediction, NLI and reasoning quality unverified'}
    args.report.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'scorer': SCORER_VERSION, 'passed': True, 'admissible_units': len(rows)}))


if __name__ == '__main__':
    main()
