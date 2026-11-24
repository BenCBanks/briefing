import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
from copy import deepcopy
from decimal import Decimal
from io import BytesIO
import logging
from pathlib import Path
import tempfile
import unittest
import wave
from datetime import datetime
import io
from unittest.mock import patch
from zoneinfo import ZoneInfo

from dev.ingest.daily_audio import daily_episode_from_edition, render_daily_audio
from dev.ingest.episodes_audio import current_audio_asset, render_episode_audio
from dev.ingest.paths import profile_identifier
from dev.ingest.tts.base import TtsResult
from dev.ingest.tts.gemini import DEFAULT_MODEL
from dev.ingest.usage import UsageLedger


PROFILE = {'minutes': 12, 'topics': [{'id': 'fiction', 'label': 'Fictional topic'}]}
EDITION = {'id': 'daily-fixture', 'revision_id': 'r1', 'local_date': '2026-11-18',
           'sections': [
               {'id': 'story-one', 'cluster_id': 'one', 'heading': 'First fictional story',
                'audio_script': 'A fictional update happened today. [1] Citations: ignored outlet.'},
               {'id': 'story-two', 'cluster_id': 'two', 'heading': 'Second fictional story',
                'body': 'A second fictional update happened today. [2]'},
           ]}


class FakeLedger:
    def __init__(self, remaining):
        self.remaining = remaining

    def daily_audio_budget_remaining(self):
        return self.remaining

    @staticmethod
    def estimate_tts_cost(characters, duration_seconds):
        return (Decimal(characters) * Decimal('0.125') / Decimal(1_000_000) +
                Decimal(str(duration_seconds)) * Decimal('0.009') / Decimal(60))


