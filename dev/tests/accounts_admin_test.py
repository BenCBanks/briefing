"""Fixture-only operator account administration and password-reset tests."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dev.test_guard import enforce_test_environment
enforce_test_environment()

import hashlib
import http.client
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from io import StringIO
from threading import Thread
from unittest.mock import patch

from dev import accounts, preview_server
from dev.tools import accounts_admin


class AccountAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'BRIEFING_ACCOUNTS': 'on',
                                           'BRIEFING_STATE_DIR': self.temp.name,
                                           'BRIEFING_INVITE_CODE_SHA256': hashlib.sha256(
                                               b'fixture-admin-invite').hexdigest()}, clear=False)
        self.env.start()
        with accounts._lock:
            accounts._attempts.clear()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), preview_server.Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        with accounts._lock:
            accounts._attempts.clear()
        self.env.stop()
        self.temp.cleanup()

    def create_account(self, name='fixture-reader'):
        account, token, _csrf = accounts.signup('fixture-admin-invite', name,
                                                  'fixture-only strong passphrase')
        return account, token

    def post_reset(self, code, password='replacement fixture passphrase'):
        body = json.dumps({'reset_code': code, 'new_password': password}).encode()
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        connection.request('POST', '/api/account/password-reset', body=body,
                           headers={'Content-Type': 'application/json',
                                    'Content-Length': str(len(body))})
        response = connection.getresponse()
        data = response.read()
        result = json.loads(data) if data else None
        connection.close()
        return response.status, result

    def test_admin_requires_flag_without_creating_state(self):
        output = StringIO()
        with patch.dict(os.environ, {'BRIEFING_ACCOUNTS': 'off',
                                     'BRIEFING_STATE_DIR': self.temp.name}), redirect_stdout(output):
            result = accounts_admin.main(['list'])
        self.assertEqual(result, 2)
        self.assertIn('disabled', output.getvalue().lower())
        self.assertFalse((Path(self.temp.name) / 'accounts').exists())

    def test_admin_list_exposes_only_short_ids(self):
        account, _token = self.create_account('fixture-private-name')
        output = StringIO()
        with redirect_stdout(output):
            result = accounts_admin.main(['list'])
        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue().strip(), account['id'][:8] + ' enabled')
        self.assertNotIn('fixture-private-name', output.getvalue())

    def test_disable_invalidates_sessions_and_login_stays_generic(self):
        account, token = self.create_account()
        with redirect_stdout(StringIO()):
            self.assertEqual(accounts_admin.main(['disable', account['id'][:8]]), 0)
        self.assertIsNone(accounts.session(token))
        with self.assertRaises(accounts.AccountError) as disabled:
            accounts.login(account['name'], 'fixture-only strong passphrase', '127.0.0.2')
        self.assertEqual((disabled.exception.status, disabled.exception.code),
                         (401, 'invalid_credentials'))
        with redirect_stdout(StringIO()):
            self.assertEqual(accounts_admin.main(['enable', account['id'][:8]]), 0)
        self.assertTrue(accounts.login(account['name'], 'fixture-only strong passphrase',
                                       '127.0.0.3'))

    def test_disable_during_password_check_cannot_create_a_session(self):
        account, _token = self.create_account()
        verify = accounts._verify

        def disable_after_check(password, record):
            result = verify(password, record)
            self.assertTrue(accounts.set_disabled(account['id'], True))
            return result

        with patch.object(accounts, '_verify', side_effect=disable_after_check):
            with self.assertRaises(accounts.AccountError) as denied:
                accounts.login(account['name'], 'fixture-only strong passphrase', '127.0.0.2')
        self.assertEqual((denied.exception.status, denied.exception.code),
                         (401, 'invalid_credentials'))

    def test_reset_code_is_hashed_single_use_and_invalidates_sessions(self):
        account, token = self.create_account()
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(accounts_admin.main(['reset', account['id'][:8]]), 0)
        code = output.getvalue().split(': ', 1)[1].strip()
        reset_store = (accounts._root() / 'password_resets.json').read_text()
        self.assertNotIn(code, reset_store)
        self.assertIn(hashlib.sha256(code.encode()).hexdigest(), reset_store)
        self.assertEqual(self.post_reset(code), (200, {'ok': True}))
        self.assertIsNone(accounts.session(token))
        self.assertEqual(self.post_reset(code), (400, {'error': 'invalid_reset_request'}))
        self.assertEqual(accounts.login(account['name'], 'replacement fixture passphrase',
                                        '127.0.0.2')[0]['id'], account['id'])

    def test_wrong_and_expired_reset_codes_share_generic_error(self):
        account, _token = self.create_account()
        code = accounts.issue_password_reset(account['id'], now=100)
        wrong = self.post_reset('not-the-reset-code')
        with self.assertRaises(accounts.AccountError) as expiry_error:
            accounts.reset_password(code, 'replacement fixture passphrase',
                                    '127.0.0.2', now=100 + accounts.PASSWORD_RESET_SECONDS)
        self.assertEqual(wrong, (400, {'error': 'invalid_reset_request'}))
        self.assertEqual((expiry_error.exception.status, expiry_error.exception.code),
                         (400, 'invalid_reset_request'))

    def test_disabled_account_reset_is_generic_and_brute_force_is_limited(self):
        account, _token = self.create_account()
        code = accounts.issue_password_reset(account['id'])
        self.assertTrue(accounts.set_disabled(account['id'], True))
        self.assertEqual(self.post_reset(code), (400, {'error': 'invalid_reset_request'}))

        with accounts._lock:
            accounts._attempts.clear()
        for _ in range(accounts.MAX_RESET_FAILURES):
            status, result = self.post_reset('wrong-code')
            self.assertEqual((status, result), (400, {'error': 'invalid_reset_request'}))
        self.assertEqual(self.post_reset('wrong-code'),
                         (429, {'error': 'reset_temporarily_locked'}))


if __name__ == '__main__':
    unittest.main()
