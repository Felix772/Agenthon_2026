"""Write dated evidence corrections while retaining historical test logs."""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
E = ROOT / 'project-evidence'
correction = '''
## 2026-09-22 correction: T4 integrity resolved locally

The earlier attribution to upstream was incorrect. Windows Git archive inherited
core.autocrlf and converted JSON bytes even in the snapshot named gitbytes.
An independent archive with per-command core.autocrlf=false at official
2b307560c8183905a030dcb4cd26ce857a039cfd matches all three manifest hashes.
No official manifest or data was edited or resealed. All 28 selected T4 contract
tests now pass. See t4-native-hash-check.json and t4-native-contract-tests.log.
Earlier failing logs remain historical evidence of the local conversion error.
Missing production judge configuration is a separate, still-open requirement.
'''
for name in ['source-conflicts.md', 'toolkit-contract-matrix.md']:
    path = E / name
    value = path.read_text(encoding='utf-8-sig')
    value = value.replace('Track as upstream integrity gap;', 'Historical diagnosis superseded by the 2026-09-22 correction below;')
    value = value.replace('This is an unresolved public-source integrity issue distinct from the missing production judge.', 'This attribution was incorrect; see the dated local CRLF correction below. The production judge remains unavailable.')
    if '## 2026-09-22 correction: T4 integrity resolved locally' not in value:
        value += correction
    path.write_text(value, encoding='utf-8')
summary = {
    'scope': 'Current House request limits; local synthetic verification only',
    'source_refs': json.loads((E / 'house-budget-source-recheck.json').read_text(encoding='utf-8-sig')),
    'image': 'qfbench-agent:house-budget-20260922',
    'image_id': 'sha256:cfade5cc601048069aca618a9184aa795c65402f31c3ee6607bfca344b18148c',
    'retained_previous_image': 'qfbench-agent:pre-house-budget-20260922',
    'request_limit': 25, 'output_tokens_per_request': 4000,
    'cumulative_token_limit': None, 'agent_tests_passed': 31, 'audit_tests_passed': 9,
    'synthetic_selftest': json.loads((ROOT / 'qfbench-agent/artifacts/selftest-889a544b38/summary.json').read_text()),
    'real_house_verified': False, 'chatgpt_review': 'pending connection repair',
    'limitations': ['No finance-quality measurement', 'No platform validation', 'Actual host RAM about 7.5 GiB']}
(E / 'house-budget-run-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
status_path = ROOT / 'planning/task-status.json'
status = json.loads(status_path.read_text(encoding='utf-8-sig'))
changes = {
    'C02': ('active', ['t4-native-hash-check.json', 't4-native-contract-tests.log'], 'All selected local contracts now pass. T4 28/28 after disabling archive CRLF conversion; previous upstream attribution corrected. ChatGPT review pending.'),
    'T3-02': ('active', ['t3-02-single-regression.log', 't3-02-semantic-tests.log', 't3-02-extra/report.json'], 'Full 65-scenario regression running; 59 semantic boundary tests pass. Six batch runner permission errors retained; independent retained-artifact verification pending. No performance claim.'),
    'T4-01': ('active', ['t4-01-verification.json', 't4-01-preview.log', 't4-01-baseline.log'], 'Minimal official baseline schema and all preview gates pass. Lexical faithfulness=0 and not gated; no outcome score. Production factory refuses missing judge spec. Local execution complete; review pending.')}
for task in status['tasks']:
    if task['id'] in changes:
        state, files, note = changes[task['id']]
        task.update(status=state, note=note)
        for file in files:
            path = 'project-evidence/' + file
            if path not in task['evidence']:
                task['evidence'].append(path)
    if task['id'] in ['C03', 'T1-03']:
        path = 'project-evidence/house-budget-run-summary.json'
        if path not in task['evidence']:
            task['evidence'].append(path)
        task['note'] += ' Current cumulative-cap removal passes 31 agent and 9 audit tests plus synthetic container selftest; new increment review pending.'
status['validation_dimensions']['T4'].update(preview_schema_pass=True, preview_citation_pass=True, production_judge_available=False, production_faithfulness_pass=None)
status['execution_state'].update(status='local-execution-review-pending', next_task='Finish T3-02; review queued local increments after connection restoration', reason='House budget corrected; T4 integrity local CRLF diagnosis corrected; T4 minimal baseline preview completed. T3 full regression in progress. No new ChatGPT review claimed.')
status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
