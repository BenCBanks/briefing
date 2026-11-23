"""Standard-library contract tests for request_form_static_server."""

import http.client
import os
from pathlib import Path
import tempfile
import threading
import unittest

from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from dev.tests.request_form_static_server import create_server


class RequestFormStaticServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='request-static-test-')
        self.root = Path(self.temp.name)
        self.html = self.root / 'request.html'
        self.script = self.root / 'request.js'
        self.style = self.root / 'request.css'
        self.replace = self.root / 'replace.html'
        self.alternate = self.root / 'alternate.html'
        self.html.write_text('<main>fictional fixture</main>', encoding='utf-8')
        self.script.write_text('globalThis.fixture = true;\n', encoding='utf-8')
        self.style.write_text('.fixture { color: black; }\n', encoding='utf-8')
        self.replace.write_text('original fictional file', encoding='utf-8')
        self.alternate.write_text('alternate fictional file', encoding='utf-8')
        self.allowlist = {
            '/': self.html,
            '/src/request.js': self.script,
            '/styles/request.css': self.style,
            '/replace.html': self.replace,
        }
        self.server = create_server(self.allowlist)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        thread_stopped = not self.thread.is_alive()
        self.server.server_close()
        self.temp.cleanup()
        self.assertTrue(thread_stopped, 'the owned fixture server thread exits during teardown')

    def request(self, method, target):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        try:
            connection.request(method, target)
            response = connection.getresponse()
            body = response.read()
            return response.status, dict(response.getheaders()), body
        finally:
            connection.close()

    def test_binds_only_loopback_and_get_serves_only_exact_mapped_files(self):
        self.assertEqual(self.server.server_address[0], '127.0.0.1')
        cases = [
            ('/', self.html, 'text/html; charset=utf-8'),
            ('/src/request.js', self.script, 'text/javascript; charset=utf-8'),
            ('/styles/request.css', self.style, 'text/css; charset=utf-8'),
        ]
        for url, path, mime in cases:
            with self.subTest(url=url):
                status, headers, body = self.request('GET', url)
                self.assertEqual(status, 200)
                self.assertEqual(body, path.read_bytes())
                self.assertEqual(headers['Content-Type'], mime)
                self.assertEqual(headers['Content-Length'], str(path.stat().st_size))
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')

    def test_head_has_get_headers_and_no_body(self):
        status, headers, body = self.request('HEAD', '/')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Length'], str(self.html.stat().st_size))
        self.assertEqual(headers['Content-Type'], 'text/html; charset=utf-8')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(body, b'')

    def test_unknown_repo_and_api_paths_are_not_served(self):
        for target in ('/README.md', '/dev/tests/request_form_static_server.py', '/api/profile', '/src/'):
            with self.subTest(target=target):
                status, _headers, body = self.request('GET', target)
                self.assertEqual(status, 404)
                self.assertEqual(body, b'Not found\n')

    def test_query_and_percent_encoded_paths_are_refused_without_decoding(self):
        for target in ('/?q=1', '//src/request.js', '/%2e%2e/README.md', '/%252e%252e/README.md', '/%2fetc/passwd'):
            with self.subTest(target=target):
                status, _headers, body = self.request('GET', target)
                self.assertEqual(status, 400)
                self.assertEqual(body, b'Bad request target\n')

    def test_non_get_head_methods_are_rejected(self):
        for method in ('POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS', 'PROPFIND'):
            with self.subTest(method=method):
                status, headers, body = self.request(method, '/')
                self.assertEqual(status, 405)
                self.assertEqual(headers['Allow'], 'GET, HEAD')
                self.assertEqual(body, b'Method not allowed\n')

    def test_allowlist_rejects_noncanonical_routes_and_nonweb_files(self):
        for route in ('relative', '//host/path', '/../outside.html', '/a/./b.html', '/a//b.css', '/%2e%2e/x.js', '/a?b=1', '/a#fragment', '/a\\b.js'):
            with self.subTest(route=route):
                with self.assertRaises(ValueError):
                    create_server({route: self.html})
        with self.assertRaises(ValueError):
            create_server({'/relative.css': 'relative.css'})
        with self.assertRaises(ValueError):
            create_server({'/data.txt': self.root / 'data.txt'})
        with self.assertRaises(ValueError):
            create_server({'/folder.html': self.root})

    @unittest.skipUnless(hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY'), 'platform lacks no-follow directory opens')
    def test_allowlist_rejects_symlink_file_and_parent_components(self):
        target_link = self.root / 'linked.html'
        target_link.symlink_to(self.html)
        with self.assertRaises(ValueError):
            create_server({'/linked.html': target_link})

        actual_dir = self.root / 'actual'
        actual_dir.mkdir()
        nested = actual_dir / 'nested.css'
        nested.write_text('.nested {}', encoding='utf-8')
        parent_link = self.root / 'alias'
        parent_link.symlink_to(actual_dir, target_is_directory=True)
        with self.assertRaises(ValueError):
            create_server({'/nested.css': parent_link / 'nested.css'})

    @unittest.skipUnless(hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY'), 'platform lacks no-follow directory opens')
    def test_replaced_target_symlink_is_refused_at_request_time(self):
        self.replace.unlink()
        self.replace.symlink_to(self.alternate)
        status, _headers, body = self.request('GET', '/replace.html')
        self.assertEqual(status, 404)
        self.assertEqual(body, b'Not found\n')


if __name__ == '__main__':
    unittest.main()
