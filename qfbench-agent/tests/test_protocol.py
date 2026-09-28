"""Synthetic transport boundaries; no House access or financial scoring."""
import http.client
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error

from agent.model_client import ModelClient, ModelError


def response(content='solution', usage=True, finish='stop', **extra):
    value = {'choices': [{'message': {'content': content, 'reasoning_content': 'private reasoning'},
                          'finish_reason': finish}], **extra}
    if usage:
        value['usage'] = {'prompt_tokens': 10, 'completion_tokens': 20}
    return io.BytesIO(json.dumps(value).encode())


class ProtocolTests(unittest.TestCase):
    def client(self):
        client = ModelClient('http://house.invalid', 'house', 'synthetic-token')
        client.opener = Mock()
        return client

    def test_cumulative_tokens_do_not_replace_request_budget(self):
        client = self.client()
        client.opener.open.side_effect = lambda *args, **kwargs: response(usage=False)
        messages = [{'role': 'user', 'content': 'x' * 50_000}]
        for _ in range(25):
            self.assertEqual(client.complete(messages, time.monotonic() + 5), 'solution')
        self.assertGreater(client.input_tokens, 1_000_000)
        self.assertEqual(client.output_tokens, 100_000)
        self.assertEqual(client.unknown_usage_requests, 25)
        self.assertTrue(all(json.loads(call.args[0].data)['max_tokens'] == 4000
                            for call in client.opener.open.call_args_list))
        with self.assertRaisesRegex(ModelError, 'request budget'):
            client.complete(messages, time.monotonic() + 5)
        self.assertEqual(client.opener.open.call_count, 25)

    def test_missing_usage_keeps_reservation_and_unknown_evidence(self):
        client = self.client()
        client.opener.open.return_value = response(usage=False)
        self.assertEqual(client.complete([], time.monotonic() + 5), 'solution')
        self.assertEqual(client.requests, 1)
        self.assertEqual(client.output_tokens, 4000)
        self.assertEqual(client.unknown_usage_requests, 1)

    def test_interrupted_body_is_classified_and_retry_is_counted(self):
        client = self.client()
        client.opener.open.side_effect = [http.client.IncompleteRead(b'partial'), response()]
        with patch('agent.model_client.time.sleep'):
            self.assertEqual(client.complete([], time.monotonic() + 5), 'solution')
        self.assertEqual(client.requests, 2)
        self.assertGreater(client.output_tokens, 20)

    def test_late_complete_response_is_not_accepted(self):
        client = self.client()
        client.opener.open.return_value = response()
        with patch('agent.model_client.time.monotonic', side_effect=[0, 3]):
            with self.assertRaisesRegex(ModelError, 'deadline'):
                client.complete([], 2)

    def test_empty_malformed_truncated_and_oversized_are_explicit(self):
        for raw in (b'', b'invalid', b'[]', b'{}', b'{"choices": []}', b'x' * 2_000_001):
            client = self.client()
            client.opener.open.return_value = io.BytesIO(raw)
            with self.subTest(kind=raw[:12]), self.assertRaises(ModelError):
                client.complete([], time.monotonic() + 5)
            self.assertEqual(client.requests, 1)
        for content, finish in (('', 'stop'), (None, 'stop'), (' ', 'stop'), ('partial', 'length')):
            client = self.client()
            client.opener.open.return_value = response(content=content, finish=finish)
            with self.assertRaises(ModelError):
                client.complete([], time.monotonic() + 5)

    def test_http_status_retry_and_admission_counts(self):
        for status in (401, 403, 400, 429, 500, 502, 503, 504):
            client = self.client()
            client.opener.open.side_effect = urllib.error.HTTPError('http://house.invalid', status, 'synthetic', {}, None)
            with patch('agent.model_client.time.sleep'), self.assertRaises(ModelError):
                client.complete([], time.monotonic() + 5)
            self.assertEqual(client.requests, 0 if status in (401, 403) else 2 if status in (429, 500, 502, 503, 504) else 1)

    def test_timeout_retry_and_exhausted_budget(self):
        for error in (TimeoutError(), urllib.error.URLError('offline')):
            client = self.client()
            client.opener.open.side_effect = error
            with patch('agent.model_client.time.sleep'), self.assertRaises(ModelError):
                client.complete([], time.monotonic() + 5)
            self.assertEqual(client.requests, 2)
        client = self.client()
        client.requests = 25
        with self.assertRaisesRegex(ModelError, 'request budget'):
            client.complete([], time.monotonic() + 5)
        client.opener.open.assert_not_called()

    def test_expired_deadline_never_sends_and_reasoning_is_not_answer(self):
        client = self.client()
        with self.assertRaisesRegex(ModelError, 'deadline'):
            client.complete([], time.monotonic() - 1)
        client.opener.open.assert_not_called()
        client.opener.open.return_value = response()
        self.assertEqual(client.complete([], time.monotonic() + 5), 'solution')
        sent = json.loads(client.opener.open.call_args.args[0].data)
        self.assertFalse(sent['stream'])
        self.assertNotIn('tools', sent)
        self.assertEqual(sent['chat_template_kwargs'], {'enable_thinking': False})

    def test_solver_watchdog_bounds_slow_response_body(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from test_agent import make_task

        class SlowHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200)
                self.send_header('Content-Length', '1000000')
                self.end_headers()
                for _ in range(30):
                    try:
                        self.wfile.write(b' ')
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        break
                    time.sleep(0.2)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), SlowHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                make_task(root / 'task', timeout=2)
                env = {**os.environ, 'MODEL_ENDPOINT': f'http://127.0.0.1:{server.server_port}',
                       'MODEL_NAME': 'synthetic', 'MODEL_TOKEN': 'synthetic-token',
                       'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'}
                started = time.monotonic()
                result = subprocess.run([sys.executable, '-m', 'agent', 'solve', '--task-dir',
                    str(root / 'task'), '--out', str(root / 'out')], env=env,
                    capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 124)
                self.assertLess(time.monotonic() - started, 4)
                self.assertNotIn(b'synthetic-token', result.stdout + result.stderr)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
