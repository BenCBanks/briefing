"""Offline fixtures for the public feed-health report tool."""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from datetime import datetime, timedelta, timezone
import json
import tempfile
from threading import Barrier
import unittest
from urllib.error import HTTPError
import xml.etree.ElementTree as ET

from dev.tools import feed_health

NOW = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
SOURCE = {'id': 'fixture-feed', 'name': 'Fictional feed',
          'url': 'https://fixture.example/feed.xml', 'format': 'rss', 'topics': ['fiction'],
          'enabled': True, 'robots_ok': True}


class FeedHealthToolTests(unittest.TestCase):
    def inspect(self, outcome):
        def fetch(_source, **_kwargs):
            if isinstance(outcome, Exception):
                raise outcome
            return outcome, {}
        return feed_health.inspect_feed(SOURCE, fetch, now=NOW)

    def test_valid_items_report_count_and_newest_date(self):
        items = [{'published': '2026-11-20T10:00:00Z'},
                 {'published': '2026-11-24T10:00:00Z'}, {'published': None}]
        result = self.inspect(items)
        self.assertEqual((result['status'], result['valid_xml'], result['item_count']), ('OK', True, 3))
        self.assertEqual(result['newest_item'], '2026-11-24')

    def test_empty_valid_feed_is_distinct_from_invalid_xml(self):
        empty = self.inspect([])
        invalid = self.inspect(ET.ParseError('bad feed'))
        self.assertEqual((empty['valid_xml'], empty['item_count']), (True, 0))
        self.assertEqual((invalid['status'], invalid['valid_xml']), ('Invalid XML', False))

    def test_known_robots_block_is_never_fetched_and_host_delay_has_sixty_second_floor(self):
        calls = []
        def fetch(source, **_kwargs):
            calls.append(source)
            return [], {}
        denied = feed_health.inspect_feed({**SOURCE, 'robots_ok': False}, fetch, now=NOW)
        self.assertEqual(denied['status'], 'Robots disallowed')
        self.assertEqual(calls, [], 'known robots restrictions prevent feed requests')
        feed_health.inspect_feed({**SOURCE, 'minimum_interval_seconds': 1}, fetch, now=NOW)
        self.assertEqual(calls[0]['minimum_interval_seconds'], 60)

    def test_http_robots_network_and_unsafe_failures_are_reported_safely(self):
        http = HTTPError(SOURCE['url'], 404, 'missing', {}, None)
        cases = [(http, 'HTTP 404'), (PermissionError(), 'Robots disallowed'),
                 (TimeoutError(), 'Network error'), (ValueError(), 'Unsafe or invalid feed response')]
        for error, expected in cases:
            with self.subTest(expected=expected):
                row = self.inspect(error)
                self.assertEqual(row['status'], expected)
                self.assertIsNone(row['valid_xml'])
                self.assertIsNone(row['item_count'])

    def test_stale_feed_is_flagged_but_not_deleted(self):
        result = {'id': SOURCE['id'], 'name': SOURCE['name'], 'url': SOURCE['url'],
                  'status': 'OK', 'valid_xml': True, 'item_count': 1,
                  'newest_item': (NOW - timedelta(days=46)).date().isoformat()}
        report = feed_health.render_report([result], checked_at=NOW, history={SOURCE['id']: {'weekly_failures': 0}})
        self.assertIn('newest item is over 45 days old', report)
        self.assertIn('never deletes registry entries', report)

    def test_three_weekly_failures_flag_removal_and_success_resets_streak(self):
        history = {}
        results = [{'id': SOURCE['id'], 'status': 'HTTP 500'}]
        for week in range(3):
            feed_health._history_update(results, history, NOW + timedelta(days=7 * week))
        self.assertEqual(history[SOURCE['id']]['weekly_failures'], 3)
        results[0]['weekly_failures'] = history[SOURCE['id']]['weekly_failures']
        results[0].update(name=SOURCE['name'], url=SOURCE['url'], valid_xml=None,
                          item_count=None, newest_item=None)
        report = feed_health.render_report(results, checked_at=NOW + timedelta(days=14), history=history)
        self.assertIn('3 weekly checks failed', report)
        results[0]['status'] = 'OK'
        feed_health._history_update(results, history, NOW + timedelta(days=21))
        self.assertEqual(history[SOURCE['id']]['weekly_failures'], 0)

    def test_run_checks_each_feed_once_and_writes_report_and_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = _Path(temporary)
            registry = root / 'registry.json'
            registry.write_text(json.dumps({'version': 1, 'sources': [SOURCE,
                {**SOURCE, 'id': 'not-a-feed', 'format': 'html'}]}), encoding='utf-8')
            calls = []
            def fetch(source, **options):
                calls.append((source['id'], options.get('robots_cache_path')))
                return ([{'published': '2026-11-24T00:00:00Z'}], {})
            report_dir, history_path = root / 'reports', root / 'history.json'
            results = feed_health.run(registry_path=registry, report_dir=report_dir,
                                      history_path=history_path, checked_at=NOW, fetcher=fetch)
            self.assertEqual(len(results), 1)
            self.assertEqual([call[0] for call in calls], ['fixture-feed'])
            self.assertTrue(calls[0][1].is_relative_to(root) is False,
                            'temporary robots cache is isolated from fixture and live state paths')
            report = report_dir / 'feed-health-2026-11-25.md'
            self.assertIn('Fictional feed', report.read_text(encoding='utf-8'))
            self.assertEqual(json.loads(history_path.read_text())['version'], 1)

    def test_run_checks_distinct_hosts_concurrently_but_keeps_host_groups_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = _Path(temporary)
            registry = root / 'registry.json'
            second = {**SOURCE, 'id': 'second-fixture-feed', 'name': 'Second fictional feed',
                      'url': 'https://second.example/feed.xml'}
            registry.write_text(json.dumps({'version': 1, 'sources': [SOURCE, second]}), encoding='utf-8')
            both_started = Barrier(2)
            def fetch(_source, **_options):
                both_started.wait(timeout=3)
                return [], {}
            results = feed_health.run(registry_path=registry, report_dir=root / 'reports',
                                      history_path=root / 'history.json', checked_at=NOW,
                                      fetcher=fetch, max_workers=2)
            self.assertEqual({row['status'] for row in results}, {'OK'})


if __name__ == '__main__':
    unittest.main()
