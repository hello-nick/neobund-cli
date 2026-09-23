"""Exercise urllib against a local HTTP server only; never contact Neobund."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'skill/neobund-publish/scripts'))
from client import Client, CLIError


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.received = []
        received = self.received

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                received.append((self.path, dict(self.headers), None))
                if self.path.endswith('/redirect'):
                    self.send_response(302)
                    self.send_header('Location', '/must-not-follow')
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":0,"data":{"records":[]}}')

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append((self.path, dict(self.headers), payload))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":0,"data":{"taskIds":[9223372036854775800]}}')

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = Client(api_key='test-only-sentinel')
        # Test-only transport override, production constructor rejects HTTP origins.
        self.client.base_url = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_headers_encoding_and_int64_wire_payload(self):
        body = {'tasks': [{'authId': 9223372036854775800, 'videoTitle': '新品'}]}
        out = self.client.request('POST', '/openapi/v1/video-publication-tasks', body)
        path, headers, received = self.received[0]
        self.assertEqual(path, '/openapi/v1/video-publication-tasks')
        self.assertEqual(headers['Nb-Api-Key'], 'test-only-sentinel')
        self.assertEqual(received, body)
        self.assertEqual(out['data']['taskIds'][0], 9223372036854775800)

    def test_query_encoding_and_redirect_does_not_leak(self):
        self.client.request('GET', '/openapi/v1/products', query={'title': 'Blue & shirt', 'authId': 101})
        self.assertIn('title=Blue+%26+shirt', self.received[0][0])
        with self.assertRaises(CLIError):
            self.client.request('GET', '/openapi/v1/redirect')
        self.assertEqual(len(self.received), 2)


if __name__ == '__main__':
    unittest.main()
