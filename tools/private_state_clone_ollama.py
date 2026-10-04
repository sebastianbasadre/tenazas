"""Loopback-only deterministic model dependency for the offline staging clone."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Never log prompts, headers or credentials.

    def respond(self, data):
        payload = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self.respond({'ok': True, 'result': []} if self.path.startswith('/bot')
                     else {'models': [{'name': 'staging-fixture', 'size': 0}]})

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        if length > 1_000_000:
            self.send_error(413)
            return
        payload = self.rfile.read(length)
        if self.path.startswith('/bot'):
            self.respond({'ok': True, 'result': {'message_id': 1}})
            return
        body = json.loads(payload or b'{}')
        if self.path == '/api/embed':
            inputs = body.get('input', [''])
            self.respond({'embeddings': [[1.0] + [0.0] * 7 for _ in (inputs if isinstance(inputs, list) else [inputs])]})
        elif self.path == '/api/embeddings':
            self.respond({'embedding': [1.0] + [0.0] * 7})
        else:
            self.respond({'response': 'Synthetic staging response', 'done': True,
                          'message': {'role': 'assistant', 'content': 'Synthetic staging response'}})


if __name__ == '__main__':
    assert os.environ.get('TENAZAS_FULL_CLONE') == 'isolated-staging'
    threading.Thread(target=ThreadingHTTPServer(('127.0.0.1', 10800), Handler).serve_forever,
                     daemon=True).start()
    ThreadingHTTPServer(('127.0.0.1', 11434), Handler).serve_forever()
