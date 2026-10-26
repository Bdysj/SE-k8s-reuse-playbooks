"""Exercise local HTTP transport without model calls or real credentials."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from .client import request


class TransportChecks(unittest.TestCase):
    def setUp(self):
        self.calls = []
        calls = self.calls
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.handle_request()
            def do_POST(self):
                self.handle_request()
            def handle_request(self):
                content = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                calls.append((self.command, self.path, content, self.headers.get('Authorization')))
                response = {'data': []} if self.command == 'GET' else {'model': 'gpt-6-sol', 'status': 'completed'}
                raw = json.dumps(response).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'
        self.temporary = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.temporary.cleanup()

    def test_discovery_and_post_endpoints_share_one_authenticated_transport(self):
        with patch.dict(os.environ, {'DDN_LLM_API_KEY': 'local-test-secret'}):
            for endpoint in ('models', 'responses', 'chat/completions'):
                output = Path(self.temporary.name) / endpoint.replace('/', '-')
                response, receipt = request(output, {'model': 'gpt-6-sol'}, endpoint=endpoint, base_url=self.url)
                self.assertTrue(receipt['http_completed'])
                self.assertEqual(receipt['client_retries'], 0)
                self.assertFalse(receipt['total_wall_deadline_enforced'])
                for p in output.iterdir():
                    self.assertNotIn(b'local-test-secret', p.read_bytes())
        self.assertEqual([x[:2] for x in self.calls],
                         [('GET', '/v1/models'), ('POST', '/v1/responses'), ('POST', '/v1/chat/completions')])
        self.assertEqual([x[3] for x in self.calls], ['Bearer local-test-secret'] * 3)

    def test_missing_key_stops_before_evidence_directory_and_http_request(self):
        output = Path(self.temporary.name) / 'missing-key'
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                request(output, {}, base_url=self.url)
        self.assertFalse(output.exists())
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
