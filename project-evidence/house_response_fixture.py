"""Synthetic response matrix server; records only allowlisted protocol metadata."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time
from urllib.parse import urlsplit

STATS = {}


def envelope(request, case, ordinal):
    content = json.loads(request['messages'][-1]['content'])
    key, excerpt = next(iter(content['excerpts'].items()))
    rows = [{'entity_id': e['entity_id'], 'point_forecast': 1, 'unit': 'USD',
             'supported': True, 'interval': {'level': .9, 'lo': 0, 'hi': 2},
             'claims': [{'excerpt_id': key, 'quote': excerpt['text'],
                         'claim': 'Synthetic protocol fixture only.'}]}
            for e in content['entities']]
    if case == 'missing': rows.pop()
    if case == 'duplicate': rows.append(rows[0])
    if case == 'unit': rows[0]['unit'] = 'bps'
    if case == 'quote': rows[0]['claims'][0]['quote'] = 'Fabricated absent quote.'
    if case == 'unsupported': rows[0]['supported'] = False
    text = json.dumps({'entity_predictions': rows})
    if case == 'fenced': text = '```json\n' + text + '\n```'
    if case == 'prefixed' or (case == 'thinking_default' and
            request.get('chat_template_kwargs') != {'enable_thinking': False}):
        text = '<think>Synthetic reasoning.</think>\n' + text
    if case == 'empty': text = ''
    if case == 'repair' and ordinal == 1: text = 'invalid JSON'
    if case == 'malformed': return {'choices': []}
    if case == 'nonobject': return []
    message = {'content': text}
    if case == 'separated': message['reasoning_content'] = 'Synthetic reasoning.'
    return {'choices': [{'finish_reason': 'length' if case == 'truncated' else 'stop',
                         'message': message}],
            'usage': {'prompt_tokens': 200, 'completion_tokens': 200}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_GET(self):
        body = json.dumps(STATS).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        case = request['model']
        stat = STATS.setdefault(case, {'sends': 0, 'shape_ok': True, 'thinking_off': []})
        stat['sends'] += 1
        # Absolute-form proves urllib reached the injected proxy.
        stat['shape_ok'] &= (self.path == 'http://house.invalid/v1/chat/completions' and
            self.headers.get('Authorization') == 'Bearer synthetic-not-a-credential' and
            request.get('max_tokens') == 4000 and request.get('stream') is False and
            request.get('seed') == 0 and 'tools' not in request)
        stat['thinking_off'].append(request.get('chat_template_kwargs') == {'enable_thinking': False})
        code = int(case[4:]) if case.startswith('http') else 200
        if case in ('recover429', 'recover503') and stat['sends'] == 1: code = int(case[-3:])
        if code != 200:
            self.send_response(code); self.send_header('Content-Length', '0'); self.end_headers(); return
        body = json.dumps(envelope(request, case, stat['sends'])).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            if case == 'slow':
                for byte in body:
                    self.wfile.write(bytes([byte])); self.wfile.flush(); time.sleep(.2)
            else: self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError): pass


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
