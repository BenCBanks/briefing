"""Mocked fetches and clock-controlled health updates; no live state or network."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
import xml.etree.ElementTree as ET

from dev.ingest import core, feed_health, generation_api
from dev.morning_check import feed_health_lines

NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
SOURCE = {'id': 'fictional', 'url': 'https://fictional.example/feed', 'format': 'rss',
          'enabled': True, 'robots_ok': True, 'topics': [], 'minimum_interval_seconds': 1}


class FeedHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'feed-health.json'
        self.health = {}

    def record(self, outcome, hours=0):
        return feed_health.record('fictional', self.health, ok=outcome in feed_health.SUCCESS,
                                  outcome=outcome, now=NOW + timedelta(hours=hours))

    def update(self, results, health=None, now=NOW):
        registry = [dict(SOURCE, id=identifier) for identifier in results]
        diagnostics = {}
        disabled = generation_api._update_feed_health(registry, self.health if health is None else health,
                    results, now=now, health_path=self.path, diagnostics=diagnostics)
        return disabled, diagnostics

    def test_transient_backoff_uses_separated_failures_and_caps_at_24_hours(self):
        for outcome in ('timeout', 'http_429', 'http_500', 'http_503', 'http_599', 'network_error', 'error'):
            with self.subTest(outcome=outcome):
                self.health = {}
                for hours, streak, wait in ((0, 1, 1), (0, 1, 1), (1, 1, 1),
                                             (5.999, 1, 1), (6, 2, 6), (12, 3, 24), (36, 4, 24)):
                    row = self.record(outcome, hours)
                    self.assertEqual(row['consecutive_failures'], streak)
                    # Duplicate reports do not extend a pending retry.
                    if hours != 0 or streak != 1:
                        self.assertEqual(row['disabled_until'],
                                         core.iso_date(NOW + timedelta(hours=hours + wait)))
                    self.assertEqual(row['retry_policy'], 'transient')
                    self.assertEqual(row['last_error'], 'transient')
                    self.assertEqual(row['first_failure_at'], core.iso_date(NOW))
                    self.assertEqual(row['retry_count'], 0)
                    self.assertFalse(row['retired'])
                self.assertEqual(core.parse_date(row['disabled_until']), NOW + timedelta(hours=60))

    def test_duplicate_report_does_not_extend_pending_backoff(self):
        row = self.record('timeout')
        original = row['disabled_until']
        row = self.record('timeout', .5)
        self.assertEqual(row['disabled_until'], original)
        self.assertEqual(row['consecutive_failures'], 1)
        row = self.record('http_404', .5)
        original = row['disabled_until']
        row = self.record('http_404', 1)
        self.assertEqual(row['disabled_until'], original)
        self.assertEqual(row['consecutive_failures'], 1)

    def test_success_with_existing_quality_rule_still_clears_failure_streak(self):
        self.record('timeout')
        row = feed_health.record('fictional', self.health, ok=True, duplicate_rate=.9, now=NOW)
        self.assertEqual(row['consecutive_failures'], 0)
        self.assertIsNone(row['first_failure_at'])
        self.assertEqual(row['reason'], 'duplicate_rate_over_80_percent')

    def test_success_immediately_resets_streak_even_inside_six_hours(self):
        for outcome in ('ok', 'not_modified'):
            self.health = {}
            self.record('timeout')
            row = self.record(outcome, .1)
            for key in ('first_failure_at', 'last_error', 'last_failure', 'disabled_until', 'last_counted_failure_at'):
                self.assertIsNone(row[key])
            self.assertEqual(row['consecutive_failures'], 0)
            self.assertFalse(row['disabled'])
            self.assertEqual(row['last_success_at'], core.iso_date(NOW + timedelta(hours=.1)))
            row = self.record('timeout', .2)
            self.assertEqual(row['consecutive_failures'], 1)
            self.assertEqual(core.parse_date(row['disabled_until']), NOW + timedelta(hours=1.2))

    def test_permanent_errors_have_weekly_retry_and_safe_reason(self):
        for outcome in ('http_404', 'http_410', 'robots_disallowed', 'http_401', 'http_403'):
            self.health = {}
            row = self.record(outcome)
            self.assertEqual(row['last_error'], 'permanent')
            self.assertEqual(row['last_failure'], outcome)
            self.assertEqual(core.parse_date(row['disabled_until']), NOW + timedelta(days=7))
            self.assertEqual(generation_api._ready_sources([SOURCE], self.health, NOW), [])
            self.assertEqual(generation_api._ready_sources([SOURCE], self.health, NOW + timedelta(days=7)), [SOURCE])
            self.assertEqual(row['consecutive_failures'], 1)

    def test_parse_failures_require_three_separated_observations(self):
        for hours, streak, policy in ((0, 1, 'transient'), (1, 1, 'transient'),
                                      (6, 2, 'transient'), (12, 3, 'weekly')):
            row = self.record('parse_error', hours)
            self.assertEqual(row['consecutive_failures'], streak)
            self.assertEqual(row['retry_policy'], policy)
        self.assertEqual(core.parse_date(row['disabled_until']), NOW + timedelta(hours=12, days=7))
        self.assertEqual(row['last_error'], 'permanent')

    def test_network_errors_do_not_supply_parse_failure_streak(self):
        self.record('timeout')
        self.record('timeout', 6)
        row = self.record('parse_error', 12)
        self.assertEqual(row['parse_failure_count'], 1)
        self.assertEqual(row['retry_policy'], 'transient')

    def test_outage_freezes_every_row_and_file_including_due_probe_and_success(self):
        self.health = {'a': {'disabled': True, 'disabled_until': core.iso_date(NOW),
                            'retry_count': 3, 'consecutive_failures': 3},
                       'b': {'consecutive_failures': 2}, 'c': {'last_success_at': core.iso_date(NOW)}}
        original = copy.deepcopy(self.health)
        self.path.write_text(json.dumps(original))
        original_bytes = self.path.read_bytes()
        generation_api._ready_sources([dict(SOURCE, id='a')], self.health, NOW)
        _, diagnostics = self.update({'a': 'robots_disallowed', 'b': 'http_404', 'c': 'ok'})
        self.assertEqual(self.health, original)
        self.assertEqual(self.path.read_bytes(), original_bytes)
        self.assertEqual(diagnostics, {'feed_health_event': 'outage', 'feed_health_attempted': 3,
                                      'feed_health_failed': 2})

    def test_exactly_half_failed_is_not_an_outage_and_success_is_reset(self):
        self.health = {'b': {'consecutive_failures': 2}}
        _, diagnostics = self.update({'a': 'http_429', 'b': 'not_modified'})
        self.assertIsNone(diagnostics['feed_health_event'])
        self.assertEqual(self.health['a']['retry_policy'], 'transient')
        self.assertEqual(self.health['b']['consecutive_failures'], 0)
        self.assertEqual(json.loads(self.path.read_text()), self.health)

    def test_skipped_and_unknown_sources_do_not_dilute_outage_denominator(self):
        diagnostics = {}
        registry = [dict(SOURCE, id=x) for x in ('a', 'b', 'c')]
        generation_api._update_feed_health(registry, {},
            {'a': 'timeout', 'b': 'recent_feed_article_first', 'c': 'kill_switch', 'outsider': 'ok'},
            now=NOW, health_path=self.path, diagnostics=diagnostics)
        self.assertEqual(diagnostics['feed_health_attempted'], 1)
        self.assertEqual(diagnostics['feed_health_event'], 'outage')

    def test_empty_run_has_no_outage(self):
        _, diagnostics = self.update({})
        self.assertIsNone(diagnostics['feed_health_event'])

    def test_old_attempt_streak_does_not_cause_early_escalation(self):
        self.health = {'fictional': {'consecutive_failures': 150, 'retry_count': 4,
                                    'disabled': True, 'disabled_until': core.iso_date(NOW)}}
        row = self.record('timeout')
        self.assertEqual(row['consecutive_failures'], 1)
        self.assertEqual(row['retry_policy'], 'transient')
        self.assertEqual(core.parse_date(row['disabled_until']), NOW + timedelta(hours=1))
        self.assertFalse(row['retired'])

    def test_legacy_weekly_rows_stay_restricted_until_due(self):
        self.health = {'fictional': {'consecutive_failures': 3, 'disabled': True,
                                    'disabled_until': core.iso_date(NOW + timedelta(days=2))}}
        self.assertEqual(generation_api._ready_sources([SOURCE], self.health, NOW), [])
        self.assertEqual(feed_health.counts(self.health, now=NOW)['disabled_permanent'], 1)

    def test_administrator_health_reset_cannot_override_registry_permissions(self):
        for changes in ({'enabled': False}, {'robots_ok': False}, {'ai_bots_disallowed': True}):
            self.assertEqual(generation_api._ready_sources([dict(SOURCE, **changes)], {}, NOW), [])
        # Even after administrator recovery a fresh robots denial remains permanent.
        self.health = {}
        row = self.record('robots_disallowed')
        self.assertEqual(row['retry_policy'], 'weekly')

    def test_quiet_feed_success_after_months_does_not_disable_it(self):
        self.health = {'fictional': {'last_new_item_at': core.iso_date(NOW - timedelta(days=90))}}
        row = feed_health.record('fictional', self.health, ok=True, new_items=False,
                                 normal_interval_seconds=604800, now=NOW)
        self.assertFalse(row['disabled'])
        self.assertEqual(row['last_success_at'], core.iso_date(NOW))

    def test_status_roundtrip_and_morning_counts(self):
        future = core.iso_date(core.utc_now() + timedelta(days=1))
        health = {'a': {}, 'b': {'disabled': True, 'retry_policy': 'transient', 'disabled_until': future},
                  'c': {'disabled': True, 'disabled_until': future}, 'd': {'retired': True}}
        counts = feed_health.counts(health)
        self.assertEqual(counts, dict(enabled=1, backing_off=1, disabled_permanent=2))
        run = generation_api._run_status(True, counts={'feed_health_counts': counts,
            'feed_health_event': 'outage', 'feed_health_attempted': 4, 'feed_health_failed': 3}, path=self.path)
        _, public = generation_api.status_response(self.path)
        for key in ('feed_health_counts', 'feed_health_event', 'feed_health_attempted', 'feed_health_failed'):
            self.assertEqual(public[key], run[key])
        self.assertEqual(feed_health_lines(health)[0],
                         'Feed health: 1 enabled / 1 backing_off / 2 disabled_permanent (4 tracked)')

    def test_registry_counts_include_untracked_sources_and_permissions(self):
        registry = [SOURCE, dict(SOURCE, id='b'), dict(SOURCE, id='c', robots_ok=False)]
        self.assertEqual(feed_health.counts({}, registry, NOW),
                         dict(enabled=2, backing_off=0, disabled_permanent=1))


class FetchClassificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Path(self.temp.name) / 'items.json'

    def test_fetch_errors_survive_ingestion_with_specific_classes(self):
        cases = [(TimeoutError(), 'timeout'), (socket.gaierror(), 'network_error'),
                 (ConnectionResetError(), 'network_error'), (URLError('network'), 'network_error'),
                 (ET.ParseError(), 'parse_error'), (json.JSONDecodeError('bad', '', 0), 'parse_error'),
                 (PermissionError(), 'robots_disallowed')]
        cases += [(HTTPError(SOURCE['url'], code, 'private upstream text', {}, None), f'http_{code}')
                  for code in (429, 500, 503, 404, 410)]
        for error, expected in cases:
            with self.subTest(expected=expected), patch.object(core, 'fetch_source', side_effect=error), \
                    patch.object(core, 'KILL_SWITCH_PATH', self.store.with_name('disabled')):
                result = core.ingest_enabled([SOURCE], self.store, now=NOW)
                self.assertEqual(result['source_results'], {'fictional': expected})
                self.assertNotIn('private upstream text', json.dumps(result))

    def test_dns_resolution_failure_is_transient_but_private_addresses_stay_blocked(self):
        with self.assertRaises(URLError):
            core._public_addresses(SOURCE['url'], {'fictional.example'},
                                   resolver=lambda *_a, **_k: (_ for _ in ()).throw(socket.gaierror()))
        with self.assertRaises(ValueError):
            core._public_addresses(SOURCE['url'], {'fictional.example'},
                resolver=lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))])

    def test_temporary_robots_errors_are_not_cached_as_denials_or_allowed(self):
        for error in (TimeoutError(), socket.gaierror(), ConnectionError(), URLError('DNS'),
                      HTTPError(SOURCE['url'], 429, 'busy', {}, None),
                      HTTPError(SOURCE['url'], 503, 'busy', {}, None)):
            with self.subTest(error=type(error).__name__), patch.object(core, '_wait_for_host'), \
                    patch.object(core, '_cache_robots_entry') as cache:
                opener = unittest.mock.Mock(side_effect=error)
                with self.assertRaises(type(error)):
                    core.fetch_source(SOURCE, opener=opener, robots_cache_path=self.store, now=NOW)
                self.assertEqual(opener.call_count, 1)  # Feed never requested without robots permission.
                cache.assert_not_called()

    def test_robots_denial_is_still_enforced_after_health_reset(self):
        with patch.object(core, '_robots_policy', return_value=(False, None)), \
                patch.object(core, '_source_opener') as opener:
            with self.assertRaises(PermissionError):
                core.fetch_source(SOURCE, now=NOW, robots_cache_path=self.store)
            opener.return_value.assert_not_called()

    def test_304_is_successful_and_not_a_parse_error(self):
        with patch.object(core, '_robots_policy', return_value=(True, None)), \
                patch.object(core, '_wait_for_host'):
            items, metadata = core.fetch_source(SOURCE, now=NOW, robots_cache_path=self.store,
                opener=unittest.mock.Mock(side_effect=HTTPError(SOURCE['url'], 304, 'unchanged', {}, None)))
        self.assertEqual(items, [])
        self.assertTrue(metadata['not_modified'])


if __name__ == '__main__':
    unittest.main()
