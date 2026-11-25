"""Fixture-only coverage for the opt-in account boundary."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import http.client
import hashlib
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
                                           'BRIEFING_INVITE_CODE_SHA256': hashlib.sha256(
                                               b'fictional-invite').hexdigest()}, clear=False)
        self.env.start()
        from dev import accounts
        with accounts._lock:
            accounts._attempts.clear()
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
        self.server.is_https = True
        status, result, cookie = self.request('POST', '/api/account/signup',
                                               {'invite_code': 'fictional-invite', 'account': 'reader-a',
                                                'password': 'correct horse battery staple'})
        self.assertEqual(status, 201)
        self.assertIn('HttpOnly', cookie); self.assertIn('SameSite=Lax', cookie); self.assertIn('Secure', cookie)
        old_cookie = cookie.split(';', 1)[0]
        from dev import accounts
        session_store = (accounts._root() / 'sessions.json').read_text()
        self.assertNotIn(old_cookie.split('=', 1)[1], session_store)
        csrf = result['csrf_token']
        status, me, _ = self.request('GET', '/api/account/me', cookie=old_cookie)
        self.assertEqual(status, 200); self.assertEqual(me['account']['name'], 'reader-a')
        status, result, _ = self.request('POST', '/api/account/logout', {}, cookie=old_cookie)
        self.assertEqual(status, 403); self.assertEqual(result['error'], 'csrf_required')
        status, login_result, rotated = self.request('POST', '/api/account/login',
                                                     {'account': 'reader-a', 'password': 'correct horse battery staple'})
        self.assertEqual(status, 200); self.assertNotEqual(rotated.split(';', 1)[0], old_cookie)
        self.assertEqual(accounts.session(old_cookie.split('=', 1)[1]), None)
        status, _, _ = self.request('POST', '/api/account/logout', {'csrf_token': login_result['csrf_token']}, cookie=rotated.split(';', 1)[0])
        self.assertEqual(status, 204)

    def test_cookie_secure_only_for_https_or_explicit_trusted_proxy(self):
        self.server.is_https = False
        status, _, cookie = self.request('POST', '/api/account/signup',
                                         {'invite_code': 'fictional-invite', 'account': 'reader-http',
                                          'password': 'strong fictional passphrase'})
        self.assertEqual(status, 201)
        self.assertNotIn('Secure', cookie)
        self.assertIn('HttpOnly', cookie); self.assertIn('SameSite=Lax', cookie)

    def test_trusted_proxy_secure_cookie_requires_opt_in(self):
        self.server.is_https = False
        headers = {'X-Forwarded-Proto': 'https'}
        with patch.dict(os.environ, {'BRIEFING_TRUST_PROXY_HEADERS': 'off'}):
            _, _, cookie = self.request('POST', '/api/account/signup',
                                         {'invite_code': 'fictional-invite', 'account': 'reader-proxy-a',
                                          'password': 'strong fictional passphrase'}, headers=headers)
            self.assertNotIn('Secure', cookie)
        # A one-use invite was consumed by the first account; use a fresh fixture
        # invite record for the trusted-proxy assertion.
        from dev import accounts
        digest = hashlib.sha256(b'fictional-proxy-invite').hexdigest()
        accounts._write('invites.json', {digest: {'expires': 2**31, 'remaining': 1}})
        headers['X-Forwarded-Proto'] = 'https'
        with patch.dict(os.environ, {'BRIEFING_TRUST_PROXY_HEADERS': 'on'}):
            _, _, cookie = self.request('POST', '/api/account/signup',
                                         {'invite_code': 'fictional-proxy-invite', 'account': 'reader-proxy-b',
                                          'password': 'strong fictional passphrase'}, headers=headers)
        self.assertIn('Secure', cookie)

    def test_daily_reader_loader_uses_saved_account_profiles(self):
        from dev import accounts
        from dev.ingest.generation_api import save_profile
        account_id = 'a' * 32
        profile = {'version': 1, 'words': 'Fictional topic interests', 'minutes': 12,
                   'topics': [{'id': 'fictional-topic', 'label': 'Fictional topic',
                               'scope': 'Fictional subject'}]}
        accounts._write('accounts.json', {'reader': {'id': account_id, 'name': 'reader'}})
        reader_dir = accounts.reader_state_dir(account_id)
        self.assertEqual(save_profile(profile, path=reader_dir / 'profile.json')[0], 200)
        self.assertEqual(accounts.daily_readers(), [{'account_id': account_id, 'profile': profile}])

    def test_wrong_password_is_generic_and_locks_out(self):
        self.request('POST', '/api/account/signup', {'invite_code': 'fictional-invite', 'account': 'reader-b',
                                                     'password': 'correct horse battery staple'})
        for _ in range(5):
            status, result, _ = self.request('POST', '/api/account/login', {'account': 'reader-b', 'password': 'wrong-password'})
            self.assertEqual(status, 401); self.assertEqual(result, {'error': 'invalid_credentials'})
        status, result, _ = self.request('POST', '/api/account/login', {'account': 'reader-b', 'password': 'correct horse battery staple'})
        self.assertEqual(status, 429); self.assertEqual(result['error'], 'login_temporarily_locked')

    def test_unknown_account_and_wrong_password_share_login_response(self):
        self.request('POST', '/api/account/signup', {'invite_code': 'fictional-invite', 'account': 'reader-c',
                                                     'password': 'long fictional password'})
        existing = self.request('POST', '/api/account/login',
                                 {'account': 'reader-c', 'password': 'different password'})
        unknown = self.request('POST', '/api/account/login',
                               {'account': 'missing-reader', 'password': 'different password'})
        self.assertEqual(existing[:2], unknown[:2])
        self.assertEqual(existing[1], {'error': 'invalid_credentials'})

    def test_accounts_are_unavailable_when_flag_is_off(self):
        with patch.dict(os.environ, {'BRIEFING_ACCOUNTS': 'off'}):
            status, result, _ = self.request('GET', '/api/account/me')
            self.assertEqual(status, 404)
            status, _, _ = self.request('POST', '/api/account/signup', {'invite_code': 'x'})
            self.assertEqual(status, 404)
            status, _, _ = self.request('POST', '/api/account/password-reset',
                                        {'reset_code': 'fixture-code', 'new_password': 'long fixture password'})
            self.assertEqual(status, 404)
        self.assertFalse((__import__('pathlib').Path(self.temp.name) / 'accounts').exists())

    def test_normalized_name_and_password_policy(self):
        from dev import accounts
        with self.assertRaises(accounts.AccountError) as common:
            accounts.signup('fictional-invite', 'reader-a', 'password123', '127.0.0.1')
        self.assertEqual(common.exception.code, 'password_too_common')
        account, _, _ = accounts.signup('fictional-invite', '  Ｒｅａｄｅｒ-A  ',
                                         'a long fictional passphrase', '127.0.0.1')
        self.assertEqual(account['name'], 'reader-a')
        digest = hashlib.sha256(b'fictional-invite').hexdigest()
        accounts._write('invites.json', {digest: {'expires': 2**31, 'remaining': 1}})
        with self.assertRaises(accounts.AccountError) as collision:
            accounts.signup('fictional-invite', 'reader-a', 'another long passphrase', '127.0.0.1')
        self.assertEqual(collision.exception.code, 'account_exists')

    def test_delete_is_synchronous_and_invalidates_every_session(self):
        from dev import accounts
        _, body, cookie = self.request('POST', '/api/account/signup',
                                       {'invite_code': 'fictional-invite', 'account': 'reader-delete',
                                        'password': 'long fictional password'})
        token_cookie = cookie.split(';', 1)[0]
        accounts._root().joinpath('readers', body['account']['id']).mkdir(parents=True)
        status, _, expired = self.request('POST', '/api/account/delete',
                                          {'confirm': 'DELETE'},
                                          headers={'X-CSRF-Token': body['csrf_token']}, cookie=token_cookie)
        self.assertEqual(status, 204)
        self.assertIn('Max-Age=0', expired)
        self.assertIsNone(accounts.session(token_cookie.split('=', 1)[1]))
        self.assertFalse((accounts._root() / 'readers' / body['account']['id']).exists())

    def test_invite_is_single_use_and_password_hash_is_upgraded_on_login(self):
        from dev import accounts
        account, _, _ = accounts.signup('fictional-invite', 'reader-rehash',
                                        'long fictional password phrase', '127.0.0.1')
        with self.assertRaises(accounts.AccountError) as reused:
            accounts.signup('fictional-invite', 'reader-other',
                            'another fictional password phrase', '127.0.0.1')
        self.assertEqual(reused.exception.code, 'invalid_invite_code')
        with patch.object(accounts, 'SCRYPT_N', 2**15):
            accounts.login('reader-rehash', 'long fictional password phrase', '127.0.0.2')
        stored = accounts._read('accounts.json', {})['reader-rehash']['password']
        self.assertEqual(stored['n'], 2**15)

    def test_expiry_sweep_drops_expired_and_malformed_sessions(self):
        from dev import accounts
        swept = accounts._sweep_sessions({
            'valid': {'absolute_expires': 200, 'idle_expires': 150},
            'expired': {'absolute_expires': 90, 'idle_expires': 200},
            'malformed': {'absolute_expires': 'bad', 'idle_expires': 200},
        }, 100)
        self.assertEqual(list(swept), ['valid'])


if __name__ == '__main__':
    unittest.main()
