import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
from threading import Thread
import unittest
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from pathlib import Path
import tempfile
from unittest.mock import patch

from dev import preview_server
from dev.api_access import is_api_request_allowed, trusted_probe_address


PROFILE = {'version': 1, 'words': 'Fictional interests', 'minutes': 10,
           'topics': [], 'muted': []}
JOB_ID = 'a' * 32


class AddressInjectedServer(ThreadingHTTPServer):
    def get_request(self):
        request, address = super().get_request()
        return request, (self.injected_address, address[1])


class ApiAccessRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = AddressInjectedServer(('127.0.0.1', 0), preview_server.Handler)
        cls.server.injected_address = '127.0.0.1'
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.patches = ExitStack()
        self.patches.enter_context(patch.object(preview_server, 'profile_response',
                                                return_value=(200, {'profile': PROFILE})))
        self.patches.enter_context(patch.object(preview_server, 'edition_response',
                                                return_value=(200, {'generated': False, 'edition': None,
                                                                    'sources': [], 'history': []})))
        self.patches.enter_context(patch.object(preview_server, 'status_response',
                                                return_value=(200, {'ok': True, 'last_run_at': '2026-11-18'})))
        self.patches.enter_context(patch.object(preview_server, 'feed_response',
                                                return_value=(200, {'items': []})))
        self.patches.enter_context(patch.object(preview_server, 'job_response',
                                                return_value=(200, {'job_id': JOB_ID, 'status': 'done'})))
        jobs = SimpleNamespace(
            list=lambda _profile: {'jobs': []}, get=lambda _profile, _job_id: None,
            cancel=lambda _profile, _job_id: (200, {'cancelled': True}),
            submit=lambda _profile, _payload: (202, {'job_id': JOB_ID, 'status': 'queued'}))
        self.patches.enter_context(patch.object(preview_server, 'EPISODE_JOBS', jobs))
        correction_service = SimpleNamespace(
            list=lambda *_args, **_kwargs: [], revision=lambda *_args: {},
            apply_edition=lambda edition, _profile: edition,
            apply_job=lambda job, _profile: job,
            create=lambda *_args: {'correction_id': 'b' * 32})
        self.patches.enter_context(patch.object(preview_server, 'CORRECTIONS', correction_service))
        self.patches.enter_context(patch.object(preview_server, 'process_ledger', return_value=SimpleNamespace(
            summary=lambda: {'ok': True, 'daily': {}, 'monthly': {}}, config={})))
        self.patches.enter_context(patch.object(preview_server, 'effective_flags', return_value={
            'episodes': {'value': 'real', 'default': 'real', 'source': 'default'},
            'retrieval_fill': {'value': True, 'default': False, 'source': 'file'}}))
        self.patches.enter_context(patch.object(preview_server, 'save_profile', return_value=(200, {'saved': True})))
        self.patches.enter_context(patch.object(preview_server, 'start_generation',
                                                return_value=(202, {'job_id': JOB_ID})))
        self.patches.enter_context(patch.object(preview_server, 'interpret_request',
                                                return_value=(200, {'profile': PROFILE})))
        self.patches.enter_context(patch.object(preview_server.privacy, 'export_profile',
                                                return_value=({'profile': PROFILE}, 'fixture-token')))
        self.patches.enter_context(patch.object(preview_server.privacy, 'delete_profile',
                                                return_value={'deleted': True}))

    def tearDown(self):
        self.patches.close()

    def request(self, address, path, method='GET', payload=None):
        self.server.injected_address = address
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(self.base + path, data=data, method=method,
                          headers={'Content-Type': 'application/json'} if data else {})
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read() or b'{}')
        except HTTPError as error:
            return error.code, json.loads(error.read() or b'{}')

    def test_network_policy_and_every_get_route_for_loopback_tailscale_and_public(self):
        normal_routes = [
            '/api/profile', '/api/edition?profile=active', '/api/status', '/api/health',
            '/api/feed?topic=payments', '/api/episodes', f'/api/episodes/{JOB_ID}',
            f'/api/episodes/{JOB_ID}/audio', f'/api/episodes/{JOB_ID}/captions.vtt',
            '/api/generate/fixture-job', '/api/corrections',
            '/api/revisions/episode/fixture-job/revision-1',
        ]
        sensitive_routes = ['/api/profile/export', '/api/usage', '/api/flags']
        for path in normal_routes:
            for address in ('127.0.0.1', '100.64.0.10'):
                with self.subTest(path=path, address=address):
                    status, payload = self.request(address, path)
                    self.assertNotEqual((status, payload.get('error')), (403, 'local_only'))
            status, payload = self.request('203.0.113.9', path)
            self.assertEqual((status, payload.get('error')), (403, 'local_only'))
        for path in sensitive_routes:
            status, payload = self.request('127.0.0.1', path)
            self.assertNotEqual(payload.get('error'), 'local_only')
            for address in ('100.64.0.10', '203.0.113.9'):
                status, payload = self.request(address, path)
                self.assertEqual((status, payload.get('error')), (403, 'local_only'))
        flags_status, flags = self.request('127.0.0.1', '/api/flags')
        self.assertEqual(flags_status, 200)
        self.assertEqual(flags['flags']['retrieval_fill']['source'], 'file')
        status, health = self.request('100.64.0.10', '/api/health')
        self.assertEqual(status, 200)
        self.assertEqual(set(health), {'ok', 'daily_run_ok', 'last_run_at', 'daily_run',
                                       'feeds', 'jobs', 'usage', 'audio'})
        self.assertEqual(set(health['feeds']), {'available', 'enabled', 'backing_off', 'disabled_permanent'})
        self.assertEqual(set(health['jobs']), {'available', 'by_state', 'by_stage'})
        self.assertEqual(set(health['usage']), {'available', 'today', 'month'})
        self.assertEqual(set(health['audio']), {'daily_status', 'episodes_by_status'})

    def test_every_post_route_is_normal_or_loopback_only_as_documented(self):
        normal_routes = [
            ('/api/interpret', {'words': 'Fictional input'}),
            ('/api/profile', {'profile': PROFILE}),
            ('/api/generate', {'profile': PROFILE}),
            ('/api/episodes', {'topic': 'fictional topic', 'length': 5, 'style': 'brief'}),
            (f'/api/episodes/{JOB_ID}/cancel', None),
        ]
        sensitive_routes = [('/api/corrections', {'target_type': 'episode'}),
                            ('/api/profile/delete', {'confirmation_token': 'fixture-token'})]
        for path, payload in normal_routes:
            for address in ('127.0.0.1', '100.64.0.10'):
                with self.subTest(path=path, address=address):
                    status, body = self.request(address, path, 'POST', payload)
                    self.assertNotEqual((status, body.get('error')), (403, 'local_only'))
            status, body = self.request('203.0.113.9', path, 'POST', payload)
            self.assertEqual((status, body.get('error')), (403, 'local_only'))
        for path, payload in sensitive_routes:
            status, body = self.request('127.0.0.1', path, 'POST', payload)
            self.assertNotEqual(body.get('error'), 'local_only')
            for address in ('100.64.0.10', '203.0.113.9'):
                status, body = self.request(address, path, 'POST', payload)
                self.assertEqual((status, body.get('error')), (403, 'local_only'))
        self.assertEqual(self.request('127.0.0.1', '/api/profile', 'DELETE'),
                         (405, {'error': 'method_not_allowed'}))
        for address in ('100.64.0.10', '203.0.113.9'):
            status, body = self.request(address, '/api/profile', 'DELETE')
            self.assertEqual((status, body.get('error')), (403, 'local_only'))

    def test_default_tailscale_network_and_configured_lan_cidrs(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'api-access.json'
            config.write_text(json.dumps({'additional_lan_cidrs': ['192.168.50.0/24', '0.0.0.0/0',
                                                                    '192.0.2.0/24', 'bad']}))
            self.assertTrue(is_api_request_allowed('GET', '/api/status', '100.64.0.10', config))
            self.assertTrue(is_api_request_allowed('GET', '/api/status', '192.168.50.70', config))
            self.assertFalse(is_api_request_allowed('GET', '/api/status', '203.0.113.9', config))
            self.assertEqual(trusted_probe_address(['127.0.0.1', '192.168.50.70'], config), '192.168.50.70')


if __name__ == '__main__':
    unittest.main()
