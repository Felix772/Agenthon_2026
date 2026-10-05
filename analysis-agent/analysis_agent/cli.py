"""Bounded analyze entry point. Failure output is explicitly non-admissible."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from .contract import validate_answer, write_answer
from .pipeline import analyze
from .retrieval import RetrievalIndex


def diagnostic(path, stage, category, started, client=None, *, reason_status=None,
               state=None, model_category=None):
    """Local opt-in numeric telemetry only; never serialize exception messages."""
    if not path:
        return
    value = {'stage': stage, 'exception_category': category,
             'elapsed_sec': time.monotonic() - started,
             'http_status': getattr(client, 'last_http_status', None),
             'requests': getattr(client, 'requests', None),
             'sends': getattr(client, 'sends', None),
             'reasons_status': reason_status,
             'input_tokens': getattr(client, 'input_tokens', None),
             'output_tokens': getattr(client, 'output_tokens', None),
             'unknown_usage_requests': getattr(client, 'unknown_usage_requests', None)}
    # Closed vocabularies and counts only. Never emit row IDs or exception/body text.
    model_categories = {'model', 'request_budget', 'deadline', 'response_size', 'truncated',
                        'empty_content', 'malformed_envelope', 'http_transient',
                        'http_permanent', 'transport'}
    value['model_error_category'] = model_category if model_category in model_categories else None
    finish = getattr(client, 'last_finish_reason', None)
    value['finish_reason'] = finish if finish in {'stop', 'length', 'content_filter',
                                                'tool_calls', 'function_call'} else None
    observation = (state or {}).get('diagnostic', {})
    phase = observation.get('prediction_stage')
    if phase in {'input_validation', 'request_capacity', 'initial_prediction', 'repair',
                 'assembly', 'optional_reasons', 'complete'}:
        value['prediction_stage'] = phase
    for field in ('unresolved_rows', 'fallback_rows', 'model_rows', 'send_budget_remaining'):
        count = observation.get(field)
        if type(count) is int and 0 <= count <= 1_000_000:
            value[field] = count
    if type(observation.get('checkpoint_complete')) is bool:
        value['checkpoint_complete'] = observation['checkpoint_complete']
    row_reasons = {'row_missing', 'row_duplicate', 'json_or_rows_invalid',
                   'no relevant pre-cutoff evidence'} | {
        'invalid_' + field for field in ('unit', 'interval', 'label', 'quote', 'excerpt',
                                         'citation', 'claim', 'nonfinite', 'point_forecast', 'schema')}
    row_reasons |= {'model_' + reason for reason in model_categories}
    counts = observation.get('row_error_counts', {})
    if isinstance(counts, dict):
        value['row_error_counts'] = {key: count for key, count in counts.items()
                                    if key in row_reasons and type(count) is int and 0 < count <= 1_000_000}
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        value.update(cpu_user_sec=usage.ru_utime, cpu_system_sec=usage.ru_stime,
                     peak_rss_bytes=usage.ru_maxrss * 1024)
    except ImportError:
        value['resource_totals_available'] = False
    try:
        Path(path).write_text(json.dumps(value, allow_nan=False)+'\n', encoding='utf-8')
    except OSError:
        pass  # Optional telemetry must never change the answer or exit outcome.


def house_client():
    # Share the already-tested participant House transport; no second budget implementation.
    path = Path(__file__).resolve().parents[2] / 'qfbench-agent/agent/model_client.py'
    spec = importlib.util.spec_from_file_location('agenthon_house_client', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ModelClient()


def failure(task_path, output, code):
    try:
        task = json.loads(Path(task_path).read_text(encoding='utf-8'))
        value = {'task_id': task.get('task_id'), 'entity_predictions': [],
                 'notes': {'status': 'unavailable', 'reason': code, 'schema_valid': False,
                           'production_faithfulness_verified': False,
                           'unresolved_entity_ids': [e.get('entity_id') for e in task.get('entities', [])]}}
    except Exception:
        value = {'notes': {'status': 'unavailable', 'reason': code, 'schema_valid': False}}
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False, allow_nan=False)
    try:
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def valid_checkpoint(task_path, corpus, output):
    """Independently recheck a complete answer before preserving it after worker failure."""
    try:
        path = Path(output)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
            return False
        task = json.loads(Path(task_path).read_text(encoding='utf-8'))
        answer = json.loads(path.read_text(encoding='utf-8'))
        validate_answer(task, answer)
        index = RetrievalIndex.load(corpus, task['cutoff_date'])
        for row in answer['entity_predictions']:
            for claim in row['claims']:
                index.validate_span(claim['doc_id'], claim['span_start'], claim['span_end'],
                                    quote=claim['claim'], entity_id=row['entity_id'])
        if 'submitted_reasons' in answer:
            from .reasons import reason_findings
            if reason_findings(task, answer, index):
                return False
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False


def bounded_checkpoint(args, deadline):
    """Include potentially expensive corpus validation in the invocation's total clock."""
    remaining = deadline - time.monotonic() - 0.25
    if remaining <= 0 or not Path(args.out).is_file():
        return False
    command = [sys.executable, '-m', 'analysis_agent.cli', 'analyze', '--task', args.task,
               '--corpus', args.corpus, '--out', args.out, '--validate-only']
    try:
        result = subprocess.run(command, timeout=remaining, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('verb', choices=['analyze'])
    parser.add_argument('--task', required=True)
    parser.add_argument('--corpus', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--timeout', type=float, default=580)
    parser.add_argument('--diagnostics', help='Optional local sanitized telemetry JSON path')
    parser.add_argument('--reasons', action='store_true', help='Opt in to one optional, validated reasoning request')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--work-deadline', type=float, help=argparse.SUPPRESS)
    parser.add_argument('--validate-only', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 600:
        parser.error('timeout must be between 1 and 600 seconds')
    if args.diagnostics and Path(args.diagnostics).resolve() in {
            Path(args.task).resolve(), Path(args.out).resolve()}:
        parser.error('diagnostics must use a separate file')
    started = time.monotonic()
    if args.validate_only:
        return 0 if valid_checkpoint(args.task, args.corpus, args.out) else 2
    if not args.worker:
        # Outputs must originate in this invocation, not a prior run of the same task.
        Path(args.out).unlink(missing_ok=True)
        deadline = started + args.timeout
        reserve = min(25.0, max(0.25, args.timeout / 10))
        work_deadline = deadline - reserve
        command = [sys.executable, '-m', 'analysis_agent.cli', 'analyze', '--task', args.task,
                   '--corpus', args.corpus, '--out', args.out, '--timeout', str(args.timeout),
                   '--worker', '--work-deadline', str(work_deadline)]
        if args.diagnostics:
            command += ['--diagnostics', args.diagnostics]
        if args.reasons:
            command.append('--reasons')
        try:
            subprocess.run(command, timeout=max(0.01, work_deadline-time.monotonic()),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            reason = 'model_or_evidence_unavailable'
        except subprocess.TimeoutExpired:
            diagnostic(args.diagnostics, 'watchdog', 'deadline', started)
            reason = 'deadline_exceeded'
        if bounded_checkpoint(args, deadline):
            return 0
        failure(args.task, args.out, reason)
        return 2
    client = None
    state = {}
    stage = 'task'
    try:
        # The normal 580-second worker reserves 25 seconds for final validation/write.
        # Short synthetic runs keep a proportional reserve; the outer watchdog is final.
        reserve = min(25.0, max(0.25, args.timeout / 10))
        deadline = args.work_deadline if args.work_deadline is not None else started + args.timeout - reserve
        task = json.loads(Path(args.task).read_text(encoding='utf-8'))
        stage = 'retrieval'
        index = RetrievalIndex.load(args.corpus, task['cutoff_date'])
        stage = 'client_configuration'
        try:
            client = house_client()
        except RuntimeError:
            client = None  # Only supported input-history estimates may replace missing House access.
        stage = 'prediction'
        answer = analyze(task, index, client, deadline,
                         checkpoint=lambda value: write_answer(task, value, args.out),
                         enable_reasons=args.reasons, state=state)
        stage = 'output'
        write_answer(task, answer, args.out)
        diagnostic(args.diagnostics, 'complete', None, started, client,
                   reason_status=state.get('reasons_status'), state=state)
        return 0
    except Exception as exc:
        # No raw prompts, model responses, endpoint credentials or exceptions in logs.
        category = type(exc).__name__
        if category not in {'ModelError', 'ContractError', 'ValueError', 'TypeError',
                            'KeyError', 'OSError', 'FileNotFoundError', 'JSONDecodeError'}:
            category = 'other'
        diagnostic(args.diagnostics, stage, category, started, client,
                   reason_status=state.get('reasons_status'), state=state,
                   model_category=getattr(exc, 'category', None))
        if valid_checkpoint(args.task, args.corpus, args.out):
            return 0
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
