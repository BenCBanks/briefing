import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json
import os
import tempfile
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dev.morning_check import main, summarize


TODAY = date(2026, 11, 18)


def sample_payload():
    sources = [
        {'id': 'source-1', 'evidence_depth': 'full-text', 'url': 'https://fictional.example.test/one', 'published_at': datetime.now(timezone.utc).isoformat()},
        {'id': 'source-2', 'evidence_depth': 'feed-full', 'url': 'https://fictional.example.test/two', 'published_at': datetime.now(timezone.utc).isoformat()},
    ]
    body = ('A fictional report gives readers a substantive verified development and useful details. ' * 6
            + '[1] A second sentence cites the same fictional report. [1]')
    edition = {'local_date': TODAY.isoformat(), 'audio': {'audio_status': 'ready'}, 'sections': [
        {'id': f'story-{index}', 'cluster_id': f'cluster-{index}', 'heading': f'Fictional story {index}',
         'source_ids': ['source-1', 'source-2'], 'body': body}
        for index in range(1, 4)
    ]}
    return {'edition': edition, 'sources': sources, 'generated': True, 'history': []}


class MorningCheckTests(unittest.TestCase):
    def test_multi_reader_status_lists_short_ids_and_cost_without_account_details(self):
        from dev.morning_check import multi_reader_lines
        today = date(2026, 11, 25)
        lines = multi_reader_lines({'daily_run': {'multi_reader': {
            'local_date': today.isoformat(), 'readers': [
                {'reader_id': 'a1b2c3d4', 'status': 'ready', 'cost_usd': 0.1,
                 'name': 'private reader', 'email': 'private@example.test'},
                {'reader_id': 'b1b2c3d4', 'status': 'not_today', 'cost_usd': 0.0},
            ]}}}, today)
        self.assertEqual(lines, ['Daily readers:', '  a1b2c3d4: ready, $0.1000',
                                 '  b1b2c3d4: not_today, $0.0000'])
        self.assertNotIn('private', '\n'.join(lines))

    def setUp(self):
        self.state_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.state_dir.cleanup)
        self.env = patch.dict(os.environ, {'BRIEFING_TESTING': '1',
                                          'BRIEFING_STATE_DIR': self.state_dir.name,
                                          'BRIEFING_EPISODES': 'fake'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_prints_smoke_and_feed_health_summary_lines(self):
        payload = sample_payload()
        def fetch(url):
            if url.endswith('/api/edition?profile=active'):
                return payload
            if url.endswith('/api/profile'):
                return {'topics': [{'label': 'fictional report'}]}
            if url.endswith('/api/usage'):
                return {'daily': {'estimated_cost_usd': '0.10000000', 'cap_usd': '0.50000000', 'remaining_usd': '0.40000000'},
                        'monthly': {'estimated_cost_usd': '1.00000000', 'cap_usd': '5.00000000', 'remaining_usd': '4.00000000'}}
            if url.endswith('/api/health'):
                audio_status = payload['edition']['audio']['audio_status']
                if audio_status == 'recorded':
                    audio_status = 'ready'
                return {'ok': True,
                        'daily_run': {'ok': True, 'age_seconds': 120},
                        'feeds': {'available': True, 'enabled': 5, 'backing_off': 0},
                        'jobs': {'available': True, 'by_state': {'done': 2}},
                        'usage': {'available': True,
                                  'today': {'estimated_cost_usd': '0.1', 'cap_usd': '0.5', 'remaining_usd': '0.4'},
                                  'month': {'estimated_cost_usd': '1', 'cap_usd': '5', 'remaining_usd': '4'}},
                        'audio': {'daily_status': audio_status,
                                  'daily_duration_seconds': 300, 'episodes_by_status': {}}}
            return {'ok': True, 'feeds_disabled': [{'source_id': 'one'}, {'source_id': 'two'}],
                    'daily_run': {'topic_supply': [{'topic_id': 'payments',
                                                    'gap': 'Payments: one story remains.'}]}}
        checks = lambda _base: [('daily edition', True, '3 stories')]
        health = {
            'alpha': {'consecutive_failures': 5},
            'bravo': {'consecutive_failures': 4},
            'charlie': {'consecutive_failures': 3},
            'delta': {'consecutive_failures': 2},
            'echo': {'consecutive_failures': 1},
            'foxtrot': {'consecutive_failures': 0, 'disabled': True},
            'golf': {'consecutive_failures': 0, 'retired': True},
        }
        lines, code = summarize('https://briefing.example.test', checks, fetch, today=TODAY,
                                health_reader=lambda: health,
                                backup_age_reader=lambda: 'Last backup age: 2d 3h')
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 22)
        self.assertTrue(all(line.startswith("Content: PASS") for line in lines[15:]))
        self.assertIn('Content: PASS quality: no issue found', lines)
        self.assertEqual(lines[0], 'Stories: 3 substantive / 3 required')
        self.assertEqual(lines[1], 'Depth: feed-full 1, full-text 1')
        self.assertEqual(lines[2], 'Status: OK; smoke 1/1')
        self.assertEqual(lines[3], 'Date: 2026-11-18 current')
        self.assertEqual(lines[4], 'Last backup age: 2d 3h')
        # The release gate deliberately forces fixture-only episode mode;
        # morning_check should report that effective non-default setting.
        self.assertEqual(lines[5], 'Non-default flags: episodes=fake (env)')
        self.assertEqual(lines[6], 'Feeds disabled: 2')
        self.assertEqual(lines[7], 'Topic supply gaps: Payments: one story remains.')
        self.assertEqual(lines[8], 'Daily run: OK · 2m ago')
        self.assertEqual(lines[9], 'Feeds: 5 enabled · 0 backing off')
        self.assertIn('2 done', lines[10])
        self.assertIn('today $0.100000 / $0.500000 cap ($0.400000 left)', lines[11])
        self.assertEqual(lines[12], 'Audio: recorded 5m')
        self.assertEqual(lines[13], 'Feed health: 5 enabled / 0 backing_off / 2 disabled_permanent (7 tracked)')
        self.assertEqual(lines[14], 'Top failing sources: alpha (5), bravo (4), charlie (3), delta (2), echo (1)')

        for audio_status in ('pending', 'failed'):
            payload['edition']['audio']['audio_status'] = audio_status
            payload['edition']['audio']['reason'] = ('daily_budget' if audio_status == 'pending'
                                                        else 'synthesis_failed')
            lines, _code = summarize('https://briefing.example.test', checks, fetch, today=TODAY,
                                    health_reader=lambda: health,
                                    backup_age_reader=lambda: 'Last backup age: unavailable')
            expected_audio = ('Audio: pending (daily audio budget was used)' if audio_status == 'pending'
                              else 'Audio: failed (audio synthesis failed)')
            self.assertEqual(lines[12], expected_audio)

    def test_audio_status_is_for_today_and_pending_reason_is_plain(self):
        from dev.morning_check import daily_audio_line
        edition = {'local_date': TODAY.isoformat(),
                   'audio': {'audio_status': 'pending', 'reason': 'tts_disabled'}}
        self.assertEqual(daily_audio_line(edition, TODAY), 'Audio: pending (audio is turned off)')
        edition['local_date'] = '2026-11-17'
        self.assertEqual(daily_audio_line(edition, TODAY), "Audio: unavailable (today's edition unavailable)")

    def test_morning_story_count_uses_short_two_fact_assessor(self):
        payload = sample_payload()
        examples = [
            ('Fictional report: route timetable change',
             'A fictional transit office changed route frequency. [1] The new timetable starts November 20. [2]'),
            ('Fictional report: bridge repair plan',
             'A fictional transit office began bridge repairs. [1] The bridge reopens December 4. [2]'),
            ('Fictional report: fare payment pilot',
             'A fictional transit office expanded a fare pilot. [1] The pilot now covers three terminals. [2]'),
        ]
        for story, (heading, body) in zip(payload['edition']['sections'], examples):
            story.update(heading=heading, body=body)
        self.assertTrue(all(len(story['body']) < 400 for story in payload['edition']['sections']))

        def fetch(url):
            if url.endswith('/api/edition?profile=active'):
                return payload
            if url.endswith('/api/profile'):
                return {'topics': [{'id': 'fictional-report', 'label': 'Fictional report'}]}
            if url.endswith('/api/status'):
                return {'ok': True, 'feeds_disabled': [], 'daily_run': {'status': 'complete'}}
            if url.endswith('/api/usage'):
                return {'daily': {}, 'monthly': {}}
            return {'ok': True}

        lines, code = summarize(
            'https://briefing.example.test',
            smoke_runner=lambda _base: [('daily edition', True, '3 stories')],
            json_fetch=fetch, today=TODAY, health_reader=lambda: {},
            backup_age_reader=lambda: 'Last backup age: unavailable',
            flags_reader=lambda: {},
            now=datetime(2026, 11, 18, 7, 0, tzinfo=ZoneInfo('America/New_York')))
        self.assertEqual(lines[0], 'Stories: 3 substantive / 3 required')
        self.assertEqual(code, 0, lines)

    def test_feed_health_handles_missing_and_empty_data(self):
        from dev.morning_check import feed_health_lines, read_feed_health
        self.assertEqual(feed_health_lines(None), ['Feed health: unavailable', 'Top failing sources: unavailable'])
        self.assertEqual(feed_health_lines({}), ['Feed health: 0 enabled / 0 backing_off / 0 disabled_permanent (0 tracked)', 'Top failing sources: none'])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'feed-health.json'
            path.write_text(json.dumps({'feed-a': {'disabled': True, 'consecutive_failures': 3}}))
            self.assertEqual(read_feed_health(path), {'feed-a': {'disabled': True, 'consecutive_failures': 3}})

    def test_smoke_failure_or_stale_date_makes_status_fail(self):
        payload = sample_payload()
        payload['edition']['local_date'] = '2026-11-17'
        def fetch(url):
            if url.endswith('/api/edition?profile=active'):
                return payload
            if url.endswith('/api/profile'):
                return {'topics': [{'label': 'fictional report'}]}
            if url.endswith('/api/usage'):
                return {'daily': {}, 'monthly': {}}
            return {'ok': True, 'feeds_disabled': []}
        lines, code = summarize('https://briefing.example.test',
                                lambda _base: [('freshness', False, 'stale')], fetch, today=TODAY,
                                health_reader=lambda: {})
        self.assertEqual(code, 1)
        self.assertIn('FAIL', lines[2])
        self.assertEqual(lines[3], 'Date: 2026-11-17 stale')

    def test_stale_edition_becomes_fatal_at_0630_eastern(self):
        payload = sample_payload()
        payload['edition']['local_date'] = '2026-11-17'
        def fetch(url):
            if url.endswith('/api/edition?profile=active'):
                return payload
            if url.endswith('/api/profile'):
                return {'topics': [{'label': 'fictional report'}]}
            if url.endswith('/api/status'):
                return {'ok': True, 'feeds_disabled': [], 'daily_run': {
                    'daily_narration': {'status': 'written', 'reason': None}}}
            return {'ok': True}
        common = {'smoke_runner': lambda _base: [('freshness', False, 'stale')],
                  'json_fetch': fetch,
                  'today': TODAY, 'health_reader': lambda: {},
                  'backup_age_reader': lambda: 'Last backup age: unavailable', 'flags_reader': lambda: {}}
        lines_before, code_before = summarize(
            'https://briefing.example.test', now=datetime(2026, 11, 18, 6, 29, tzinfo=ZoneInfo('America/New_York')),
            **common)
        lines_due, code_due = summarize(
            'https://briefing.example.test', now=datetime(2026, 11, 18, 6, 30, tzinfo=ZoneInfo('America/New_York')),
            **common)
        self.assertEqual(code_before, 0)
        self.assertIn('not due by 06:30 ET', lines_before[3])
        self.assertEqual(code_due, 1)
        self.assertEqual(lines_due[3], 'Date: 2026-11-17 stale')

    def test_daily_narration_fallback_makes_morning_check_fail(self):
        payload = sample_payload()
        fallback_status = {'ok': True, 'feeds_disabled': [], 'daily_run': {
            'daily_narration': {'status': 'fallback', 'reason': 'narration_writer_shape'}}}
        def fetch(url):
            if url.endswith('/api/edition?profile=active'):
                return payload
            if url.endswith('/api/profile'):
                return {'topics': [{'label': 'fictional report'}]}
            if url.endswith('/api/status'):
                return fallback_status
            return {'ok': True}
        lines, code = summarize('https://briefing.example.test',
            smoke_runner=lambda _base: [('smoke', True, 'ok')], json_fetch=fetch, today=TODAY,
            now=datetime(2026, 11, 18, 7, 0, tzinfo=ZoneInfo('America/New_York')),
            health_reader=lambda: {}, backup_age_reader=lambda: 'Last backup age: unavailable',
            flags_reader=lambda: {})
        self.assertEqual(code, 1)
        self.assertIn('daily narration fallback', lines[2])

    def test_missing_url_still_prints_seven_lines(self):
        with patch.dict('os.environ', {'BRIEFING_LIVE_URL': '', 'BRIEFING_URL': ''}):
            from contextlib import redirect_stdout
            import io
            output = io.StringIO()
            with patch('dev.morning_check.read_feed_health', return_value=None):
                with redirect_stdout(output):
                    result = main([])
        self.assertEqual(result, 2)
        self.assertEqual(len(output.getvalue().splitlines()), 14)

    def test_morning_flags_line_shows_only_nondefault_flags_and_source(self):
        from dev.morning_check import flag_summary_line
        self.assertEqual(flag_summary_line({
            'retrieval_fill': {'value': True, 'default': False, 'source': 'file'},
            'episodes': {'value': 'real', 'default': 'real', 'source': 'default'},
        }), 'Non-default flags: retrieval_fill=True (file)')

    def test_last_backup_age_uses_latest_timestamped_archive_metadata(self):
        from dev.morning_check import last_backup_age_line
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            latest = directory / 'briefing-state-20261116T090000Z.tar.gz'
            latest.write_bytes(b'fixture archive placeholder')
            now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
            mtime = (now - timedelta(days=2, hours=3)).timestamp()
            import os
            os.utime(latest, (mtime, mtime))
            (directory / 'briefing-state-not-an-archive.tar.gz').write_bytes(b'ignored')
            self.assertEqual(last_backup_age_line(directory, now), 'Last backup age: 2d 3h')


if __name__ == '__main__':
    unittest.main()
