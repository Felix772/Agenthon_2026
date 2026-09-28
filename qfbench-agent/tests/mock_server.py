"""Test-only scripted endpoint. Not a model and not included in the agent image."""
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import os
from urllib.parse import urlsplit


@contextmanager
def mock_model(responses, proxy=False):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"path": self.path, "payload": payload, "authorization": self.headers.get("Authorization"),
                             "proxy_authorization": self.headers.get("Proxy-Authorization")})
            if (urlsplit(self.path).path if proxy else self.path) != "/v1/chat/completions":
                self.send_error(403)
                return
            if self.headers.get("Authorization") != "Bearer synthetic-test-token":
                self.send_error(401)
                return
            item = responses[min(len(requests) - 1, len(responses) - 1)]
            status, body = item if isinstance(item, tuple) else (200, {
                "choices": [{"message": {"content": item}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            })
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode("utf-8"))

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch.dict(os.environ, {"MODEL_TOKEN": "synthetic-test-token", "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}):
            yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
