"""External Linux smoke verification of saved exact-image outputs; no quality score."""
import argparse
import hashlib
import json
from pathlib import Path

from qfbench2_common.smoke import run_smoke
from qfbench2_track_analysis.scoring import build_smoke_verifier, SCORER_VERSION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--units', type=Path, required=True)
    parser.add_argument('--results', type=Path, required=True)
    args = parser.parse_args()
    assert SCORER_VERSION == '5.2.0'
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
    print(json.dumps({'image': report['image'], 'scorer': SCORER_VERSION,
                      'passed': True, 'units': rows,
                      'scope': 'External official smoke only; prediction, NLI and reasoning quality unverified'}, indent=2))


if __name__ == '__main__':
    main()
