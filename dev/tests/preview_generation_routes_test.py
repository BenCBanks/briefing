import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import preview_server
from ingest import edition_api, generation_api, score_edition, write_edition


class PreviewGenerationRouteTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), preview_server.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)
        encoded = json.dumps(body).encode() if body is not None else None
        connection.request(method, path, body=encoded, headers={'Content-Type': 'application/json'} if encoded else {})
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        return response.status, payload

    def test_generate_job_and_progress_routes_return_json(self):
        with patch.object(preview_server, 'start_generation', return_value=(202, {'job_id': 'abc', 'status': 'queued'})):
            status, body = self.request('POST', '/api/generate', {'profile': {'topics': []}})
        self.assertEqual((status, body), (202, {'job_id': 'abc', 'status': 'queued'}))
        with patch.object(preview_server, 'job_response', return_value=(200, {
            'job_id': 'abc', 'status': 'running', 'progress': {'stage': 'fetching', 'message': 'Fetching 2 of 5 feeds'}})):
            status, body = self.request('GET', '/api/generate/abc')
        self.assertEqual(status, 200)
        self.assertEqual(body['progress']['message'], 'Fetching 2 of 5 feeds')

    def test_profile_save_route_forwards_profile_without_logging(self):
        profile = {'version': 1, 'topics': [], 'words': 'Fictional profile'}
        with patch.object(preview_server, 'save_profile', return_value=(200, {'saved': True})) as save:
            status, body = self.request('POST', '/api/profile', {'profile': profile})
        self.assertEqual((status, body), (200, {'saved': True}))
        save.assert_called_once_with(profile)

    def test_profile_read_route_returns_the_saved_profile(self):
        profile = {'version': 1, 'topics': [], 'words': 'Fictional saved profile'}
        with patch.object(preview_server, 'profile_response', return_value=(200, {'profile': profile})) as read:
            status, body = self.request('GET', '/api/profile')
        self.assertEqual((status, body), (200, {'profile': profile}))
        read.assert_called_once_with()

    def test_seen_edition_route_forwards_profile_edition_identity(self):
        payload = {'profile_id': 'a' * 24, 'edition_id': 'fictional-daily', 'revision_id': 'fictional-r1'}
        with patch.object(preview_server.generation_api_module, 'mark_edition_seen',
                          return_value=(200, {'seen': True, 'changed': True})) as mark:
            status, body = self.request('POST', '/api/edition/seen', payload)
        self.assertEqual(status, 200)
        self.assertEqual(body, {'seen': True, 'changed': True})
        mark.assert_called_once_with(payload['profile_id'], payload['edition_id'], payload['revision_id'])

    def test_real_shaped_profile_posts_generates_polls_and_reads_published_edition(self):
        # Match the legacy saved-profile shape: words, with no profile id or
        # interpreted topic rows. This is enough for /api/edition?profile=active
        # to resolve the existing pre-profile edition before the next build.
        profile = {'version': 1, 'words': 'Fictional payments profile', 'topics': []}
        source = {'id': 'payments-test', 'name': 'Fictional payments feed',
                  'url': 'https://example.org/feed.xml', 'format': 'rss', 'topics': ['payments'],
                  'enabled': True, 'robots_ok': True}
        edition = {'id': 'generated-payments', 'revision_id': 'generated-payments-r1',
                   'kind': 'daily', 'generated': True, 'generated_at': '2026-11-17T12:00:00Z',
                   'summary': 'Three fictional payment stories', 'coverage': [],
                   'sections': [{'id': f'payments-story-{index}', 'cluster_id': f'payments-cluster-{index}',
                                 'heading': f'Fictional payment story {index}', 'body': 'Fictional details.',
                                 'topic_ids': ['payments'], 'source_ids': []} for index in range(1, 4)]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated = root / 'generated'
            legacy = {'id': 'legacy-daily', 'revision_id': 'legacy-r1', 'kind': 'daily',
                      'generated': True, 'sections': [{'id': 'legacy-story', 'source_ids': ['legacy-source']}]}
            older = {'id': 'legacy-older', 'revision_id': 'legacy-older-r1', 'kind': 'daily',
                     'generated': True, 'sections': [{'id': 'older-story', 'source_ids': ['legacy-old-source']}]}
            legacy_sources = {'sources': [{'id': 'legacy-source', 'publisher': 'Fictional Legacy Publisher'}]}
            older_sources = {'sources': [{'id': 'legacy-old-source', 'publisher': 'Fictional Older Publisher'}]}
            legacy_bundle = {'editions': {'editions': [legacy]}, 'sources': legacy_sources,
                             'scorecard': {'critical_failed': False}}
            older_bundle = {'editions': {'editions': [older]}, 'sources': older_sources}
            generated.mkdir()
            (generated / 'current.json').write_text(json.dumps(legacy_bundle))
            (generated / 'editions.json').write_text(json.dumps(legacy_bundle['editions']))
            (generated / 'sources.json').write_text(json.dumps(legacy_sources))
            (generated / 'editions-history.json').write_text(json.dumps([legacy_bundle, older_bundle]))
            patches = [
                patch.object(generation_api, 'STATUS_PATH', root / 'status.json'),
                patch.object(generation_api, 'FEED_HEALTH_PATH', root / 'feed-health.json'),
                patch.object(generation_api, 'GENERATION_LOCK_PATH', root / 'generation.lock'),
                patch.object(generation_api, 'PROFILE_PATH', root / 'profile.json'),
                patch.object(generation_api, 'GENERATED_DIR', generated),
                patch.object(edition_api, 'GENERATED_DIR', generated / 'default'),
                patch.object(edition_api, 'LEGACY_GENERATED_DIR', generated),
                patch.object(preview_server, 'save_profile', side_effect=lambda value: generation_api.save_profile(value, root / 'profile.json')),
                patch.object(preview_server, 'start_generation', side_effect=lambda value, **kwargs: generation_api.start_generation(value, registry=[source])),
                patch.object(generation_api.core, 'ingest_enabled', return_value={
                    'parsed_items': 1, 'fetched_sources': 1, 'errors': 0,
                    'source_results': {'payments-test': 'ok'}}),
                patch.object(write_edition, 'generate_edition', return_value=(
                    {'editions': [edition]}, {'sources': []}, {'sentence_evidence': {}})),
                patch.object(score_edition, 'score_edition', return_value={
                    'critical_failed': False, 'failed_critical': [], 'results': []}),
            ]
            for active_patch in patches:
                active_patch.start()
            try:
                self.assertEqual(self.request('POST', '/api/profile', {'profile': profile}),
                                 (200, {'saved': True}))
                active_legacy_status, active_legacy = self.request('GET', '/api/edition?profile=active&history=1')
                self.assertEqual(active_legacy_status, 200)
                self.assertTrue(active_legacy['generated'])
                self.assertEqual(active_legacy['edition']['id'], 'legacy-daily')
                self.assertEqual(active_legacy['history'][0]['edition']['id'], 'legacy-older')
                active_dir = generation_api.profile_generated_dir(profile, generated)
                self.assertTrue((active_dir / 'current.json').is_file())

                default_legacy_status, default_legacy = self.request('GET', '/api/edition')
                self.assertEqual(default_legacy_status, 200)
                self.assertTrue(default_legacy['generated'])
                self.assertEqual(default_legacy['edition']['id'], 'legacy-daily')
                explicit_default_status, explicit_default = self.request('GET', '/api/edition?profile=default')
                self.assertEqual(explicit_default_status, 200)
                self.assertEqual(explicit_default['edition']['id'], 'legacy-daily')

                status, started = self.request('POST', '/api/generate', {'profile': profile})
                self.assertEqual(status, 202)
                self.assertTrue(started.get('job_id'))
                for _ in range(100):
                    status, job = self.request('GET', '/api/generate/' + started['job_id'])
                    if job.get('status') in {'complete', 'failed'}:
                        break
                    time.sleep(0.01)
                self.assertEqual((status, job.get('status')), (200, 'complete'), job)
                status, published = self.request('GET', '/api/edition?profile=active&history=1')
                self.assertEqual(status, 200)
                self.assertTrue(published['generated'])
                self.assertEqual(published['edition']['id'], 'generated-payments')
                self.assertEqual([row['edition']['id'] for row in published['history']],
                                 ['legacy-daily', 'legacy-older'])
                self.assertTrue((active_dir / 'current.json').is_file())
                default_status, default = self.request('GET', '/api/edition')
                self.assertEqual(default_status, 200)
                self.assertTrue(default['generated'])
                self.assertEqual(default['edition']['id'], 'generated-payments')
                active_status, active = self.request('GET', '/api/edition?profile=active')
                self.assertEqual(active_status, 200)
                self.assertEqual(active['edition']['id'], default['edition']['id'])
                explicit_default_status, explicit_default = self.request('GET', '/api/edition?profile=default')
                self.assertEqual(explicit_default_status, 200)
                self.assertEqual(explicit_default['edition']['id'], 'legacy-daily')
                self.assertEqual(json.loads((root / 'profile.json').read_text()), profile)
            finally:
                for active_patch in reversed(patches):
                    active_patch.stop()

    def test_status_route_returns_the_single_run_status_document(self):
        payload = {'last_run_at': '2026-11-17T11:02:00Z', 'ok': True,
                   'items_fetched': 12, 'stories_written': 6, 'feeds_disabled': [], 'reason': None}
        with patch.object(preview_server, 'status_response', return_value=(200, payload)):
            status, body = self.request('GET', '/api/status')
        self.assertEqual(status, 200)
        self.assertEqual(body, payload)


if __name__ == '__main__':
    unittest.main()
