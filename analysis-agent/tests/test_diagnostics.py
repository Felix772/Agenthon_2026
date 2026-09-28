"""Opt-in diagnostics cannot publish sensitive response or exception text."""
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'analysis-agent'))
from analysis_agent.cli import diagnostic, main


def test_diagnostic_has_only_sanitized_allowlist(tmp_path):
    path = tmp_path/'diagnostics.json'
    client = SimpleNamespace(last_http_status=403, requests=0, input_tokens=0,
        output_tokens=0, unknown_usage_requests=0, token='SECRET_TOKEN',
        model='SECRET_MODEL', url='SECRET_ENDPOINT', response='SECRET_RESPONSE')
    diagnostic(path, 'prediction', 'ModelError', time.monotonic(), client)
    raw = path.read_text()
    assert 'SECRET' not in raw
    value = json.loads(raw)
    assert value['http_status'] == 403 and value['requests'] == 0
    assert value['exception_category'] == 'ModelError'
    assert set(value) <= {'stage', 'exception_category', 'elapsed_sec', 'http_status',
        'requests', 'input_tokens', 'output_tokens', 'unknown_usage_requests',
        'cpu_user_sec', 'cpu_system_sec', 'peak_rss_bytes', 'resource_totals_available'}


def test_disabled_or_unwritable_diagnostics_do_not_raise(tmp_path):
    diagnostic(None, 'task', None, time.monotonic())
    diagnostic(tmp_path/'absent'/'x.json', 'task', None, time.monotonic())


def test_worker_does_not_serialize_exception_message(tmp_path):
    task = tmp_path/'task.json'; task.write_text('{}')
    report = tmp_path/'diagnostics.json'
    with patch('analysis_agent.cli.house_client', side_effect=ValueError('SECRET_TOKEN RESPONSE CORPUS')):
        result = main(['analyze', '--task', str(task), '--corpus', str(tmp_path),
            '--out', str(tmp_path/'answer.json'), '--worker', '--diagnostics', str(report)])
    assert result == 2
    assert 'SECRET' not in report.read_text()
    assert json.loads(report.read_text())['stage'] == 'client_configuration'
