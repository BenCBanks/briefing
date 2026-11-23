import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import io
import json
from contextlib import redirect_stderr
from pathlib import Path
import socket
import tempfile
from http.server import ThreadingHTTPServer
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dev import preview_server


class SecurityBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), preview_server.Handler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        preview_server._post_rate_rows.clear()

    def request(self, path, method='GET', data=None, headers=None):
        status, _headers, body = self.request_with_headers(path, method, data, headers)
        return status, body

    def request_with_headers(self, path, method='GET', data=None, headers=None):
        request = Request(self.base + path, data=data, method=method, headers=headers or {})
        try:
            with urlopen(request, timeout=3) as response:
                body = response.read()
                return response.status, response.headers, body
        except HTTPError as error:
            return error.code, error.headers, error.read()

    def raw_request(self, request_text):
        with socket.create_connection(('127.0.0.1', self.server.server_port), timeout=3) as client:
            client.sendall(request_text.encode('ascii'))
            response = bytearray()
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    break
                response.extend(chunk)
        return bytes(response)

    def test_rate_limit_is_per_ip_route_and_window(self):
        key = '/api/generate'
        limit = preview_server.POST_RATE_LIMITS[key]
        for index in range(limit):
            self.assertFalse(preview_server._post_rate_limited('127.0.0.1', key, now=100 + index))
        self.assertTrue(preview_server._post_rate_limited('127.0.0.1', key, now=109.5))
        self.assertFalse(preview_server._post_rate_limited('127.0.0.2', key, now=109.5))
        self.assertFalse(preview_server._post_rate_limited('127.0.0.1', key, now=170))

    def test_rate_limit_buckets_have_a_fixed_memory_bound(self):
        with patch.object(preview_server, 'MAX_POST_RATE_BUCKETS', 2):
            self.assertFalse(preview_server._post_rate_limited('127.0.0.1', '/api/profile', now=100))
            self.assertFalse(preview_server._post_rate_limited('127.0.0.2', '/api/profile', now=101))
            self.assertFalse(preview_server._post_rate_limited('127.0.0.3', '/api/profile', now=102))
        self.assertLessEqual(len(preview_server._post_rate_rows), 2)
        self.assertNotIn(('127.0.0.1', '/api/profile'), preview_server._post_rate_rows)

    def test_post_route_rate_limit_blocks_handler_before_profile_write(self):
        with patch.object(preview_server, '_post_rate_limited', return_value=True), \
             patch.object(preview_server, 'save_profile', side_effect=AssertionError('write must not run')):
            status, body = self.request('/api/profile', 'POST', b'{"profile":{}}',
                                        {'Content-Type': 'application/json'})
        self.assertEqual(status, 429)
        self.assertEqual(json.loads(body)['error'], 'rate_limited')

    def test_large_json_and_action_bodies_are_rejected_before_dispatch(self):
        cases = [('/api/interpret', 32769), ('/api/profile', 64001),
                 ('/api/generate', 64001), ('/api/episodes', 4097),
                 ('/api/corrections', 16001),
                 ('/api/voice', 1025), ('/api/profile/delete', 2049),
                 ('/api/episodes/1234567890abcdef1234567890abcdef/cancel', 1025)]
        with patch.object(preview_server, 'profile_response', return_value=(200, {'profile': {}})), \
             patch.object(preview_server, 'start_generation', side_effect=AssertionError('must not dispatch')), \
             patch.object(preview_server, 'save_profile', side_effect=AssertionError('must not dispatch')), \
             patch.object(preview_server, 'interpret_request', side_effect=AssertionError('must not dispatch')):
            for path, size in cases:
                with self.subTest(path=path):
                    status, body = self.request(path, 'POST', b'x' * size,
                                                {'Content-Type': 'application/json'})
                    self.assertEqual(status, 413)
                    self.assertEqual(json.loads(body)['error'], 'invalid_request_size')

    def test_duplicate_content_length_and_transfer_encoding_are_rejected(self):
        requests = (
            'POST /api/profile HTTP/1.1\r\nHost: localhost\r\n'
            'Content-Type: application/json\r\nContent-Length: 2\r\n'
            'Content-Length: 2\r\nConnection: close\r\n\r\n{}',
            'POST /api/profile HTTP/1.1\r\nHost: localhost\r\n'
            'Content-Type: application/json\r\nContent-Length: +2\r\n'
            'Connection: close\r\n\r\n{}',
            'POST /api/profile HTTP/1.1\r\nHost: localhost\r\n'
            'Content-Type: application/json\r\nTransfer-Encoding: chunked\r\n'
            'Connection: close\r\n\r\n0\r\n\r\n',
        )
        with patch.object(preview_server, 'save_profile',
                          side_effect=AssertionError('malformed framing must not dispatch')):
            for raw in requests:
                with self.subTest(request=raw.splitlines()[0]):
                    response = self.raw_request(raw)
                    headers, body = response.split(b'\r\n\r\n', 1)
                    self.assertIn(b' 400 ', headers.split(b'\r\n', 1)[0])
                    self.assertEqual(json.loads(body), {'error': 'invalid_content_length'})

    def test_api_queries_have_byte_and_field_bounds(self):
        with patch.object(preview_server, 'edition_response',
                          side_effect=AssertionError('invalid query must not dispatch')):
            too_many_fields = '&'.join(f'k{index}=x' for index in range(
                preview_server.MAX_API_QUERY_FIELDS + 1))
            status, body = self.request('/api/edition?' + too_many_fields)
            self.assertEqual(status, 400)
            self.assertEqual(json.loads(body), {'error': 'invalid_query'})

            too_many_bytes = 'x=' + ('a' * preview_server.MAX_API_QUERY_BYTES)
            status, body = self.request('/api/edition?' + too_many_bytes)
            self.assertEqual(status, 414)
            self.assertEqual(json.loads(body), {'error': 'request_target_too_large'})

    def test_api_responses_do_not_reflect_untrusted_request_headers(self):
        status, headers, body = self.request_with_headers(
            '/api/no-such-route', headers={'X-Injected-Header': 'fictional attacker value'})
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body), {'error': 'not_found'})
        self.assertIsNone(headers.get('X-Injected-Header'))

    def test_every_mutating_api_route_has_an_ip_route_rate_limit(self):
        routes = {
            '/api/interpret': '/api/interpret', '/api/profile': '/api/profile',
            '/api/generate': '/api/generate', '/api/episodes': '/api/episodes',
            '/api/corrections': '/api/corrections', '/api/voice': '/api/voice',
            '/api/profile/delete': '/api/profile/delete',
            '/api/episodes/1234567890abcdef1234567890abcdef/cancel': '/api/episode-action',
        }
        for path, expected_key in routes.items():
            with self.subTest(path=path):
                self.assertEqual(preview_server._post_rate_key(path), expected_key)
                self.assertGreater(preview_server.POST_RATE_LIMITS[expected_key], 0)

    def test_unknown_and_unsupported_api_requests_keep_json_error_shape(self):
        for method, path, body, expected_status, expected_error in (
            ('GET', '/api/no-such-route?topic=FictionalSecret', None, 404, 'not_found'),
            ('PUT', '/api/profile?topic=FictionalSecret', None, 501, 'method_not_allowed'),
            ('POST', '/api/no-such-route', b'{"topic":"FictionalSecret"}', 404, 'not_found'),
        ):
            with self.subTest(method=method, path=path):
                status, headers, response = self.request_with_headers(method=method, path=path, data=body)
                self.assertEqual(status, expected_status)
                self.assertIn('application/json', headers.get('Content-Type', ''))
                self.assertEqual(json.loads(response), {'error': expected_error})

    def test_api_access_logs_do_not_include_query_or_body_content(self):
        content = 'FictionalPrivateTopicSentinel'
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            status, headers, body = self.request_with_headers(
                f'/api/interpret?topic={content}', 'POST',
                f'{{"topic":"{content}"'.encode(),
                {'Content-Type': 'application/json'})
        self.assertEqual(status, 400)
        self.assertIn('application/json', headers.get('Content-Type', ''))
        self.assertEqual(json.loads(body)['error'], 'invalid_json')
        self.assertNotIn(content, stderr.getvalue())

    def test_fixture_run_log_and_error_body_scan_excludes_content_and_secrets(self):
        markers = ('fixture_topic_PRIVATE', 'fixture_story_PRIVATE',
                   'fixture_api_key_PRIVATE', 'fixture_profile_PRIVATE')
        secret_text = ' '.join(markers)
        bodies = []
        stderr = io.StringIO()
        profile = {'topics': [{'id': 'fixture-topic', 'label': markers[0]}],
                   'words': markers[3]}
        with redirect_stderr(stderr):
            with patch.object(preview_server, 'profile_response',
                              return_value=(200, {'profile': profile})), \
                 patch.object(preview_server.CORRECTIONS, 'create',
                              side_effect=preview_server.CorrectionError(
                                  secret_text, code='invalid_correction')):
                status, body = self.request('/api/corrections', 'POST',
                    json.dumps({'correction': markers[1], 'evidence': markers[2]}).encode(),
                    {'Content-Type': 'application/json'})
                self.assertEqual(status, 400)
                self.assertEqual(json.loads(body), {'error': 'invalid_correction'})
                bodies.append(body.decode())

            with patch.object(preview_server, 'feed_response',
                              side_effect=RuntimeError(secret_text)):
                status, body = self.request(
                    f'/api/feed?topic={markers[0]}&profile={markers[3]}')
                self.assertEqual(status, 500)
                self.assertEqual(json.loads(body), {'error': 'request_failed'})
                bodies.append(body.decode())

            with patch.object(preview_server, 'profile_response',
                              return_value=(200, {'profile': profile})), \
                 patch.object(preview_server.EPISODE_JOBS, 'submit',
                              side_effect=RuntimeError(secret_text)):
                status, body = self.request('/api/episodes', 'POST',
                    json.dumps({'topic': markers[0], 'length': 5, 'style': 'news-brief'}).encode(),
                    {'Content-Type': 'application/json'})
                self.assertEqual(status, 500)
                self.assertEqual(json.loads(body), {'error': 'request_failed'})
                bodies.append(body.decode())

        captured = stderr.getvalue() + '\n'.join(bodies)
        for marker in markers:
            self.assertNotIn(marker, captured)

    def test_private_api_posts_are_loopback_only(self):
        with patch.object(preview_server.api_access, 'is_loopback_address', return_value=False):
            status, body = self.request('/api/corrections', 'POST', b'{"correction":{}}',
                                        {'Content-Type': 'application/json'})
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)['error'], 'local_only')

    def test_every_static_root_rejects_encoded_traversal(self):
        for path in ('/src/%2e%2e/docs/episodes-api.md',
                     '/styles/%2e%2e/docs/episodes-api.md',
                     '/content/%2e%2e/docs/episodes-api.md',
                     '/api/episodes/%2e%2e/audio'):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)

    def test_private_api_gets_are_loopback_only(self):
        with patch.object(preview_server.api_access, 'is_loopback_address', return_value=False):
            status, body = self.request('/api/usage')
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)['error'], 'local_only')

    def test_static_and_audition_routes_reject_symlinks_outside_their_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / 'outside'
            outside.mkdir()
            secret = outside / 'secret.txt'
            secret.write_text('fictional private fixture')
            static_root = root / 'static'
            roots = [static_root / name for name in ('src', 'styles', 'content')]
            paths = ['/src/leak.js', '/styles/leak.css', '/content/leak.json']
            for directory, name in zip(roots, ('leak.js', 'leak.css', 'leak.json'), strict=True):
                directory.mkdir(parents=True)
                (directory / name).symlink_to(secret)
            with patch.object(preview_server, 'ROOT', static_root), \
                 patch.object(preview_server, 'ALLOWED', tuple(roots)):
                for path in paths:
                    with self.subTest(path=path):
                        self.assertEqual(self.request(path)[0], 404)

            audio_root = root / 'audition'
            audio_root.mkdir()
            (audio_root / 'escape.wav').symlink_to(secret)
            with patch.object(preview_server, 'AUDITION_AUDIO_ROOT', audio_root):
                self.assertEqual(self.request('/audition-audio/escape.wav')[0], 404)


if __name__ == '__main__':
    unittest.main()
