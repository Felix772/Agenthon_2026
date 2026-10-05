"""Bind the current public rules, frozen T4 image, and local restage checks."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'project-evidence/t14-experiments/R0_T4/restage-521-20260930/audit.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repository(name, extra=()):
    folder = ROOT / name
    head = subprocess.check_output(['git', '-C', str(folder), 'rev-parse', 'HEAD'], text=True).strip()
    files = ('README.md', 'AGENTS.md', 'CONTRIBUTING.md', 'SUBMISSION_CLI.md') + tuple(extra)
    return {'head': head, 'consulted_files': {name: sha(folder / name) for name in files if (folder / name).is_file()}}


def reference(relative):
    path = ROOT / relative
    return {'path': relative, 'sha256': sha(path)}


def main():
    assert not DEST.exists(), 'refusing to overwrite restage audit'
    shape = json.loads((ROOT / 'project-evidence/t4-r0-521-shapes-20260930-v1/report.json').read_text())
    smoke = json.loads((ROOT / 'project-evidence/t4-r0-521-shapes-20260930-v1/official-smoke-521.json').read_text())
    claims = json.loads((ROOT / 'project-evidence/t4-r0-521-shapes-20260930-v1/claim-rules-522.json').read_text())
    package = json.loads((DEST.parent / 'package-251-validation.json').read_text())
    registry = json.loads((DEST.parent / 'anonymous-registry.json').read_text())
    assert shape['passed'] and len(shape['units']) == 11 and shape['total_sends'] == 29
    assert smoke['passed'] and all(row['admissible'] and row['score'] is None for row in smoke['units'])
    assert claims['unit_count'] == 11 and claims['claim_count'] == 78
    assert set(claims['finding_counts']) <= {'claim_tokens_unchecked'}
    assert package['passed'] and package['toolkit_version'] == '2.5.1'
    assert package['image_digest'] == shape['image'] == registry['reference_digest']
    assert registry['config_platform'] == 'linux/amd64' and len(registry['layer_checks']) == 13
    data = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'purpose': 'T4 Development 5.2.1 restage decision, not a quality claim',
        'official_deployment': {
            'scorer': '5.2.1', 'effective_utc': '2026-09-30T06:07:00Z',
            'notice': 'https://github.com/Agenthon-2026/track4-analysis-public/issues/2#issuecomment-5906065544',
            'future_published_scorer': '5.2.2',
            'future_notice': 'https://github.com/Agenthon-2026/track4-analysis-public/issues/2#issuecomment-5914254721',
            'future_development_switch_announced': False,
        },
        'repositories': {
            'shared': repository('Agenthon2026-public', (
                'docs/DEVELOPMENT-RUNTIME.md', 'docs/HOUSE-MODEL.md',
                'starter-packs/track1/AGENTS.md', 'starter-packs/track4/AGENTS.md',
                'starter-packs/track4/SUBMISSION-DESCRIPTOR.md')),
            'track1': repository('track1-coding-public'),
            'track2': repository('track2-forecasting-public'),
            'track3': repository('track3-simulation-public'),
            'track4': repository('track4-analysis-public', (
                'docs/ARTIFACT-POLICY.md', 'docs/TRAINING-POLICY.md',
                'baselines/guardrails_example/README.md')),
        },
        'candidate': {
            'image_digest': shape['image'], 'zip_sha256': package['archive_sha256'],
            'zip_bytes': package['archive_size_bytes'],
            'previous_submission': 952504,
            'previous_result': '0 of 10 scored; 10 container_crashed under earlier scorer 3.1.0; exact cause unavailable',
            'unchanged_participant_image': True,
        },
        'local_checks': {
            'shape_units': len(shape['units']),
            'complete_entity_rows': sum(row['prediction_count'] for row in shape['units']),
            'synthetic_house_sends': shape['total_sends'],
            'smoke_521_admissible': sum(row['admissible'] for row in smoke['units']),
            'smoke_scores': None,
            'published_522_claims_checked': claims['claim_count'],
            'published_522_false_claim_findings': 0,
            'judge_tokenizer_available': False,
            'maximum_synthetic_claim_chars': 160,
            'package_valid_under_toolkit_251': True,
            'anonymous_registry_layers_first_byte_read': len(registry['layer_checks']),
        },
        'platform_snapshot': {
            'competition': 17768, 'account': 'felix772', 'phase': 'Development',
            'daily_uploads_used': 1, 'daily_uploads_max': 5,
            'total_uploads_used': 2, 'total_uploads_max': 20,
            'observed_at_utc_approx': '2026-09-30T16:24:00Z',
        },
        'limitations': [
            'Synthetic House responses do not establish live model compatibility, latency, NLI, or prediction quality.',
            'Public practice units are format exemplars, not a representative hidden-task sample.',
            'The existing ZIP was packed with toolkit 2.5.0; it validates under 2.5.1 but a live Team Key was not re-entered or independently derived.',
            'The 5.2.1 scorer is deployed; 5.2.2 is published but its Development deployment has not been announced.',
            'A second T4 upload requires separate approval for this exact ZIP.',
        ],
        'evidence': [reference(p) for p in (
            'project-evidence/t4-r0-521-shapes-20260930-v1/report.json',
            'project-evidence/t4-r0-521-shapes-20260930-v1/official-smoke-521.json',
            'project-evidence/t4-r0-521-shapes-20260930-v1/claim-rules-522.json',
            'project-evidence/t14-experiments/R0_T4/restage-521-20260930/package-251-validation.json',
            'project-evidence/t14-experiments/R0_T4/restage-521-20260930/anonymous-registry.json',
        )],
    }
    DEST.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    print(f'{DEST}: SHA-256 {sha(DEST)}')


if __name__ == '__main__':
    main()
