"""Two-reader route isolation for the accounts opt-in."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import hashlib
import http.client
import json
import os
import tempfile
import unittest
from pathlib import Path
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch

from dev import accounts, preview_server
from dev.ingest.jobs import EpisodeJobStore, fake_episode
from dev.ingest.paths import profile_identifier


class AccountIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = ExitStack()
        self.patches.enter_context(patch.dict(os.environ, {
            'BRIEFING_ACCOUNTS': 'on',
            'BRIEFING_STATE_DIR': str(self.root / 'state'),
            'BRIEFING_INVITE_CODE_SHA256': hashlib.sha256(b'fixture-invite').hexdigest(),
            'BRIEFING_EPISODES': 'fake',
        }, clear=False))
        with accounts._lock:
            accounts._attempts.clear()
        digest = hashlib.sha256(b'fixture-invite').hexdigest()
        accounts._write('invites.json', {digest: {'expires': 2**31, 'remaining': 20}})
        self.reader_a = self._signup('reader-a')
        self.reader_b = self._signup('reader-b')
        self.store_a = preview_server._account_episode_store(
            accounts.session(self.reader_a['token'])[2]['id'])
        self.manifest = self.root / 'voice-manifest.json'
        self.manifest.write_text(json.dumps({'samples': [
            {'label': letter, 'voice': f'FixtureVoice{letter}'} for letter in 'ABCDEF'
        ]}), encoding='utf-8')
        self.patches.enter_context(patch.object(preview_server, 'EPISODE_AUDITION_MANIFEST', self.manifest))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), preview_server.Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        for reader in (self.reader_a, self.reader_b):
            preview_server._ACCOUNT_JOB_STORES.pop(reader['id'], None)
            preview_server._ACCOUNT_CORRECTIONS.pop(reader['id'], None)
        self.patches.close()
        self.temp.cleanup()

    def _signup(self, name):
        account, token, csrf = accounts.signup('fixture-invite', name,
                                               'a fictional account password', 'signup-' + name)
        return {'name': name, 'id': account['id'], 'token': token, 'csrf': csrf}

    def _request(self, method, path, reader=None, body=None, *, csrf=True):
        headers = {}
        if reader:
            headers['Cookie'] = f"briefing_session={reader['token']}"
            if csrf:
                headers['X-CSRF-Token'] = reader['csrf']
        encoded = json.dumps(body).encode() if body is not None else None
        if encoded is not None:
            headers['Content-Type'] = 'application/json'
            headers['Content-Length'] = str(len(encoded))
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        data = response.read()
        result = json.loads(data) if data else None
        connection.close()
        return response.status, result

    def test_authentication_is_required_for_legacy_state_routes(self):
        status, result = self._request('GET', '/api/profile')
        self.assertEqual(status, 401)
        self.assertEqual(result['error'], 'authentication_required')

    def test_profile_episodes_corrections_edition_and_audio_are_reader_scoped(self):
        # Re-authenticate each account to obtain its opaque token; successful
        # login rotates sessions and keeps the test fixture flow realistic.
        _, token_a, csrf_a = accounts.login('reader-a', 'a fictional account password', 'login-a')
        _, token_b, csrf_b = accounts.login('reader-b', 'a fictional account password', 'login-b')
        a = {'token': token_a, 'csrf': csrf_a}
        b = {'token': token_b, 'csrf': csrf_b}
        profile_a = {'minutes': 12, 'topics': [{'id': 'reader-a-topic', 'label': 'Reader A topic'}]}
        profile_b = {'minutes': 12, 'topics': [{'id': 'reader-b-topic', 'label': 'Reader B topic'}]}
        self.assertEqual(self._request('POST', '/api/profile', a, {'profile': profile_a})[0], 200)
        self.assertEqual(self._request('POST', '/api/profile', b, {'profile': profile_b})[0], 200)

        status_a, result_a = self._request('GET', '/api/profile', a)
        status_b, result_b = self._request('GET', '/api/profile', b)
        self.assertEqual(status_a, 200); self.assertEqual(status_b, 200)
        self.assertEqual(result_a['profile']['topics'][0]['id'], 'reader-a-topic')
        self.assertEqual(result_b['profile']['topics'][0]['id'], 'reader-b-topic')
        self.assertNotIn('_account_scope', result_a['profile'])
        export_a = self._request('GET', '/api/account/export', a)[1]
        export_b = self._request('GET', '/api/account/export', b)[1]
        self.assertEqual(export_a['profile']['topics'][0]['id'], 'reader-a-topic')
        self.assertEqual(export_b['profile']['topics'][0]['id'], 'reader-b-topic')
        self.assertEqual(self._request('GET', '/api/profile/export', b)[0], 404)

        scoped_a = {**profile_a, '_account_scope': accounts.session(token_a)[2]['id']}
        _, created = self.store_a.submit(scoped_a, {'topic': 'Reader A episode', 'length': 5,
                                                    'style': 'news-brief'}, start=False)
        job_id = created['job_id']
        list_a = self._request('GET', '/api/episodes', a)[1]['jobs']
        list_b = self._request('GET', '/api/episodes', b)[1]['jobs']
        self.assertTrue(any(row['job_id'] == job_id for row in list_a))
        self.assertFalse(any(row['job_id'] == job_id for row in list_b))

        self.assertEqual(self._request('GET', f'/api/episodes/{job_id}', a)[0], 200)
        self.assertEqual(self._request('GET', f'/api/episodes/{job_id}', b)[0], 404)
        self.assertEqual(self._request('GET', f'/api/episodes/{job_id}/audio', b)[0], 404)
        self.assertEqual(self._request('GET', f'/api/episodes/{job_id}/captions.vtt', b)[0], 404)
        self.assertEqual(self._request('GET',
                                       f'/api/episodes/{job_id}/chapters/chapter-1/audio', b)[0], 404)
        status, _ = self._request('POST', f'/api/episodes/{job_id}/cancel', a)
        self.assertIn(status, (200, 202))
        status, _ = self._request('POST', f'/api/episodes/{job_id}/cancel', b)
        self.assertEqual(status, 404)
        self.assertEqual(self._request('GET', '/api/corrections', a)[0], 200)
        self.assertEqual(self._request('GET', '/api/corrections', b)[0], 200)
        self.assertEqual(self._request('GET', '/api/edition?profile=active', a)[0], 200)
        self.assertEqual(self._request('GET', '/api/edition?profile=active', b)[0], 200)
        self.assertEqual(self._request('GET', '/api/edition?profile=default', b)[0], 200)
        self.assertEqual(self._request('GET', '/api/daily/audio', b)[0], 404)
        edition_a = accounts.reader_state_dir(accounts.session(token_a)[2]['id']) / 'generated' / profile_identifier(scoped_a)
        edition_b = accounts.reader_state_dir(accounts.session(token_b)[2]['id']) / 'generated' / profile_identifier({**profile_b, '_account_scope': accounts.session(token_b)[2]['id']})
        self.assertNotEqual(edition_a, edition_b)
        for directory, scoped in ((edition_a, scoped_a),
                                  (edition_b, {**profile_b, '_account_scope': accounts.session(token_b)[2]['id']})):
            directory.mkdir(parents=True, exist_ok=True)
            edition = {'id': 'same-fixture-edition', 'revision_id': 'same-fixture-revision',
                       'profile_id': profile_identifier(scoped), 'generated': True,
                       'kind': 'daily', 'seen_by_reader': False}
            (directory / 'current.json').write_text(json.dumps({
                'editions': {'editions': [edition]}, 'sources': {'sources': []}
            }), encoding='utf-8')
        status, _ = self._request('POST', '/api/edition/seen', a, {
            'profile_id': profile_identifier(scoped_a), 'edition_id': 'same-fixture-edition',
            'revision_id': 'same-fixture-revision'})
        self.assertEqual(status, 200)
        saved_a = json.loads((edition_a / 'current.json').read_text())
        saved_b = json.loads((edition_b / 'current.json').read_text())
        self.assertTrue(saved_a['editions']['editions'][0]['seen_by_reader'])
        self.assertFalse(saved_b['editions']['editions'][0]['seen_by_reader'])

    def test_reader_b_cannot_overwrite_reader_a_profile(self):
        _, token_a, csrf_a = accounts.login('reader-a', 'a fictional account password', 'login-a2')
        _, token_b, csrf_b = accounts.login('reader-b', 'a fictional account password', 'login-b2')
        a = {'token': token_a, 'csrf': csrf_a}; b = {'token': token_b, 'csrf': csrf_b}
        self._request('POST', '/api/profile', a, {'profile': {'minutes': 12, 'topics': [{'id': 'owned-a', 'label': 'Owned A'}]}})
        self._request('POST', '/api/profile', b, {'profile': {'minutes': 12, 'topics': [{'id': 'owned-b', 'label': 'Owned B'}]}})
        status, _ = self._request('POST', '/api/profile', b,
                                  {'profile': {'minutes': 12, 'topics': [{'id': 'overwritten-a', 'label': 'Attack'}]}})
        self.assertEqual(status, 200)
        result_a = self._request('GET', '/api/profile', a)[1]
        self.assertEqual(result_a['profile']['topics'][0]['id'], 'owned-a')

    def test_voice_preference_is_stored_per_reader(self):
        _, token_a, csrf_a = accounts.login('reader-a', 'a fictional account password', 'voice-a')
        _, token_b, csrf_b = accounts.login('reader-b', 'a fictional account password', 'voice-b')
        a = {'token': token_a, 'csrf': csrf_a}; b = {'token': token_b, 'csrf': csrf_b}
        self.assertEqual(self._request('GET', '/api/voice', b)[1], {'letter': 'A'})
        self.assertEqual(self._request('POST', '/api/voice', a, {'letter': 'C'})[0], 200)
        self.assertEqual(self._request('GET', '/api/voice', a)[1], {'letter': 'C'})
        self.assertEqual(self._request('GET', '/api/voice', b)[1], {'letter': 'A'})


if __name__ == '__main__':
    unittest.main()
