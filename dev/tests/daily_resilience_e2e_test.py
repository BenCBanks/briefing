"""Exercise the scheduled daily coordinator and retry decision with fixtures only."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dev.ingest import daily, generation_api
from dev.tools import daily_guard


def _edition(local_date, *, topics=('fictional-topic',), narration=None, audio=None):
    sources = []
    stories = []
    for index, topic in enumerate(topics, 1):
        source_id = f'fictional-source-{index}'
        sources.append({'id': source_id,
                        'url': f'https://source-{index}.example.test/briefing',
                        'published_at': f'{local_date}T10:00:00Z', 'source_class': 'primary'})
        stories.append({
            'id': f'fictional-story-{index}', 'cluster_id': f'fictional-cluster-{index}',
            'kind': 'story', 'heading': f'Fictional topic update {index}',
            'body': (f'Fictional researchers documented a separate update for topic {index}. [1] '
                     f'The dated report explains what changed and what remains uncertain. [1]'),
            'source_ids': [source_id], 'lead_source_ids': [source_id], 'topic_ids': [topic],
            'read_takeaway': f'Readers can follow the documented topic {index} update.'})
    value = {'id': f'fictional-edition-{local_date}', 'kind': 'daily', 'local_date': local_date,
             'sections': stories, 'sources': sources,
             'daily_briefing': {'spoken_words': 900, 'target_spoken_words': 750}}
    if narration is not None:
        value['daily_narration'] = narration
        value['daily_briefing'] = {'briefing_script': [], 'background_segments': [],
                                   'spoken_words': 0, 'duration_seconds': 0, 'text_only': True}
    if audio is not None:
        value['audio'] = audio
    return value, sources


class DailyResilienceEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        self.generated = self.state / 'generated' / 'default'
        self.generated.mkdir(parents=True)
        self.profile = {'version': 1, 'minutes': 12, 'topics': [
            {'id': 'fictional-topic', 'label': 'Fictional topic', 'scope': 'fictional topic'}]}
        (self.state / 'profile.json').write_text(json.dumps(self.profile), encoding='utf-8')
        self.today = daily_guard.today_et_date()
        self.yesterday = (datetime.fromisoformat(self.today) - timedelta(days=1)).date().isoformat()
        self.environment = {
            'BRIEFING_TESTING': '1', 'BRIEFING_STATE_DIR': str(self.state),
            'BRIEFING_TEST_LEDGER_DIR': self.ledger.name,
        }

    def tearDown(self):
        self.temp.cleanup()
        self.ledger.cleanup()

    def _persist_edition(self, edition, sources):
        payload = {'editions': {'editions': [edition]}, 'sources': {'sources': sources}}
        (self.generated / 'current.json').write_text(json.dumps(payload), encoding='utf-8')
        (self.generated / 'editions.json').write_text(json.dumps(payload), encoding='utf-8')

    def _read_edition(self):
        payload = json.loads((self.generated / 'current.json').read_text(encoding='utf-8'))
        return payload['editions']['editions'][0]

    def _run_case(self, case):
        if case in {'feed_timeout', 'provider_rate_limit'}:
            prior, prior_sources = _edition(self.yesterday, audio={
                'audio_status': 'ready', 'audio_url': '/api/daily/audio'})
            self._persist_edition(prior, prior_sources)

        narration = None
        if case == 'narration_raises':
            narration = {'status': 'fallback', 'reason': 'NarrationError'}
        elif case == 'narration_too_short':
            narration = {'status': 'fallback', 'reason': 'narration_underdeveloped'}

        topics = ('fictional-topic',)
        if case == 'topic_empty':
            # The edition has a checked story for one of two selected topics.
            self.profile['topics'].append({'id': 'other-fictional-topic',
                                           'label': 'Another fictional topic',
                                           'scope': 'another fictional topic'})
            (self.state / 'profile.json').write_text(json.dumps(self.profile), encoding='utf-8')
            topics = ('fictional-topic',)
        edition, sources = _edition(self.today, topics=topics, narration=narration)
        if case == 'topic_empty':
            edition['coverage'] = [
                {'topic_id': 'fictional-topic', 'story_count': 1, 'state': 'covered'},
                {'topic_id': 'other-fictional-topic', 'story_count': 0, 'state': 'quiet',
                 'gap_note': 'No checked story was found.'},
            ]

        status = {'ok': True, 'last_run_at': '2026-11-24T13:00:00Z',
                  'daily_run': {'status': 'complete'}}
        (self.state / 'status.json').write_text(json.dumps(status), encoding='utf-8')

        def fake_start(_profile):
            return 202, {'job_id': f'fixture-{case}'}

        def fake_get_job(_job_id):
            if case in {'feed_timeout', 'provider_rate_limit'}:
                reason = 'generation_timeout' if case == 'feed_timeout' else 'generation_failed:feed_fetch:RateLimitError'
                generation_api._run_status(False, reason=reason, path=self.state / 'status.json')
                return 200, {'status': 'failed', 'error': reason}
            self._persist_edition(edition, sources)
            generation_api._run_status(True, stories=len(edition['sections']),
                                        path=self.state / 'status.json')
            return 200, {
                'status': 'complete', 'edition_id': edition['id'],
                'edition_ready': {'edition': edition, 'sources': sources},
                'daily_narration': narration or {'status': 'written'},
                'daily_briefing': edition['daily_briefing'],
            }

        def fake_audio(_edition, **_kwargs):
            if case == 'tts_fails':
                raise RuntimeError('fixture TTS failure')
            if case in {'narration_raises', 'narration_too_short'}:
                return {'audio_status': 'pending', 'audio_url': None, 'reason': 'no_spoken_stories'}
            return {'audio_status': 'ready', 'audio_url': '/api/daily/audio'}

        def fake_persist(_profile, current_edition, audio):
            current_edition['audio'] = audio
            self._persist_edition(current_edition, sources)
            return audio

        with patch.dict(os.environ, self.environment), \
                patch.object(daily_guard, 'check_content', return_value=[{'passed': True}]):
            code, result = daily.run_daily(
                profile_loader=lambda: self.profile, start=fake_start, get_job=fake_get_job,
                sleep=lambda _seconds: None, report_writer=daily.write_run_report,
                daily_audio_renderer=fake_audio,
                daily_audio_persister=fake_persist)
            status_value = json.loads((self.state / 'status.json').read_text(encoding='utf-8'))
            decision, reasons = daily_guard.inspect_state(
                self.state, budget_checker=lambda _edition: case == 'tts_fails')

        if case in {'feed_timeout', 'provider_rate_limit'}:
            self.assertEqual(code, 1)
            self.assertEqual(status_value['ok'], False)
            self.assertEqual(status_value['daily_run']['status'], 'failed')
            self.assertEqual(self._read_edition()['local_date'], self.yesterday)
            self.assertTrue(decision)
            self.assertIn('today_edition_missing', reasons)
        else:
            self.assertEqual(code, 0)
            self.assertEqual(status_value['ok'], True)
            self.assertEqual(status_value['daily_run']['status'], 'complete')
            self.assertEqual(self._read_edition()['local_date'], self.today)
            self.assertNotIn('today_edition_missing', reasons)
            if case in {'narration_raises', 'narration_too_short'}:
                self.assertEqual(status_value['daily_run']['daily_narration']['status'], 'fallback')
                self.assertEqual(self._read_edition()['daily_briefing']['briefing_script'], [])
                self.assertTrue(self._read_edition()['daily_briefing']['text_only'])
                self.assertTrue(decision)
                self.assertIn('spoken_under_3_minutes', reasons)
            if case == 'tts_fails':
                self.assertEqual(result['daily_audio']['audio_status'], 'pending')
                self.assertEqual(status_value['daily_run']['daily_audio']['audio_status'], 'pending')
                self.assertTrue(decision)
                self.assertIn('audio_missing_within_cap', reasons)
            if case == 'topic_empty':
                topic_supply = status_value['daily_run']['topic_supply']
                self.assertEqual(next(row['stories'] for row in topic_supply
                                      if row['topic_id'] == 'other-fictional-topic'), 0)
                self.assertFalse(decision)

    def test_narration_exceptions_short_output_tts_and_thin_topic_publish_truthfully(self):
        for case in ('narration_raises', 'narration_too_short', 'tts_fails', 'topic_empty'):
            with self.subTest(case=case):
                self._run_case(case)

    def test_feed_timeout_and_provider_rate_limit_preserve_previous_edition_and_retry(self):
        for case in ('feed_timeout', 'provider_rate_limit'):
            with self.subTest(case=case):
                self._run_case(case)

    def test_recent_ok_written_by_another_process_does_not_suppress_retry(self):
        old, sources = _edition(self.yesterday, audio={
            'audio_status': 'ready', 'audio_url': '/api/daily/audio'})
        self._persist_edition(old, sources)
        (self.state / 'status.json').write_text(json.dumps({
            'ok': True, 'last_run_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'daily_run': {'status': 'complete'},
        }), encoding='utf-8')
        with patch.dict(os.environ, self.environment), \
                patch.object(daily_guard, 'check_content', return_value=[{'passed': True}]):
            decision, reasons = daily_guard.inspect_state(
                self.state, budget_checker=lambda _edition: False)
        self.assertTrue(decision)
        self.assertIn('today_edition_missing', reasons)
        self.assertTrue(json.loads((self.state / 'status.json').read_text())['ok'])


if __name__ == '__main__':
    unittest.main()
