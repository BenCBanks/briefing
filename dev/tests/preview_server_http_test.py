import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import gzip
import json
from http.server import ThreadingHTTPServer
from threading import Thread
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dev import preview_server


class PreviewStaticHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), preview_server.Handler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, path, headers=None):
        return urlopen(Request(self.base + path, headers=headers or {}))

    def test_static_text_is_gzipped_with_revalidating_etag(self):
        for path in ('/src/app.js', '/styles/base.css', '/content/editions.json'):
            with self.subTest(path=path):
                response = self.request(path, {'Accept-Encoding': 'br, gzip'})
                encoded = response.read()
                self.assertEqual(response.headers.get('Content-Encoding'), 'gzip')
                self.assertEqual(response.headers.get('Vary'), 'Accept-Encoding')
                self.assertEqual(response.headers.get('Cache-Control'), 'public, max-age=0, must-revalidate')
                etag = response.headers.get('ETag')
                self.assertTrue(etag)
                self.assertLess(len(encoded), len(gzip.decompress(encoded)))
                self.assertTrue(gzip.decompress(encoded))
                with self.assertRaises(HTTPError) as cached:
                    self.request(path, {'If-None-Match': etag, 'Accept-Encoding': 'gzip'})
                self.assertEqual(cached.exception.code, 304)

    def test_gzip_quality_zero_is_respected(self):
        response = self.request('/src/app.js', {'Accept-Encoding': 'gzip;q=0, identity'})
        self.assertIsNone(response.headers.get('Content-Encoding'))

    def test_static_asset_cache_reuses_gzip_and_invalidates_changed_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'src'
            source.mkdir()
            asset = source / 'fixture.js'
            original_body = b'export const fixtureValue = "fictional";\n'
            changed_body = b'export const fixtureValue = "updated";\n'
            asset.write_bytes(original_body)
            old_cache = preview_server._STATIC_ASSET_CACHE.copy()
            preview_server._STATIC_ASSET_CACHE.clear()
            try:
                with patch.object(preview_server, 'ROOT', root), \
                     patch.object(preview_server, 'ALLOWED', (source, root / 'styles', root / 'content')):
                    first = self.request('/src/fixture.js', {'Accept-Encoding': 'gzip'})
                    first_etag = first.headers['ETag']
                    self.assertEqual(gzip.decompress(first.read()), original_body)
                    cached = next(iter(preview_server._STATIC_ASSET_CACHE.values()))
                    self.assertIsNotNone(cached[3], 'compressed bytes are retained for repeat requests')
                    second = self.request('/src/fixture.js', {'Accept-Encoding': 'gzip'})
                    self.assertEqual(second.headers['ETag'], first_etag)
                    self.assertEqual(gzip.decompress(second.read()), original_body)

                    asset.write_bytes(changed_body)
                    changed = self.request('/src/fixture.js', {'Accept-Encoding': 'gzip'})
                    self.assertNotEqual(changed.headers['ETag'], first_etag)
                    self.assertEqual(gzip.decompress(changed.read()), changed_body)
            finally:
                preview_server._STATIC_ASSET_CACHE.clear()
                preview_server._STATIC_ASSET_CACHE.update(old_cache)

    def test_static_asset_cache_stays_within_memory_bounds(self):
        old_cache = preview_server._STATIC_ASSET_CACHE.copy()
        preview_server._STATIC_ASSET_CACHE.clear()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for index in range(preview_server._STATIC_ASSET_CACHE_MAX_ENTRIES + 8):
                    asset = root / f'asset-{index}.js'
                    asset.write_bytes(b'x' * 64)
                    preview_server._cached_static_asset(asset, include_gzip=True)
                self.assertLessEqual(len(preview_server._STATIC_ASSET_CACHE),
                                     preview_server._STATIC_ASSET_CACHE_MAX_ENTRIES)
                total = sum(len(row[1]) + (len(row[3]) if row[3] else 0)
                            for row in preview_server._STATIC_ASSET_CACHE.values())
                self.assertLessEqual(total, preview_server._STATIC_ASSET_CACHE_MAX_BYTES)
                oversized = root / 'oversized.js'
                oversized.write_bytes(b'z' * (preview_server._STATIC_ASSET_CACHE_MAX_BYTES + 1))
                preview_server._cached_static_asset(oversized, include_gzip=True)
                total = sum(len(row[1]) + (len(row[3]) if row[3] else 0)
                            for row in preview_server._STATIC_ASSET_CACHE.values())
                self.assertLessEqual(total, preview_server._STATIC_ASSET_CACHE_MAX_BYTES)
                self.assertNotIn(str(oversized.resolve()), preview_server._STATIC_ASSET_CACHE)
        finally:
            preview_server._STATIC_ASSET_CACHE.clear()
            preview_server._STATIC_ASSET_CACHE.update(old_cache)

    def test_edition_query_can_omit_and_restore_history(self):
        payload = {
            'generated': True,
            'edition': {'id': 'current', 'kind': 'daily', 'revision_id': 'r2', 'script_hash': 'hash',
                        'local_date': '2026-11-17', 'sections': [{'id': 'story', 'heading': 'Story', 'body': 'Fiction.',
                        'source_ids': [], 'topic_ids': [], 'audio_script': {'text': 'Spoken.', 'debug_evidence': 'drop'}}],
                        'coverage': [], 'debug_scorecard': {'drop': True}},
            'sources': [{'id': 'source', 'publisher': 'Fictional outlet', 'url': 'https://fictional.example', 'debug': 'drop'}],
            'history': [{'edition': {'id': 'old', 'kind': 'daily', 'revision_id': 'r1', 'script_hash': 'old-hash',
                                     'sections': [], 'coverage': [], 'candidate_records': ['drop']}, 'sources': []}],
        }
        original = preview_server.edition_response
        preview_server.edition_response = lambda profile, include_history=True: (200, payload)
        try:
            today = json.loads(self.request('/api/edition?profile=active&history=0').read())
            default = json.loads(self.request('/api/edition?profile=active').read())
            earlier = json.loads(self.request('/api/edition?profile=active&history=1').read())
        finally:
            preview_server.edition_response = original
        self.assertNotIn('history', today)
        self.assertNotIn('history', default, 'Omitting history also returns the lean current-edition payload')
        self.assertEqual(today['edition']['sections'][0]['audio_script'], {'text': 'Spoken.'})
        self.assertNotIn('debug_scorecard', today['edition'])
        self.assertNotIn('debug', today['sources'][0])
        self.assertEqual(earlier['history'][0]['edition']['id'], 'old')
        self.assertNotIn('candidate_records', earlier['history'][0]['edition'])

    def test_api_json_is_gzipped_when_requested(self):
        original = preview_server.edition_response
        preview_server.edition_response = lambda profile, include_history=True: (200, {
            'generated': True, 'edition': {'id': 'current', 'sections': [
                {'id': 'story', 'heading': 'Fictional story', 'body': 'Fictional story text. ' * 100}]},
            'sources': [], 'history': []})
        try:
            response = self.request('/api/edition?history=0', {'Accept-Encoding': 'gzip'})
            encoded = response.read()
            self.assertEqual(response.headers.get('Content-Encoding'), 'gzip')
            self.assertEqual(response.headers.get('Vary'), 'Accept-Encoding')
            payload = json.loads(gzip.decompress(encoded))
            self.assertEqual(payload['edition']['id'], 'current')
            self.assertEqual(payload['edition']['sections'][0]['body'], 'Fictional story text. ' * 100)
            self.assertLess(len(encoded), len(gzip.decompress(encoded)))
        finally:
            preview_server.edition_response = original

    def test_usage_api_reports_estimated_spend_remaining_and_price_status(self):
        with tempfile.TemporaryDirectory() as directory:
            from dev.ingest.usage import UsageLedger
            ledger = UsageLedger(ledger_dir=directory)
            ledger.record_search('perplexity')
            with patch.object(preview_server, 'UsageLedger', return_value=ledger):
                payload = json.loads(self.request('/api/usage').read())
        self.assertEqual(payload['price_list_status'], 'verified 18 Nov 2026 from aistudio.google.com/docs/pricing')
        self.assertIn('remaining_usd', payload['daily'])
        self.assertIn('remaining_usd', payload['monthly'])
        self.assertEqual(payload['daily']['search_queries'], {'perplexity': 1})

    def test_health_endpoint_returns_only_count_summary(self):
        with patch.object(preview_server, 'status_response', return_value=(200, {
                'ok': True, 'last_run_at': '2026-11-18T11:00:00+00:00',
                'edition_text': 'must not appear'})), \
             patch.object(preview_server.feed_health_status, 'counts', return_value={
                 'enabled': 4, 'backing_off': 1, 'disabled_permanent': 2}), \
             patch.object(preview_server.generation_api_module, '_read_feed_health', return_value={}), \
             patch.object(preview_server.ingest_core, 'load_registry', return_value=[]), \
             patch.object(preview_server.EPISODE_JOBS, 'health_counts', return_value={
                 'by_state': {'running': 1}, 'by_stage': {'writing': 1},
                 'by_audio_status': {'recorded': 1}}), \
             patch.object(preview_server, 'process_ledger') as ledger_factory, \
             patch.object(preview_server, 'edition_response', return_value=(200, {
                 'edition': {'audio': {'audio_status': 'ready'}, 'body': 'private text'}})):
            ledger_factory.return_value.summary.return_value = {
                'daily': {'estimated_cost_usd': '0.1', 'cap_usd': '0.5', 'remaining_usd': '0.4'},
                'monthly': {'estimated_cost_usd': '1', 'cap_usd': '5', 'remaining_usd': '4'},
            }
            payload = json.loads(self.request('/api/health').read())
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['feeds']['enabled'], 4)
        self.assertEqual(payload['feeds']['backing_off'], 1)
        self.assertEqual(payload['jobs']['by_state']['running'], 1)
        self.assertEqual(payload['audio']['daily_status'], 'ready')
        self.assertNotIn('edition_text', repr(payload))
        self.assertNotIn('private text', repr(payload))
        self.assertNotIn('job_id', repr(payload))


if __name__ == '__main__':
    unittest.main()