class FiveMinuteFakeProvider:
    """Fixture TTS provider returning two 150-second WAV story chapters."""
    provider_name = 'fixture'
    model = DEFAULT_MODEL

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def synthesize(self, text, voice_name):
        self.calls.append((text, voice_name))
        if self.fail:
            raise RuntimeError('fictional TTS failure')
        output = BytesIO()
        with wave.open(output, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(1000)
            wav_file.writeframes(b'\0\0' * 150_000)
        return TtsResult(output.getvalue(), 'audio/wav', 150.0, len(text),
                         Decimal('0.001'), DEFAULT_MODEL, voice_name)


class DailyAudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.voice_file = Path(self.temp.name) / 'voice.json'
        self.voice_file.write_text(json.dumps({'voice_name': 'Kore'}), encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_daily_script_has_one_clean_spoken_chapter_per_story(self):
        episode = daily_episode_from_edition(EDITION)
        self.assertEqual(len(episode['chapters']), 2)
        self.assertEqual(episode['chapters'][0]['spoken_text'],
                         'First fictional story. A fictional update happened today.')
        self.assertNotIn('[1]', episode['chapters'][0]['spoken_text'])

    def test_render_returns_audio_url_duration_and_story_offsets(self):
        provider = FiveMinuteFakeProvider()
        result = render_daily_audio(EDITION, profile=PROFILE, voice_file=self.voice_file,
                                    state_dir=self.temp.name, ledger=FakeLedger(Decimal('1')),
                                    provider=provider)
        self.assertEqual(result['audio_status'], 'ready')
        self.assertEqual(result['audio_url'], '/api/daily/audio')
        self.assertEqual(result['duration_seconds'], 300.0)
        self.assertEqual(len(result['chapter_offsets']), 2)
        self.assertEqual(result['chapter_offsets'][1]['start_seconds'], 150.0)
        self.assertEqual([row['duration_seconds'] for row in result['chapter_offsets']], [150.0, 150.0])
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(result['voice_name'], 'Kore')

    def test_episode_tts_spend_does_not_exhaust_daily_audio_cap_early(self):
        ledger = UsageLedger(ledger_dir=Path(self.temp.name) / 'ledger')
        now = datetime(2026, 11, 18, 9, tzinfo=ZoneInfo('America/New_York'))
        ledger.record_tts_usage(300_000, 900, model='fictional-tts',
                                now=now)
        self.assertEqual(ledger.daily_audio_budget_remaining(now),
                         Decimal('0.32750000'))

        provider = FiveMinuteFakeProvider()
        result = render_daily_audio(
            EDITION, profile=PROFILE, voice_file=self.voice_file,
            state_dir=Path(self.temp.name) / 'state', ledger=ledger, provider=provider)

        self.assertEqual(result['audio_status'], 'ready')
        self.assertEqual(len(provider.calls), 2)

    def test_material_correction_gets_new_audio_hash_and_retry_reuses_asset(self):
        provider = FiveMinuteFakeProvider()
        old_edition = deepcopy(EDITION)
        state_dir = Path(self.temp.name) / 'revision-audio'
        old_result = render_daily_audio(
            old_edition, profile=PROFILE, voice_file=self.voice_file,
            state_dir=state_dir, ledger=FakeLedger(Decimal('1')), provider=provider)
        self.assertEqual(old_result['audio_status'], 'ready')
        self.assertEqual(old_result['episode']['audio']['revision_id'], 'r1')
        old_script_hash = old_result['episode']['audio']['script_hash']

        unchanged_retry = render_daily_audio(
            old_edition, profile=PROFILE, voice_file=self.voice_file,
            state_dir=state_dir, ledger=FakeLedger(Decimal('1')), provider=provider)
        self.assertEqual(unchanged_retry['audio_status'], 'ready')
        self.assertTrue(unchanged_retry['resumed'])
        self.assertEqual(unchanged_retry['episode']['audio']['script_hash'], old_script_hash)

        corrected_edition = deepcopy(old_edition)
        corrected_edition['revision_id'] = 'r2'
        corrected_edition['sections'][0]['audio_script'] = (
            'A material correction changes this fictional update.')
        profile_id = profile_identifier(PROFILE)
        old_state, old_manifest, old_path = current_audio_asset(
            daily_episode_from_edition(old_edition), profile_id,
            voice_name='Kore', state_dir=state_dir, provider_name='fixture')
        self.assertEqual(old_state, 'ready')
        self.assertEqual(old_manifest['revision_id'], 'r1')
        corrected_episode = daily_episode_from_edition(corrected_edition)
        stale_state, stale_manifest, stale_path = current_audio_asset(
            corrected_episode, profile_id, voice_name='Kore', state_dir=state_dir,
            provider_name='fixture')
        self.assertEqual((stale_state, stale_manifest, stale_path), ('stale', None, None))

        stale_episode = deepcopy(old_result['episode'])
        stale_episode['revision_id'] = 'r2'
        stale_episode['audio']['revision_id'] = 'r2'
        stale_manifest = {
            'episode_id': stale_episode['id'], 'revision_id': 'r2',
            'script_hash': old_script_hash, 'duration_seconds': old_result['duration_seconds'],
            'voice_name': old_result['voice_name'], 'stitched_file': 'episode.wav',
            'chapters': [{'chapter_id': row['chapter_id'], 'start_seconds': row['start_seconds'],
                          'duration_seconds': row['duration_seconds']}
                         for row in old_result['chapter_offsets']],
        }
        stale_render = {'manifest': stale_manifest, 'episode': stale_episode,
                        'directory': None, 'cached': True}
        mismatched_retry = render_daily_audio(
            corrected_edition, profile=PROFILE, voice_file=self.voice_file,
            state_dir=state_dir, ledger=FakeLedger(Decimal('1')), provider=provider,
            renderer=lambda *_args, **_kwargs: stale_render)
        self.assertNotEqual(mismatched_retry['audio_status'], 'ready')
        self.assertIsNone(mismatched_retry['audio_url'])
        old_state, old_manifest, old_path = current_audio_asset(
            daily_episode_from_edition(old_edition), profile_id,
            voice_name='Kore', state_dir=state_dir, provider_name='fixture')
        stale_state, stale_manifest, stale_path = current_audio_asset(
            corrected_episode, profile_id, voice_name='Kore', state_dir=state_dir,
            provider_name='fixture')
        self.assertEqual(old_state, 'ready', 'prior audio remains available until replacement is complete')
        self.assertEqual(old_manifest['revision_id'], 'r1')
        self.assertEqual((stale_state, stale_manifest, stale_path), ('stale', None, None))

        corrected_result = render_daily_audio(
            corrected_edition, profile=PROFILE, voice_file=self.voice_file,
            state_dir=state_dir, ledger=FakeLedger(Decimal('1')), provider=provider)
        self.assertEqual(corrected_result['audio_status'], 'ready')
        self.assertEqual(corrected_result['episode']['audio']['revision_id'], 'r2')
        self.assertNotEqual(corrected_result['episode']['audio']['script_hash'], old_script_hash)
        corrected_state, corrected_manifest, corrected_path = current_audio_asset(
            corrected_episode, profile_id, voice_name='Kore', state_dir=state_dir,
            provider_name='fixture')
        self.assertEqual(corrected_state, 'ready')
        self.assertEqual(corrected_manifest['revision_id'], 'r2')
        self.assertNotEqual(corrected_path, None)
        self.assertNotEqual(corrected_path, old_path)

        calls_after_correction = len(provider.calls)
        retry = render_daily_audio(
            corrected_edition, profile=PROFILE, voice_file=self.voice_file,
            state_dir=state_dir, ledger=FakeLedger(Decimal('1')), provider=provider)
        self.assertEqual(retry['audio_status'], 'ready')
        self.assertTrue(retry['resumed'])
        self.assertEqual(retry['episode']['audio']['revision_id'], 'r2')
        self.assertEqual(len(provider.calls), calls_after_correction)

    def test_late_render_after_correction_preserves_current_audio_failure(self):
        edition = deepcopy(EDITION)
        stale_chapters = daily_episode_from_edition(edition)['chapters']
        asset_directory = Path(self.temp.name) / 'fictional-late-asset'
        asset_key = 'fictional-r1-asset'
        manifest = {
            'episode_id': edition['id'], 'revision_id': 'r1',
            'script_hash': 'fictional-r1-script', 'asset_key': asset_key,
            'voice_name': 'Kore', 'duration_seconds': 300.0,
            'chapters': [{'chapter_id': chapter['id'], 'start_seconds': index * 150.0,
                          'duration_seconds': 150.0}
                         for index, chapter in enumerate(stale_chapters)],
        }
        stale_rendered_episode = daily_episode_from_edition(edition)
        stale_rendered_episode['audio'] = {
            'revision_id': 'r1', 'script_hash': 'fictional-r1-script',
            'voice_name': 'Kore', 'duration_seconds': 300.0,
        }
        stale_result = {'manifest': manifest, 'episode': stale_rendered_episode,
                        'directory': asset_directory, 'cached': False}

        def late_renderer(_episode, _profile_id, **_kwargs):
            edition['revision_id'] = 'r2'
            edition['sections'][0]['audio_script'] = 'A corrected fictional notice replaces revision one.'
            edition['audio'] = {
                'audio_status': 'failed', 'reason': 'synthesis_failed',
                'audio_url': None, 'chapter_offsets': [],
            }
            return stale_result

        def current_asset(episode, _profile_id, **_kwargs):
            self.assertEqual(episode['revision_id'], 'r1')
            return 'ready', manifest, asset_directory / 'episode.wav'

        with patch('dev.ingest.daily_audio.current_audio_asset', side_effect=current_asset):
            late = render_daily_audio(
                edition, profile=PROFILE, voice_file=self.voice_file,
                state_dir=Path(self.temp.name) / 'late-response-state',
                ledger=FakeLedger(Decimal('1')), renderer=late_renderer)

        self.assertEqual(edition['revision_id'], 'r2')
        self.assertIn('corrected fictional notice', edition['sections'][0]['audio_script'])
        self.assertEqual(late['audio_status'], 'failed', 'the current error must not be cleared by stale success')
        self.assertEqual(late['reason'], 'synthesis_failed')
        self.assertIsNone(late['audio_url'])
        self.assertEqual(late['chapter_offsets'], [])

    def test_late_timeout_after_correction_preserves_current_failure_reason(self):
        edition = deepcopy(EDITION)

        def timed_out_renderer(_episode, _profile_id, **_kwargs):
            edition['revision_id'] = 'r2'
            edition['sections'][0]['audio_script'] = 'A corrected fictional notice is current.'
            edition['audio'] = {
                'audio_status': 'failed', 'reason': 'synthesis_failed',
                'audio_url': None, 'chapter_offsets': [],
            }
            raise TimeoutError('fictional timeout detail')

        late = render_daily_audio(
            edition, profile=PROFILE, voice_file=self.voice_file,
            state_dir=Path(self.temp.name) / 'late-timeout-state',
            ledger=FakeLedger(Decimal('1')), renderer=timed_out_renderer)

        self.assertEqual(edition['revision_id'], 'r2')
        self.assertEqual(late['audio_status'], 'failed')
        self.assertEqual(late['reason'], 'synthesis_failed')
        self.assertNotIn('fictional timeout detail', str(late))
        self.assertIsNone(late['audio_url'])
        self.assertEqual(late['chapter_offsets'], [])

    def test_late_timeout_keeps_current_audio_separate_from_narration_fallback(self):
        edition = deepcopy(EDITION)
        edition['daily_narration'] = {
            'status': 'fallback', 'reason': 'budget_estimate'}
        state_dir = Path(self.temp.name) / 'late-ready-state'
        provider = FiveMinuteFakeProvider()

        def timed_out_renderer(_episode, profile_id, **kwargs):
            edition['revision_id'] = 'r2'
            edition['sections'][0]['audio_script'] = 'A corrected fictional notice is current.'
            current_episode = daily_episode_from_edition(edition)
            current = render_episode_audio(
                current_episode, profile_id, provider=kwargs['provider'],
                voice_name='Kore', voice_file=self.voice_file,
                state_dir=kwargs['state_dir'])
            manifest = current['manifest']
            current_offsets = [{'chapter_id': row['chapter_id'],
                                'start_seconds': row['start_seconds'],
                                'duration_seconds': row['duration_seconds']}
                               for row in manifest['chapters']]
            edition['audio'] = {
                'audio_status': 'ready', 'audio_url': '/api/daily/audio',
                'duration_seconds': manifest['duration_seconds'], 'chapter_offsets': current_offsets,
                'voice_name': manifest['voice_name'], 'ready_at': '2026-11-22T10:00:00Z',
                'revision_id': manifest['revision_id'], 'script_hash': manifest['script_hash'],
                'asset_key': manifest['asset_key'],
            }
            raise TimeoutError('late fictional timeout detail')

        late = render_daily_audio(
            edition, profile=PROFILE, voice_file=self.voice_file,
            state_dir=state_dir, ledger=FakeLedger(Decimal('1')), provider=provider,
            renderer=timed_out_renderer)

        self.assertEqual(late['audio_status'], 'ready')
        self.assertEqual(late['asset_key'], edition['audio']['asset_key'])
        self.assertEqual(late['audio_url'], '/api/daily/audio')
        self.assertEqual(late['duration_seconds'], edition['audio']['duration_seconds'])
        self.assertEqual(late['chapter_offsets'], edition['audio']['chapter_offsets'])
        self.assertEqual(late['voice_name'], 'Kore')
        self.assertEqual(edition['daily_narration'], {
            'status': 'fallback', 'reason': 'budget_estimate'})

    def test_late_timeout_does_not_reuse_old_asset_when_corrected_script_keeps_chapter_ids(self):
        edition = deepcopy(EDITION)
        state_dir = Path(self.temp.name) / 'late-stale-state'
        provider = FiveMinuteFakeProvider()
        old = render_daily_audio(
            edition, profile=PROFILE, voice_file=self.voice_file, state_dir=state_dir,
            ledger=FakeLedger(Decimal('1')), provider=provider)
        old_audio = old['episode']['audio']
        edition['audio'] = {
            'audio_status': 'ready', 'audio_url': old['audio_url'],
            'duration_seconds': old['duration_seconds'],
            'chapter_offsets': deepcopy(old['chapter_offsets']),
            'voice_name': old['voice_name'], 'revision_id': old_audio['revision_id'],
            'script_hash': old_audio['script_hash'], 'ready_at': 'old-ready-time',
        }

        def timed_out_renderer(_episode, _profile_id, **_kwargs):
            edition['revision_id'] = 'r2'
            edition['sections'][0]['audio_script'] = 'A corrected fictional notice is current.'
            edition['audio']['ready_at'] = 'newer-status-time'
            raise TimeoutError('late fictional timeout detail')

        late = render_daily_audio(
            edition, profile=PROFILE, voice_file=self.voice_file, state_dir=state_dir,
            ledger=FakeLedger(Decimal('1')), provider=provider, renderer=timed_out_renderer)

        corrected_chapter_ids = [chapter['id'] for chapter in daily_episode_from_edition(edition)['chapters']]
        self.assertEqual(corrected_chapter_ids,
                         [row['chapter_id'] for row in edition['audio']['chapter_offsets']])
        self.assertEqual(late['audio_status'], 'pending')
        self.assertEqual(late['reason'], 'superseded')
        self.assertIsNone(late['audio_url'])
        self.assertEqual(late['chapter_offsets'], [])

    def test_voice_change_renders_distinct_asset_and_each_voice_retry_reuses_its_asset(self):
        edition = deepcopy(EDITION)
        state_dir = Path(self.temp.name) / 'voice-identity-audio'
        provider = FiveMinuteFakeProvider()
        profile_id = profile_identifier(PROFILE)

        kore = render_daily_audio(
            edition, profile=PROFILE, voice_file=self.voice_file, state_dir=state_dir,
            ledger=FakeLedger(Decimal('1')), provider=provider)
        self.assertEqual(kore['audio_status'], 'ready')
        self.assertEqual(kore['voice_name'], 'Kore')
        self.assertEqual(kore['episode']['audio']['voice_name'], 'Kore')
        self.assertEqual(len(provider.calls), 2)
        kore_retry = render_daily_audio(
            edition, profile=PROFILE, voice_file=self.voice_file, state_dir=state_dir,
            ledger=FakeLedger(Decimal('1')), provider=provider)
        self.assertEqual(kore_retry['audio_status'], 'ready')
        self.assertTrue(kore_retry['resumed'])
        self.assertEqual(kore_retry['voice_name'], 'Kore')
        self.assertEqual(len(provider.calls), 2)

        self.voice_file.write_text(json.dumps({'voice_name': 'Orus'}), encoding='utf-8')
        episode = daily_episode_from_edition(edition)
        kore_state, kore_manifest, kore_path = current_audio_asset(
            episode, profile_id, voice_name='Kore', state_dir=state_dir,
            provider_name='fixture')
        orus_before_state, orus_before_manifest, orus_before_path = current_audio_asset(
            episode, profile_id, voice_name='Orus', state_dir=state_dir,
            provider_name='fixture')
        self.assertEqual(kore_state, 'ready')
        self.assertEqual(kore_manifest['voice_name'], 'Kore')
        self.assertEqual((orus_before_state, orus_before_manifest, orus_before_path),
                         ('stale', None, None),
                         'a prior voice asset is not current for the newly selected voice')

        orus = render_daily_audio(
            edition, profile=PROFILE, voice_file=self.voice_file, state_dir=state_dir,
            ledger=FakeLedger(Decimal('1')), provider=provider)
        self.assertEqual(orus['audio_status'], 'ready')
        self.assertFalse(orus['resumed'])
        self.assertEqual(orus['voice_name'], 'Orus')
        self.assertEqual(orus['episode']['audio']['voice_name'], 'Orus')
        self.assertEqual(len(provider.calls), 4)
        orus_state, orus_manifest, orus_path = current_audio_asset(
            episode, profile_id, voice_name='Orus', state_dir=state_dir,
            provider_name='fixture')
        self.assertEqual(orus_state, 'ready')
        self.assertEqual(orus_manifest['voice_name'], 'Orus')
        self.assertEqual(orus_manifest['script_hash'], kore_manifest['script_hash'],
                         'voice selection changes the asset without changing the spoken script')
        self.assertEqual([row['chapter_id'] for row in orus_manifest['chapters']],
                         [row['chapter_id'] for row in kore_manifest['chapters']])
        self.assertNotEqual(orus_manifest['asset_key'], kore_manifest['asset_key'])
        self.assertNotEqual(orus_path, kore_path)

        calls_after_voice_change = len(provider.calls)
        orus_retry = render_daily_audio(
            edition, profile=PROFILE, voice_file=self.voice_file, state_dir=state_dir,
            ledger=FakeLedger(Decimal('1')), provider=provider)
        self.assertEqual(orus_retry['audio_status'], 'ready')
        self.assertTrue(orus_retry['resumed'])
        self.assertEqual(orus_retry['voice_name'], 'Orus')
        self.assertEqual(orus_retry['episode']['audio']['voice_name'], 'Orus')
        self.assertEqual(len(provider.calls), calls_after_voice_change)
        self.assertEqual(current_audio_asset(
            episode, profile_id, voice_name='Orus', state_dir=state_dir,
            provider_name='fixture')[2], orus_path)
        self.assertEqual(current_audio_asset(
            episode, profile_id, voice_name='Kore', state_dir=state_dir,
            provider_name='fixture')[2], kore_path)

    def test_exhausted_budget_keeps_corrected_audio_pending_and_prior_asset_intact(self):
        previous_edition = deepcopy(EDITION)
        corrected_edition = deepcopy(previous_edition)
        corrected_edition['revision_id'] = 'r2'
        corrected_edition['sections'][0]['audio_script'] = (
            'A material fictional correction changes the delivery plan.')
        corrected_text = deepcopy(corrected_edition)
        state_dir = Path(self.temp.name) / 'budget-denial-audio'
        provider = FiveMinuteFakeProvider()
        ready = render_daily_audio(
            previous_edition, profile=PROFILE, voice_file=self.voice_file,
            state_dir=state_dir, ledger=FakeLedger(Decimal('1')), provider=provider)
        self.assertEqual(ready['audio_status'], 'ready')
        profile_id = profile_identifier(PROFILE)
        previous_episode = daily_episode_from_edition(previous_edition)
        corrected_episode = daily_episode_from_edition(corrected_edition)
        previous_state, previous_manifest, previous_path = current_audio_asset(
            previous_episode, profile_id, voice_name='Kore', state_dir=state_dir,
            provider_name='fixture')
        self.assertEqual(previous_state, 'ready')
        self.assertEqual(previous_manifest['revision_id'], 'r1')
        previous_audio = previous_path.read_bytes()
        calls_before_denial = len(provider.calls)
        corrected_before, _corrected_manifest, _corrected_path = current_audio_asset(
            corrected_episode, profile_id, voice_name='Kore', state_dir=state_dir,
            provider_name='fixture')
        self.assertEqual(corrected_before, 'stale')

        denied = render_daily_audio(
            corrected_edition, profile=PROFILE, voice_file=self.voice_file,
            state_dir=state_dir, ledger=FakeLedger(Decimal('0')), provider=provider)

        self.assertEqual(denied['audio_status'], 'pending')
        self.assertEqual(denied['reason'], 'daily_budget')
        self.assertIsNone(denied['audio_url'])
        self.assertIsNone(denied['duration_seconds'])
        self.assertEqual(denied['chapter_offsets'], [])
        self.assertEqual(len(provider.calls), calls_before_denial,
                         'budget denial must happen before any fake provider call')
        self.assertEqual(corrected_edition, corrected_text,
                         'budget denial must retain the corrected text edition')

        previous_after, manifest_after, path_after = current_audio_asset(
            previous_episode, profile_id, voice_name='Kore', state_dir=state_dir,
            provider_name='fixture')
        corrected_after, corrected_manifest_after, corrected_path_after = current_audio_asset(
            corrected_episode, profile_id, voice_name='Kore', state_dir=state_dir,
            provider_name='fixture')
        self.assertEqual(previous_after, 'ready')
        self.assertEqual(manifest_after['asset_key'], previous_manifest['asset_key'])
        self.assertEqual(path_after, previous_path)
        self.assertEqual(path_after.read_bytes(), previous_audio)
        self.assertEqual((corrected_after, corrected_manifest_after, corrected_path_after),
                         ('stale', None, None),
                         'the prior revision remains available only for its own transcript')

    def test_daily_tts_normalizes_only_the_synthesis_copy(self):
        edition = deepcopy(EDITION)
        edition['sections'][0]['audio_script'] = (
            'SNB reported $90 billion over five years. The notice began August 7, 2024 at '
            '11:00 a.m. Eastern, with 12.5% exposure. U.S. officials discussed CMS and AI. [1]')
        provider = FiveMinuteFakeProvider()
        result = render_daily_audio(edition, profile=PROFILE, voice_file=self.voice_file,
                                    state_dir=self.temp.name, ledger=FakeLedger(Decimal('1')),
                                    provider=provider)
        self.assertEqual(result['audio_status'], 'ready')
        self.assertIn('S-N-B reported ninety billion dollars over five years.', provider.calls[0][0])
        self.assertIn('August seventh, twenty twenty-four at eleven a m Eastern', provider.calls[0][0])
        self.assertIn('with twelve point five percent exposure. U-S officials discussed C-M-S and A-I.',
                      provider.calls[0][0])
        self.assertNotIn('[1]', provider.calls[0][0])
        self.assertIn('$90 billion', edition['sections'][0]['audio_script'],
                      'TTS normalization must not alter source-linked display/script text')

    def test_daily_budget_and_disabled_tts_degrade_to_pending(self):
        renderer = lambda *_args, **_kwargs: self.fail('must not render without budget')
        result = render_daily_audio(EDITION, profile=PROFILE, voice_file=self.voice_file,
                                    state_dir=self.temp.name, ledger=FakeLedger(Decimal('0')),
                                    renderer=renderer)
        self.assertEqual((result['audio_status'], result['reason']), ('pending', 'daily_budget'))
        self.assertEqual(len(EDITION['sections']), 2, 'reserve refusal must preserve edition text')
        with patch.dict('os.environ', {'BRIEFING_TTS': 'disabled'}):
            result = render_daily_audio(EDITION, profile=PROFILE, voice_file=self.voice_file,
                                        state_dir=self.temp.name, ledger=FakeLedger(Decimal('1')),
                                        renderer=renderer)
        self.assertEqual((result['audio_status'], result['reason']), ('pending', 'tts_disabled'))

    def test_budget_recovery_becomes_ready_only_for_current_revision_and_voice(self):
        edition = deepcopy(EDITION)
        text_snapshot = deepcopy(edition)
        state_dir = Path(self.temp.name) / 'budget-recovery-audio'
        provider = FiveMinuteFakeProvider()
        denied = render_daily_audio(
            edition, profile=PROFILE, voice_file=self.voice_file, state_dir=state_dir,
            ledger=FakeLedger(Decimal('0')), provider=provider,
            renderer=lambda *_args, **_kwargs: self.fail('denied audio must remain pending'))
        self.assertEqual((denied['audio_status'], denied['reason']), ('pending', 'daily_budget'))
        self.assertIsNone(denied['audio_url'])
        self.assertEqual(provider.calls, [])
        self.assertEqual(edition, text_snapshot)

        completed_render = {}

        def recording_renderer(episode, profile_id, **kwargs):
            rendered = render_episode_audio(episode, profile_id, **kwargs)
            completed_render['result'] = rendered
            return rendered

        ready = render_daily_audio(
            edition, profile=PROFILE, voice_file=self.voice_file, state_dir=state_dir,
            ledger=FakeLedger(Decimal('1')), provider=provider,
            renderer=recording_renderer)
        self.assertEqual(ready['audio_status'], 'ready')
        self.assertEqual(ready['episode']['revision_id'], 'r1')
        self.assertEqual(ready['voice_name'], 'Kore')
        self.assertEqual(ready['episode']['audio']['voice_name'], 'Kore')
        self.assertEqual(len(provider.calls), len(edition['sections']))
        profile_id = profile_identifier(PROFILE)
        episode = daily_episode_from_edition(edition)
        current_state, manifest, audio_path = current_audio_asset(
            episode, profile_id, voice_name='Kore', state_dir=state_dir,
            provider_name='fixture')
        self.assertEqual(current_state, 'ready')
        self.assertEqual(manifest['revision_id'], 'r1')
        self.assertEqual(manifest['voice_name'], 'Kore')
        self.assertEqual(audio_path, completed_render['result']['directory'] / 'episode.wav')
        self.assertEqual(edition, text_snapshot, 'audio recovery must preserve edition text')

        self.voice_file.write_text(json.dumps({'voice_name': 'Orus'}), encoding='utf-8')
        mismatched_voice = render_daily_audio(
            edition, profile=PROFILE, voice_file=self.voice_file, state_dir=state_dir,
            ledger=FakeLedger(Decimal('1')), provider=provider,
            renderer=lambda *_args, **_kwargs: completed_render['result'])
        self.assertEqual(mismatched_voice['audio_status'], 'failed')
        self.assertIsNone(mismatched_voice['audio_url'])
        self.assertEqual(mismatched_voice['reason'], 'ValueError')
        self.assertEqual(len(provider.calls), len(edition['sections']),
                         'a cached result for another voice must not trigger synthesis or appear ready')
        self.assertEqual(edition, text_snapshot)

    def test_accepted_narration_after_budget_fallback_uses_current_audio_identity(self):
        fallback_edition = deepcopy(EDITION)
        fallback_edition['daily_narration'] = {
            'status': 'fallback', 'reason': 'budget_estimate'}
        state_dir = Path(self.temp.name) / 'narration-fallback-recovery'
        fallback_provider = FiveMinuteFakeProvider()
        fallback_audio = render_daily_audio(
            fallback_edition, profile=PROFILE, voice_file=self.voice_file,
            state_dir=state_dir, ledger=FakeLedger(Decimal('1')), provider=fallback_provider)
        self.assertEqual(fallback_audio['audio_status'], 'ready')
        self.assertEqual(fallback_edition['daily_narration'], {
            'status': 'fallback', 'reason': 'budget_estimate'})

        profile_id = profile_identifier(PROFILE)
        fallback_episode = daily_episode_from_edition(fallback_edition)
        old_state, old_manifest, old_path = current_audio_asset(
            fallback_episode, profile_id, voice_name='Kore', state_dir=state_dir,
            provider_name='fixture')
        self.assertEqual(old_state, 'ready')
        old_audio_bytes = old_path.read_bytes()
        fallback_edition['audio'] = {
            'audio_status': 'ready', 'audio_url': fallback_audio['audio_url'],
            'revision_id': old_manifest['revision_id'], 'script_hash': old_manifest['script_hash'],
            'voice_name': 'Kore', 'chapter_offsets': deepcopy(fallback_audio['chapter_offsets']),
        }

        accepted_edition = deepcopy(fallback_edition)
        accepted_edition['revision_id'] = 'r2'
        accepted_edition['daily_narration'] = {'status': 'written', 'reason': None}
        accepted_edition['sections'][0]['audio_script'] = (
            'Accepted fictional narration updates the delivery schedule.')
        accepted_episode = daily_episode_from_edition(accepted_edition)
        stale_state, stale_manifest, stale_path = current_audio_asset(
            accepted_episode, profile_id, voice_name='Kore', state_dir=state_dir,
            provider_name='fixture')
        self.assertEqual((stale_state, stale_manifest, stale_path), ('stale', None, None),
                         'the fallback recording cannot stand in for accepted current narration')

        accepted_provider = FiveMinuteFakeProvider()
        accepted_audio = render_daily_audio(
            accepted_edition, profile=PROFILE, voice_file=self.voice_file,
            state_dir=state_dir, ledger=FakeLedger(Decimal('1')), provider=accepted_provider)
        current_state, current_manifest, current_path = current_audio_asset(
            accepted_episode, profile_id, voice_name='Kore', state_dir=state_dir,
            provider_name='fixture')

        self.assertEqual(accepted_audio['audio_status'], 'ready')
        self.assertEqual(accepted_edition['daily_narration'], {'status': 'written', 'reason': None})
        self.assertEqual(accepted_audio['episode']['revision_id'], 'r2')
        self.assertEqual(accepted_audio['episode']['audio']['script_hash'], current_manifest['script_hash'])
        self.assertNotEqual(current_manifest['script_hash'], old_manifest['script_hash'])
        self.assertEqual(current_state, 'ready')
        self.assertEqual(current_manifest['revision_id'], 'r2')
        self.assertEqual(current_path.parent.name, current_manifest['asset_key'])
        self.assertEqual(accepted_audio['chapter_offsets'], [
            {'chapter_id': row['chapter_id'], 'start_seconds': row['start_seconds'],
             'duration_seconds': row['duration_seconds']} for row in current_manifest['chapters']])
        self.assertEqual(current_audio_asset(
            fallback_episode, profile_id, voice_name='Kore', state_dir=state_dir,
            provider_name='fixture')[:2], ('ready', old_manifest))
        self.assertEqual(old_path.read_bytes(), old_audio_bytes)

    def test_tts_failure_is_pending_without_losing_edition_text(self):
        result = render_daily_audio(EDITION, profile=PROFILE, voice_file=self.voice_file,
                                    state_dir=self.temp.name, ledger=FakeLedger(Decimal('1')),
                                    provider=FiveMinuteFakeProvider(fail=True))
        self.assertEqual((result['audio_status'], result['reason']), ('pending', 'synthesis_failed'))
        self.assertIsNone(result['audio_url'])
        self.assertEqual(EDITION['sections'][0]['heading'], 'First fictional story')

    def test_review_candidate_shape_remains_pending_without_ready_asset_lookup(self):
        candidate = {'status': 'pending_human_listening', 'batch_id': 'fictional-review',
                     'candidate_directory': Path(self.temp.name) / 'private-review',
                     'candidate_manifest': {'status': 'pending_human_listening'}}
        edition = deepcopy(EDITION)
        with patch('dev.ingest.daily_audio.current_audio_asset') as current:
            result = render_daily_audio(
                edition, profile=PROFILE, voice_file=self.voice_file,
                state_dir=self.temp.name, ledger=FakeLedger(Decimal('1')),
                renderer=lambda *_args, **_kwargs: candidate)
        current.assert_not_called()
        self.assertEqual((result['audio_status'], result['reason']), ('pending', 'pending_human_listening'))
        self.assertIsNone(result['audio_url'])
        self.assertNotIn('candidate_directory', result)
        self.assertEqual(edition, EDITION)

    def test_daily_runner_persistence_updates_current_bundle_and_edition_export(self):
        from dev.ingest import daily, generation_api
        root = Path(self.temp.name)
        default_dir, profile_dir = root / 'default', root / 'profile'
        default_dir.mkdir(); profile_dir.mkdir()
        edition = dict(EDITION, script_hash='a' * 64)
        edition_bundle = {'editions': {'editions': [edition]}, 'sources': {'sources': []}}
        edition_export = {'editions': [edition]}
        for directory in (default_dir, profile_dir):
            (directory / 'current.json').write_text(json.dumps(edition_bundle), encoding='utf-8')
            (directory / 'editions.json').write_text(json.dumps(edition_export), encoding='utf-8')
        audio = {'audio_status': 'ready', 'audio_url': '/api/daily/audio',
                 'asset_key': 'b' * 64,
                 'duration_seconds': 299.0, 'chapter_offsets': [{'chapter_id': 'story-one',
                 'start_seconds': 0.0, 'duration_seconds': 299.0}], 'voice_name': 'Kore',
                 'episode': {'id': edition['id'], 'revision_id': edition['revision_id'],
                             'audio': {'revision_id': edition['revision_id'],
                                       'script_hash': edition['script_hash'], 'voice_name': 'Kore'}},
                 'ready_at': '2026-11-19T12:00:00Z', 'file_size_bytes': 8192, 'resumed': False}
        with patch.object(generation_api, 'DEFAULT_EDITION_DIR', default_dir), \
                patch.object(generation_api, 'profile_generated_dir', return_value=profile_dir):
            result = daily._persist_daily_audio(PROFILE, dict(edition), audio)
        self.assertEqual(result['audio_status'], 'ready')
        self.assertEqual((result['revision_id'], result['script_hash']),
                         (edition['revision_id'], edition['script_hash']))
        for directory in (default_dir, profile_dir):
            bundle = json.loads((directory / 'current.json').read_text(encoding='utf-8'))
            export = json.loads((directory / 'editions.json').read_text(encoding='utf-8'))
            self.assertEqual(bundle['editions']['editions'][0]['audio']['audio_url'], '/api/daily/audio')
            self.assertEqual((bundle['editions']['editions'][0]['audio']['revision_id'],
                              bundle['editions']['editions'][0]['audio']['script_hash']),
                             (edition['revision_id'], edition['script_hash']))
            self.assertEqual(export['editions'][0]['audio']['duration_seconds'], 299.0)
            self.assertEqual((export['editions'][0]['audio']['revision_id'],
                              export['editions'][0]['audio']['script_hash']),
                             (edition['revision_id'], edition['script_hash']))
            self.assertEqual(export['editions'][0]['audio']['ready_at'], '2026-11-19T12:00:00Z')
            self.assertEqual(export['editions'][0]['audio']['file_size_bytes'], 8192)
            self.assertFalse(export['editions'][0]['audio']['resumed'])

        # The same persistence boundary must retain an explicitly approved bounded reference.
        identity = {'asset_key': 'b' * 64, 'episode_id': edition['id'],
                    'provider': 'gemini', 'model': DEFAULT_MODEL,
                    'batch_id': 'fictional-approved-batch', 'tts_output_token_limit': 4096,
                    'max_segment_attempts': 3, 'coverage_status': 'human_listening_approved',
                    'human_listening_approval': {'revision_id': edition['revision_id'],
                                                 'script_hash': edition['script_hash']}}
        audio.update(deepcopy(identity))
        audio['episode']['audio'].update(deepcopy(identity), status='recorded')
        with patch.object(generation_api, 'DEFAULT_EDITION_DIR', default_dir), \
                patch.object(generation_api, 'profile_generated_dir', return_value=profile_dir):
            bounded = daily._persist_daily_audio(PROFILE, dict(edition), audio)
        self.assertEqual(bounded['audio_status'], 'ready')
        for directory in (default_dir, profile_dir):
            bundle = json.loads((directory / 'current.json').read_text(encoding='utf-8'))
            export = json.loads((directory / 'editions.json').read_text(encoding='utf-8'))
            for stored in (bundle['editions']['editions'][0]['audio'], export['editions'][0]['audio']):
                self.assertEqual({key: stored[key] for key in identity}, identity)

    def test_daily_audio_persistence_rejects_stale_revision_or_script_identity(self):
        from dev.ingest import daily, generation_api
        root = Path(self.temp.name)
        default_dir, profile_dir = root / 'identity-default', root / 'identity-profile'
        default_dir.mkdir(); profile_dir.mkdir()
        previous = deepcopy(EDITION)
        previous.update(revision_id='r1', script_hash='a' * 64)
        previous['audio'] = {'audio_status': 'ready', 'audio_url': '/api/daily/audio',
                             'duration_seconds': 300.0, 'revision_id': 'r1',
                             'script_hash': 'a' * 64, 'voice_name': 'Kore'}
        corrected = deepcopy(previous)
        corrected.update(revision_id='r2', script_hash='b' * 64)
        corrected['sections'][0]['body'] = 'Corrected fictional text for revision two.'
        corrected['sections'][0]['audio_script'] = 'Corrected fictional spoken text for revision two.'
        corrected['corrections'] = [{'previous_revision_id': 'r1', 'revision_id': 'r2'}]
        current_manifest_audio = {'revision_id': 'r2', 'script_hash': 'b' * 64,
                                  'voice_name': 'Kore'}
        ready_audio = {'audio_status': 'ready', 'audio_url': '/api/daily/audio',
                       'duration_seconds': 300.0, 'chapter_offsets': [
                           {'chapter_id': 'story-one', 'start_seconds': 0.0, 'duration_seconds': 150.0}],
                       'voice_name': 'Kore', 'episode': {
                           'id': corrected['id'], 'revision_id': 'r2',
                           'audio': current_manifest_audio}}
        current_bundle = {'editions': {'editions': [deepcopy(corrected)]}, 'sources': {'sources': []}}
        # Old revision deliberately appears first. Persistence must locate the exact current row.
        export = {'editions': [deepcopy(previous), deepcopy(corrected)]}
        for directory in (default_dir, profile_dir):
            (directory / 'current.json').write_text(json.dumps(current_bundle), encoding='utf-8')
            (directory / 'editions.json').write_text(json.dumps(export), encoding='utf-8')

        with patch.object(generation_api, 'DEFAULT_EDITION_DIR', default_dir), \
                patch.object(generation_api, 'profile_generated_dir', return_value=profile_dir):
            metadata = daily._persist_daily_audio(PROFILE, deepcopy(corrected), ready_audio)

            current_edition = deepcopy(corrected)
            current_edition['audio'] = deepcopy(metadata)
            snapshot_paths = [directory / filename for directory in (default_dir, profile_dir)
                              for filename in ('current.json', 'editions.json')]
            ready_snapshots = {path: path.read_text(encoding='utf-8') for path in snapshot_paths}

            for directory in (default_dir, profile_dir):
                stored = json.loads((directory / 'editions.json').read_text(encoding='utf-8'))['editions']
                self.assertEqual((stored[0]['revision_id'], stored[0]['audio']['revision_id'],
                                  stored[0]['audio']['script_hash']),
                                 ('r1', 'r1', 'a' * 64))
                self.assertEqual((stored[1]['revision_id'], stored[1]['script_hash'],
                                  stored[1]['audio']['revision_id'], stored[1]['audio']['script_hash']),
                                 ('r2', 'b' * 64, 'r2', 'b' * 64))

            stale_revision_audio = deepcopy(ready_audio)
            stale_revision_audio['episode']['revision_id'] = 'r1'
            stale_revision_audio['episode']['audio'] = {
                'revision_id': 'r1', 'script_hash': 'a' * 64, 'voice_name': 'Kore'}
            stale_script_audio = deepcopy(ready_audio)
            stale_script_audio['episode']['audio']['script_hash'] = 'a' * 64
            # Approval of r1 finishes after corrected r2 has already been persisted.
            late_approved = deepcopy(stale_revision_audio)
            approved_identity = {
                'episode_id': previous['id'], 'batch_id': 'fictional-late-r1-approval',
                'provider': 'gemini', 'model': DEFAULT_MODEL,
                'asset_key': 'c' * 64, 'tts_output_token_limit': 4096,
                'max_segment_attempts': 1, 'coverage_status': 'human_listening_approved',
                'human_listening_approval': {'revision_id': 'r1', 'script_hash': 'a' * 64},
            }
            late_approved.update(deepcopy(approved_identity))
            late_approved['episode']['audio'].update(deepcopy(approved_identity), status='recorded')
            late_approved_before = deepcopy(late_approved)
            stale_results = []
            stale_editions = []
            for stale in (stale_revision_audio, stale_script_audio, late_approved):
                stale_edition = deepcopy(current_edition)
                stale_editions.append(stale_edition)
                stale_results.append(daily._persist_daily_audio(PROFILE, stale_edition, stale))
            self.assertEqual([edition['audio'] for edition in stale_editions],
                             [metadata] * 3)
            self.assertEqual({path: path.read_text(encoding='utf-8') for path in snapshot_paths},
                             ready_snapshots)

        self.assertEqual((metadata['audio_status'], metadata['revision_id'], metadata['script_hash']),
                         ('ready', 'r2', 'b' * 64))
        for rejected in stale_results:
            self.assertEqual(rejected['audio_status'], 'pending')
            self.assertEqual(rejected['reason'], 'superseded')
            self.assertIsNone(rejected['audio_url'])
            self.assertIsNone(rejected['duration_seconds'])
            self.assertEqual(rejected['chapter_offsets'], [])
            self.assertNotIn('revision_id', rejected)
            self.assertNotIn('script_hash', rejected)
        for directory in (default_dir, profile_dir):
            bundle = json.loads((directory / 'current.json').read_text(encoding='utf-8'))
            self.assertEqual(bundle['editions']['editions'][0]['audio'], metadata)
            stored = json.loads((directory / 'editions.json').read_text(encoding='utf-8'))['editions']
            self.assertEqual((stored[0]['audio']['revision_id'], stored[0]['audio']['script_hash']),
                             ('r1', 'a' * 64))
            self.assertEqual((stored[1]['audio']['audio_status'], stored[1]['audio']['revision_id'],
                              stored[1]['audio']['script_hash']),
                             ('ready', 'r2', 'b' * 64))

        # A worker retaining an r1 edition snapshot must not write its approved
        # identity onto r2, even though r1 is still retained in edition history.
        active_paths = [directory / 'current.json' for directory in (default_dir, profile_dir)]
        active_before = {path: path.read_bytes() for path in active_paths}
        with patch.object(generation_api, 'DEFAULT_EDITION_DIR', default_dir), \
                patch.object(generation_api, 'profile_generated_dir', return_value=profile_dir):
            retained = daily._persist_daily_audio(PROFILE, deepcopy(previous), late_approved)
        self.assertEqual(retained['audio_status'], 'ready', 'r1 approval remains valid only for r1')
        self.assertEqual({path: path.read_bytes() for path in active_paths}, active_before)
        for directory in (default_dir, profile_dir):
            rows = json.loads((directory / 'editions.json').read_text())['editions']
            current = next(row for row in rows if row['revision_id'] == 'r2')
            self.assertEqual(current['audio'], metadata)
            self.assertNotIn('human_listening_approval', current['audio'])
            self.assertNotIn('batch_id', current['audio'])
        self.assertEqual(late_approved, late_approved_before)

    def test_daily_audio_status_json_records_ready_time_size_and_resume(self):
        from dev.ingest import daily
        root = Path(self.temp.name)
        status_path = root / 'status.json'
        text_path = root / 'current.json'
        text_path.write_text(json.dumps({'edition': EDITION}), encoding='utf-8')
        provider = FiveMinuteFakeProvider()

        def renderer(edition, *, profile):
            return render_daily_audio(edition, profile=profile, voice_file=self.voice_file,
                state_dir=root / 'audio-state', ledger=FakeLedger(Decimal('1')), provider=provider)

        def persist(_profile, edition, audio):
            edition['audio'] = {key: audio.get(key) for key in (
                'audio_status', 'audio_url', 'duration_seconds', 'file_size_bytes', 'ready_at',
                'resumed', 'chapter_offsets')}
            text = json.loads(text_path.read_text(encoding='utf-8'))
            text['edition']['audio'] = edition['audio']
            text_path.write_text(json.dumps(text), encoding='utf-8')
            return edition['audio']

        states = iter([(200, {'status': 'complete', 'job_id': 'fixture-job',
                              'edition_ready': {'edition': dict(EDITION), 'sources': []}})])
        code, result = daily.run_daily(profile_loader=lambda: PROFILE,
            start=lambda _profile: (202, {'job_id': 'fixture-job'}),
            get_job=lambda _job: next(states), sleep=lambda _delay: None,
            report_writer=lambda value: daily.write_run_report(value, status_path),
            daily_audio_renderer=renderer, daily_audio_persister=persist)
        report = json.loads(status_path.read_text(encoding='utf-8'))['daily_run']
        audio = report['daily_audio']
        self.assertEqual(code, 0)
        self.assertEqual(result['daily_audio']['audio_status'], 'ready')
        self.assertRegex(audio['ready_at'], r'^\d{4}-\d\d-\d\dT.*Z$')
        self.assertEqual(audio['duration_seconds'], 300.0)
        self.assertGreater(audio['file_size_bytes'], 44)
        self.assertFalse(audio['resumed'])
        self.assertEqual(len(provider.calls), 2)
        resumed = renderer(EDITION, profile=PROFILE)
        self.assertEqual(resumed['audio_status'], 'ready')
        self.assertTrue(resumed['resumed'])
        self.assertEqual(len(provider.calls), 2, 'a repeated run reuses the verified stitched recording')
        self.assertEqual(resumed['file_size_bytes'], audio['file_size_bytes'])
        expected_chapters = daily_episode_from_edition(EDITION)['chapters']
        speech_chapters = [{key: row[key] for key in ('id', 'title', 'spoken_text')}
                           for row in resumed['episode']['chapters']]
        self.assertEqual(speech_chapters, expected_chapters,
                         'the stitched recording must use the final edition speech chapter text')
        self.assertEqual([text for text, _voice in provider.calls],
                         [chapter['spoken_text'] for chapter in expected_chapters])
        saved = json.loads(text_path.read_text(encoding='utf-8'))['edition']
        self.assertEqual(len(saved['sections']), len(EDITION['sections']))

    def test_daily_audio_failure_keeps_text_and_logs_count_only(self):
        from dev.ingest import daily
        root = Path(self.temp.name)
        status_path = root / 'status.json'
        text_path = root / 'current.json'
        text_path.write_text(json.dumps({'edition': EDITION}), encoding='utf-8')
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger('dev.ingest.daily_audio')
        logger.addHandler(handler)
        try:
            states = iter([(200, {'status': 'complete', 'job_id': 'fixture-job',
                                  'edition_ready': {'edition': dict(EDITION), 'sources': []}})])
            code, result = daily.run_daily(profile_loader=lambda: PROFILE,
                start=lambda _profile: (202, {'job_id': 'fixture-job'}),
                get_job=lambda _job: next(states), sleep=lambda _delay: None,
                report_writer=lambda value: daily.write_run_report(value, status_path),
                daily_audio_renderer=lambda edition, **kwargs: render_daily_audio(
                    edition, profile=PROFILE, voice_file=self.voice_file, state_dir=root / 'failed-audio',
                    ledger=FakeLedger(Decimal('1')), provider=FiveMinuteFakeProvider(fail=True)),
                daily_audio_persister=lambda _profile, _edition, audio: audio)
        finally:
            logger.removeHandler(handler)
        self.assertEqual(code, 0)
        self.assertEqual(result['daily_audio']['audio_status'], 'pending')
        self.assertEqual(result['daily_audio']['reason'], 'synthesis_failed')
        self.assertEqual(json.loads(status_path.read_text())['daily_run']['daily_audio']['audio_status'], 'pending')
        self.assertEqual(json.loads(text_path.read_text())['edition']['sections'], EDITION['sections'])
        logged = stream.getvalue()
        self.assertIn('Daily audio render failed: Chapter 1 failed after 3 attempts', logged)
        for private in ('fictional TTS failure', 'First fictional story', 'Kore'):
            self.assertNotIn(private, logged)

    def test_renderer_exception_persists_text_only_audio_state(self):
        from dev.ingest import daily
        states = iter([(200, {'status': 'complete', 'job_id': 'fixture-job',
                              'edition_ready': {'edition': dict(EDITION), 'sources': []}})])
        persisted = {}

        def persist(_profile, _edition, audio):
            persisted.update(audio)
            return audio

        code, result = daily.run_daily(
            profile_loader=lambda: PROFILE,
            start=lambda _profile: (202, {'job_id': 'fixture-job'}),
            get_job=lambda _job: next(states), sleep=lambda _delay: None,
            report_writer=lambda _value: None,
            daily_audio_renderer=lambda _edition, **_kwargs: (_ for _ in ()).throw(
                RuntimeError('fictional renderer failure')),
            daily_audio_persister=persist)
        self.assertEqual(code, 0)
        self.assertEqual(result['daily_audio']['audio_status'], 'pending')
        self.assertEqual(result['daily_audio']['reason'], 'RuntimeError')
        self.assertEqual(persisted['audio_status'], 'pending')

    def test_failed_corrected_synthesis_stays_pending_without_exposing_stale_audio(self):
        previous_edition = deepcopy(EDITION)
        state_dir = Path(self.temp.name) / 'failed-correction-audio'
        previous = render_daily_audio(
            previous_edition, profile=PROFILE, voice_file=self.voice_file,
            state_dir=state_dir, ledger=FakeLedger(Decimal('1')),
            provider=FiveMinuteFakeProvider())
        self.assertEqual(previous['audio_status'], 'ready')
        profile_id = profile_identifier(PROFILE)
        previous_episode = daily_episode_from_edition(previous_edition)
        old_state, old_manifest, old_path = current_audio_asset(
            previous_episode, profile_id, voice_name='Kore', state_dir=state_dir,
            provider_name='fixture')
        self.assertEqual(old_state, 'ready')
        self.assertEqual(old_manifest['revision_id'], 'r1')
        old_audio = old_path.read_bytes()

        corrected_edition = deepcopy(previous_edition)
        corrected_edition['revision_id'] = 'r2'
        corrected_edition['sections'][0]['audio_script'] = (
            'The corrected fictional notice changes the delivery date.')
        text_before_synthesis = deepcopy(corrected_edition)
        failed_provider = FiveMinuteFakeProvider(fail=True)
        fallback = render_daily_audio(
            corrected_edition, profile=PROFILE, voice_file=self.voice_file,
            state_dir=state_dir, ledger=FakeLedger(Decimal('1')),
            provider=failed_provider)

        self.assertEqual(fallback['audio_status'], 'pending')
        self.assertEqual(fallback['reason'], 'synthesis_failed')
        self.assertIsNone(fallback['audio_url'])
        self.assertEqual(fallback['chapter_offsets'], [])
        self.assertNotIn('episode', fallback, 'failed corrected audio must not return older episode data')
        self.assertEqual(len(failed_provider.calls), 3)
        self.assertEqual(corrected_edition, text_before_synthesis,
                         'corrected display text stays available after synthesis failure')
        self.assertIn('corrected fictional notice',
                      daily_episode_from_edition(corrected_edition)['chapters'][0]['spoken_text'])

        corrected_state, corrected_manifest, corrected_path = current_audio_asset(
            daily_episode_from_edition(corrected_edition), profile_id,
            voice_name='Kore', state_dir=state_dir, provider_name='fixture')
        self.assertEqual((corrected_state, corrected_manifest, corrected_path),
                         ('stale', None, None),
                         'the prior revision audio is not current for corrected text')
        old_state_after, old_manifest_after, old_path_after = current_audio_asset(
            previous_episode, profile_id, voice_name='Kore', state_dir=state_dir,
            provider_name='fixture')
        self.assertEqual(old_state_after, 'ready')
        self.assertEqual(old_manifest_after['asset_key'], old_manifest['asset_key'])
        self.assertEqual(old_path_after.read_bytes(), old_audio)

    def test_failed_corrected_synthesis_retry_publishes_only_current_revision_and_voice(self):
        previous_edition = deepcopy(EDITION)
        state_dir = Path(self.temp.name) / 'synthesis-retry-audio'
        previous = render_daily_audio(
            previous_edition, profile=PROFILE, voice_file=self.voice_file,
            state_dir=state_dir, ledger=FakeLedger(Decimal('1')),
            provider=FiveMinuteFakeProvider())
        self.assertEqual(previous['audio_status'], 'ready')
        profile_id = profile_identifier(PROFILE)
        previous_episode = daily_episode_from_edition(previous_edition)
        previous_state, previous_manifest, previous_path = current_audio_asset(
            previous_episode, profile_id, voice_name='Kore', state_dir=state_dir,
            provider_name='fixture')
        self.assertEqual(previous_state, 'ready')
        previous_audio = previous_path.read_bytes()

        corrected_edition = deepcopy(previous_edition)
        corrected_edition['revision_id'] = 'r2'
        corrected_edition['sections'][0]['audio_script'] = (
            'A corrected fictional transit notice changes the opening time.')
        corrected_text = deepcopy(corrected_edition)
        corrected_episode = daily_episode_from_edition(corrected_edition)
        self.voice_file.write_text(json.dumps({'voice_name': 'Orus'}), encoding='utf-8')
        failing_provider = FiveMinuteFakeProvider(fail=True)
        failed = render_daily_audio(
            corrected_edition, profile=PROFILE, voice_file=self.voice_file,
            state_dir=state_dir, ledger=FakeLedger(Decimal('1')),
            provider=failing_provider)

        self.assertEqual((failed['audio_status'], failed['reason']),
                         ('pending', 'synthesis_failed'))
        self.assertIsNone(failed['audio_url'])
        self.assertEqual(failed['chapter_offsets'], [])
        self.assertNotIn('episode', failed)
        self.assertEqual(len(failing_provider.calls), 3)
        self.assertTrue(all(voice == 'Orus' for _text, voice in failing_provider.calls))
        self.assertEqual(corrected_edition, corrected_text)
        pending_state, pending_manifest, pending_path = current_audio_asset(
            corrected_episode, profile_id, voice_name='Orus', state_dir=state_dir,
            provider_name='fixture')
        self.assertEqual((pending_state, pending_manifest, pending_path), ('stale', None, None))
        self.assertEqual(previous_path.read_bytes(), previous_audio)

        retry_provider = FiveMinuteFakeProvider()
        retried = render_daily_audio(
            corrected_edition, profile=PROFILE, voice_file=self.voice_file,
            state_dir=state_dir, ledger=FakeLedger(Decimal('1')),
            provider=retry_provider)
        self.assertEqual(retried['audio_status'], 'ready')
        self.assertEqual(retried['episode']['revision_id'], 'r2')
        self.assertEqual(retried['voice_name'], 'Orus')
        self.assertEqual(retried['episode']['audio']['voice_name'], 'Orus')
        self.assertEqual(len(retry_provider.calls), len(corrected_episode['chapters']))
        self.assertTrue(all(voice == 'Orus' for _text, voice in retry_provider.calls))
        self.assertEqual(corrected_edition, corrected_text)
        current_state, current_manifest, current_path = current_audio_asset(
            corrected_episode, profile_id, voice_name='Orus', state_dir=state_dir,
            provider_name='fixture')
        self.assertEqual(current_state, 'ready')
        self.assertEqual(current_manifest['revision_id'], 'r2')
        self.assertEqual(current_manifest['voice_name'], 'Orus')
        self.assertEqual(current_path, previous_path.parent.parent /
                         current_manifest['asset_key'] / 'episode.wav')
        wrong_voice_state, wrong_voice_manifest, wrong_voice_path = current_audio_asset(
            corrected_episode, profile_id, voice_name='Kore', state_dir=state_dir,
            provider_name='fixture')
        self.assertEqual((wrong_voice_state, wrong_voice_manifest, wrong_voice_path),
                         ('stale', None, None))
        previous_state_after, previous_manifest_after, previous_path_after = current_audio_asset(
            previous_episode, profile_id, voice_name='Kore', state_dir=state_dir,
            provider_name='fixture')
        self.assertEqual(previous_state_after, 'ready')
        self.assertEqual(previous_manifest_after['asset_key'], previous_manifest['asset_key'])
        self.assertEqual(previous_path_after.read_bytes(), previous_audio)

if __name__ == '__main__':
    unittest.main()
