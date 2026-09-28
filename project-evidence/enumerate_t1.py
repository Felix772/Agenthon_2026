"""Inventory public task contracts without reading evaluator answers or solving units."""
import collections
import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import tomllib
from evidence_audit import audit_output

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--units', type=Path, default=ROOT / '.validation/track1-current/units')
parser.add_argument('--out', type=Path, default=ROOT / 'project-evidence')
parser.add_argument('--reviews', type=Path)
parser.add_argument('--source-ref', default='83535bd0278504f8f2bc8b717c80a307dba4f868')
parser.add_argument('--run-id', default='t1-roster-20260921')
args = parser.parse_args()
UNITS, OUT = args.units, args.out
OUT.mkdir(parents=True, exist_ok=True)
STARTED = datetime.now(timezone.utc).isoformat()
review_path = args.reviews or OUT / 't1-output-contract-review.json'
reviews = json.loads(review_path.read_text(encoding='utf-8-sig')) if review_path.exists() else {}
spec = importlib.util.spec_from_file_location('existing_evaluate', ROOT / 'qfbench-agent/tools/evaluate.py')
evaluate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluate)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write(name, value):
    path = OUT / name
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return digest(path)

records = []
for unit in sorted(p for p in UNITS.iterdir() if p.is_dir()):
    record = {'unit_id': unit.name, 'anomalies': []}
    records.append(record)
    try:
        card = tomllib.loads((unit / 'card.toml').read_text(encoding='utf-8'))
        instruction = (unit / 'instruction.md').read_text(encoding='utf-8')
        files = sorted(p.relative_to(unit).as_posix() for p in unit.rglob('*') if p.is_file())
        evaluator = [p for p in files if any(x in evaluate.EXCLUDED for x in Path(p).parts)]
        visible = [p for p in files if p not in evaluator]
        # Only participant instructions provide deliverable candidates. Never inspect checks.
        extensions = r'(?:json|csv|parquet|txt|md|npy|npz|png|pdf|py)'
        outputs = set(re.findall(r'/(?:app/)?output/([\w./-]+\.' + extensions + r')', instruction))
        section_depth = None
        evidence_lines = []
        for number, line in enumerate(instruction.splitlines(), 1):
            heading = re.match(r'^(#{1,6})\s+(.*)', line)
            if heading:
                depth, title = len(heading[1]), heading[2]
                if section_depth is not None and depth <= section_depth:
                    section_depth = None
                if re.search(r'\b(output|outputs|deliverables)\b', title, re.I):
                    section_depth = depth
            if section_depth is not None and re.match(r'^\s*(?:#+|[-*]|\d+\.|\|)', line):
                declaration = re.sub(r'^\s*(?:#+\s*|[-*]\s+|\|\s*|\d+\.\s*)', '', line)
                declaration = re.sub(r'^(?:(?:File\s+)?\d+[.:]\s*|Output:\s*)', '', declaration, flags=re.I)
                declaration = declaration.lstrip('`* ')
                names = re.findall(r'\b([\w-]+\.' + extensions + r')\b', line) if re.match(r'[\w-]+\.' + extensions + r'\b', declaration) else []
                if names:
                    outputs.update(names)
                    evidence_lines.append(number)
        outputs = sorted(outputs)
        reserved = ['reward.json', 'pytest_report.json', 'reward.txt']
        checker_outputs = [p for p in outputs if p in reserved]
        outputs = [p for p in outputs if p not in reserved]
        if not outputs:
            record['anomalies'].append('output_filename_requires_manual_instruction_review')
        if checker_outputs:
            record['anomalies'].append('instruction_mentions_evaluator_reward_artifact')
        data = [p for p in visible if p.startswith(('environment/data/', 'data/'))]
        with tempfile.TemporaryDirectory(prefix='t1-roster-') as temporary:
            staged = Path(temporary) / 'input'
            evaluate.stage_input(unit, staged)
            actual = sorted(p.relative_to(staged).as_posix() for p in staged.rglob('*') if p.is_file())
            if actual != visible:
                record['anomalies'].append('staged_input_mismatch')
            isolation = not any(any(x in evaluate.EXCLUDED for x in Path(p).parts) for p in actual)
        record.update({
            'task_id': card['task']['id'], 'category': card['metadata'].get('category'),
            'difficulty': card['metadata'].get('difficulty'), 'visibility': card['task']['split'],
            'agent_timeout_sec': card['agent']['timeout_sec'],
            'verifier_timeout_sec': card['verifier']['timeout_sec'],
            'environment': card['environment'], 'input_files': data,
            'input_formats': sorted(set(Path(p).suffix for p in data)),
            'input_shape': 'single-file' if len(data) == 1 else 'multi-file' if data else 'no-data-file',
            'input_bytes': sum((unit / p).stat().st_size for p in data),
            'expected_output_candidates': outputs, 'output_formats': sorted(set(Path(p).suffix for p in outputs)),
            'output_extraction': 'absolute instruction paths and leading filename declarations within output sections; source lines retained for review',
            'output_declaration_lines': evidence_lines,
            'evaluator_artifacts_mentioned': checker_outputs,
            'agent_visible': visible, 'evaluator_only': evaluator,
            'staging_verified': isolation and actual == visible,
            'legacy_app_paths': sorted(set(re.findall(r'/app/(?!output/)([\w./-]+)', instruction))),
            'source_hashes': {p: digest(unit / p) for p in ('card.toml', 'instruction.md')},
        })
        review = reviews.get(unit.name)
        if review and review['instruction_sha256'] == digest(unit / 'instruction.md') and review['outputs'] == outputs:
            record['output_contract_review'] = review['disposition']
            record['expected_outputs'] = outputs
        else:
            record['anomalies'].append('output_contract_review_pending')
    except Exception as exc:
        record['anomalies'].append(type(exc).__name__ + ': ' + str(exc))

