"""Production job orchestration with injected research/model/TTS; no network or keys."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from contextlib import ExitStack
from copy import deepcopy
from functools import partial
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from dev.ingest.episodes.generate import generate_episode, EpisodeCancelled
from dev.ingest.episodes.policy import BUDGET_MESSAGE, WORDS_PER_MINUTE, estimate_episode
from dev.ingest.episodes_audio import render_episode_audio, current_audio_asset, DEFAULT_VOICE
from dev.ingest.jobs import EpisodeJobStore
from dev.ingest.paths import profile_identifier
from dev.ingest.usage import UsageLedger, UsageCapReached, episode_usage, UsageCancelled
from dev.tests.episode_generation_test import (Client, Provider, BODY, TOPIC, PROFILE, draft,
                                              add_test_signposts)
from dev.tests.episodes_audio_test import FakeProvider


class LiveEpisodeTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ, {'BRIEFING_EPISODES': 'real', 'BRIEFING_TESTING': '1',
            'BRIEFING_STATE_DIR': str(self.root / 'state'), 'BRIEFING_TEST_LEDGER_DIR': str(self.root / 'ledger')}))
        for name in ('socket.create_connection', 'socket.socket.connect', 'socket.getaddrinfo'):
            self.stack.enter_context(patch(name, side_effect=AssertionError('network forbidden')))
        prices = json.loads(Path('dev/ingest/usage-prices.json').read_text())
        prices['caps'] = {'daily_usd': '10', 'monthly_usd': '20', 'daily_run_reserve_usd': '0.15'}
        config = self.root / 'prices.json'
        config.write_text(json.dumps(prices))
        self.ledger = UsageLedger(ledger_dir=self.root / 'ledger', config_path=config)

    def store(self, root=None, provider=None, client=None, tts_enabled=True, audio_renderer=None):
        client = client or Client()
        return EpisodeJobStore(root or self.root / 'state', ledger=self.ledger, key_available=lambda: True,
            real_generator=partial(generate_episode, registry=[], providers=[Provider()],
                                   fetcher=lambda *a, **k: BODY, client=client),
            audio_renderer=audio_renderer or partial(render_episode_audio, provider=provider or FakeProvider()),
            tts_enabled=tts_enabled)

    def test_each_length_runs_real_stages_and_renders_only_verified_words(self):
        for minutes in (3, 5, 10, 15):
            with self.subTest(minutes=minutes):
                provider, client = FakeProvider(), Client()
                store = self.store(self.root / str(minutes), provider, client)
                status, job = store.submit(PROFILE, {'topic': TOPIC, 'length': minutes, 'style': 'explainer'}, start=False)
                self.assertEqual(status, 202)
                self.assertEqual(job['target_words'], minutes * WORDS_PER_MINUTE)
                self.assertRegex(job['cost_estimate']['label'], r'^about \d+ cents$')
                seen = []
                mutate = store._mutate
                def progress(*args, **updates):
                    seen.append(deepcopy(updates))
                    return mutate(*args, **updates)
                store._mutate = progress
                store.run_pending(PROFILE, job['job_id'])
                result = store.get(PROFILE, job['job_id'])
                self.assertEqual(result['status'], 'done', result)
                episode = result['episode']
                self.assertEqual(sum(p['target_words'] for p in client.payloads
                                     if 'chapter_assignment' in p and not p.get('expansion_pass')), minutes * WORDS_PER_MINUTE)
                self.assertTrue({'researching', 'reading', 'evidence', 'writing', 'checking', 'recording', 'done'} <= {u.get('stage') for u in seen})
                self.assertGreater(result['counts']['sources_found'], 0)
                self.assertEqual(result['counts']['sources_read'], 1)
                self.assertEqual(result['counts']['claims_matched'], 8)
                self.assertTrue(result['shorter_than_requested'])
                metrics = result['quality_metrics']
                self.assertEqual(metrics['requested_minutes'], minutes)
                self.assertEqual(metrics['sources_read'], 1)
                self.assertEqual(metrics['chapters_answered'], len(episode['chapters']))
                self.assertEqual(episode['quality_metrics'], metrics)
                self.assertIn('writing', metrics['stage_seconds'])
                self.assertGreaterEqual(metrics['spoken_minutes'], 0)
                self.assertIn('episode is shorter', episode['transcript'])
                self.assertIn("couldn't find solid evidence", episode['transcript'])
                self.assertEqual('\n\n'.join(text for text, voice in provider.calls), episode['transcript'])
                self.assertTrue(all(voice == DEFAULT_VOICE for _, voice in provider.calls))
                self.assertEqual(episode['audio']['script_hash'], episode['script_hash'])
                self.assertEqual(episode['audio']['url'], f"/api/episodes/{job['job_id']}/audio")
                state, manifest, audio = current_audio_asset(episode, profile_identifier(PROFILE),
                    voice_name=DEFAULT_VOICE, state_dir=store.state_dir)
                self.assertEqual(state, 'ready')
                self.assertEqual(result['counts']['audio_seconds'], manifest['duration_seconds'])
                self.assertTrue(audio.is_file())
                self.assertEqual([c['audio_start_seconds'] for c in episode['chapters']],
                                 [round(index * .1, 1) for index in range(len(episode['chapters']))])
                self.assertEqual(episode['cost_record']['text_calls'], 0)  # injected calls do not fabricate spend

    def test_default_real_missing_key_and_fake_test_only(self):
        with patch.dict(os.environ):
            os.environ.pop('BRIEFING_EPISODES', None)
            store = EpisodeJobStore(self.root, key_available=lambda: False, ledger=self.ledger)
            self.assertTrue(store.real)
            status, result = store.submit(PROFILE, {'topic': TOPIC, 'length': 3, 'style': 'explainer'})
            self.assertEqual(status, 503)
            self.assertNotIn('job_id', result)
        with patch.dict(os.environ, {'BRIEFING_EPISODES': 'fake', 'BRIEFING_TESTING': '0'}):
            with self.assertRaises(ValueError):
                EpisodeJobStore(self.root)

    def test_tts_disabled_finishes_verified_text_with_pending_audio(self):
        renderer = Mock(side_effect=AssertionError('disabled TTS must not run'))
        store = self.store(tts_enabled=False, audio_renderer=renderer)
        status, job = store.submit(PROFILE, {'topic': TOPIC, 'length': 5, 'style': 'explainer'}, start=False)
        self.assertEqual(status, 202)
        self.assertEqual(job['cost_estimate']['tts_usd'], '0.00000000')
        store.run_pending(PROFILE, job['job_id'])
        result = store.get(PROFILE, job['job_id'])
        self.assertEqual(result['status'], 'done')
        self.assertEqual(result['stage'], 'done')
        self.assertEqual(result['audio_status'], 'pending')
        self.assertEqual(result['episode']['audio_status'], 'pending')
        self.assertEqual(result['episode']['audio']['status'], 'pending')
        self.assertNotIn('url', result['episode']['audio'])
        self.assertEqual(result['counts']['audio_seconds'], 0)
        self.assertEqual(result['episode']['cost_record']['tts_characters'], 0)
        renderer.assert_not_called()

    def test_tts_environment_switch_defaults_on_and_can_disable(self):
        with patch.dict(os.environ, {'BRIEFING_TTS': '0'}):
            store = EpisodeJobStore(self.root / 'disabled', tts_enabled=None,
                                    ledger=self.ledger, key_available=lambda: True)
            self.assertFalse(store.tts_enabled)
            self.assertIsNone(store._preflight(3)['tts_model'])
        with patch.dict(os.environ, {'BRIEFING_TTS': '1'}):
            store = EpisodeJobStore(self.root / 'enabled', tts_enabled=None,
                                    ledger=self.ledger, key_available=lambda: True)
            self.assertTrue(store.tts_enabled)
            self.assertEqual(store._preflight(3)['tts_model'], 'gemini-3.8-flash-lite-tts')


    def test_cancel_during_tts_stops_next_segment_and_never_publishes(self):
        provider = FakeProvider()
        store = self.store(provider=provider)
        _, job = store.submit(PROFILE, {'topic': TOPIC, 'length': 3, 'style': 'explainer'}, start=False)
        synthesize = provider.synthesize
        def cancel(text, voice):
            result = synthesize(text, voice)
            store.cancel(PROFILE, job['job_id'])
            return result
        provider.synthesize = cancel
        store.run_pending(PROFILE, job['job_id'])
        self.assertEqual(store.get(PROFILE, job['job_id'])['status'], 'cancelled')
        self.assertEqual(len(provider.calls), 1)
        self.assertFalse(list(store.state_dir.rglob('manifest.json')))

    def test_per_job_ledger_attribution_survives_parallel_chapter_calls(self):
        base = Client()
        def client(system, payload, schema, timeout):
            reservation = self.ledger.reserve_text_call(100, 100, 100, model='fictional')
            self.ledger.settle_text_call(reservation, model='fictional', prompt_tokens=10,
                                        candidates_tokens=20, thoughts_tokens=3)
            if 'targeted quality repair' in system:
                return add_test_signposts(draft(), payload)
            return base(system, payload, schema, timeout)
        store = self.store(client=client)
        _, job = store.submit(PROFILE, {'topic': TOPIC, 'length': 3, 'style': 'explainer'}, start=False)
        below = {'passed': False, 'partial': True, 'score': 5, 'out_of': 7,
                 'bar': 6, 'issues': ['spoken_style'], 'checks': []}
        above = {'passed': True, 'partial': False, 'score': 7, 'out_of': 7,
                 'bar': 6, 'issues': [], 'checks': []}
        with patch('dev.ingest.episodes.script_checks.score_episode_quality', side_effect=[below, above]):
            store.run_pending(PROFILE, job['job_id'])
        record = store.get(PROFILE, job['job_id'])['cost_record']
        self.assertEqual(record['text_calls'], 10)  # three evidence-only expansions plus prior writer/check/repair calls
        self.assertEqual(record['prompt_tokens'], 100)
        self.assertEqual(record['thoughts_tokens'], 30)
        self.assertEqual(record['pending_calls'], 0)
        with episode_usage('cancelled-fixture', lambda: True), self.assertRaises(UsageCancelled):
            self.ledger.record_search('gemini')
        self.assertEqual(self.ledger.episode_record('cancelled-fixture')['text_calls'], 0)


if __name__ == '__main__':
    unittest.main()
