"""Fixture-only coverage for the opt-in account boundary."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import http.client
import json
import os
import tempfile
import unittest
from http.server import ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch

from dev import preview_server


class AccountAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'BRIEFING_ACCOUNTS': 'on',
                                           'BRIEFING_STATE_DIR': self.temp.name,
                                           'BRIEFING_INVITE_CODE': 'fictional-invite'}, clear=False)
        self.env.start()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), preview_server.Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        self.env.stop(); self.temp.cleanup()

    def request(self, method, path, payload=None, headers=None, cookie=None):
        headers = dict(headers or {})
        if cookie:
            headers['Cookie'] = cookie
        body = json.dumps(payload).encode() if payload is not None else None
        if body is not None:
            headers['Content-Type'] = 'application/json'
            headers['Content-Length'] = str(len(body))
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        result = json.loads(data) if data else None
        cookie_header = response.getheader('Set-Cookie')
        connection.close()
        return response.status, result, cookie_header

    def test_signup_cookie_flags_login_rotation_and_csrf(self):
        status, result, cookie = self.request('POST', '/api/account/signup',
                                               {'invite_code': 'fictional-invite', 'account': 'reader-a',
                                                'password': 'correct horse battery staple'})
        self.assertEqual(status, 201)
        self.assertIn('HttpOnly', cookie); self.assertIn('SameSite=Lax', cookie); self.assertIn('Secure', cookie)
        old_cookie = cookie.split(';', 1)[0]
        csrf = result['csrf_token']
        status, me, _ = self.request('GET', '/api/account/me', cookie=old_cookie)
        self.assertEqual(status, 200); self.assertEqual(me['account']['name'], 'reader-a')
        status, _, rotated = self.request('POST', '/api/account/login',
                                           {'account': 'reader-a', 'password': 'correct horse battery staple'})
        self.assertEqual(status, 200); self.assertNotEqual(rotated.split(';', 1)[0], old_cookie)
        status, result, _ = self.request('POST', '/api/account/logout', {}, cookie=old_cookie)
        self.assertEqual(status, 403); self.assertEqual(result['error'], 'csrf_required')
        status, _, _ = self.request('POST', '/api/account/logout', {'csrf_token': csrf}, cookie=old_cookie)
        self.assertEqual(status, 204)

    def test_wrong_password_is_generic_and_locks_out(self):
        self.request('POST', '/api/account/signup', {'invite_code': 'fictional-invite', 'account': 'reader-b',
                                                     'password': 'correct horse battery staple'})
        for _ in range(5):
            status, result, _ = self.request('POST', '/api/account/login', {'account': 'reader-b', 'password': 'wrong-password'})
            self.assertEqual(status, 401); self.assertEqual(result, {'error': 'invalid_credentials'})
        status, result, _ = self.request('POST', '/api/account/login', {'account': 'reader-b', 'password': 'correct horse battery staple'})
        self.assertEqual(status, 429); self.assertEqual(result['error'], 'login_temporarily_locked')

    def test_accounts_are_unavailable_when_flag_is_off(self):
        with patch.dict(os.environ, {'BRIEFING_ACCOUNTS': 'off'}):
            status, result, _ = self.request('GET', '/api/account/me')
            self.assertEqual(status, 404)


if __name__ == '__main__':
    unittest.main()