manifest = {'source_ref': args.source_ref, 'denominator': len(records),
            'excluded': 0, 'units': records}
roster_hash = write('public-suite-manifest.json', manifest)
strata = collections.defaultdict(list)
for record in records:
    for key in ('category', 'input_shape', 'agent_timeout_sec'):
        strata[f'{key}:{record.get(key)}'].append(record['unit_id'])
    for key in ('input_formats', 'output_formats'):
        for value in record.get(key, []):
            strata[f'{key}:{value}'].append(record['unit_id'])
selected = set()
for members in strata.values():
    # Deterministic hash tie-break, independent of answers and success rates.
    selected.add(min(members, key=lambda name: hashlib.sha256(('20260921:' + name).encode()).hexdigest()))
selected.add(max(records, key=lambda row: row.get('input_bytes', 0))['unit_id'])
frozen = all('expected_outputs' in r for r in records)
sample_hash = write('stratified-sample.json', {'roster_sha256': roster_hash, 'seed': 20260921,
    'selection': 'one hash-selected representative per nonempty field stratum plus largest input',
    'units': sorted(selected), 'strata': dict(strata),
    'anomalous_units': [r['unit_id'] for r in records if r['anomalies']],
    'status': 'frozen' if frozen else 'provisional pending output-contract manual review'})
summary = {'task_id': 'T1-02', 'track': 'T1', 'run_id': args.run_id,
    'roster_sha256': roster_hash, 'sample_sha256': sample_hash, 'unit_count': len(records),
    'attempt_count': 0, 'solve_attempts': 0, 'score': None,
    'unscorable_reason': 'inventory only; no model or solver executed',
    'included': len(records), 'excluded': 0, 'anomalous': sum(bool(r['anomalies']) for r in records),
    'staging_verified': sum(r.get('staging_verified', False) for r in records),
    'category_counts': dict(collections.Counter(r.get('category') for r in records)),
    'timeout_counts': dict(collections.Counter(r.get('agent_timeout_sec') for r in records)),
    'sample_count': len(selected), 'model_requests': 0, 'credentials_used': False}
summary.update(started_at=STARTED, finished_at=datetime.now(timezone.utc).isoformat(),
    source_ref=manifest['source_ref'], source_dirty_identity={'enumerator_sha256': digest(Path(__file__)),
    'staging_helper_sha256': digest(ROOT / 'qfbench-agent/tools/evaluate.py')},
    toolkit_version=version('qfbench2-common'), image_id=None, registry_digest=None,
    image_reason='host inventory; no candidate container executed', model_revision=None,
    input_tokens=None, output_tokens=None, verifier_factory=None, gates=None,
    resource_contract={'output_limit_bytes': 67108864, 'execution': 'host inventory only'},
    failure_class=None, output_contracts_reviewed=sum('expected_outputs' in r for r in records),
    before_after_verification={'status': 'not_applicable', 'reason': 'no verifier executed'})
write('t1-02-run-summary.json', summary)
with tempfile.TemporaryDirectory(prefix='t1-evidence-audit-') as temporary:
    import shutil
    names = ['public-suite-manifest.json', 'stratified-sample.json', 't1-02-run-summary.json']
    for name in names:
        shutil.copyfile(OUT / name, Path(temporary) / name)
    audit = audit_output(temporary, expected=names)
    audit['credential_scope'] = 'No credentials used; no secret set supplied; scan not claimed.'
    write('t1-02-artifact-audit.json', audit)
    assert audit['passed'], 'Roster evidence artifact audit failed'
print(json.dumps(summary, indent=2))
