"""Synthetic House proxy for public T4 interface checks; no outcome data."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json


STATS = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        payload = json.dumps(STATS).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        model = request['model']
        stat = STATS.setdefault(model, {'sends': 0, 'shape_ok': True, 'thinking_off': []})
        stat['sends'] += 1
        off = request.get('chat_template_kwargs') == {'enable_thinking': False}
        stat['thinking_off'].append(off)
        stat['shape_ok'] &= (
            self.path == 'http://house.invalid/v1/chat/completions'
            and self.headers.get('Authorization') == 'Bearer synthetic-not-a-credential'
            and request.get('max_tokens') == 4000
            and request.get('stream') is False
            and request.get('seed') == 0
            and 'tools' not in request
        )
        content = json.loads(request['messages'][-1]['content'])
        excerpt_id, excerpt = next(iter(content['excerpts'].items()))
        target = content['target']
        rows = []
        for entity in content['entities']:
            row = {
                'entity_id': entity['entity_id'],
                'point_forecast': 0,
                'interval': {'level': 0.9, 'lo': -1, 'hi': 1},
                'supported': True,
                'claims': [{
                    'excerpt_id': excerpt_id,
                    'quote': excerpt['text'],
                    'claim': 'Synthetic interface check only.',
                }],
            }
            if target['type'] == 'classification':
                row['label'] = target['labels'][0]
            unit = entity.get('unit', entity.get('units',
                target.get('unit', target.get('units'))))
            if unit is not None:
                row['unit'] = unit
            rows.append(row)
        response_text = json.dumps({'entity_predictions': rows}, ensure_ascii=False)
        if model.startswith('thinking-') and not off:
            response_text = '<think>Synthetic reasoning.</think>\n' + response_text
        payload = json.dumps({
            'choices': [{'finish_reason': 'stop', 'message': {'content': response_text}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 100},
        }, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
