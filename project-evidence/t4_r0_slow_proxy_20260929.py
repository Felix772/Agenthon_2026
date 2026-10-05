"""Synthetic first-prediction slow body for immutable core-v1 watchdog verification."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time

STATS = {'sends': 0, 'drip_chunks': 0, 'natural_end': False, 'client_closed': False}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps(STATS).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        STATS['sends'] += 1
        STATS['shape_ok'] = (self.path == 'http://house.invalid/v1/chat/completions'
            and request.get('max_tokens') == 4000 and request.get('stream') is False
            and request.get('temperature') == 0 and request.get('seed') == 0
            and request.get('chat_template_kwargs') == {'enable_thinking': False})
        self.send_response(200)
        self.send_header('Content-Length', '100000')
        self.end_headers()
        started = time.monotonic()
        try:
            while time.monotonic() - started < 70:
                self.wfile.write(b' ')
                self.wfile.flush()
                STATS['drip_chunks'] += 1
                STATS['last_drip_elapsed_sec'] = time.monotonic() - started
                time.sleep(.2)
        except (BrokenPipeError, ConnectionResetError):
            STATS['client_closed'] = True
            STATS['client_closed_elapsed_sec'] = time.monotonic() - started
        else:
            STATS['natural_end'] = True

if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
