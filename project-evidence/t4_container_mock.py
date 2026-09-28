"""Internal-network synthetic protocol service, never a House model or forecast."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'ready')
    def do_POST(self):
        if self.path != '/v1/chat/completions':
            self.send_error(404); return
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        content=json.loads(data['messages'][-1]['content'])
        key=next(iter(content['excerpts']))
        rows=[{'entity_id':entity['entity_id'],'point_forecast':1,'unit':'USD','supported':True,
               'interval':{'level':0.9,'lo':0,'hi':2},'claims':[{'excerpt_id':key,
               'quote':content['excerpts'][key]['text'],'claim':'Synthetic protocol fixture only.'}]}
              for entity in content['entities']]
        result={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'entity_predictions':rows})}}],
                'usage':{'prompt_tokens':200,'completion_tokens':200}}
        body=json.dumps(result).encode()
        self.send_response(200); self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)

HTTPServer(('0.0.0.0',8080),Handler).serve_forever()
