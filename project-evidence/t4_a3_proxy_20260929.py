"""Four-case synthetic House fixture for optional reasons; never predicts real outcomes."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time

STATS = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, value):
        payload = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self.reply(STATS)

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        case = request['model']
        stat = STATS.setdefault(case, {'sends': 0, 'prediction_sends': 0, 'reason_sends': 0,
                                      'shape_ok': True, 'entity_requests': [], 'drip_chunks': 0})
        stat['sends'] += 1
        stat['shape_ok'] &= (
            self.path == 'http://house.invalid/v1/chat/completions'
            and self.headers.get('Authorization') == 'Bearer synthetic-not-a-credential'
            and request.get('max_tokens') == 4000 and request.get('stream') is False
            and request.get('temperature') == 0 and request.get('seed') == 0
            and request.get('chat_template_kwargs') == {'enable_thinking': False}
            and 'tools' not in request)
        content = json.loads(request['messages'][-1]['content'])
        if content.get('mode') == 'submitted_reasons':
            stat['reason_sends'] += 1
            stat['frozen_answer'] = content['final_answer']
            if case == 'slow':
                # Bytes keep arriving, defeating an inactivity-only socket timeout.
                # The participant's process watchdog must preserve its saved base.
                self.send_response(200)
                self.send_header('Content-Length', '100000')
                self.end_headers()
                stat['reason_started_monotonic'] = time.monotonic()
                stat['drip_completed_naturally'] = False
                until = stat['reason_started_monotonic'] + 70
                try:
                    while time.monotonic() < until:
                        self.wfile.write(b' ')
                        self.wfile.flush()
                        stat['drip_chunks'] += 1
                        stat['last_drip_elapsed_sec'] = time.monotonic() - stat['reason_started_monotonic']
                        time.sleep(.2)
                except (BrokenPipeError, ConnectionResetError):
                    stat['client_closed'] = True
                    stat['client_closed_elapsed_sec'] = time.monotonic() - stat['reason_started_monotonic']
                else:
                    stat['drip_completed_naturally'] = True
                return
            key, excerpt = next(iter(content['excerpts'].items()))
            quote = excerpt['text'][:140]
            entity = excerpt['entity_ids'][0]
            value = {'submitted_reasons': [{
                'reason_id': 'synthetic-r1', 'premise': quote,
                'mechanism': ('Compare against the leaderboard.' if case == 'invalid' else
                              'The cited operating context provides information about future earnings.'),
                'answer_implication': 'This synthetic explanation accompanies the frozen prediction for ' + entity + '.',
                'citations': [{'excerpt_id': key, 'quote': quote}]}]}
        else:
            stat['prediction_sends'] += 1
            rows, target = [], content['target']
            for entity in content['entities']:
                eid = entity['entity_id']
                stat['entity_requests'].append(eid)
                key, excerpt = next((key, excerpt) for key, excerpt in content['excerpts'].items()
                                    if eid in excerpt.get('entity_ids', []))
                row = {'entity_id': eid, 'point_forecast': 0,
                       'interval': {'level': .9, 'lo': -1, 'hi': 1}, 'supported': False,
                       'claims': [{'excerpt_id': key, 'quote': excerpt['text'][:140]}]}
                if target['type'] == 'classification':
                    row['label'] = target['labels'][0]
                unit = entity.get('unit', entity.get('units', target.get('unit', target.get('units'))))
                if unit is not None:
                    row['unit'] = unit
                rows.append(row)
            value = {'entity_predictions': rows}
        self.reply({'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps(value, ensure_ascii=False)}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 100}})


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
