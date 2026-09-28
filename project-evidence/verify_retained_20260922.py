"""Independent artifact verification; never converts runner errors to successful runs."""
import dataclasses
import hashlib
import importlib.resources
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
track = sys.argv[1]
source = ROOT / '.validation' / ('track4-native' if track == 't4' else 'track3-current-update')
sys.path.insert(0, str(source))
if track == 't4':
    import jsonschema
    from qfbench2_track_analysis.scoring import build_smoke_verifier, build_verifier
    output = ROOT / 'project-evidence/t4-01-output'
    ctx = {'unit_dir': source / 'units/t4-EXAMPLE-eps-beat', 'output_dir': output}
    schema = json.loads((importlib.resources.files('qfbench2_common') / 'schemas/analysis.schema.json').read_text())
    jsonschema.validate(json.loads((output / 'answer.json').read_text()), schema)
    report = {'schema_pass': True, 'preview': dataclasses.asdict(build_smoke_verifier(ctx).run(ctx)),
              'production_verified': False}
    try:
        build_verifier(dict(ctx))
        report['production_factory_available'] = True
    except Exception as exc:
        report['production_factory_available'] = False
        report['production_error_type'] = type(exc).__name__
        report['production_error'] = str(exc)
    path = ROOT / 'project-evidence/t4-01-verification.json'
else:
    from qfbench2_track_simulation.scoring import build_developer_verifier
    outputs = ROOT / 'project-evidence/t3-02-extra/outputs'
    rows = []
    for output in sorted(outputs.iterdir()):
        ctx = {'unit_dir': source / 'units' / output.name, 'output_dir': output}
        row = {'unit': output.name}
        try:
            row['verdict'] = dataclasses.asdict(build_developer_verifier(ctx).run(ctx))
        except Exception as exc:
            row['error'] = str(exc)
            row['error_type'] = type(exc).__name__
        row['artifacts'] = [{'path': str(p.relative_to(output)), 'bytes': p.stat().st_size,
                             'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                            for p in sorted(output.rglob('*')) if p.is_file()]
        rows.append(row)
    report = {'records': rows, 'rankable': False, 'production_verified': False,
              'scope': 'Retained artifact verification only; original runner errors remain unresolved.'}
    path = ROOT / 'project-evidence/t3-02-retained-verification.json'
path.write_text(json.dumps(report, indent=2, default=str) + '\n')
print(json.dumps(report, indent=2, default=str))
