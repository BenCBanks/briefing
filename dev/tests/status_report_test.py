import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from dev.tools.status_report import build_health_payload, format_status_report, main


class StatusReportTests(unittest.TestCase):
    def test_health_projection_is_allowlisted_and_calculates_age(self):
        now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
        status = {'ok': True, 'last_run_at': '2026-11-18T11:30:00+00:00',
                  'profile': 'private-profile', 'message': 'private content'}
        feeds = {'enabled': 5, 'backing_off': 2, 'disabled_permanent': 1,
                 'source_name': 'must not appear'}
        jobs = {'by_state': {'done': 3, 'failed': 1, 'private': 77},
                'by_stage': {'writing': 1},
                'by_audio_status': {'recorded': 2, 'pending': 1},
                'job_id': 'private-job-id'}
        usage = {'daily': {'estimated_cost_usd': Decimal('0.1'), 'cap_usd': '0.5',
                           'remaining_usd': '0.4'},
                 'monthly': {'estimated_cost_usd': '1', 'cap_usd': '5',
                             'remaining_usd': '4'},
                 'key': 'secret'}
        projected = build_health_payload(status=status, feeds=feeds, jobs=jobs, usage=usage,
                                         daily_audio_status='recorded',
                                         daily_audio_duration_seconds=242.4, now=now)
        self.assertEqual(projected['daily_run']['age_seconds'], 1800)
        self.assertEqual(projected['feeds'], {'available': True, 'enabled': 5,
                                               'backing_off': 2, 'disabled_permanent': 1})
        self.assertEqual(projected['jobs']['by_state']['done'], 3)
        self.assertNotIn('private-job-id', repr(projected))
        self.assertNotIn('private content', repr(projected))
        self.assertNotIn('secret', repr(projected))
        self.assertEqual(projected['audio']['daily_status'], 'ready')
        self.assertEqual(projected['audio']['daily_duration_seconds'], 242.4)

    def test_formatter_is_compact_and_does_not_echo_untrusted_fields(self):
        health = {
            'daily_run': {'ok': True, 'age_seconds': 3600},
            'feeds': {'available': True, 'enabled': 8, 'backing_off': 2},
            'jobs': {'available': True,
                     'by_state': {'queued': 1, 'running': 2, 'done': 3, 'failed': 4, 'cancelled': 0}},
            'usage': {'available': True,
                      'today': {'estimated_cost_usd': '0.100000', 'cap_usd': '0.500000', 'remaining_usd': '0.400000'},
                      'month': {'estimated_cost_usd': '1.000000', 'cap_usd': '5.000000', 'remaining_usd': '4.000000'}},
            'audio': {'daily_status': 'pending', 'daily_duration_seconds': 300,
                      'episodes_by_status': {'recorded': 2}},
            'profile_name': 'private name',
        }
        lines = format_status_report(health)
        self.assertEqual(len(lines), 5)
        self.assertEqual(lines[0], 'Daily run: OK · 1h ago')
        self.assertEqual(lines[1], 'Feeds: 8 enabled · 2 backing off')
        self.assertIn('2 running', lines[2])
        self.assertIn('$0.100000 / $0.500000 cap ($0.400000 left)', lines[3])
        self.assertIn('Audio: daily pending · 5m 0s · episodes recorded 2', lines[4])
        self.assertNotIn('private name', '\n'.join(lines))
        health['audio']['daily_duration_seconds'] = float('nan')
        self.assertIn('Audio: daily pending · duration unavailable',
                      format_status_report(health)[4])

    def test_correction_audio_pending_failed_and_fallback_states_are_distinct_and_private(self):
        private_request = 'FICTIONAL REQUEST: private correction wording'
        private_source = 'FICTIONAL SOURCE: confidential outlet and article text'
        correction_jobs = {
            'by_state': {'queued': 1},
            'by_audio_status': {'pending': 1, 'failed': 1, 'superseded': 1},
            'request_text': private_request,
            'source_text': private_source,
        }
        status = {'ok': True, 'last_run_at': '2026-11-21T04:00:00Z',
                  'request_text': private_request, 'source_text': private_source}
        feeds = {'enabled': 1}
        usage = {'daily': {}, 'monthly': {}}

        pending_payload = build_health_payload(
            status=status, feeds=feeds, jobs=correction_jobs, usage=usage,
            daily_audio_status='pending', now=datetime(2026, 11, 21, 4, 1, tzinfo=timezone.utc))
        fallback_payload = build_health_payload(
            status=status, feeds=feeds, jobs=correction_jobs, usage=usage,
            daily_audio_status='recorded', now=datetime(2026, 11, 21, 4, 1, tzinfo=timezone.utc))

        pending_report = '\n'.join(format_status_report(pending_payload))
        fallback_report = '\n'.join(format_status_report(fallback_payload))
        self.assertIn('Audio: daily pending', pending_report)
        self.assertIn('pending 1', pending_report)
        self.assertIn('failed 1', pending_report)
        self.assertIn('superseded 1', pending_report)
        self.assertIn('Audio: daily ready', fallback_report)
        self.assertIn('pending 1', fallback_report)
        self.assertIn('failed 1', fallback_report)
        for private_value in (private_request, private_source):
            self.assertNotIn(private_value, repr(pending_payload))
            self.assertNotIn(private_value, repr(fallback_payload))
            self.assertNotIn(private_value, pending_report)
            self.assertNotIn(private_value, fallback_report)

    def test_failed_health_fetch_keeps_report_plain_and_returns_failure(self):
        lines = []
        result = main(['http://briefing.example.test'],
                      fetcher=lambda _url: (_ for _ in ()).throw(RuntimeError('private URL')),
                      output=lines.append)
        self.assertEqual(result, 1)
        self.assertEqual(len(lines), 1)
        self.assertEqual(len(lines[0].splitlines()), 5)
        self.assertNotIn('private URL', lines[0])


if __name__ == '__main__':
    unittest.main()
