import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import sys
import json
import os
import tempfile
import unittest
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingest import daily, generation_api
from dev.ingest.daily_audio import daily_episode_from_edition
from dev.ingest.episodes_audio import audio_directory, current_audio_asset, render_episode_audio
from dev.ingest import paths
from dev.ingest.tts.base import TtsResult
from dev.ingest.tts.gemini import DEFAULT_MODEL


class DailyTests(unittest.TestCase):
    def test_daily_uses_saved_profile_and_waits_for_completion(self):
        observed = []
        states = iter([
            (200, {'job_id': 'j', 'status': 'running', 'progress': {'message': 'Fetching 1 of 2 feeds'}}),
            (200, {'job_id': 'j', 'status': 'complete', 'edition_id': 'daily-fictional'})
        ])
        def start(profile):
            observed.append(profile)
            return 202, {'job_id': 'j'}
        code, result = daily.run_daily(profile_loader=lambda: {'topics': []}, start=start,
                                       get_job=lambda _job: next(states), sleep=lambda _wait: None,
                                       report_writer=lambda _result: None)
        self.assertEqual(code, 0)
        self.assertEqual(observed, [{'topics': []}])
        self.assertEqual(result['edition_id'], 'daily-fictional')

    def test_daily_audio_runs_only_after_successful_edition_completion(self):
        profile = {'topics': [{'id': 'fictional', 'label': 'Fictional topic'}]}
        edition = {'id': 'daily-fixture', 'sections': [{'cluster_id': 'story'}]}
        states = iter([
            (200, {'job_id': 'j', 'status': 'running'}),
            (200, {'job_id': 'j', 'status': 'complete',
                   'edition_ready': {'edition': edition, 'sources': []}}),
        ])
        audio_renderer = lambda _edition, **_kwargs: {'audio_status': 'pending', 'audio_url': None,
                                                     'chapter_offsets': [], 'duration_seconds': None}
        audio_persister = lambda _profile, _edition, result: result
        code, result = daily.run_daily(profile_loader=lambda: profile,
            start=lambda _profile: (202, {'job_id': 'j'}), get_job=lambda _job: next(states),
            sleep=lambda _delay: None, report_writer=lambda _result: None,
            daily_audio_renderer=audio_renderer, daily_audio_persister=audio_persister)
        self.assertEqual(code, 0)
        self.assertEqual(result['daily_audio']['audio_status'], 'pending')

    def test_daily_run_writes_once_per_topic_set_and_reports_every_profile(self):
        topic = {'id': 'payments', 'label': 'Payments', 'scope': 'Fictional payments', 'include': [], 'exclude': []}
        profiles = [
            {'version': 1, 'words': 'Fictional phone profile', 'minutes': 12, 'topics': [topic]},
            {'version': 1, 'words': 'Fictional tablet profile', 'minutes': 15, 'topics': [topic]},
            {'version': 1, 'words': 'Fictional transit profile', 'minutes': 10,
             'topics': [{'id': 'transit', 'label': 'Transit', 'scope': 'Fictional transport'}]},
        ]
        starts = []

        def start(profile):
            starts.append(profile)
            return 202, {'job_id': f'job-{len(starts)}'}

        def get_job(job_id):
            return 200, {'job_id': job_id, 'status': 'complete', 'gemini_calls': 2,
                         'edition_ready': {'edition': {'id': job_id, 'sections': [
                             {'cluster_id': f'cluster-{job_id}'}]}}}

        code, result = daily.run_daily(
            profile_loader=lambda: profiles, start=start, get_job=get_job, sleep=lambda _delay: None,
            report_writer=lambda _result: None,
            daily_audio_renderer=lambda _edition, **_kwargs: {'audio_status': 'pending'},
            daily_audio_persister=lambda _profile, _edition, audio: audio)

        self.assertEqual(code, 0)
        self.assertEqual(starts, [profiles[0], profiles[2]])
        self.assertEqual(result['profiles_attempted'], 3)
        self.assertEqual(result['profiles_completed'], 3)
        self.assertEqual(result['gemini_calls'], 4)
        self.assertEqual(result['profile_runs'][1]['reused_from_profile_id'],
                         paths.profile_identifier(profiles[0]))
        self.assertEqual(result['profile_runs'][1]['edition_id'], result['profile_runs'][0]['edition_id'])

    def test_scheduled_profiles_share_feed_context_and_only_first_publishes_default(self):
        profiles = [
            {'version': 1, 'words': 'Fictional first profile', 'minutes': 12, 'topics': []},
            {'version': 1, 'words': 'Fictional second profile', 'minutes': 12,
             'topics': [{'id': 'other', 'label': 'Other', 'scope': 'Fictional'}]},
        ]
        calls = []

        def start_generation(profile, **options):
            calls.append((profile, options['publish_default'], options['shared_context']))
            return 202, {'job_id': f'job-{len(calls)}'}

        def get_job(job_id):
            return 200, {'job_id': job_id, 'status': 'complete',
                         'edition_ready': {'edition': {'id': job_id, 'sections': []}}}

        with patch.object(daily.generation_api, 'migrate_legacy_generated_files'), \
             patch.object(daily.generation_api, 'start_generation', side_effect=start_generation):
            code, _result = daily.run_daily(
                profile_loader=lambda: profiles, get_job=get_job, sleep=lambda _delay: None,
                report_writer=lambda _result: None,
                daily_audio_renderer=lambda _edition, **_kwargs: {'audio_status': 'pending'},
                daily_audio_persister=lambda _profile, _edition, audio: audio)

        self.assertEqual(code, 0)
        self.assertEqual([row[1] for row in calls], [True, False])
        self.assertIs(calls[0][2], calls[1][2])

    def test_scheduled_profiles_with_same_topics_copy_one_published_edition(self):
        topic = {'id': 'payments', 'label': 'Payments', 'scope': 'Fictional payment systems'}
        profiles = [
            {'version': 1, 'words': 'Fictional phone', 'minutes': 12, 'topics': [topic]},
            {'version': 1, 'words': 'Fictional tablet', 'minutes': 12, 'topics': [topic]},
        ]
        starts = []

        def start_generation(profile, **options):
            starts.append((profile, options))
            return 202, {'job_id': 'shared-job'}

        with patch.object(daily.generation_api, 'migrate_legacy_generated_files'), \
             patch.object(daily.generation_api, 'start_generation', side_effect=start_generation), \
             patch.object(daily, 'mirror_scheduled_edition', return_value=True) as mirror:
            code, result = daily.run_daily(
                profile_loader=lambda: profiles,
                get_job=lambda _job: (200, {'status': 'complete', 'edition_id': 'shared-edition',
                                            'edition_ready': {'edition': {'id': 'shared-edition',
                                                                          'sections': []}}}),
                sleep=lambda _delay: None, report_writer=lambda _result: None,
                daily_audio_renderer=lambda _edition, **_kwargs: {'audio_status': 'pending'},
                daily_audio_persister=lambda _profile, _edition, audio: audio)

        self.assertEqual(code, 0)
        self.assertEqual(len(starts), 1)
        self.assertTrue(starts[0][1]['publish_default'])
        mirror.assert_called_once_with(profiles[0], profiles[1])
        self.assertEqual(result['profile_runs'][1]['reused_from_profile_id'],
                         paths.profile_identifier(profiles[0]))

    def test_daily_summary_is_one_line_and_content_free(self):
        line = daily.summary_line({'status': 'complete', 'job_id': 'job-1', 'elapsed_seconds': 12.5,
                                   'stories_written': 3, 'gemini_calls': 7,
                                   'topic_story_counts': {'private-topic': 2, 'health': 1}, 'feed_counts': {'parsed_items': 9,
                                   'source_results': {'private-feed': 'ok'}},
                                   'run_status': {'items_fetched': 9, 'feeds_disabled': [{'source_id': 'x'}]}})
        self.assertEqual(line.count('\n'), 0)
        self.assertIn('"event":"daily_job_finished"', line)
        self.assertIn('"stories_written":3', line)
        self.assertIn('"gemini_calls":7', line)
        self.assertIn('"topic_count":2', line)
        self.assertNotIn('private-topic', line)
        self.assertNotIn('source_results', line)
        self.assertNotIn('private-feed', line)

    def test_daily_summary_sanitizes_untrusted_fields(self):
        line = daily.summary_line({'status': 'failed', 'job_id': 'private job body',
                                   'error': 'private story title sentinel', 'elapsed_seconds': 'bad',
                                   'stories_written': 'bad'})
        self.assertNotIn('private', line)
        self.assertIn('"job_id":null', line)
        self.assertIn('"reason":"generation_failed"', line)
        self.assertIn('"stories_written":0', line)

    def test_run_report_is_compact_aggregated_atomic_and_redacts_details(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'status.json'
            path.write_text(json.dumps({'ok': True, 'last_run_at': '2026-11-18T11:00:00Z',
                                        'feed_health_attempted': 5, 'feed_health_failed': 1,
                                        'feeds_disabled': [{'source_id': 'private-feed'}]}))
            result = {
                'status': 'complete', 'elapsed_seconds': 42.25, 'gemini_calls': 4,
                'profiles_attempted': 2, 'profiles_completed': 1,
                'profile_runs': [{'profile_id': 'a' * 24, 'status': 'complete',
                                  'edition_id': 'fictional-edition', 'stories': 3, 'topic_count': 2},
                                 {'profile_id': 'b' * 24, 'status': 'failed',
                                  'failure_reason': 'private error text'}],
                'feed_counts': {'parsed_items': 12, 'source_results': {'private-feed': 'ok'}},
                'run_status': {'items_fetched': 12, 'feed_health_attempted': 5,
                               'feed_health_failed': 1, 'feeds_disabled': [{'source_id': 'private-feed'}],
                               'repeat_suppressed': 3, 'stale_suppressed': 2,
                               'article_fetch_by_host': {'news.example.test': {
                                   'attempted': 3, 'succeeded': 2,
                                   'methods': {'jsonld_article_body': 1, 'readability': 1}}}},
                'edition_ready': {
                    'edition': {'local_date': '2026-11-18', 'thin_day': True,
                                'daily_briefing': {'spoken_words': 488, 'duration_seconds': 195}, 'sections': [
                        {'cluster_id': 'fictional-cluster', 'source_ids': ['fictional-source']}]},
                    'sources': [{'id': 'fictional-source', 'evidence_depth': 'full-text',
                                 'url': 'https://private.example.test/story'}]},
                'daily_narration': {'status': 'fallback', 'reason': 'narration_style_or_evidence_check'},
            }
            report = daily.write_run_report(result, path)
            payload = json.loads(path.read_text())
            text_report = (Path(temp) / 'morning-report.txt').read_text(encoding='utf-8')
        self.assertEqual(report['stories'], 1)
        self.assertTrue(report['thin_day'])
        self.assertEqual(report['repeat_suppressed'], 3)
        self.assertEqual(report['stale_suppressed'], 2)
        self.assertEqual(report['evidence_depth'], {'full-text': 1})
        self.assertEqual(report['feeds'], {'attempted': 5, 'failed': 1, 'disabled': 1, 'items_fetched': 12})
        self.assertEqual(report['usage'], {'gemini_calls': 4, 'elapsed_seconds': 42.25})
        self.assertEqual(report['daily_narration'], {
            'status': 'fallback', 'reason': 'narration_style_or_evidence_check'})
        self.assertEqual((report['profiles_attempted'], report['profiles_completed']), (2, 1))
        self.assertEqual(report['profile_runs'][0]['topic_count'], 2)
        self.assertEqual(report['profile_runs'][1]['failure_reason'], 'generation_failed')
        self.assertEqual(report['article_fetch_by_host']['news.example.test']['succeeded'], 2)
        serialized = json.dumps(payload['daily_run'])
        self.assertNotIn('private-feed', serialized)
        self.assertNotIn('private.example.test', serialized)
        self.assertIn('Narration: fallback (narration_style_or_evidence_check); 3.2 min / 488 words', text_report)
        self.assertIn('Feeds: 1 failing', text_report)
        self.assertEqual(len(text_report.splitlines()), 5)

    def test_owner_status_keeps_narration_fallback_separate_from_ready_audio_and_recovery(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as ledger:
            root = Path(temp)
            path = root / 'status.json'
            path.write_text('{}', encoding='utf-8')
            environment = {'BRIEFING_TESTING': '1', 'BRIEFING_STATE_DIR': temp,
                           'BRIEFING_TEST_LEDGER_DIR': ledger}
            audio = {'audio_status': 'ready', 'ready_at': '2026-11-21T10:00:00Z',
                     'duration_seconds': 240, 'file_size_bytes': 2048}
            common = {
                'status': 'complete',
                'edition_ready': {'edition': {'local_date': '2026-11-21', 'sections': [
                    {'cluster_id': 'fictional-story'}], 'daily_briefing': {
                        'spoken_words': 520, 'duration_seconds': 240}}},
                'daily_audio': audio,
            }

            with patch.dict(os.environ, environment):
                first = daily.write_run_report({
                    **common, 'daily_narration': {'status': 'fallback', 'reason': 'budget_estimate'}
                }, path)
                first_status = json.loads(path.read_text(encoding='utf-8'))['daily_run']
                first_text = (root / 'morning-report.txt').read_text(encoding='utf-8')

                recovered = daily.write_run_report({
                    **common, 'daily_narration': {'status': 'written', 'reason': None}
                }, path)
                recovered_status = json.loads(path.read_text(encoding='utf-8'))['daily_run']
                recovered_text = (root / 'morning-report.txt').read_text(encoding='utf-8')

        self.assertEqual(first['daily_narration'], {'status': 'fallback', 'reason': 'budget_estimate'})
        self.assertEqual(first['daily_audio']['audio_status'], 'ready')
        self.assertEqual(first_status['daily_narration'], {'status': 'fallback', 'reason': 'budget_estimate'})
        self.assertEqual(first_status['daily_audio']['audio_status'], 'ready')
        self.assertIn('Narration: fallback (budget_estimate)', first_text)
        self.assertIn('audio ready', first_text)

        self.assertEqual(recovered['daily_narration'], {'status': 'written', 'reason': None})
        self.assertEqual(recovered['daily_audio']['audio_status'], 'ready')
        self.assertEqual(recovered_status['daily_narration'], {'status': 'written', 'reason': None})
        self.assertEqual(recovered_status['daily_audio']['audio_status'], 'ready')
        self.assertIn('Narration: written;', recovered_text)
        self.assertIn('audio ready', recovered_text)
        self.assertNotIn('budget_estimate', recovered_text)

    def test_run_report_uses_only_safe_failure_reasons(self):
        report = daily._daily_run_report({'status': 'failed', 'failure_reason': 'secret token https://private.test'})
        self.assertEqual(report['failure_reason'], 'generation_failed')
        self.assertEqual(daily._daily_run_report({'status': 'failed'})['failure_reason'], 'generation_failed')

    def test_test_mode_routes_default_failure_status_to_configured_state_and_blocks_live_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            test_state = root / 'configured-state'
            protected_live = root / 'protected-live'
            stale_import_path = protected_live / 'status.json'
            with patch.dict(os.environ, {'BRIEFING_TESTING': '1',
                                         'BRIEFING_STATE_DIR': str(test_state)}), \
                 patch.object(paths, 'LIVE_STATE_DIR', protected_live), \
                 patch.object(daily.generation_api, '_STATUS_PATH_AT_IMPORT', stale_import_path), \
                 patch.object(daily.generation_api, 'STATUS_PATH', stale_import_path):
                code, result = daily.run_daily(
                    profile_loader=lambda: {'topics': []},
                    start=lambda _profile: (_ for _ in ()).throw(AssertionError()),
                    report_writer=daily.write_run_report)
                saved = json.loads((test_state / 'status.json').read_text(encoding='utf-8'))
                self.assertFalse(stale_import_path.exists())
                self.assertEqual(saved['daily_run']['failure_reason'], 'daily_start_failed:AssertionError')
                with self.assertRaisesRegex(RuntimeError, 'test state path resolves into live state'):
                    daily.write_run_report(result, stale_import_path)
                with self.assertRaisesRegex(RuntimeError, 'test state path resolves into live state'):
                    daily.generation_api._run_status(False, reason='fixture', path=stale_import_path)
                with self.assertRaisesRegex(RuntimeError, 'test state path resolves into live state'):
                    daily.generation_api._atomic_json(stale_import_path, {'ok': False})
            self.assertEqual(code, 1)

    def test_job_poll_exception_records_safe_failure(self):
        with patch.object(daily, '_run_status', return_value={'last_run_at': 'now'}) as write_status:
            code, result = daily.run_daily(profile_loader=lambda: {'topics': []},
                start=lambda _profile: (202, {'job_id': 'j'}),
            get_job=lambda _job: (_ for _ in ()).throw(RuntimeError('private text')), sleep=lambda _delay: None,
            report_writer=lambda _result: None)
        self.assertEqual(code, 1)
        self.assertEqual(result['error'], 'daily_poll_failed:RuntimeError')
        self.assertEqual(write_status.call_args.kwargs['reason'], 'daily_poll_failed:RuntimeError')

    def test_daily_returns_failure_without_waiting_when_job_fails(self):
        code, result = daily.run_daily(profile_loader=lambda: {'topics': []},
            start=lambda _profile: (202, {'job_id': 'j'}),
            get_job=lambda _job: (200, {'status': 'failed', 'error': 'generation_failed'}), sleep=lambda _wait: None,
            report_writer=lambda _result: None)
        self.assertEqual(code, 1)
        self.assertEqual(result['error'], 'generation_failed')

    def test_failed_generation_preserves_prior_audio_metadata_manifest_and_selected_asset(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as ledger:
            root = Path(temp)
            state_dir = root / 'state'
            profile = {'version': 1, 'topics': [{'id': 'fictional', 'label': 'Fictional topic'}]}
            profile_id = paths.profile_identifier(profile)
            episode_id = 'daily-prior-fixture'
            revision_id = 'revision-prior-fixture'
            prior_edition = {
                'id': episode_id, 'revision_id': revision_id, 'local_date': '2026-11-22',
                'duration_minutes': 1,
                'sections': [{'id': 'story-one', 'cluster_id': 'fictional-story',
                              'heading': 'Fictional prior story',
                              'body': 'A previously published fictional update.'}],
            }
            default_dir = root / 'generated' / 'default'
            profile_dir = root / 'generated' / 'profile'
            with patch.dict(os.environ, {
                    'BRIEFING_TESTING': '1', 'BRIEFING_STATE_DIR': str(state_dir),
                    'BRIEFING_TEST_LEDGER_DIR': ledger}), \
                    patch.object(daily.generation_api, 'DEFAULT_EDITION_DIR', default_dir), \
                    patch.object(daily.generation_api, 'profile_generated_dir', return_value=profile_dir):
                class FictionalProvider:
                    provider_name = 'gemini'
                    model = DEFAULT_MODEL

                    def synthesize(self, text, voice_name):
                        output = BytesIO()
                        with wave.open(output, 'wb') as wav_file:
                            wav_file.setnchannels(1)
                            wav_file.setsampwidth(2)
                            wav_file.setframerate(1000)
                            wav_file.writeframes(b'\0\0' * 1000)
                        return TtsResult(output.getvalue(), 'audio/wav', 1.0, len(text),
                                         Decimal('0.001'), self.model, voice_name)

                prior_episode = daily_episode_from_edition(prior_edition)
                rendered = render_episode_audio(
                    prior_episode, profile_id, provider=FictionalProvider(), voice_name='Kore',
                    state_dir=state_dir)
                prior_edition['script_hash'] = rendered['manifest']['script_hash']
                ready_audio = {
                    'audio_status': 'ready', 'audio_url': '/api/daily/audio',
                    'duration_seconds': rendered['manifest']['duration_seconds'],
                    'file_size_bytes': (rendered['directory'] / 'episode.wav').stat().st_size,
                    'ready_at': '2026-11-22T12:00:00Z', 'resumed': False,
                    'chapter_offsets': rendered['manifest']['chapters'],
                    'voice_name': 'Kore', 'episode': rendered['episode'],
                }
                edition_payload = json.dumps(
                    {'editions': {'editions': [prior_edition]}}, sort_keys=True).encode('utf-8')
                edition_files = []
                for directory in (default_dir, profile_dir):
                    for filename in ('current.json', 'editions.json'):
                        path = directory / filename
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(edition_payload)
                daily._persist_daily_audio(profile, prior_edition, ready_audio)
                edition_files = [
                    (directory / filename, (directory / filename).read_bytes())
                    for directory in (default_dir, profile_dir)
                    for filename in ('current.json', 'editions.json')
                ]
                persisted_before = json.loads((default_dir / 'current.json').read_text(
                    encoding='utf-8'))['editions']['editions'][0]
                audio_before = persisted_before['audio']
                episode_before = daily_episode_from_edition(persisted_before)
                asset_state_before, manifest_before, selected_before = current_audio_asset(
                    episode_before, profile_id, voice_name='Kore', state_dir=state_dir)
                self.assertEqual(asset_state_before, 'ready')
                manifest_path = rendered['directory'] / 'manifest.json'
                wav_path = rendered['directory'] / 'episode.wav'
                manifest_bytes = manifest_path.read_bytes()
                manifest_identity = {
                    key: manifest_before[key] for key in (
                        'episode_id', 'profile_id', 'revision_id', 'script_hash', 'asset_key',
                        'voice_name', 'provider', 'model', 'stitched_sha256')
                }
                wav_bytes = wav_path.read_bytes()

                start = Mock(return_value=(202, {'job_id': 'fictional-failed-job'}))
                get_job = Mock(return_value=(200, {
                    'job_id': 'fictional-failed-job', 'status': 'failed',
                    'error': 'generation_failed',
                }))
                renderer = Mock(name='daily_audio_renderer')
                persister = Mock(name='daily_audio_persister')
                code, result = daily.run_daily(
                    profile_loader=Mock(return_value=profile), start=start, get_job=get_job,
                    sleep=Mock(), report_writer=Mock(), daily_audio_renderer=renderer,
                    daily_audio_persister=persister)
                persisted_after = json.loads((default_dir / 'current.json').read_text(
                    encoding='utf-8'))['editions']['editions'][0]
                asset_state_after, manifest_after, selected_after = current_audio_asset(
                    daily_episode_from_edition(persisted_after), profile_id,
                    voice_name='Kore', state_dir=state_dir)

            self.assertEqual(code, 1)
            self.assertEqual(result['error'], 'generation_failed')
            start.assert_called_once_with(profile)
            get_job.assert_called_once_with('fictional-failed-job')
            renderer.assert_not_called()
            persister.assert_not_called()
            self.assertEqual(
                persisted_after['audio'], audio_before,
            )
            self.assertEqual(manifest_path.read_bytes(), manifest_bytes)
            self.assertEqual(wav_path.read_bytes(), wav_bytes)
            self.assertEqual(asset_state_after, 'ready')
            self.assertEqual(manifest_after, manifest_before)
            self.assertEqual(
                {key: manifest_after[key] for key in manifest_identity}, manifest_identity)
            self.assertEqual(selected_after, selected_before)
            self.assertEqual(audio_directory(state_dir, profile_id, episode_id,
                                              manifest_after['asset_key']), selected_after.parent)
            for path, original_bytes in edition_files:
                self.assertEqual(path.read_bytes(), original_bytes)

    def test_ben_style_daily_run_publishes_at_least_three_stories(self):
        from dev.ingest import generation_api as daily_generation_api
        from dev.ingest import score_edition as daily_score_edition, write_edition as daily_write_edition
        words = 'Payments and banking news, NC health insurance and finance policy, AI in finance. Skip crypto hype.'
        profile = {'version': 1, 'words': words, 'minutes': 12, 'background': None,
                   'muted': [{'label': 'Crypto'}], 'topics': [
            {'id': 'payments-banking', 'label': 'Payments and banking', 'scope': 'Payments and banking news',
             'include': ['payments', 'banking'], 'exclude': [], 'depth': 'standard', 'cadence': 'daily',
             'mustFollow': False, 'sourcePhrase': 'Payments and banking news'},
            {'id': 'nc-health-finance-policy', 'label': 'NC health insurance and finance policy',
             'scope': 'NC health insurance and finance policy', 'include': ['health insurance', 'finance policy'],
             'exclude': [], 'depth': 'standard', 'cadence': 'daily', 'mustFollow': False,
             'sourcePhrase': 'NC health insurance and finance policy'},
            {'id': 'ai-in-finance', 'label': 'AI in finance', 'scope': 'AI in financial services',
             'include': ['AI', 'finance'], 'exclude': [], 'depth': 'standard', 'cadence': 'daily',
             'mustFollow': False, 'sourcePhrase': 'AI in finance'}]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated = root / 'generated'
            edition = {'id': 'ben-style-fictional', 'revision_id': 'ben-style-fictional-r1',
                       'kind': 'daily', 'generated': True, 'generated_at': '2026-11-17T11:00:00Z',
                       'duration_minutes': 12, 'summary': 'Fictional updates across three topics.',
                       'coverage': [{'topic_id': topic['id'], 'state': 'covered'} for topic in profile['topics']],
                       'sections': [{'id': f'story-{index}', 'cluster_id': f'cluster-{index}',
                                     'heading': f'Fictional story {index}', 'body': 'Fictional source-linked detail.',
                                     'source_ids': [], 'topic_ids': [profile['topics'][(index - 1) % 3]['id']]}
                                    for index in range(1, 5)]}
            score = {'critical_failed': False, 'failed_critical': [], 'checks': []}
            patches = [
                patch.object(daily_generation_api, 'STATUS_PATH', root / 'status.json'),
                patch.object(daily_generation_api, 'FEED_HEALTH_PATH', root / 'feed-health.json'),
                patch.object(daily_generation_api, 'GENERATION_LOCK_PATH', root / 'generation.lock'),
                patch.object(daily_generation_api.core, 'ingest_enabled', return_value={
                    'parsed_items': 12, 'fetched_sources': 3, 'errors': 0, 'source_results': {}}),
                patch.object(daily_write_edition, 'generate_edition', return_value=(
                    {'editions': [edition]}, {'sources': []}, {'sentence_evidence': {},
                    'candidate_attempts': 4, 'candidate_attempt_limit': 4,
                    'candidate_results': [{'candidate': index, 'status': 'passed'} for index in range(1, 5)],
                    'candidate_pool_diagnostics': {topic['id']: {'candidate_clusters': 1}
                                                   for topic in profile['topics']}})),
                patch.object(daily_score_edition, 'score_edition', return_value=score),
            ]
            for active in patches:
                active.start()
            try:
                code, result = daily.run_daily(
                    profile_loader=lambda: profile,
                    start=lambda value: daily_generation_api.start_generation(value, generated, registry=[]),
                    wait_seconds=3, poll_seconds=0.01,
                    daily_audio_renderer=lambda _edition, **_kwargs: {'audio_status': 'pending',
                        'audio_url': None, 'duration_seconds': None, 'chapter_offsets': [],
                        'reason': 'fixture'},
                    daily_audio_persister=lambda _profile, _edition, audio: audio)
            finally:
                for active in reversed(patches):
                    active.stop()
            self.assertEqual(code, 0, result)
            published = json.loads((generated / 'editions.json').read_text())['editions'][0]
            stories = [section for section in published['sections'] if section.get('cluster_id')]
            self.assertGreaterEqual(len(stories), 3)
            self.assertEqual(published['daily_briefing']['target_minutes'], 5)
            self.assertEqual(published['duration_minutes'],
                             round(published['duration_seconds'] / 60, 2))
            self.assertEqual(set(published['coverage'][i]['topic_id'] for i in range(3)),
                             {topic['id'] for topic in profile['topics']})
            status_report = json.loads((root / 'status.json').read_text())['daily_run']
            self.assertEqual(status_report['stories'], 4)
            self.assertFalse(status_report['thin_day'])
            self.assertIn('evidence_depth', status_report)
            self.assertIn('feeds', status_report)
            self.assertIn('usage', status_report)

    def test_missing_profile_records_failed_status_and_returns_nonzero(self):
        with patch.object(daily, '_run_status', return_value={'last_run_at': 'now'}) as write_status:
            code, result = daily.run_daily(profile_loader=lambda: (_ for _ in ()).throw(FileNotFoundError()),
                                           report_writer=lambda _result: None)
            self.assertEqual(code, 1)
            self.assertEqual(result['reason'], 'profile_missing_or_invalid')
            write_status.assert_called_once_with(False, reason='profile_missing_or_invalid')

    def test_unknown_job_poll_does_not_overwrite_last_run_status(self):
        with tempfile.TemporaryDirectory() as temp:
            status_path = Path(temp) / 'status.json'
            status_path.write_text(json.dumps({'last_run_at': 'prior', 'ok': True,
                                               'items_fetched': 952, 'stories_written': 4,
                                               'feeds_disabled': [], 'reason': None}))
            with patch.object(daily, '_run_status', return_value={'last_run_at': 'now'}) as write_status:
                code, result = daily.run_daily(
                    profile_loader=lambda: {'topics': []}, start=lambda _profile: (202, {'job_id': 'j'}),
                    get_job=lambda _job: (404, {'error': 'job_not_found'}),
                    sleep=lambda _delay: None, wait_seconds=0.5)
            self.assertEqual(code, 1)
            self.assertEqual(result['error'], 'job_not_found')
            write_status.assert_not_called()
            self.assertEqual(json.loads(status_path.read_text())['last_run_at'], 'prior')


if __name__ == '__main__':
    unittest.main()
