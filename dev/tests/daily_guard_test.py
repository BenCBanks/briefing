import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import contextlib
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dev.tools import daily_guard


def bundle(*, words=900, audio_status='ready', local_date=None):
    stories = []
    sources = []
    for index in range(1, 4):
        source_id = f'source-{index}'
        story = {'id': f'fictional-story-{index}', 'cluster_id': f'fixture-story-{index}',
                 'kind': 'story', 'heading': f'Fictional banking policy update {index}',
                 'body': (f'Fictional banks changed payment rules for merchants in update {index}. [1] '
                          f'The new settlement schedule gives operations teams a separate reconciliation window {index}. [1]'),
                 'source_ids': [source_id], 'lead_source_ids': [source_id],
                 'topic_ids': ['payments'],
                 'read_takeaway': f'Payment operations can use schedule update {index}.'}
        stories.append(story)
        sources.append({'id': source_id, 'url': f'https://fictional-{index}.example.test/story',
                        'published_at': '2026-11-18T10:00:00Z', 'source_class': 'primary'})
    edition = {'id': 'fictional-edition', 'kind': 'daily',
               'local_date': local_date or daily_guard.today_et_date(),
               'sections': stories, 'sources': sources,
               'daily_briefing': {'spoken_words': words, 'target_spoken_words': 750},
               'audio': {'audio_status': audio_status,
                         'audio_url': '/api/daily/audio' if audio_status == 'ready' else None}}
    return {'editions': {'editions': [edition]}, 'sources': {'sources': sources}}, edition


