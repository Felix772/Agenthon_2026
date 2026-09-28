"""Development-only scripted endpoint serving a synthetic test program.

This is not inference and contains no public finance-task solutions.
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--response", type=Path, required=True)
    args = parser.parse_args()
    content = args.response.read_text(encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.headers.get("Authorization") != "Bearer synthetic-test-token":
                self.send_error(401)
                return
            if self.path != "/v1/chat/completions" or request.get("model") != "synthetic-mock":
                self.send_error(400)
                return
            if not 0 < request.get("max_tokens", 0) <= 4000 or request.get("tools"):
                self.send_error(400)
                return
            payload = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 100, "completion_tokens": 100}}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, *args):
            pass

    print("Synthetic mock endpoint ready", flush=True)
    HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()


if __name__ == "__main__":
    main()
