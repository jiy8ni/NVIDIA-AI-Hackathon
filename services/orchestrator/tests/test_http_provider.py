import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from handoff.providers import HttpProvider, provider_of
from handoff.contracts import AgentError
from test_engine import records, request


def test_actual_http_retrieval_handoff(monkeypatch):
    received = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append((payload, self.headers.get('Authorization')))
            values = [r.model_dump() for r in records() if provider_of(r) in payload['sources']]
            response = {'requestId': payload['requestId'], 'status': 'ok', 'records': values, 'nextCursor': None,
                        'coverage': [{'source': 'notion', 'status': 'searched', 'recordCount': len(values)}], 'errors': []}
            data = json.dumps(response).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    monkeypatch.setenv('HANDOFF_RETRIEVAL_URL', f'http://127.0.0.1:{server.server_port}/search')
    monkeypatch.setenv('HANDOFF_RETRIEVAL_TOKEN', 'test-only-token')
    try:
        result = asyncio.run(HttpProvider().search('*', ['notion'], None, request().scope, 'request-test'))
        assert result.records and result.requestId == 'request-test'
        assert received[0][0] == {'requestId': 'request-test', 'query': '*', 'sources': ['notion'], 'cursor': None, 'scope': request().scope.model_dump()}
        assert received[0][1] == 'Bearer test-only-token'
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_retrieval_rejects_remote_plaintext(monkeypatch):
    monkeypatch.setenv('HANDOFF_RETRIEVAL_URL', 'http://example.com/search')
    with pytest.raises(ValueError):
        HttpProvider()
