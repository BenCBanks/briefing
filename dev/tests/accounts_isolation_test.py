"""Two-reader route isolation for the accounts opt-in."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import ast
import hashlib
import http.client
import inspect
import json
import os
import re
import tempfile
import textwrap
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch

from dev import accounts, preview_server
from dev.ingest.jobs import EpisodeJobStore, fake_episode
from dev.ingest.paths import profile_identifier
from dev.ingest.usage import UsageLedger


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
        if data:
            try:
                result = json.loads(data)
            except (ValueError, UnicodeDecodeError):
                result = data.decode('utf-8', errors='replace')
        else:
            result = None
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
        self.assertRegex(job_id, r'^[a-f0-9]{32}$')
        self.store_a._mutate(self.store_a._profile_dir(scoped_a), job_id,
                             cost_record={'estimated_cost_usd': '0.12345678'})
        export_a = self._request('GET', '/api/account/export', a)[1]
        export_b = self._request('GET', '/api/account/export', b)[1]
        self.assertEqual([row['topic'] for row in export_a['episodes']], ['Reader A episode'])
        self.assertEqual(export_b['episodes'], [])
        list_a = self._request('GET', '/api/episodes', a)[1]['jobs']
        list_b = self._request('GET', '/api/episodes', b)[1]['jobs']
        self.assertTrue(any(row['job_id'] == job_id for row in list_a))
        self.assertFalse(any(row['job_id'] == job_id for row in list_b))
        usage_a = self._request('GET', '/api/usage', a)[1]
        usage_b = self._request('GET', '/api/usage', b)[1]
        self.assertEqual(usage_a['episode_jobs'], 1)
        self.assertEqual(usage_a['recorded_cost_usd'], '0.12345678')
        self.assertEqual(usage_b['episode_jobs'], 0)
        self.assertEqual(usage_b['recorded_cost_usd'], '0.00000000')
        self.assertEqual(self._request('GET', '/api/account/me', a)[1]['usage'], usage_a)

        self.assertEqual(self._request('GET', f'/api/episodes/{job_id}', a)[0], 200)
        self.assertEqual(self._request('GET', f'/api/episodes/{job_id}', b)[0], 404)
        self.assertEqual(self._request('GET', f'/api/episodes/{job_id}/audio', b)[0], 404)
        self.assertEqual(self._request('GET', f'/api/episodes/{job_id}/captions.vtt', b)[0], 404)
        self.assertEqual(self._request('GET',
                                       f'/api/episodes/{job_id}/chapters/chapter-1/audio', b)[0], 404)
        status, _ = self._request('POST', f'/api/episodes/{job_id}/cancel', a)
        self.assertIn(status, (200, 202))
        status, result = self._request('POST', f'/api/episodes/{job_id}/cancel', b)
        self.assertEqual(status, 404)
        self.assertEqual(result, {'error': 'job_not_found'})
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

        exported_a = self._request('GET', '/api/account/export', a)[1]
        exported_b = self._request('GET', '/api/account/export', b)[1]
        self.assertEqual(exported_a['editions'][0]['editions']['editions'][0]['id'],
                         'same-fixture-edition')
        self.assertEqual(exported_b['editions'][0]['editions']['editions'][0]['id'],
                         'same-fixture-edition')
        self.assertTrue(exported_a['editions'][0]['editions']['editions'][0]['seen_by_reader'])
        self.assertFalse(exported_b['editions'][0]['editions']['editions'][0]['seen_by_reader'])

        second_a_token, _second_a_csrf = accounts._new_session(accounts.session(token_a)[2]['id'])
        deleted_id = accounts.session(token_a)[2]['id']
        status, _ = self._request('POST', '/api/account/delete', a,
                                  {'confirm': 'DELETE'})
        self.assertEqual(status, 204)
        self.assertIsNone(accounts.session(token_a))
        self.assertIsNone(accounts.session(second_a_token))
        self.assertFalse(accounts.reader_state_dir(deleted_id, create=False).exists())
        self.assertEqual(self._request('GET', '/api/profile', b)[0], 200)

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

    def test_episode_daily_limit_is_account_wide_across_concurrent_profile_changes(self):
        store = EpisodeJobStore(
            self.root / 'limited-reader', generator=fake_episode, stage_delay=0,
            ledger=UsageLedger(ledger_dir=self.root / 'limited-ledger'),
            key_available=lambda: True, tts_enabled=False, account_daily_limit=True)
        profiles = [
            {'minutes': 12, 'topics': [{'id': f'profile-{index}', 'label': f'Profile {index}'}]}
            for index in range(4)
        ]

        def submit(index):
            return store.submit(profiles[index], {
                'topic': f'Fictional topic {index}', 'length': 3, 'style': 'explainer'}, start=False)

        with ThreadPoolExecutor(max_workers=4) as workers:
            responses = list(workers.map(submit, range(4)))
        statuses = [row[0] for row in responses]
        self.assertEqual(statuses.count(202), 3)
        self.assertEqual(statuses.count(429), 1)
        ids = [row[1]['job_id'] for row in responses if row[0] == 202]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(re.fullmatch(r'[a-f0-9]{32}', job_id) for job_id in ids))

    def test_generation_polling_delete_and_action_routes_do_not_cross_readers(self):
        _, token_a, csrf_a = accounts.login('reader-a', 'a fictional account password', 'generation-a')
        _, token_b, csrf_b = accounts.login('reader-b', 'a fictional account password', 'generation-b')
        a = {'token': token_a, 'csrf': csrf_a}; b = {'token': token_b, 'csrf': csrf_b}
        profile_a = {'minutes': 12, 'topics': [{'id': 'gen-a', 'label': 'Reader A'}]}
        profile_b = {'minutes': 12, 'topics': [{'id': 'gen-b', 'label': 'Reader B'}]}
        self.assertEqual(self._request('POST', '/api/profile', a, {'profile': profile_a})[0], 200)
        self.assertEqual(self._request('POST', '/api/profile', b, {'profile': profile_b})[0], 200)
        job_id = 'a' * 32
        scoped_a = {**profile_a, '_account_scope': accounts.session(token_a)[2]['id']}
        with patch.object(preview_server, 'start_generation',
                          return_value=(202, {'job_id': job_id, 'status': 'queued'})) as start:
            status, _ = self._request('POST', '/api/generate', a, {'profile': profile_a})
        self.assertEqual(status, 202)
        self.assertEqual(start.call_args.args[0]['_account_scope'], accounts.session(token_a)[2]['id'])
        with patch.object(preview_server, 'job_response',
                          return_value=(200, {'job_id': job_id,
                                              'profile_id': profile_identifier(scoped_a)})):
            self.assertEqual(self._request('GET', f'/api/generate/{job_id}', a)[0], 200)
            status, result = self._request('GET', f'/api/generate/{job_id}', b)
            self.assertEqual(status, 404)
            self.assertEqual(result, {'error': 'job_not_found'})

        # The legacy delete path and the old action alias never reveal a target;
        # cancellation is the routed action and is also tested reader-scoped above.
        status_a, result_a = self._request('POST', '/api/profile/delete', a, {'profile_id': 'reader-b'})
        status_b, result_b = self._request('POST', '/api/profile/delete', b, {'profile_id': 'reader-a'})
        self.assertEqual((status_a, result_a), (404, {'error': 'not_found'}))
        self.assertEqual((status_b, result_b), (404, {'error': 'not_found'}))
        self.assertEqual(self._request('GET', '/api/profile', a)[1]['profile']['topics'][0]['id'], 'gen-a')
        status_a, result_a = self._request('POST', '/api/episode-action', a, {'episode_id': job_id})
        status_b, result_b = self._request('POST', '/api/episode-action', b, {'episode_id': job_id})
        self.assertEqual(status_a, 404)
        self.assertEqual(status_b, 404)
        self.assertEqual(result_a, result_b)

    def test_revisions_are_reader_scoped_while_interpret_and_feed_are_stateless(self):
        _, token_a, csrf_a = accounts.login('reader-a', 'a fictional account password', 'routes-a')
        _, token_b, csrf_b = accounts.login('reader-b', 'a fictional account password', 'routes-b')
        a = {'token': token_a, 'csrf': csrf_a}; b = {'token': token_b, 'csrf': csrf_b}
        self._request('POST', '/api/profile', a, {'profile': {'minutes': 12, 'topics': [{'id': 'a', 'label': 'A'}]}})
        self._request('POST', '/api/profile', b, {'profile': {'minutes': 12, 'topics': [{'id': 'b', 'label': 'B'}]}})
        account_a = accounts.session(token_a)[2]['id']

        class ScopedRevision:
            def revision(_self, profile, kind, target, revision):
                if profile.get('_account_scope') != account_a:
                    raise preview_server.CorrectionError('Revision not found.', status=404,
                                                         code='revision_not_found')
                return {'id': target, 'revision_id': revision, 'chapters': []}

        with patch.object(preview_server.Handler, '_reader_corrections',
                          autospec=True, return_value=ScopedRevision()):
            self.assertEqual(self._request('GET', '/api/revisions/episode/topic/rev-one', a)[0], 200)
            status, result = self._request('GET', '/api/revisions/episode/topic/rev-one', b)
            self.assertEqual(status, 404)
            self.assertEqual(result['error'], 'revision_not_found')

        with patch.object(preview_server, 'interpret_request',
                          side_effect=lambda payload, _ip: (200, {'topic': payload['topic']})), \
                patch.object(preview_server, 'feed_response',
                             return_value=(200, {'items': [{'id': 'global-fixture'}]})), \
                patch.object(preview_server, 'status_response',
                             return_value=(200, {'status': 'global-fixture'})), \
                patch.object(preview_server, '_health_snapshot',
                             return_value={'health': 'global-fixture'}), \
                patch.object(preview_server, 'effective_flags',
                             return_value={'accounts': True}):
            self.assertEqual(self._request('POST', '/api/interpret', a, {'topic': 'A question'})[1],
                             {'topic': 'A question'})
            self.assertEqual(self._request('POST', '/api/interpret', b, {'topic': 'B question'})[1],
                             {'topic': 'B question'})
            self.assertEqual(self._request('GET', '/api/feed', a)[1],
                             self._request('GET', '/api/feed', b)[1])
            for path in ('/api/status', '/api/health', '/api/flags'):
                with self.subTest(global_path=path):
                    self.assertEqual(self._request('GET', path, a), self._request('GET', path, b))

    def test_every_dispatched_api_route_has_a_security_classification(self):
        allowed = {'GLOBAL', 'READER_SCOPED', 'ACCOUNT'}
        self.assertTrue(set(preview_server.API_ROUTE_CLASSIFICATION.values()) <= allowed)
        dispatchers = {
            'POST': ('_handle_post', '_account_post'),
            'GET': ('_handle_get', '_account_get'),
        }
        discovered = set()
        for method, names in dispatchers.items():
            for name in names:
                tree = ast.parse(textwrap.dedent(inspect.getsource(
                    getattr(preview_server.Handler, name))))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Compare):
                        for value in (node.left, *node.comparators):
                            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                                if value.value.startswith('/api/') and value.value != '/api/':
                                    discovered.add((method, value.value))
                            elif isinstance(value, ast.Set):
                                discovered.update((method, item.value) for item in value.elts
                                                  if isinstance(item, ast.Constant) and
                                                  isinstance(item.value, str) and
                                                  item.value.startswith('/api/'))
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                        if node.func.attr in {'fullmatch', 'startswith'} and node.args and isinstance(node.args[0], ast.Constant):
                            value = node.args[0].value
                            if isinstance(value, str) and value.startswith('/api/') and value != '/api/':
                                key = f're:{value}' if node.func.attr == 'fullmatch' else f'prefix:{value}'
                                discovered.add((method, key))
        self.assertEqual(discovered, set(preview_server.API_ROUTE_CLASSIFICATION))


if __name__ == '__main__':
    unittest.main()
