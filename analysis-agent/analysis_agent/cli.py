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

from .contract import write_answer
from .pipeline import analyze
from .retrieval import RetrievalIndex


def diagnostic(path, stage, category, started, client=None):
    """Local opt-in numeric telemetry only; never serialize exception messages."""
    if not path:
        return
    value = {'stage': stage, 'exception_category': category,
             'elapsed_sec': time.monotonic() - started,
             'http_status': getattr(client, 'last_http_status', None),
             'requests': getattr(client, 'requests', None),
             'input_tokens': getattr(client, 'input_tokens', None),
             'output_tokens': getattr(client, 'output_tokens', None),
             'unknown_usage_requests': getattr(client, 'unknown_usage_requests', None)}
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
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('verb', choices=['analyze'])
    parser.add_argument('--task', required=True)
    parser.add_argument('--corpus', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--timeout', type=float, default=580)
    parser.add_argument('--diagnostics', help='Optional local sanitized telemetry JSON path')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 600:
        parser.error('timeout must be between 1 and 600 seconds')
    if args.diagnostics and Path(args.diagnostics).resolve() in {
            Path(args.task).resolve(), Path(args.out).resolve()}:
        parser.error('diagnostics must use a separate file')
    started = time.monotonic()
    if not args.worker:
        command = [sys.executable, '-m', 'analysis_agent.cli', 'analyze', '--task', args.task,
                   '--corpus', args.corpus, '--out', args.out, '--timeout', str(args.timeout), '--worker']
        if args.diagnostics:
            command += ['--diagnostics', args.diagnostics]
        try:
            result = subprocess.run(command, timeout=args.timeout, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            if result.returncode == 0:
                return 0
            failure(args.task, args.out, 'model_or_evidence_unavailable')
        except subprocess.TimeoutExpired:
            failure(args.task, args.out, 'deadline_exceeded')
            diagnostic(args.diagnostics, 'watchdog', 'deadline', started)
        return 2
    client = None
    stage = 'task'
    try:
        deadline = time.monotonic()+args.timeout-0.5
        task = json.loads(Path(args.task).read_text(encoding='utf-8'))
        stage = 'client_configuration'
        client = house_client()
        stage = 'retrieval'
        index = RetrievalIndex.load(args.corpus, task['cutoff_date'])
        stage = 'prediction'
        answer = analyze(task, index, client, deadline)
        stage = 'output'
        write_answer(task, answer, args.out)
        diagnostic(args.diagnostics, 'complete', None, started, client)
        return 0
    except Exception as exc:
        # No raw prompts, model responses, endpoint credentials or exceptions in logs.
        category = type(exc).__name__
        if category not in {'ModelError', 'ContractError', 'ValueError', 'TypeError',
                            'KeyError', 'OSError', 'FileNotFoundError', 'JSONDecodeError'}:
            category = 'other'
        diagnostic(args.diagnostics, stage, category, started, client)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
