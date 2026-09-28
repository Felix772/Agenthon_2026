"""Local evidence checks; never computes or substitutes official track scores."""
import hashlib
from pathlib import Path
import stat

LIMIT = 64 * 1024 * 1024


def audit_output(root, expected=(), secrets=(), limit=LIMIT):
    """Call after the producer has exited, on an isolated run output directory.

    Secret values stay in memory. The report contains no contents or matching
    excerpts. No supplied secrets means not_checked, not a clean-secret claim.
    """
    root = Path(root)
    secret_bytes = tuple(s.encode() if isinstance(s, str) else s for s in secrets if s)
    overlap = max((len(s) for s in secret_bytes), default=1) - 1
    report = {'passed': True, 'total_bytes': 0, 'files': [], 'errors': [],
              'credential_check': 'passed' if secret_bytes else 'not_checked',
              'credential_scopes': {scope: 'not_checked' for scope in ('stdout', 'stderr', 'artifacts')}}
    if root.is_symlink() or not root.is_dir():
        report.update(passed=False, errors=['invalid_output_root'])
        return report
    present = set()
    for path in sorted(root.rglob('*')):
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            report['errors'].append('non_regular_output')
            continue
        relative = path.relative_to(root).as_posix()
        present.add(relative)
        size = path.stat().st_size
        report['total_bytes'] += size
        if size > limit:
            report['errors'].append('file_size_limit')
        digest = hashlib.sha256()
        found = any(s in relative.encode() for s in secret_bytes)
        tail = b''
        with path.open('rb') as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                window = tail + chunk
                found |= any(s in window for s in secret_bytes)
                tail = window[-overlap:] if overlap else b''
        if found:
            report['credential_check'] = 'failed'
        scope = {'stdout.log': 'stdout', 'stderr.log': 'stderr'}.get(relative, 'artifacts')
        if secret_bytes and report['credential_scopes'][scope] != 'failed':
            report['credential_scopes'][scope] = 'failed' if found else 'passed'
        report['files'].append({'path': '[REDACTED]' if found else relative,
                                'bytes': size, 'sha256': digest.hexdigest()})
    if report['total_bytes'] > limit:
        report['errors'].append('aggregate_size_limit')
    # Do not echo an unexpected or credential-bearing expected filename.
    report['missing_deliverable_count'] = len(set(expected) - present)
    if report['missing_deliverable_count']:
        report['errors'].append('missing_deliverable')
    if report['credential_check'] == 'failed':
        report['errors'].append('credential_match')
    report['passed'] = not report['errors']
    return report


def audit_budget(attempts, input_tokens, output_tokens_per_call, now, deadline):
    """Audit observed accounting only; the runtime client owns enforcement.

    Attempts include retries and uncertain admissions conservatively. Unknown
    usage remains unknown, and cannot be called a complete budget verification.
    """
    errors = []
    if attempts < 0 or attempts > 25:
        errors.append('request_budget')
    if input_tokens is not None and input_tokens < 0:
        errors.append('invalid_input_token_usage')
    if any(value is not None and (value < 0 or value > 4000) for value in output_tokens_per_call):
        errors.append('output_token_budget')
    if now >= deadline:
        errors.append('deadline_expired')
    usage_known = input_tokens is not None and len(output_tokens_per_call) == attempts and all(
        value is not None for value in output_tokens_per_call)
    return {'status': 'failed' if errors else 'passed' if usage_known else 'unknown',
            'attempts_including_retries': attempts, 'input_tokens': input_tokens,
            'usage_known': usage_known, 'errors': errors}