class DailyGuardTests(unittest.TestCase):
    def test_accounts_guard_retries_only_incomplete_multi_reader_runs(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'BRIEFING_ACCOUNTS': 'on'}):
            state = Path(temp)
            today = daily_guard.today_et_date()
            status_path = state / 'status.json'
            status_path.write_text(json.dumps({'daily_run': {'multi_reader': {
                'local_date': today, 'readers': [
                    {'reader_id': 'a1b2c3d4', 'status': 'ready'},
                    {'reader_id': 'b1b2c3d4', 'status': 'not_today'},
                    {'reader_id': 'c1b2c3d4', 'status': 'failed'},
                ]}}}))
            retry, reasons = daily_guard.inspect_state(state)
            self.assertTrue(retry)
            self.assertEqual(reasons, ['multi_reader_incomplete'])

            status_path.write_text(json.dumps({'daily_run': {'multi_reader': {
                'local_date': today, 'readers': [
                    {'reader_id': 'a1b2c3d4', 'status': 'ready'},
                    {'reader_id': 'b1b2c3d4', 'status': 'ready'},
                ]}}}))
            retry, reasons = daily_guard.inspect_state(state)
            self.assertFalse(retry)
            self.assertEqual(reasons, [])

    def test_retry_follows_today_edition_and_content_health_not_ok_flag(self):
        ok_status = {'ok': True, 'daily_run': {'status': 'complete'}}
        good_checks = [{'name': 'quality', 'passed': True}]
        _bundle, full_edition = bundle()
        short = dict(full_edition)
        short['daily_briefing'] = {'spoken_words': 250}
        cases = [
            (ok_status, [{'passed': False}], full_edition, False, 'content_check_failed'),
            (ok_status, good_checks, short, False, 'spoken_under_3_minutes'),
        ]
        for status, checks, edition, cap, reason in cases:
            with self.subTest(reason=reason):
                retry, reasons = daily_guard.decide(status=status, content_checks=checks,
                                                    edition=edition, audio_budget_allows=cap)
                self.assertTrue(retry)
                self.assertIn(reason, reasons)

        retry, reasons = daily_guard.decide(status={'ok': False}, content_checks=good_checks,
            edition=full_edition, audio_budget_allows=False, today=full_edition['local_date'])
        self.assertFalse(retry)
        self.assertEqual(reasons, [])

        retry, reasons = daily_guard.decide(status=ok_status, content_checks=good_checks,
            edition=dict(full_edition, local_date='2000-01-01'), audio_budget_allows=False,
            today=full_edition['local_date'])
        self.assertTrue(retry)
        self.assertIn('today_edition_missing', reasons)

    def test_missing_audio_retries_only_when_estimate_fits_daily_reserve(self):
        status = {'ok': True, 'daily_run': {'status': 'complete'}}
        checks = [{'passed': True}]
        _bundle, edition = bundle(audio_status='pending')
        retry, reasons = daily_guard.decide(status=status, content_checks=checks,
                                            edition=edition, audio_budget_allows=True)
        self.assertTrue(retry)
        self.assertEqual(reasons, ['audio_missing_within_cap'])
        retry, reasons = daily_guard.decide(status=status, content_checks=checks,
                                            edition=edition, audio_budget_allows=False)
        self.assertFalse(retry)
        self.assertEqual(reasons, [])

    def test_tts_disabled_does_not_schedule_audio_only_retry(self):
        status = {'ok': True, 'daily_run': {'status': 'complete'}}
        checks = [{'passed': True}]
        _bundle, edition = bundle(audio_status='pending')
        with patch.dict('os.environ', {'BRIEFING_TTS': 'disabled'}):
            retry, reasons = daily_guard.decide(status=status, content_checks=checks,
                                                edition=edition, audio_budget_allows=True)
        self.assertFalse(retry)
        self.assertEqual(reasons, [])

    def test_fixture_state_calls_content_checker_and_prints_count_only_decision(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            payload, _edition = bundle()
            generated = state / 'generated' / 'default'
            generated.mkdir(parents=True)
            (generated / 'current.json').write_text(json.dumps(payload), encoding='utf-8')
            (generated / 'editions-history.json').write_text('[]', encoding='utf-8')
            (state / 'profile.json').write_text(json.dumps({'topics': [
                {'id': 'payments', 'label': 'Fictional banking and payments'}]}), encoding='utf-8')
            (state / 'status.json').write_text(json.dumps({'ok': True,
                'daily_run': {'status': 'complete'}}), encoding='utf-8')
            output = io.StringIO()
            with patch.dict('os.environ', {'BRIEFING_TTS': 'disabled'}), contextlib.redirect_stdout(output):
                code = daily_guard.main(['--state-dir', temp], budget_checker=lambda _edition: True)
        self.assertEqual(code, 1)
        self.assertEqual(output.getvalue().strip(), 'daily_guard decision=skip reasons=healthy')
        self.assertNotIn('Fictional', output.getvalue())

    def test_missing_state_retries_without_accessing_live_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            retry, reasons = daily_guard.inspect_state(temp, budget_checker=lambda _edition: True)
        self.assertTrue(retry)
        self.assertIn('today_edition_missing', reasons)
        self.assertIn('content_check_failed', reasons)

    def test_recent_ok_status_does_not_skip_when_no_today_edition_exists(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            payload, edition = bundle(local_date='2000-01-01')
            generated = state / 'generated' / 'default'
            generated.mkdir(parents=True)
            (generated / 'current.json').write_text(json.dumps(payload), encoding='utf-8')
            (state / 'profile.json').write_text(json.dumps({'topics': [
                {'id': 'payments', 'label': 'Fictional banking and payments'}]}), encoding='utf-8')
            (state / 'status.json').write_text(json.dumps({'ok': True,
                'last_run_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                'daily_run': {'status': 'complete'}}), encoding='utf-8')
            with patch.object(daily_guard, 'check_content', return_value=[{'passed': True}]), \
                    patch.dict('os.environ', {'BRIEFING_TTS': 'disabled'}):
                retry, reasons = daily_guard.inspect_state(temp, budget_checker=lambda _edition: False)
        self.assertTrue(retry)
        self.assertIn('today_edition_missing', reasons)

    def test_editions_snapshot_can_supply_today_when_current_pointer_is_old(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            old_payload, old = bundle(local_date='2000-01-01')
            today_payload, today = bundle(local_date=daily_guard.today_et_date())
            generated = state / 'generated' / 'default'
            generated.mkdir(parents=True)
            (generated / 'current.json').write_text(json.dumps(old_payload), encoding='utf-8')
            (generated / 'editions.json').write_text(json.dumps({
                'editions': {'editions': [today, old]},
                'sources': today_payload['sources'],
            }), encoding='utf-8')
            (state / 'profile.json').write_text(json.dumps({'topics': [
                {'id': 'payments', 'label': 'Fictional banking and payments'}]}), encoding='utf-8')
            (state / 'status.json').write_text(json.dumps({'ok': True}), encoding='utf-8')
            checked_dates = []

            def check(bundle_value, _history, _profile):
                checked_dates.append(daily_guard.unpack(bundle_value)[0]['local_date'])
                return [{'passed': True}]

            with patch.object(daily_guard, 'check_content', side_effect=check), \
                    patch.dict('os.environ', {'BRIEFING_TTS': 'disabled'}):
                retry, reasons = daily_guard.inspect_state(temp, budget_checker=lambda _edition: False)
        self.assertFalse(retry)
        self.assertEqual(reasons, [])
        self.assertEqual(checked_dates, [daily_guard.today_et_date()])


if __name__ == '__main__':
    unittest.main()
