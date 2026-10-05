"""Apply published scorer-5.2.2 deterministic claim rules to frozen-image outputs."""
import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--units', type=Path, required=True)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source))
    from qfbench2_track_analysis.scoring import SCORER_VERSION
    from baselines.guardrails_example.citation_rail import check_claim_rules

    assert SCORER_VERSION == '5.2.2', SCORER_VERSION
    rows = []
    for unit in sorted(args.units.iterdir()):
        if not unit.is_dir():
            continue
        answer_path = args.results / unit.name / 'answer.json'
        answer = json.loads(answer_path.read_text(encoding='utf-8'))
        findings = [asdict(item) for item in check_claim_rules(answer, unit)]
        rows.append({'unit': unit.name, 'answer_sha256': hashlib.sha256(answer_path.read_bytes()).hexdigest(),
                     'claims': sum(len(row['claims']) for row in answer['entity_predictions']),
                     'findings': findings})
    counts = Counter(f['code'] for row in rows for f in row['findings'])
    report = {'scorer': SCORER_VERSION, 'unit_count': len(rows),
              'claim_count': sum(row['claims'] for row in rows),
              'finding_counts': dict(counts), 'units': rows,
              'scope': 'Deterministic claim rules only; no NLI or prediction quality'}
    args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'scorer': SCORER_VERSION, 'unit_count': len(rows),
                      'claim_count': report['claim_count'], 'finding_counts': dict(counts)}))
    raise SystemExit(0 if not (set(counts) - {'claim_tokens_unchecked'}) else 1)


if __name__ == '__main__':
    main()
