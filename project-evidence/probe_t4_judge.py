"""Read-only production-judge readiness probe, independent of participant inference."""
import importlib.metadata
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.validation/track4-20260923'))
from qfbench2_track_analysis.judge_factory import load_judge_spec, build_production_judge
from qfbench2_track_analysis.codes import T4OrganizerFault

os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_HUB_OFFLINE'] = '1'
versions = {}
for name in ('qfbench2-common', 'torch', 'transformers'):
    try: versions[name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError: versions[name] = None
report = {'environment': 'isolated evaluator linux-t234', 'dependencies': versions,
          'configured': {k: bool(os.environ.get(k)) for k in
                         ('QFBENCH2_T4_JUDGE_SPEC', 'QFBENCH2_T4_MODEL_CACHE')},
          'network_downloads_permitted': False, 'model_ids': None, 'model_revisions': None,
          'production_judge_available': False, 'production_faithfulness_pass': None}
try:
    spec = load_judge_spec()
    report.update(model_ids=list(spec.model_ids), model_revisions=dict(spec.model_revisions),
                  tokenizer_digest=spec.tokenizer_digest, cache_tree_digest=spec.cache_tree_digest)
    judge, provenance = build_production_judge(spec)
    report['production_judge_available'] = True
    report['provenance'] = provenance.to_mapping()
except T4OrganizerFault as exc:
    report['status'] = 'blocked-external'
    report['error_type'] = type(exc).__name__
    report['reason'] = str(exc)
(ROOT / 'project-evidence/t4-05a-readiness.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps(report, indent=2))
