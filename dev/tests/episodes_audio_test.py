import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from copy import deepcopy
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import BytesIO, StringIO
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from types import MappingProxyType
import unittest
import wave
from unittest.mock import patch
from zoneinfo import ZoneInfo

from dev.ingest.episodes_audio import (
    DEFAULT_VOICE, MAX_SEGMENT_ATTEMPTS, EpisodeAudioError, _chapter_output_token_estimate, _chapter_scripts,
    MAX_TTS_CHUNK_CHARS, _tts_chunks,
    _fingerprints, add_chapter_timestamps, approve_bounded_audio_candidate,
    _read_wav, audio_directory, current_audio_asset, load_voice, main, persisted_audio_matches_manifest,
    persisted_audio_selector, render_episode_audio,
)
from dev.ingest import episodes_audio
from dev.ingest.daily_audio import daily_episode_from_edition
from dev.ingest.tts.gemini import (
    DEFAULT_BATCH_OUTPUT_TOKEN_LIMIT, DEFAULT_MODEL,
    MODEL_INPUT_TOKEN_LIMIT, MODEL_OUTPUT_TOKEN_LIMIT, TtsOutputLimitError, TtsProviderError,
)
from dev.ingest.tts.base import TtsResult
from dev.ingest.tts import GeminiTtsProvider
from dev.ingest.tts.normalize import NORMALIZER_VERSION, normalize_narration
from dev.ingest.usage import UsageCapReached, UsageLedger, reset_usage_caller, usage_caller


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests' / 'fixtures' / 'episode-audio.json'
def read_fixture():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


def wav_bytes(frames=100, sample_rate=1000):
    output = BytesIO()
    with wave.open(output, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b'\0\0' * frames)
    return output.getvalue()


class FakeProvider:
    provider_name = 'gemini'
    model = DEFAULT_MODEL

    def __init__(self, failures=()):
        self.failures = set(failures)
        self.calls = []

    def synthesize(self, text, voice_name):
        self.calls.append((text, voice_name))
        if len(self.calls) in self.failures:
            raise RuntimeError('fictional provider failure detail')
        return TtsResult(wav_bytes(), 'audio/wav', 0.1, len(text), Decimal('0.0001'),
                         DEFAULT_MODEL, voice_name)


class ShortRequestProvider(FakeProvider):
    def __init__(self, *, fail_call_once=None):
        super().__init__()
        self.fail_call_once = fail_call_once
        self.failed = False

    def synthesize(self, text, voice_name):
        self.calls.append((text, voice_name))
        if len(text) > MAX_TTS_CHUNK_CHARS:
            raise RuntimeError('fictional provider rejects long requests')
        if self.fail_call_once == len(self.calls) and not self.failed:
            self.failed = True
            raise RuntimeError('fictional transient failure')
        return TtsResult(wav_bytes(), 'audio/wav', 0.1, len(text), Decimal('0.0001'),
                         DEFAULT_MODEL, voice_name)


class CorrectionDurationProvider(FakeProvider):
    def synthesize(self, text, voice_name):
        self.calls.append((text, voice_name))
        frames = 200 if 'corrected wording' in text else 100
        duration = frames / 1000
        return TtsResult(wav_bytes(frames, sample_rate=1000), 'audio/wav', duration,
                         len(text), Decimal('0.0001'), DEFAULT_MODEL, voice_name)




class EpisodeAudioRenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp.name) / 'state'
        self.voice_file = Path(self.temp.name) / 'voice.json'

    def tearDown(self):
        self.temp.cleanup()

    def test_fingerprints_reject_frozen_transcript_chapter_and_hash_mismatches(self):
        chapters = [
            {'id': 'fictional-chapter-01', 'text': 'Alder Bay tests a library delivery locker.'},
            {'id': 'fictional-chapter-02', 'text': 'The fictional pilot will report results in June.'},
        ]
        transcript = '\n\n'.join(chapter['text'] for chapter in chapters)
        episode = {
            'id': 'fictional-fingerprint-episode',
            'revision_id': 'fictional-fingerprint-r1',
            'transcript': transcript,
            'script_hash': hashlib.sha256(transcript.encode('utf-8')).hexdigest(),
        }

        script_hash, _asset_key = _fingerprints(
            episode, chapters, DEFAULT_VOICE, 'gemini', DEFAULT_MODEL)
        self.assertEqual(script_hash, episode['script_hash'])

        changed_chapters = deepcopy(chapters)
        changed_chapters[1]['text'] += ' The comparison remains fictional.'
        with self.assertRaisesRegex(ValueError, 'Frozen transcript and chapter speech differ'):
            _fingerprints(episode, changed_chapters, DEFAULT_VOICE, 'gemini', DEFAULT_MODEL)

        changed_hash_episode = {**episode, 'script_hash': '0' * 64}
        with self.assertRaisesRegex(ValueError, 'Frozen transcript hash differs'):
            _fingerprints(changed_hash_episode, chapters, DEFAULT_VOICE, 'gemini', DEFAULT_MODEL)

    def test_fingerprints_match_independent_frozen_fallback_and_limit_vectors(self):
        chapters = [
            {'id': 'fictional-chapter-01', 'text': 'Alder Bay tests a library delivery locker.'},
            {'id': 'fictional-chapter-02', 'text': 'The fictional pilot will report results in June.'},
        ]
        episode = {
            'id': 'fictional-fingerprint-episode',
            'revision_id': 'fictional-fingerprint-r1',
        }

        # These constants were derived from the specified JSON/hash bytes independently.
        fallback = _fingerprints(episode, chapters, DEFAULT_VOICE, 'gemini', DEFAULT_MODEL)
        self.assertEqual(fallback, (
            '2fb547545e1ff1f3bcd432f2ad3bd23b96c0b80958e42342760579632d1a754b',
            '650d62e66d34fb4067117e49b8d26886cdb607950f12f872a2608f4bbdfd94a6'))

        transcript = '\n\n'.join(chapter['text'] for chapter in chapters)
        frozen_episode = {
            **episode,
            'transcript': transcript,
            'script_hash': '397147fad551e53dc2d61a77bdecd4204f5b98cac2022bb6b27d00aad0262a5a',
        }
        self.assertEqual(_fingerprints(
            frozen_episode, chapters, DEFAULT_VOICE, 'gemini', DEFAULT_MODEL), (
                '397147fad551e53dc2d61a77bdecd4204f5b98cac2022bb6b27d00aad0262a5a',
                'f43f6d95f43d673a97505ffe8c3e0c179bf3d1c32eb5321c631d95c36e8ac888'))

        self.assertEqual(_fingerprints(
            episode, chapters, DEFAULT_VOICE, 'gemini', DEFAULT_MODEL,
            tts_output_token_limit=4096, max_segment_attempts=3), (
                '2fb547545e1ff1f3bcd432f2ad3bd23b96c0b80958e42342760579632d1a754b',
                'f7a2ae1591bc35006913e073e17cb47af8a86b8d5fccd63b94e9816d19ebc556'))

    def test_fingerprints_wrapper_uses_local_normalizer_and_version_aliases(self):
        chapters = [
            {'id': 'fictional-chapter-01', 'text': 'Alder Bay tests a library delivery locker.'},
            {'id': 'fictional-chapter-02', 'text': 'The fictional pilot will report results in June.'},
        ]
        episode = {
            'id': 'fictional-fingerprint-episode',
            'revision_id': 'fictional-fingerprint-r1',
        }
        with patch.object(episodes_audio, 'normalize_narration',
                          side_effect=lambda text: 'Fictional patched narration: ' + text):
            self.assertEqual(_fingerprints(
                episode, chapters, DEFAULT_VOICE, 'gemini', DEFAULT_MODEL), (
                    '2fb547545e1ff1f3bcd432f2ad3bd23b96c0b80958e42342760579632d1a754b',
                    '8a1bba3a81c176311f5bbd6d7f35856aa0f1d5c5c8ca97ee69f46bd62cb40f44'))
        with patch.object(episodes_audio, 'NORMALIZER_VERSION', 'fictional-ear-test'):
            self.assertEqual(_fingerprints(
                episode, chapters, DEFAULT_VOICE, 'gemini', DEFAULT_MODEL), (
                    '2fb547545e1ff1f3bcd432f2ad3bd23b96c0b80958e42342760579632d1a754b',
                    'a4d3648a3fafcd1a861547a7ce5a23c380d6fe96d12f27cc715b0f46661f6ae7'))

    def test_fingerprint_helper_import_does_not_load_gemini_provider(self):
        result = subprocess.run(
            [sys.executable, '-c',
             'import sys; import dev.ingest.audio_fingerprints; '
             'assert "dev.ingest.tts.gemini" not in sys.modules'],
            check=False, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)



    def test_voice_file_defaults_to_an_audition_voice_and_accepts_selection(self):
        self.assertEqual(load_voice(self.voice_file), DEFAULT_VOICE)
        manifest = Path(self.temp.name) / 'manifest.json'
        manifest.write_text(json.dumps({'samples': [{'label': 'A', 'voice': 'Charon'}]}), encoding='utf-8')
        self.assertEqual(load_voice(Path(self.temp.name) / 'missing-voice.json', manifest_path=manifest), 'Charon')
        self.voice_file.write_text('{"voice_name":"Kore"}', encoding='utf-8')
        self.assertEqual(load_voice(self.voice_file), 'Kore')
        self.voice_file.write_text('{"voice_name":"Unknown"}', encoding='utf-8')
        with self.assertRaises(EpisodeAudioError):
            load_voice(self.voice_file)

    def test_renders_in_order_stitches_audio_and_writes_measured_chapter_times(self):
        episode = read_fixture()
        provider = FakeProvider()
        result = render_episode_audio(episode, episode['profile_id'], provider=provider,
                                     voice_file=self.voice_file, state_dir=self.state_dir)
        manifest = result['manifest']
        self.assertEqual([call[0] for call in provider.calls],
                         [chapter['spoken_text'] for chapter in episode['chapters']])
        for text, _voice in provider.calls:
            self.assertNotRegex(text, r'\[\d+(?:,\s*\d+)*\]', 'display citation numbers must never reach narration')
        self.assertEqual([call[1] for call in provider.calls], [DEFAULT_VOICE, DEFAULT_VOICE])
        self.assertEqual([row['chapter_id'] for row in manifest['chapters']],
                         [chapter['id'] for chapter in episode['chapters']])
        self.assertEqual([row['start_seconds'] for row in manifest['chapters']], [0.0, 0.1])
        self.assertEqual(manifest['duration_seconds'], 0.2)
        self.assertGreater(Decimal(manifest['estimated_cost_usd']), 0)
        self.assertNotIn('settled_cost_usd', manifest)
        self.assertNotIn('pending_reservation_usd', manifest)
        self.assertNotIn('cost_basis', manifest)
        with wave.open(str(result['directory'] / 'episode.wav'), 'rb') as stitched:
            self.assertEqual(stitched.getnframes(), 200)
            self.assertEqual(stitched.getframerate(), 1000)
        self.assertEqual(result['episode']['chapters'][0]['audio_start_seconds'], 0.0)
        self.assertEqual(result['episode']['chapters'][1]['audio_start_seconds'], 0.1)
        saved_episode = json.loads((result['directory'] / 'episode.json').read_text(encoding='utf-8'))
        self.assertEqual(saved_episode['chapters'][1]['audio_duration_seconds'], 0.1)
        state, current_manifest, path = current_audio_asset(
            episode, episode['profile_id'], voice_name=DEFAULT_VOICE, state_dir=self.state_dir)
        self.assertEqual(state, 'ready')
        self.assertEqual(current_manifest['stitched_sha256'], manifest['stitched_sha256'])
        self.assertEqual(path, result['directory'] / 'episode.wav')
        profile_directory = (self.state_dir / 'episodes' / 'profiles' / episode['profile_id'])
        self.assertEqual(profile_directory.stat().st_mode & 0o777, 0o700)
        cached = render_episode_audio(episode, episode['profile_id'], provider=FakeProvider(),
                                      state_dir=self.state_dir)
        self.assertTrue(cached['cached'])
        self.assertEqual(cached['provider_calls'], 0)

    def test_tts_chunks_respect_limit_and_keep_sentence_boundaries(self):
        text = ' '.join(f'Fictional sentence number {index} ends here.' for index in range(50))
        chunks = _tts_chunks(text)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(0 < len(chunk) <= MAX_TTS_CHUNK_CHARS for chunk in chunks))
        self.assertTrue(all(chunk.endswith('.') for chunk in chunks[:-1]))
        self.assertEqual(' '.join(chunks), text)

    def test_failed_chunk_retries_without_repeating_prior_audio(self):
        sentences = [f'Fictional sentence {index} ' + 'word ' * 35 + 'ends here.'
                     for index in range(3)]
        text = ' '.join(sentences)
        chunks = _tts_chunks(normalize_narration(text))
        self.assertGreaterEqual(len(chunks), 2)
        episode = {'id': 'fictional-chunk-retry', 'profile_id': 'a' * 24,
                   'revision_id': 'fixture-r1', 'transcript': text,
                   'script_hash': hashlib.sha256(text.encode()).hexdigest(),
                   'chapters': [{'id': 'chapter-one', 'title': 'Fixture', 'spoken_text': text}]}
        provider = ShortRequestProvider(fail_call_once=2)
        result = render_episode_audio(episode, episode['profile_id'], provider=provider,
                                      voice_file=self.voice_file, state_dir=self.state_dir)
        self.assertEqual(provider.calls[0][0], chunks[0])
        self.assertEqual(provider.calls[1][0], chunks[1])
        self.assertEqual(provider.calls[2][0], chunks[1])
        self.assertEqual(result['provider_calls'], len(chunks) + 1)

    def test_fifteen_minute_2200_word_episode_stitches_short_tts_requests(self):
        sentence = 'Fictional systems move clear signals between separate stages safely today.'
        chapters = []
        for index in range(4):
            chapters.append({'id': f'chapter-{index + 1}', 'title': f'Fixture {index + 1}',
                             'spoken_text': ' '.join([sentence] * 55)})
        transcript = '\n\n'.join(row['spoken_text'] for row in chapters)
        episode = {'id': 'fictional-long-episode', 'profile_id': 'b' * 24,
                   'revision_id': 'fixture-r1', 'duration_minutes': 15,
                   'transcript': transcript,
                   'script_hash': hashlib.sha256(transcript.encode()).hexdigest(),
                   'chapters': chapters}
        provider = ShortRequestProvider()
        result = render_episode_audio(episode, episode['profile_id'], provider=provider,
                                      voice_file=self.voice_file, state_dir=self.state_dir)
        words = len(transcript.split())
        self.assertEqual(words, 2200)
        self.assertEqual(len(result['manifest']['chapters']), 4)
        self.assertGreater(result['provider_calls'], len(chapters))
        self.assertTrue(all(0 < len(text) <= MAX_TTS_CHUNK_CHARS for text, _voice in provider.calls))
        self.assertEqual(result['manifest']['provider_calls'], result['provider_calls'])
        with wave.open(str(result['directory'] / 'episode.wav'), 'rb') as stitched:
            self.assertEqual(stitched.getnframes(), len(provider.calls) * 100)

    def test_stream_callback_publishes_each_validated_chapter_in_recording_order(self):
        episode = read_fixture()
        published = []

        def ready(row, audio_bytes):
            info = _read_wav(audio_bytes)
            published.append((row['chapter_id'], len(published), info.duration_seconds))

        result = render_episode_audio(episode, episode['profile_id'], provider=FakeProvider(),
            voice_file=self.voice_file, state_dir=self.state_dir, chapter_ready=ready)
        self.assertEqual([row[0] for row in published],
                         [chapter['id'] for chapter in episode['chapters']])
        self.assertEqual([row[1] for row in published], [0, 1])
        self.assertEqual([row[2] for row in published], [0.1, 0.1])
        self.assertEqual(result['manifest']['status'], 'recorded')

    def test_old_normalizer_recording_is_stale_without_revision_change(self):
        episode = deepcopy(read_fixture())
        episode['revision_id'] = 'fictional-r1'
        with patch('dev.ingest.episodes_audio.NORMALIZER_VERSION', 'ear-1'):
            old = render_episode_audio(
                episode, episode['profile_id'], provider=FakeProvider(), state_dir=self.state_dir)
        old_manifest = old['manifest']
        self.assertEqual(old_manifest['normalizer_version'], 'ear-1')
        self.assertEqual(old['episode']['revision_id'], episode['revision_id'])
        self.assertEqual(old['episode']['audio']['revision_id'], episode['revision_id'])
        self.assertEqual(old['episode']['audio']['script_hash'], old_manifest['script_hash'])
        self.assertIn('url', old['episode']['audio'])
        self.assertIn('audio_start_seconds', old['episode']['chapters'][0])

        with patch('dev.ingest.episodes_audio.NORMALIZER_VERSION', NORMALIZER_VERSION):
            current_hash, current_key = _fingerprints(
                episode, _chapter_scripts(episode), DEFAULT_VOICE, 'gemini', DEFAULT_MODEL)
            self.assertEqual(current_hash, old_manifest['script_hash'],
                             'the transcript revision and text are unchanged')
            self.assertNotEqual(current_key, old_manifest['asset_key'],
                                'the changed normalizer changes audio identity')
            state, manifest, path = current_audio_asset(
                episode, episode['profile_id'], voice_name=DEFAULT_VOICE,
                state_dir=self.state_dir)
            self.assertEqual((state, manifest, path), ('stale', None, None),
                             'an older normalization cannot satisfy current audio identity')

            updated = add_chapter_timestamps(
                {'audio_state': 'recorded', 'episode': old['episode']},
                episode['profile_id'], voice_name=DEFAULT_VOICE, state_dir=self.state_dir)
        self.assertEqual(updated['audio_state'], 'stale')
        self.assertNotIn('audio', updated['episode'])
        for chapter in updated['episode']['chapters']:
            self.assertNotIn('audio_start_seconds', chapter)
            self.assertNotIn('audio_duration_seconds', chapter)

    def test_invalid_finish_reason_or_wav_never_publishes_current_audio_url(self):
        original = read_fixture()
        original['chapters'] = [original['chapters'][0]]
        original = render_episode_audio(original, original['profile_id'], provider=FakeProvider(),
                                        state_dir=self.state_dir)['episode']
        prior_state, prior_manifest, prior_path = current_audio_asset(
            original, original['profile_id'], voice_name=DEFAULT_VOICE, state_dir=self.state_dir)
        prior_bytes = prior_path.read_bytes()
        valid_wav = wav_bytes()
        cases = (
            ('missing-finish-reason', None, valid_wav),
            ('unknown-finish-reason', 'FUTURE_UNKNOWN_REASON', valid_wav),
            ('truncated-wav', 'STOP', valid_wav[:-2]),
        )
        for index, (case, finish_reason, audio_bytes) in enumerate(cases, 2):
            with self.subTest(case=case):
                candidate = {'content': {'parts': [{'inlineData': {
                    'mimeType': 'audio/wav',
                    'data': base64.b64encode(audio_bytes).decode('ascii'),
                }}]}}
                if finish_reason is not None:
                    candidate['finishReason'] = finish_reason
                payload = {'candidates': [candidate]}

                class FixtureResponse:
                    def __enter__(self):
                        return self

                    def __exit__(self, *_args):
                        return False

                    def read(self, _amount=-1):
                        return json.dumps(payload).encode('utf-8')

                key_file = Path(self.temp.name) / 'fixture-key'
                key_file.write_text('fictional-fixture-key', encoding='utf-8')
                provider = GeminiTtsProvider(
                    key_path=key_file,
                    transport=lambda _request, _timeout: FixtureResponse(),
                    usage_ledger=UsageLedger(ledger_dir=Path(self.temp.name) / f'ledger-{case}'))
                corrected = deepcopy(original)
                corrected['revision_id'] = f'fictional-r{index}'
                corrected['chapters'][0]['spoken_text'] += f' Corrected wording {case}.'
                with self.assertRaisesRegex(EpisodeAudioError, r'failed after 1 attempts'):
                    render_episode_audio(corrected, corrected['profile_id'], provider=provider,
                                         state_dir=self.state_dir, max_segment_attempts=1)

                current_state, current_manifest, current_path = current_audio_asset(
                    corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE,
                    state_dir=self.state_dir)
                self.assertEqual((current_state, current_manifest, current_path), ('stale', None, None))

        old_state, old_manifest, old_path = current_audio_asset(
            original, original['profile_id'], voice_name=DEFAULT_VOICE, state_dir=self.state_dir)
        self.assertEqual(old_state, 'ready')
        self.assertEqual(old_manifest['revision_id'], 'fictional-r1')
        self.assertEqual(old_path.read_bytes(), prior_bytes)
        episode_files = list(prior_path.parent.parent.glob('*/episode.wav'))
        self.assertEqual(len(episode_files), 1, 'failed staging audio is not published as an asset')

    def test_bounded_renderer_rejects_max_tokens_result_before_review_staging(self):
        original = read_fixture()
        original['revision_id'] = 'fictional-r1'
        first = render_episode_audio(original, original['profile_id'], provider=FakeProvider(),
                                     state_dir=self.state_dir)
        prior_bytes = (first['directory'] / 'episode.wav').read_bytes()
        corrected = deepcopy(original)
        corrected['revision_id'] = 'fictional-r2'
        corrected['chapters'][0]['spoken_text'] += ' The corrected sentence is fictional.'

        class MaxTokensResultProvider:
            provider_name = 'gemini'
            model = DEFAULT_MODEL

            def __init__(self):
                self.calls = []

            def synthesize(self, text, voice_name, *, max_output_tokens, **_kwargs):
                self.calls.append((text, voice_name, max_output_tokens))
                return TtsResult(
                    wav_bytes(), 'audio/wav', 0.1, len(text), Decimal('0.0001'),
                    DEFAULT_MODEL, voice_name, 32, min(10, max_output_tokens),
                    'MAX_TOKENS', 'pending_human_listening')

        provider = MaxTokensResultProvider()
        with self.assertRaisesRegex(EpisodeAudioError, 'Chapter 1 failed after 1 attempts'):
            render_episode_audio(
                corrected, corrected['profile_id'], provider=provider,
                state_dir=self.state_dir, max_segment_attempts=1,
                tts_output_token_limit=4096,)

        self.assertEqual(len(provider.calls), 1,
                         'MAX_TOKENS fails before another chapter is staged')
        old_state, old_manifest, old_path = current_audio_asset(
            original, original['profile_id'], voice_name=DEFAULT_VOICE, state_dir=self.state_dir)
        self.assertEqual(old_state, 'ready')
        self.assertEqual(old_manifest['revision_id'], 'fictional-r1')
        self.assertEqual(old_path.read_bytes(), prior_bytes)
        current_state, current_manifest, current_path = current_audio_asset(
            corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE,
            state_dir=self.state_dir, tts_output_token_limit=4096, max_segment_attempts=1)
        self.assertEqual((current_state, current_manifest, current_path), ('stale', None, None))
        asset_root = audio_directory(self.state_dir, original['profile_id'], original['id'])
        published = [row.name for row in asset_root.iterdir()
                     if row.is_dir() and not row.name.startswith('.')]
        self.assertEqual(published, [old_manifest['asset_key']])
        self.assertFalse(any(row.is_dir() and row.name.startswith('.review-')
                             for row in asset_root.iterdir()))
        self.assertFalse(any(row.is_dir() and row.name.startswith('.render-')
                             for row in asset_root.iterdir()))

    def test_correction_invalidates_old_audio_and_rerender_uses_new_script(self):
        original = read_fixture()
        provider = FakeProvider()
        first = render_episode_audio(original, original['profile_id'], provider=provider,
                                     state_dir=self.state_dir)
        corrected = deepcopy(original)
        corrected['revision_id'] = 'fictional-r2'
        corrected['chapters'][0]['spoken_text'] = 'Alder Bay will test a corrected fictional delivery plan this spring.'
        state, manifest, path = current_audio_asset(
            corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE, state_dir=self.state_dir)
        self.assertEqual((state, manifest, path), ('stale', None, None))
        rerendered = render_episode_audio(corrected, corrected['profile_id'], provider=provider,
                                          state_dir=self.state_dir)
        self.assertNotEqual(first['manifest']['asset_key'], rerendered['manifest']['asset_key'])
        state, manifest, path = current_audio_asset(
            corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE, state_dir=self.state_dir)
        self.assertEqual(state, 'ready')
        self.assertEqual(manifest['script_hash'], rerendered['manifest']['script_hash'])
        self.assertEqual(provider.calls[2][0], corrected['chapters'][0]['spoken_text'])

        calls_before_retry = len(provider.calls)
        retry = render_episode_audio(corrected, corrected['profile_id'], provider=provider,
                                     state_dir=self.state_dir)
        self.assertTrue(retry['cached'])
        self.assertEqual(retry['provider_calls'], 0)
        self.assertEqual(retry['manifest']['asset_key'], rerendered['manifest']['asset_key'])
        self.assertEqual(retry['directory'], rerendered['directory'])
        self.assertEqual(len(provider.calls), calls_before_retry)
        published = [row for row in first['directory'].parent.iterdir()
                     if row.is_dir() and not row.name.startswith('.')]
        self.assertEqual({row.name for row in published},
                         {first['manifest']['asset_key'], rerendered['manifest']['asset_key']})


    def test_max_tokens_correction_stays_unpublished_and_old_hash_stays_on_old_revision(self):
        original = read_fixture()
        original['chapters'] = [original['chapters'][0]]
        original['revision_id'] = 'fictional-r1'
        first = render_episode_audio(original, original['profile_id'], provider=FakeProvider(),
                                     state_dir=self.state_dir)
        old_state, old_manifest, old_path = current_audio_asset(
            original, original['profile_id'], voice_name=DEFAULT_VOICE, state_dir=self.state_dir)
        self.assertEqual(old_state, 'ready')
        old_audio = old_path.read_bytes()

        corrected = deepcopy(original)
        corrected['revision_id'] = 'fictional-r2'
        corrected['chapters'][0]['spoken_text'] = (
            'A corrected fictional script with an exact current revision identity.')
        expected_script_hash, _asset_key = _fingerprints(
            corrected, _chapter_scripts(corrected), DEFAULT_VOICE, 'gemini', DEFAULT_MODEL)

        class MaxTokensProvider(FakeProvider):
            def synthesize(self, text, voice_name, **_bounded):
                self.calls.append((text, voice_name))
                raise TtsOutputLimitError('fictional output token limit reached')

        truncated_provider = MaxTokensProvider()
        with self.assertRaisesRegex(EpisodeAudioError, 'Chapter 1 failed after 3 attempts'):
            render_episode_audio(corrected, corrected['profile_id'], provider=truncated_provider,
                                 state_dir=self.state_dir, tts_output_token_limit=4096,)

        self.assertEqual(len(truncated_provider.calls), 3)
        self.assertTrue(all('exact current revision identity' in text
                            for text, _voice in truncated_provider.calls))
        corrected_state, corrected_manifest, corrected_path = current_audio_asset(
            corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE, state_dir=self.state_dir)
        self.assertEqual((corrected_state, corrected_manifest, corrected_path), ('stale', None, None))
        self.assertEqual(old_manifest['revision_id'], 'fictional-r1')
        self.assertNotEqual(old_manifest['script_hash'], expected_script_hash)
        self.assertEqual(old_path.read_bytes(), old_audio)

        asset_root = first['directory'].parent
        published = [row.name for row in asset_root.iterdir()
                     if row.is_dir() and not row.name.startswith('.')]
        self.assertEqual(published, [old_manifest['asset_key']])
        self.assertFalse(any(row.is_dir() and row.name.startswith('.render-')
                             for row in asset_root.iterdir()))


    def test_approval_cannot_substitute_an_existing_target_from_another_candidate_batch(self):
        episode = read_fixture()
        key = Path(self.temp.name) / 'fictional-key'
        key.write_text('fictional-test-key', encoding='utf-8')
        ledger = UsageLedger(ledger_dir=Path(self.temp.name) / 'candidate-identity-ledger')
        recording = {'frames': 100}

        def transport(_request, timeout):
            payload = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{
                'inlineData': {'mimeType': 'audio/wav', 'data': base64.b64encode(
                    wav_bytes(frames=recording['frames'])).decode('ascii')}
            }]}}], 'usageMetadata': {'promptTokenCount': 1, 'candidatesTokenCount': 1}}
            return BytesIO(json.dumps(payload).encode('utf-8'))

        provider = GeminiTtsProvider(key_path=key, transport=transport, usage_ledger=ledger)
        identity = {'voice_name': DEFAULT_VOICE, 'state_dir': self.state_dir,
                    'tts_output_token_limit': 4096, 'max_segment_attempts': 1}
        first = render_episode_audio(
            episode, episode['profile_id'], provider=provider, **identity)
        recording['frames'] = 200
        second = render_episode_audio(
            episode, episode['profile_id'], provider=provider, **identity)
        self.assertNotEqual(first['batch_id'], second['batch_id'])
        for field in ('revision_id', 'script_hash', 'asset_key'):
            self.assertEqual(first['candidate_manifest'][field], second['candidate_manifest'][field])
        self.assertNotEqual((first['candidate_directory'] / 'episode.wav').read_bytes(),
                            (second['candidate_directory'] / 'episode.wav').read_bytes())
        approved = approve_bounded_audio_candidate(
            episode, episode['profile_id'], batch_id=first['batch_id'],
            approved_revision_id=episode['revision_id'], approved_script_hash=first['script_hash'],
            human_approved=True, **identity)  # Fictional attestation only.
        self.assertEqual(approved['manifest']['batch_id'], first['batch_id'])
        approved_bytes = {p.name: p.read_bytes() for p in approved['directory'].iterdir()}
        pending_bytes = {p.name: p.read_bytes() for p in second['candidate_directory'].iterdir()}

        with self.assertRaisesRegex(EpisodeAudioError, r'batch|candidate'):
            approve_bounded_audio_candidate(
                episode, episode['profile_id'], batch_id=second['batch_id'],
                approved_revision_id=episode['revision_id'], approved_script_hash=second['script_hash'],
                human_approved=True, **identity)

        self.assertEqual({p.name: p.read_bytes() for p in approved['directory'].iterdir()}, approved_bytes)
        self.assertEqual({p.name: p.read_bytes() for p in second['candidate_directory'].iterdir()}, pending_bytes)
        pending_episode = json.loads(pending_bytes['episode.json'])
        self.assertNotIn('url', pending_episode['audio'])
        self.assertEqual(json.loads(pending_bytes['manifest.json'])['status'], 'pending_human_listening')
        state, manifest, path = current_audio_asset(episode, episode['profile_id'], **identity)
        self.assertEqual((state, manifest['batch_id']), ('ready', first['batch_id']))
        self.assertEqual(path.read_bytes(), approved_bytes['episode.wav'])

    def test_bounded_audio_promoter_marks_and_rolls_back_only_owned_candidate(self):
        episode = read_fixture()
        episode['profile_id'] = 'a' * 24
        voice_id = DEFAULT_VOICE
        normalized = [normalize_narration(row['spoken_text']) for row in episode['chapters']]
        output_limit = sum(_chapter_output_token_estimate(text) for text in normalized)
        attempts = 1
        key_file = Path(self.temp.name) / 'fictional-promoter-key'
        key_file.write_text('fictional-fixture-key', encoding='utf-8')
        ledger = UsageLedger(ledger_dir=Path(self.temp.name) / 'promoter-ledger')

        def fake_transport(_request, timeout):
            payload = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{
                'inlineData': {'mimeType': 'audio/wav',
                               'data': base64.b64encode(wav_bytes()).decode('ascii')}
            }]}}], 'usageMetadata': {'promptTokenCount': 1, 'candidatesTokenCount': 1}}
            return BytesIO(json.dumps(payload).encode('utf-8'))

        provider = GeminiTtsProvider(key_path=key_file, transport=fake_transport,
                                     usage_ledger=ledger)
        candidate = render_episode_audio(
            episode, episode['profile_id'], provider=provider, state_dir=self.state_dir,
            tts_output_token_limit=output_limit, max_segment_attempts=attempts,)
        self.assertEqual(candidate['status'], 'pending_human_listening')
        review_dir = candidate['candidate_directory']
        original_manifest = (review_dir / 'manifest.json').read_bytes()
        original_episode = (review_dir / 'episode.json').read_bytes()
        original_wav = (review_dir / 'episode.wav').read_bytes()
        asset_key = candidate['candidate_manifest']['asset_key']
        target = audio_directory(self.state_dir, episode['profile_id'], episode['id'], asset_key)
        expected_identity = {
            'profile_id': episode['profile_id'],
            'episode_id': episode['id'], 'revision_id': candidate['revision_id'],
            'script_sha256': candidate['script_hash'],
            'candidate_batch_id': candidate['batch_id'], 'asset_key': asset_key,
            'voice_id': voice_id, 'provider_id': 'gemini', 'model_id': DEFAULT_MODEL,
            'normalizer_id': NORMALIZER_VERSION,
            'tts_output_token_limit': output_limit, 'max_segment_attempts': attempts,
        }

        def journal_for(operation_id):
            backup_root = self.state_dir / '.audio-approval-journal' / operation_id
            backup_root.mkdir(parents=True, exist_ok=True)
            manifest_backup = backup_root / 'candidate-manifest.json'
            episode_backup = backup_root / 'candidate-episode.json'
            manifest_backup.write_bytes(original_manifest)
            episode_backup.write_bytes(original_episode)
            return MappingProxyType({
                **expected_identity, 'operation_id': operation_id,
                'candidate_directory': review_dir, 'target_directory': target,
                'target_preexisted': False, 'prior_manifest_sha256': None,
                'candidate_manifest_backup_path': manifest_backup,
                'candidate_manifest_backup_sha256': hashlib.sha256(original_manifest).hexdigest(),
                'candidate_episode_backup_path': episode_backup,
                'candidate_episode_backup_sha256': hashlib.sha256(original_episode).hexdigest(),
            })

        operation_id = 'a' * 32
        journal = journal_for(operation_id)

        from dev.ingest.audio_approval import issue_fictional_listening_approval_for_test
        self.assertTrue(callable(issue_fictional_listening_approval_for_test))
        fixture_capability = issue_fictional_listening_approval_for_test(expected_identity)
        promoter_type = getattr(episodes_audio, 'BoundedAudioPromoter', None)
        self.assertIsNotNone(promoter_type, 'the reviewed audio promoter helper is missing')
        promoter = promoter_type(state_dir=self.state_dir)
        candidate_bytes_before_replay = {
            path.name: path.read_bytes() for path in review_dir.iterdir()
        }
        other_profile_id = 'd' * 24
        with self.assertRaisesRegex(EpisodeAudioError, 'does not match this candidate'):
            promoter.promote_candidate(
                episode, other_profile_id, operation_id=operation_id,
                candidate_batch_id=candidate['batch_id'], asset_key=asset_key,
                revision_id=candidate['revision_id'], script_sha256=candidate['script_hash'],
                voice_id=voice_id, provider_id='gemini', model_id=DEFAULT_MODEL,
                normalizer_id=NORMALIZER_VERSION,
                tts_output_token_limit=output_limit, max_segment_attempts=attempts,
                listening_approval=fixture_capability)
        self.assertEqual(
            {path.name: path.read_bytes() for path in review_dir.iterdir()},
            candidate_bytes_before_replay,
            'cross-profile capability replay must be rejected before candidate mutation')
        self.assertFalse(target.exists(), 'cross-profile replay must not create an approved target')

        class SimulatedCrash(RuntimeError):
            pass

        def promote(selected_operation_id):
            return promoter.promote_candidate(
                episode, episode['profile_id'], operation_id=selected_operation_id,
                candidate_batch_id=candidate['batch_id'], asset_key=asset_key,
                revision_id=candidate['revision_id'], script_sha256=candidate['script_hash'],
                voice_id=voice_id, provider_id='gemini', model_id=DEFAULT_MODEL,
                normalizer_id=NORMALIZER_VERSION,
                tts_output_token_limit=output_limit, max_segment_attempts=attempts,
                listening_approval=fixture_capability)

        write_json = episodes_audio._atomic_json

        def crash_after_manifest_write(path, value):
            write_json(path, value)
            if path == review_dir / 'manifest.json':
                raise SimulatedCrash('interrupted after manifest rewrite, before episode rewrite/rename')

        with patch.object(episodes_audio, '_atomic_json', side_effect=crash_after_manifest_write):
            with self.assertRaisesRegex(SimulatedCrash, 'manifest rewrite'):
                promote(operation_id)
        self.assertTrue(review_dir.is_dir())
        self.assertFalse(target.exists())
        interrupted_manifest = json.loads((review_dir / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(interrupted_manifest['operation_id'], operation_id)
        self.assertEqual(interrupted_manifest['status'], 'recorded')
        self.assertEqual((review_dir / 'episode.json').read_bytes(), original_episode,
                         'episode metadata remains pending at this interruption point')
        promoter = promoter_type(state_dir=self.state_dir)  # fresh promoter instance, same process
        promoter.rollback_owned_promotion(operation_id, journal)
        self.assertEqual((review_dir / 'manifest.json').read_bytes(), original_manifest)
        self.assertEqual((review_dir / 'episode.json').read_bytes(), original_episode)
        self.assertEqual((review_dir / 'episode.wav').read_bytes(), original_wav)

        operation_id = 'b' * 32
        journal = journal_for(operation_id)
        promoted = promoter.promote_candidate(
            episode, episode['profile_id'], operation_id=operation_id,
            candidate_batch_id=candidate['batch_id'], asset_key=asset_key,
            revision_id=candidate['revision_id'], script_sha256=candidate['script_hash'],
            voice_id=voice_id, provider_id='gemini', model_id=DEFAULT_MODEL,
            normalizer_id=NORMALIZER_VERSION,
            tts_output_token_limit=output_limit, max_segment_attempts=attempts,
            listening_approval=fixture_capability)
        self.assertEqual(promoted.operation_id, operation_id)
        self.assertEqual(promoted.asset_key, asset_key)
        self.assertEqual(promoted.manifest['operation_id'], operation_id)
        self.assertEqual(promoted.manifest['batch_id'], candidate['batch_id'])
        self.assertTrue(target.is_dir())
        self.assertFalse(review_dir.exists())

        promoter.rollback_owned_promotion(operation_id, journal)

        self.assertFalse(target.exists(), 'rollback removes only this operation-owned target')
        self.assertEqual((review_dir / 'manifest.json').read_bytes(), original_manifest)
        self.assertEqual((review_dir / 'episode.json').read_bytes(), original_episode)
        self.assertEqual((review_dir / 'episode.wav').read_bytes(), original_wav)

        operation_id = 'c' * 32
        journal = journal_for(operation_id)
        real_fsync_directory = episodes_audio._fsync_directory
        fsync_calls = 0

        def crash_after_rename(path):
            nonlocal fsync_calls
            fsync_calls += 1
            if fsync_calls == 2:
                raise SimulatedCrash('interrupted after target rename, before profile fsync')
            return real_fsync_directory(path)

        with patch.object(episodes_audio, '_fsync_directory', side_effect=crash_after_rename):
            with self.assertRaisesRegex(SimulatedCrash, 'target rename'):
                promote(operation_id)
        self.assertFalse(review_dir.exists())
        self.assertTrue(target.is_dir())
        renamed_manifest = json.loads((target / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(renamed_manifest['operation_id'], operation_id)
        self.assertEqual((target / 'episode.wav').read_bytes(), original_wav)
        promoter = promoter_type(state_dir=self.state_dir)  # fresh promoter instance, same process
        promoter.rollback_owned_promotion(operation_id, journal)
        self.assertFalse(target.exists())
        self.assertEqual((review_dir / 'manifest.json').read_bytes(), original_manifest)
        self.assertEqual((review_dir / 'episode.json').read_bytes(), original_episode)
        self.assertEqual((review_dir / 'episode.wav').read_bytes(), original_wav)

        # The standalone approval API retains its explicit fixture behavior.
        approved = approve_bounded_audio_candidate(
            episode, episode['profile_id'], voice_name=voice_id,
            batch_id=candidate['batch_id'], approved_revision_id=candidate['revision_id'],
            approved_script_hash=candidate['script_hash'], human_approved=True,
            tts_output_token_limit=output_limit, max_segment_attempts=attempts,
            state_dir=self.state_dir)
        self.assertEqual(approved['status'], 'ready')
        self.assertNotIn('operation_id', approved['manifest'])

        # A distinct pending batch with the same asset key cannot replace or reuse
        # the already-approved target for batch A.
        batch_b = 'fictional-collision-batch-b'
        candidate_b_dir = review_dir.parent / f'.review-{batch_b}'
        candidate_b_dir.mkdir(mode=0o700)
        for filename, payload in (('episode.json', original_episode), ('episode.wav', original_wav)):
            (candidate_b_dir / filename).write_bytes(payload)
        manifest_b = json.loads(original_manifest)
        manifest_b['batch_id'] = batch_b
        (candidate_b_dir / 'manifest.json').write_text(
            json.dumps(manifest_b, separators=(',', ':')), encoding='utf-8')
        candidate_b_bytes = {path.name: path.read_bytes() for path in candidate_b_dir.iterdir()}
        target_a_bytes = {path.name: path.read_bytes() for path in target.iterdir()}
        identity_b = {**expected_identity, 'candidate_batch_id': batch_b}
        capability_b = issue_fictional_listening_approval_for_test(identity_b)
        with self.assertRaisesRegex(EpisodeAudioError, 'target already exists'):
            promoter.promote_candidate(
                episode, episode['profile_id'], operation_id='f' * 32,
                candidate_batch_id=batch_b, asset_key=asset_key,
                revision_id=candidate['revision_id'], script_sha256=candidate['script_hash'],
                voice_id=voice_id, provider_id='gemini', model_id=DEFAULT_MODEL,
                normalizer_id=NORMALIZER_VERSION,
                tts_output_token_limit=output_limit, max_segment_attempts=attempts,
                listening_approval=capability_b)
        self.assertEqual({path.name: path.read_bytes() for path in candidate_b_dir.iterdir()},
                         candidate_b_bytes,
                         'collision refusal preserves the distinct pending candidate')
        self.assertEqual({path.name: path.read_bytes() for path in target.iterdir()},
                         target_a_bytes,
                         'collision refusal preserves batch A approved bytes')

    def test_audio_promoter_rollback_checks_owner_and_hash_and_preserves_existing_target(self):
        episode = read_fixture()
        profile_id = episode['profile_id']
        voice_id = DEFAULT_VOICE
        output_limit = 4096
        attempts = 1
        chapters = _chapter_scripts(episode)
        script_hash, asset_key = _fingerprints(
            episode, chapters, voice_id, 'gemini', DEFAULT_MODEL,
            tts_output_token_limit=output_limit, max_segment_attempts=attempts)
        batch_id = 'fictional-rollback-batch'
        operation_id = 'c' * 32
        profile_root = audio_directory(self.state_dir, profile_id, episode['id'])
        profile_root.mkdir(parents=True, exist_ok=True)
        candidate = profile_root / f'.review-{batch_id}'
        target = audio_directory(self.state_dir, profile_id, episode['id'], asset_key)
        candidate.mkdir(mode=0o700)
        identity_fields = {
            'profile_id': profile_id, 'episode_id': episode['id'],
            'revision_id': episode['revision_id'], 'script_hash': script_hash,
            'batch_id': batch_id, 'asset_key': asset_key, 'voice_name': voice_id,
            'provider': 'gemini', 'model': DEFAULT_MODEL,
            'normalizer_version': NORMALIZER_VERSION,
            'tts_output_token_limit': output_limit, 'max_segment_attempts': attempts,
        }
        segment_audio = []
        chapter_rows = []
        offset = 0.0
        for index, chapter in enumerate(episode['chapters'], 1):
            audio = wav_bytes()
            info = episodes_audio._read_wav(audio)
            filename = f'chapter-{index:02d}.wav'
            (candidate / filename).write_bytes(audio)
            chapter_rows.append({
                'chapter_id': chapter['id'], 'title': chapter['title'], 'file': filename,
                'start_seconds': offset, 'duration_seconds': info.duration_seconds,
                'frame_count': info.frame_count, 'sample_rate': info.sample_rate,
                'character_count': len(chapter['spoken_text']), 'attempts': 1,
                'estimated_cost_usd': '0.00000000', 'sha256': hashlib.sha256(audio).hexdigest(),
            })
            segment_audio.append((info, info.frames))
            offset += info.duration_seconds
        wav, duration = episodes_audio._wav_bytes(segment_audio)
        (candidate / 'episode.wav').write_bytes(wav)
        pending_manifest = {
            **identity_fields, 'format_version': 1, 'status': 'pending_human_listening',
            'coverage_status': 'pending_human_listening', 'mime_type': 'audio/wav',
            'stitched_file': 'episode.wav', 'stitched_sha256': hashlib.sha256(wav).hexdigest(),
            'chapters': chapter_rows, 'duration_seconds': duration,
            'character_count': sum(row['character_count'] for row in chapter_rows),
            'provider_calls': len(chapter_rows), 'estimated_cost_usd': '0.00000000',
        }
        original_manifest = json.dumps(pending_manifest, separators=(',', ':')).encode()
        (candidate / 'manifest.json').write_bytes(original_manifest)
        pending_episode = deepcopy(episode)
        pending_episode['audio'] = {'status': 'pending', 'revision_id': episode['revision_id'],
                                    'script_hash': script_hash,
                                    'coverage_status': 'pending_human_listening'}
        original_episode = json.dumps(pending_episode, separators=(',', ':')).encode()
        (candidate / 'episode.json').write_bytes(original_episode)
        backup_root = self.state_dir / '.audio-approval-journal' / operation_id
        backup_root.mkdir(parents=True, exist_ok=True)
        manifest_backup = backup_root / 'candidate-manifest.json'
        episode_backup = backup_root / 'candidate-episode.json'
        manifest_backup.write_bytes(original_manifest)
        episode_backup.write_bytes(original_episode)
        approved_manifest = {**pending_manifest, 'status': 'recorded',
                             'coverage_status': 'human_listening_approved',
                             'human_listening_approval': {'revision_id': episode['revision_id'],
                                                          'script_hash': script_hash},
                             'operation_id': operation_id}
        approved_episode = episodes_audio._apply_timestamps(pending_episode, approved_manifest)
        (candidate / 'manifest.json').write_text(json.dumps(approved_manifest, separators=(',', ':')))
        (candidate / 'episode.json').write_text(json.dumps(approved_episode, separators=(',', ':')))
        journal = MappingProxyType({
            'operation_id': operation_id, 'profile_id': profile_id,
            'episode_id': episode['id'], 'revision_id': episode['revision_id'],
            'script_sha256': script_hash, 'candidate_batch_id': batch_id,
            'asset_key': asset_key, 'voice_id': voice_id, 'provider_id': 'gemini',
            'model_id': DEFAULT_MODEL, 'normalizer_id': NORMALIZER_VERSION,
            'tts_output_token_limit': output_limit, 'max_segment_attempts': attempts,
            'candidate_directory': candidate, 'target_directory': target,
            'target_preexisted': False, 'prior_manifest_sha256': None,
            'candidate_manifest_backup_path': manifest_backup,
            'candidate_manifest_backup_sha256': hashlib.sha256(original_manifest).hexdigest(),
            'candidate_episode_backup_path': episode_backup,
            'candidate_episode_backup_sha256': hashlib.sha256(original_episode).hexdigest(),
        })
        promoter = episodes_audio.BoundedAudioPromoter(state_dir=self.state_dir)
        approved_episode['script_hash'] = script_hash
        approved_episode['audio_status'] = 'recorded'
        approved_episode['audio']['status'] = 'recorded'
        approved_episode['audio']['state'] = 'recorded'
        approved_job = {
            'profile_id': profile_id, 'job_id': episode['id'], 'status': 'done',
            'audio_status': 'recorded', 'episode': approved_episode,
        }
        validation_journal = MappingProxyType({
            **journal, 'job_id': episode['id'], 'target_directory': str(target),
            'phase': 'promoted', 'promoted_asset': {
                'operation_id': operation_id, 'profile_id': profile_id,
                'episode_id': episode['id'], 'asset_key': asset_key,
            },
        })
        candidate_bytes = {path.name: path.read_bytes() for path in candidate.iterdir()}
        with self.assertRaisesRegex(EpisodeAudioError, 'requires recovery'):
            promoter.validate_promoted_asset(validation_journal, approved_job)
        self.assertEqual({path.name: path.read_bytes() for path in candidate.iterdir()}, candidate_bytes)
        self.assertFalse(target.exists(), 'a missing approved target is refused without mutation')
        candidate.replace(target)
        promoted_target_bytes = {path.name: path.read_bytes() for path in target.iterdir()}
        validated = promoter.validate_promoted_asset(validation_journal, approved_job)
        self.assertEqual(validated.identity['operation_id'], operation_id)
        self.assertEqual(validated.identity['manifest_operation_id'], operation_id)
        self.assertEqual(validated.identity['profile_id'], profile_id)
        self.assertEqual(validated.identity['revision_id'], episode['revision_id'])
        self.assertEqual(validated.identity['script_sha256'], script_hash)
        self.assertEqual(validated.identity['candidate_batch_id'], batch_id)
        self.assertEqual(validated.identity['asset_key'], asset_key)
        self.assertEqual(validated.manifest['stitched_sha256'], hashlib.sha256(wav).hexdigest())
        with self.assertRaises(TypeError):
            validated.identity['operation_id'] = 'd' * 32
        with self.assertRaises(TypeError):
            validated.manifest['human_listening_approval']['script_hash'] = '0' * 64

        mismatched_job = deepcopy(approved_job)
        mismatched_job['episode']['audio']['batch_id'] = 'fictional-other-batch'
        with self.assertRaisesRegex(EpisodeAudioError, 'requires recovery'):
            promoter.validate_promoted_asset(validation_journal, mismatched_job)
        self.assertEqual({path.name: path.read_bytes() for path in target.iterdir()},
                         promoted_target_bytes,
                         'identity refusal preserves the complete approved target')

        mismatched_job_id = deepcopy(approved_job)
        mismatched_job_id['job_id'] = 'fictional-other-job'
        with self.assertRaisesRegex(EpisodeAudioError, 'requires recovery'):
            promoter.validate_promoted_asset(validation_journal, mismatched_job_id)
        self.assertEqual({path.name: path.read_bytes() for path in target.iterdir()},
                         promoted_target_bytes,
                         'job identity refusal preserves the complete approved target')

        mismatched_config = deepcopy(approved_job)
        mismatched_config['episode']['audio']['model'] = 'fictional-other-model'
        with self.assertRaisesRegex(EpisodeAudioError, 'requires recovery'):
            promoter.validate_promoted_asset(validation_journal, mismatched_config)
        self.assertEqual({path.name: path.read_bytes() for path in target.iterdir()},
                         promoted_target_bytes,
                         'render config refusal preserves the complete approved target')

        corrupt_wav = target / 'episode.wav'
        corrupt_wav.write_bytes(b'fictional corrupt WAV bytes')
        corrupt_target_bytes = {path.name: path.read_bytes() for path in target.iterdir()}
        with self.assertRaisesRegex(EpisodeAudioError, 'requires recovery'):
            promoter.validate_promoted_asset(validation_journal, approved_job)
        self.assertEqual({path.name: path.read_bytes() for path in target.iterdir()},
                         corrupt_target_bytes,
                         'corrupt-asset refusal leaves every observed byte untouched')
        corrupt_wav.write_bytes(wav)

        original_approved_manifest = promoted_target_bytes['manifest.json']
        older_marker_manifest = json.loads(original_approved_manifest)
        older_marker_manifest['operation_id'] = 'e' * 32
        (target / 'manifest.json').write_text(
            json.dumps(older_marker_manifest, separators=(',', ':')), encoding='utf-8')
        self.assertEqual(validated.identity['operation_id'], operation_id)
        self.assertEqual(validated.identity['manifest_operation_id'], operation_id,
                         'later target manifest mutation cannot change an earlier identity snapshot')
        preexisting_validation_journal = MappingProxyType({
            **validation_journal, 'target_preexisted': True,
            'prior_manifest_sha256': hashlib.sha256(
                (target / 'manifest.json').read_bytes()).hexdigest(),
            'promoted_asset': {
                'operation_id': 'e' * 32, 'profile_id': profile_id,
                'episode_id': episode['id'], 'asset_key': asset_key,
            },
        })
        preexisting_result = promoter.validate_promoted_asset(
            preexisting_validation_journal, approved_job)
        self.assertEqual(preexisting_result.identity['operation_id'], operation_id)
        self.assertEqual(preexisting_result.identity['manifest_operation_id'], 'e' * 32)
        (target / 'manifest.json').write_bytes(original_approved_manifest)

        mismatched_profile_journal = MappingProxyType({**journal, 'profile_id': 'd' * 24})
        with self.assertRaisesRegex(EpisodeAudioError, 'journal paths are invalid'):
            promoter.rollback_owned_promotion(operation_id, mismatched_profile_journal)
        self.assertEqual({path.name: path.read_bytes() for path in target.iterdir()},
                         promoted_target_bytes,
                         'a journal from another profile cannot move or delete this target')
        self.assertFalse(candidate.exists())

        mismatched_marker = json.loads((target / 'manifest.json').read_text())
        mismatched_marker['operation_id'] = 'd' * 32
        (target / 'manifest.json').write_text(json.dumps(mismatched_marker, separators=(',', ':')))
        marker_mismatch_bytes = {path.name: path.read_bytes() for path in target.iterdir()}
        with self.assertRaisesRegex(EpisodeAudioError, 'not owned'):
            promoter.rollback_owned_promotion(operation_id, journal)
        self.assertEqual({path.name: path.read_bytes() for path in target.iterdir()}, marker_mismatch_bytes)
        (target / 'manifest.json').write_bytes(promoted_target_bytes['manifest.json'])

        bad_hash_journal = MappingProxyType({**journal, 'candidate_episode_backup_sha256': '0' * 64})
        with self.assertRaisesRegex(EpisodeAudioError, 'backup hash'):
            promoter.rollback_owned_promotion(operation_id, bad_hash_journal)
        self.assertTrue(target.is_dir())
        self.assertFalse(candidate.exists())

        promoter.rollback_owned_promotion(operation_id, journal)
        self.assertFalse(target.exists())
        self.assertEqual((candidate / 'manifest.json').read_bytes(), original_manifest)
        self.assertEqual((candidate / 'episode.json').read_bytes(), original_episode)
        self.assertEqual((candidate / 'episode.wav').read_bytes(), wav)

        # A repeated rollback must not remove an unrelated asset that later
        # occupies this key after the original operation was already restored.
        target.mkdir(mode=0o700)
        unrelated_manifest = {
            **pending_manifest, 'status': 'recorded',
            'coverage_status': 'human_listening_approved',
            'operation_id': 'd' * 32,
            'human_listening_approval': {'revision_id': episode['revision_id'],
                                         'script_hash': script_hash},
        }
        (target / 'manifest.json').write_text(
            json.dumps(unrelated_manifest, separators=(',', ':')), encoding='utf-8')
        (target / 'episode.json').write_bytes(original_episode)
        (target / 'episode.wav').write_bytes(b'unrelated later approved fictional WAV')
        unrelated_target_bytes = {path.name: path.read_bytes() for path in target.iterdir()}
        restored_candidate_bytes = {path.name: path.read_bytes() for path in candidate.iterdir()}
        with self.assertRaisesRegex(EpisodeAudioError, 'not owned'):
            promoter.rollback_owned_promotion(operation_id, journal)
        self.assertEqual({path.name: path.read_bytes() for path in target.iterdir()},
                         unrelated_target_bytes,
                         'a repeated rollback preserves a later unrelated asset')
        self.assertEqual({path.name: path.read_bytes() for path in candidate.iterdir()},
                         restored_candidate_bytes,
                         'a repeated rollback preserves the already-restored candidate')
        for path in target.iterdir():
            path.unlink()
        target.rmdir()

        # A target that existed before this operation is never removed.
        target.mkdir(mode=0o700)
        preexisting_manifest = {**pending_manifest, 'status': 'recorded',
                                'coverage_status': 'human_listening_approved',
                                'human_listening_approval': {'revision_id': episode['revision_id'],
                                                             'script_hash': script_hash}}
        (target / 'manifest.json').write_text(json.dumps(preexisting_manifest, separators=(',', ':')))
        (target / 'episode.json').write_text(json.dumps(approved_episode, separators=(',', ':')))
        (target / 'episode.wav').write_bytes(b'pre-existing approved fictional WAV')
        preexisting_bytes = {path.name: path.read_bytes() for path in target.iterdir()}
        preexisting_journal = MappingProxyType({
            **journal, 'operation_id': 'e' * 32, 'target_preexisted': True,
            'prior_manifest_sha256': hashlib.sha256(preexisting_bytes['manifest.json']).hexdigest(),
        })
        promoter.rollback_owned_promotion('e' * 32, preexisting_journal)
        self.assertEqual({path.name: path.read_bytes() for path in target.iterdir()}, preexisting_bytes)
        self.assertEqual((candidate / 'manifest.json').read_bytes(), original_manifest)

    def test_core_rollback_projection_invokes_real_audio_restore(self):
        from dev.ingest.corrections import CorrectionService
        from dev.ingest.jobs import EpisodeJobStore

        profile = {'minutes': 5, 'topics': [{'id': 'fictional-topic', 'label': 'Fictional topic'}]}
        store = EpisodeJobStore(self.state_dir, stage_delay=0, generator=lambda *_args: {})
        status, created = store.submit(
            profile, {'topic': 'Fictional local transit', 'length': 5,
                      'style': 'explainer'}, start=False)
        self.assertEqual(status, 202)
        job_id = created['job_id']
        profile_id = store._profile_id(profile)
        job_path = store._path(store._profile_dir(profile), job_id)
        episode = read_fixture()
        episode.update(id=job_id, profile_id=profile_id,
                       revision_id='fictional-core-rollback-r1')
        output_limit = sum(_chapter_output_token_estimate(
            normalize_narration(chapter['spoken_text'])) for chapter in episode['chapters'])
        ledger = UsageLedger(ledger_dir=Path(self.temp.name) / 'core-rollback-ledger')
        key_file = Path(self.temp.name) / 'fictional-core-rollback-key'
        key_file.write_text('fictional-test-key', encoding='utf-8')

        def fake_transport(_request, timeout):
            payload = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{
                'inlineData': {'mimeType': 'audio/wav',
                               'data': base64.b64encode(wav_bytes()).decode('ascii')}
            }]}}], 'usageMetadata': {'promptTokenCount': 1, 'candidatesTokenCount': 1}}
            return BytesIO(json.dumps(payload).encode('utf-8'))

        provider = GeminiTtsProvider(key_path=key_file, transport=fake_transport,
                                     usage_ledger=ledger)
        candidate = render_episode_audio(
            episode, profile_id, provider=provider, state_dir=self.state_dir,
            tts_output_token_limit=output_limit, max_segment_attempts=1,)
        self.assertEqual(candidate['status'], 'pending_human_listening')
        batch_id, asset_key = candidate['batch_id'], candidate['candidate_manifest']['asset_key']
        candidate_dir = candidate['candidate_directory']
        original_manifest = (candidate_dir / 'manifest.json').read_bytes()
        original_episode = (candidate_dir / 'episode.json').read_bytes()
        original_wav = (candidate_dir / 'episode.wav').read_bytes()
        episode['script_hash'] = candidate['script_hash']
        stored_episode = deepcopy(episode)
        stored_episode['audio'] = {
            'status': 'pending', 'state': 'pending', 'revision_id': episode['revision_id'],
            'script_hash': candidate['script_hash'],
            'coverage_status': 'pending_human_listening',
        }
        stored_episode['audio_status'] = 'pending'
        job = store._read(job_path)
        job.update(status='done', stage='done', audio_status='pending', episode=stored_episode)
        store._write(job_path, job)
        old_raw = job_path.read_bytes()
        target = audio_directory(self.state_dir, profile_id, job_id, asset_key)
        operation_id = 'c' * 32
        corrections = CorrectionService(
            self.state_dir, episode_reader=lambda selected_profile, selected_id:
            store.get(selected_profile, selected_id))
        core_record = {
            'schema_version': 1, 'operation_id': operation_id, 'phase': 'prepared',
            'profile_id': profile_id, 'job_id': job_id,
            'revision_id': episode['revision_id'], 'script_sha256': candidate['script_hash'],
            'candidate_batch_id': batch_id, 'asset_key': asset_key,
            'voice_id': DEFAULT_VOICE, 'provider_id': 'gemini', 'model_id': DEFAULT_MODEL,
            'normalizer_id': NORMALIZER_VERSION,
            'tts_output_token_limit': output_limit, 'max_segment_attempts': 1,
            'prior_job_sha256': hashlib.sha256(old_raw).hexdigest(),
            'expected_audio_reference_sha256': store._audio_reference_sha256(job),
            'candidate_directory': str(candidate_dir), 'target_directory': str(target),
            'target_preexisted': False, 'prior_manifest_sha256': None,
        }
        with corrections.approval_publication(profile_id):
            with store._profile_lock(job_path.parent):
                journal_path = store._persist_approval_intent_locked(
                    profile=profile, corrections=corrections, job_path=job_path,
                    old_raw=old_raw, journal=core_record,
                    candidate_backups={'manifest': original_manifest,
                                       'episode': original_episode})
                core_record = json.loads(journal_path.read_text(encoding='utf-8'))
                self.assertEqual(core_record['phase'], 'prepared')
                self.assertEqual(core_record['job_id'], job_id)
                for name, payload in (('manifest', original_manifest),
                                      ('episode', original_episode)):
                    backup = core_record[f'candidate_{name}_backup']
                    self.assertEqual(Path(backup['path']).read_bytes(), payload)
                    self.assertEqual(backup['sha256'], hashlib.sha256(payload).hexdigest())
                core_record = store._advance_approval_intent(
                    corrections=corrections, job_path=job_path,
                    journal_path=journal_path, journal=core_record,
                    phase='promotion_started')
                from dev.ingest.audio_approval import issue_fictional_listening_approval_for_test
                identity = {
                    'profile_id': profile_id, 'episode_id': job_id,
                    'revision_id': episode['revision_id'],
                    'script_sha256': candidate['script_hash'],
                    'candidate_batch_id': batch_id, 'asset_key': asset_key,
                    'voice_id': DEFAULT_VOICE, 'provider_id': 'gemini',
                    'model_id': DEFAULT_MODEL, 'normalizer_id': NORMALIZER_VERSION,
                    'tts_output_token_limit': output_limit, 'max_segment_attempts': 1,
                }
                approval = issue_fictional_listening_approval_for_test(identity)
                promoter = episodes_audio.BoundedAudioPromoter(state_dir=self.state_dir)
                promoted = promoter.promote_candidate(
                    episode, profile_id, operation_id=operation_id,
                    candidate_batch_id=batch_id, asset_key=asset_key,
                    revision_id=episode['revision_id'], script_sha256=candidate['script_hash'],
                    voice_id=DEFAULT_VOICE, provider_id='gemini', model_id=DEFAULT_MODEL,
                    normalizer_id=NORMALIZER_VERSION,
                    tts_output_token_limit=output_limit, max_segment_attempts=1,
                    listening_approval=approval)
                core_record = store._advance_approval_intent(
                    corrections=corrections, job_path=job_path,
                    journal_path=journal_path, journal=core_record,
                    phase='promoted', promoted_asset={
                        'operation_id': promoted.manifest['operation_id'],
                        'profile_id': profile_id, 'episode_id': job_id,
                        'asset_key': asset_key,
                    })

        self.assertTrue(target.is_dir())
        self.assertFalse(candidate_dir.exists())
        self.assertEqual(json.loads((target / 'manifest.json').read_text())['status'], 'recorded')
        self.assertEqual((target / 'episode.wav').read_bytes(), original_wav)
        adapt = getattr(EpisodeJobStore, '_approval_rollback_record', None)
        if not callable(adapt):
            self.skipTest('unimplemented core nested-to-flat rollback adapter')
        target_before = {path.name: path.read_bytes() for path in target.iterdir()}
        backup_paths = [Path(core_record[f'candidate_{name}_backup']['path'])
                        for name in ('manifest', 'episode')]
        backup_bytes_before = [path.read_bytes() for path in backup_paths]
        journal_bytes_before = journal_path.read_bytes()
        job_bytes_before = job_path.read_bytes()
        malformed_core_record = deepcopy(core_record)
        malformed_core_record['candidate_manifest_backup']['sha256'] = '0' * 64
        malformed_audio_record = adapt(malformed_core_record)
        with self.assertRaisesRegex(EpisodeAudioError, 'backup hash'):
            promoter.rollback_owned_promotion(operation_id, malformed_audio_record)
        self.assertEqual({path.name: path.read_bytes() for path in target.iterdir()}, target_before,
                         'malformed backup identity must not move or alter the approved target')
        self.assertFalse(candidate_dir.exists())
        self.assertEqual([path.read_bytes() for path in backup_paths], backup_bytes_before)
        self.assertEqual(journal_path.read_bytes(), journal_bytes_before)
        self.assertEqual(job_path.read_bytes(), job_bytes_before)

        audio_record = adapt(core_record)
        self.assertEqual(audio_record['episode_id'], job_id,
                         'core job_id must map to the audio episode_id')
        for name, payload in (('manifest', original_manifest), ('episode', original_episode)):
            self.assertEqual(audio_record[f'candidate_{name}_backup_path'],
                             core_record[f'candidate_{name}_backup']['path'])
            self.assertEqual(audio_record[f'candidate_{name}_backup_sha256'],
                             hashlib.sha256(payload).hexdigest())
        promoter.rollback_owned_promotion(operation_id, audio_record)
        self.assertFalse(target.exists())
        self.assertEqual((candidate_dir / 'manifest.json').read_bytes(), original_manifest)
        self.assertEqual((candidate_dir / 'episode.json').read_bytes(), original_episode)
        self.assertEqual((candidate_dir / 'episode.wav').read_bytes(), original_wav)

    def test_bounded_model_drift_is_rejected_at_provider_and_renderer_boundaries(self):
        episode = read_fixture()
        requests = []
        ledger = UsageLedger(ledger_dir=Path(self.temp.name) / 'model-drift-ledger')
        provider = GeminiTtsProvider(
            key_path=Path(self.temp.name) / 'missing-fixture-key',
            transport=lambda request, timeout: requests.append((request, timeout)),
            usage_ledger=ledger)
        provider.model = 'gemini-3.8-flash-pro-tts'
        limits = {
            'max_output_tokens': 455,
            'batch_id': 'fictional-model-drift',
            'batch_output_token_limit': 4096,
        }

        with self.assertRaisesRegex(ValueError, 'verified Gemini default Lite model'):
            provider.synthesize('Fictional model-drift sample.', 'Sulafat', **limits)
        self.assertEqual(requests, [], 'the direct provider refuses drift before transport')

        with patch.object(provider, 'synthesize', wraps=provider.synthesize) as synthesize:
            with self.assertRaisesRegex(ValueError, 'verified Gemini default Lite model'):
                render_episode_audio(
                    episode, episode['profile_id'], provider=provider,
                    state_dir=self.state_dir, tts_output_token_limit=4096,)
            synthesize.assert_not_called()

        self.assertEqual(requests, [], 'the renderer refuses the same drifted provider before transport')
        self.assertFalse(ledger.path.exists(),
                         'neither refusal reads or writes the temporary ledger to reserve a paid attempt')

    def test_bounded_render_rejects_unpriced_provider_or_model_before_transport(self):
        episode = read_fixture()

        class ProviderIdentityProbe:
            def __init__(self, provider_name, model):
                self.provider_name = provider_name
                self.model = model
                self.calls = []

            def synthesize(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                raise AssertionError('unsupported bounded provider reached transport')

        for provider_name, model in (
                ('gemini', 'fictional-unpriced-tts-model'),
                ('fictional-provider', DEFAULT_MODEL)):
            with self.subTest(provider=provider_name, model=model):
                provider = ProviderIdentityProbe(provider_name, model)
                with self.assertRaisesRegex(ValueError, 'verified Gemini default Lite model'):
                    render_episode_audio(
                        episode, episode['profile_id'], provider=provider,
                        state_dir=self.state_dir, tts_output_token_limit=4096,)
                self.assertEqual(provider.calls, [],
                                 'unpriced bounded identities are rejected before transport')

        class MissingModelProvider:
            provider_name = 'gemini'

            def __init__(self):
                self.calls = []

            def synthesize(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                raise AssertionError('provider without a model identity reached transport')

        missing_model = MissingModelProvider()
        with self.assertRaisesRegex(ValueError, 'verified Gemini default Lite model'):
            render_episode_audio(
                episode, episode['profile_id'], provider=missing_model,
                state_dir=self.state_dir, tts_output_token_limit=4096,)
        self.assertEqual(missing_model.calls, [],
                         'missing model identity is rejected before transport')

        class AlternateUnboundedProvider(FakeProvider):
            provider_name = 'fictional-provider'
            model = 'fictional-unpriced-tts-model'

            def synthesize(self, text, voice_name):
                result = super().synthesize(text, voice_name)
                return TtsResult(result.audio_bytes, result.mime_type, result.duration_seconds,
                                 result.character_count, result.estimated_cost_usd,
                                 self.model, voice_name)

        ordinary = render_episode_audio(
            episode, episode['profile_id'], provider=AlternateUnboundedProvider(),
            state_dir=self.state_dir)
        self.assertEqual(ordinary['manifest']['provider'], 'fictional-provider',
                         'unbounded provider selection remains unchanged')

        class UntaggedUnboundedProvider:
            def __init__(self):
                self.calls = []

            def synthesize(self, text, voice_name):
                self.calls.append((text, voice_name))
                return TtsResult(wav_bytes(), 'audio/wav', 0.1, len(text),
                                 Decimal('0.0001'), DEFAULT_MODEL, voice_name)

        untagged = UntaggedUnboundedProvider()
        untagged_result = render_episode_audio(
            episode, episode['profile_id'], provider=untagged,
            state_dir=self.state_dir)
        self.assertEqual(len(untagged.calls), len(episode['chapters']))
        self.assertEqual(untagged_result['manifest']['provider'], 'gemini')
        self.assertEqual(untagged_result['manifest']['model'], DEFAULT_MODEL,
                         'unbounded provider fallback metadata remains unchanged')


    def test_failed_bounded_correction_preserves_approved_bounded_recording(self):
        original = read_fixture()
        corrected_script = deepcopy(original)
        corrected_script['revision_id'] = 'fictional-r2'
        corrected_script['chapters'][0]['spoken_text'] += ' The fictional review now includes June.'
        normalized = [normalize_narration(row['spoken_text']) for row in corrected_script['chapters']]
        output_pool = sum(_chapter_output_token_estimate(text) for text in normalized)
        self.assertLessEqual(output_pool, DEFAULT_BATCH_OUTPUT_TOKEN_LIMIT)
        identity = {'voice_name': DEFAULT_VOICE, 'state_dir': self.state_dir,
                    'tts_output_token_limit': output_pool, 'max_segment_attempts': 3}
        render_options = {**identity}
        key_file = Path(self.temp.name) / 'fictional-bounded-preservation-key'
        key_file.write_text('fictional-fixture-key', encoding='utf-8')
        ledger = UsageLedger(ledger_dir=Path(self.temp.name) / 'bounded-preservation-ledger')
        correction_requests = []
        staged_at_transport = []
        staged_audio_at_failure = []
        phase = {'correction': False}
        asset_root = audio_directory(self.state_dir, original['profile_id'], original['id'])

        def fake_transport(request, timeout):
            body = json.loads(request.data)
            finish_reason = 'STOP'
            if phase['correction']:
                correction_requests.append(body)
                staged_at_transport.append([
                    sorted(path.name for path in directory.iterdir())
                    for directory in asset_root.iterdir()
                    if directory.is_dir() and directory.name.startswith('.render-')])
                if len(correction_requests) == 2:
                    finish_reason = 'MAX_TOKENS'
                    staged_audio_at_failure.extend(
                        (directory / 'chapter-01.wav').read_bytes()
                        for directory in asset_root.iterdir()
                        if directory.is_dir() and directory.name.startswith('.render-'))
            audio_bytes = wav_bytes(frames=200 if phase['correction'] else 100)
            payload = {'candidates': [{'finishReason': finish_reason, 'content': {'parts': [{
                'inlineData': {'mimeType': 'audio/wav',
                               'data': base64.b64encode(audio_bytes).decode('ascii')}
            }]}}], 'usageMetadata': {
                'promptTokenCount': 64,
                'candidatesTokenCount': body['generationConfig']['maxOutputTokens'],
            }}
            return BytesIO(json.dumps(payload).encode('utf-8'))

        provider = GeminiTtsProvider(key_path=key_file, transport=fake_transport, usage_ledger=ledger)
        candidate = render_episode_audio(original, original['profile_id'], provider=provider,
                                         **render_options)
        self.assertEqual(candidate['status'], 'pending_human_listening')
        approved = approve_bounded_audio_candidate(
            original, original['profile_id'], batch_id=candidate['batch_id'],
            approved_revision_id=original['revision_id'], approved_script_hash=candidate['script_hash'],
            human_approved=True, **identity)  # Fictional attestation only; no real speech approval.
        old_state, old_manifest, old_path = current_audio_asset(
            original, original['profile_id'], **identity)
        self.assertEqual(old_state, 'ready', 'r1 must already be playable with the same bounded settings')
        self.assertEqual(old_manifest, approved['manifest'])
        self.assertEqual(old_manifest['coverage_status'], 'human_listening_approved')
        self.assertEqual(old_manifest['human_listening_approval'], {
            'revision_id': original['revision_id'], 'script_hash': candidate['script_hash']})
        prior_files = {path.name: path.read_bytes() for path in old_path.parent.iterdir()}
        prior_episode = json.loads(prior_files['episode.json'])
        prior_url = prior_episode['audio']['url'].encode('utf-8')
        self.assertEqual(prior_url, f"/api/episodes/{original['id']}/audio".encode('utf-8'))

        # Carry genuine approved r1 metadata into r2 so removal is observable.
        corrected = deepcopy(prior_episode)
        corrected['revision_id'] = corrected_script['revision_id']
        corrected['chapters'][0]['spoken_text'] = corrected_script['chapters'][0]['spoken_text']
        before_correction = deepcopy(corrected)
        expected_text_only = deepcopy(corrected)
        expected_text_only.pop('audio')
        for chapter in expected_text_only['chapters']:
            chapter.pop('audio_start_seconds')
            chapter.pop('audio_duration_seconds')
        phase['correction'] = True
        with self.assertRaisesRegex(EpisodeAudioError,
                                    r'Chapter 2 failed after 2 attempts \(TtsOutputLimitError\)'):
            render_episode_audio(corrected, corrected['profile_id'], provider=provider, **render_options)

        self.assertEqual(len(correction_requests), 2, 'pool exhaustion refuses the next retry')
        self.assertEqual([body['contents'][0]['parts'][0]['text'] for body in correction_requests], normalized)
        self.assertEqual(staged_at_transport, [[[]], [['chapter-01.wav']]],
                         'the correction fails only after a full first chapter was staged')
        self.assertEqual(len(staged_audio_at_failure), 1)
        self.assertNotEqual(staged_audio_at_failure[0], prior_files['chapter-01.wav'],
                            'distinct corrected WAV bytes make accidental overwrites observable')
        self.assertEqual(corrected, before_correction, 'failed correction cannot mutate current text')
        self.assertEqual(current_audio_asset(corrected, corrected['profile_id'], **identity),
                         ('stale', None, None), 'r2 cannot select the approved bounded r1 recording')
        projected = add_chapter_timestamps({'episode': corrected}, corrected['profile_id'],
                                           voice_name=DEFAULT_VOICE, state_dir=self.state_dir)
        self.assertEqual(projected['audio_state'], 'stale')
        self.assertEqual(projected['episode'], expected_text_only,
                         'corrected text has no old URL or measured chapter timing')

        preserved_state, preserved_manifest, preserved_path = current_audio_asset(
            original, original['profile_id'], **identity)
        self.assertEqual((preserved_state, preserved_manifest, preserved_path),
                         ('ready', old_manifest, old_path))
        self.assertEqual({path.name: path.read_bytes() for path in preserved_path.parent.iterdir()},
                         prior_files, 'approved WAVs, manifest, approval and episode JSON stay byte-for-byte intact')
        self.assertEqual(json.loads((preserved_path.parent / 'episode.json').read_bytes())['audio']['url']
                         .encode('utf-8'), prior_url)
        self.assertEqual(sorted(path.name for path in asset_root.iterdir() if path.is_dir()),
                         [old_manifest['asset_key']], 'no partial correction or new review candidate remains')

    def test_failed_corrected_audio_retry_keeps_old_revision_stale_until_replacement_is_ready(self):
        original = read_fixture()
        first = render_episode_audio(original, original['profile_id'], provider=FakeProvider(),
                                     state_dir=self.state_dir)
        old_state, old_manifest, old_path = current_audio_asset(
            original, original['profile_id'], voice_name=DEFAULT_VOICE, state_dir=self.state_dir)
        self.assertEqual(old_state, 'ready')
        prior_audio = old_path.read_bytes()
        asset_root = old_path.parent.parent
        self.assertEqual({row.name for row in asset_root.iterdir()
                          if row.is_dir() and not row.name.startswith('.')},
                         {old_manifest['asset_key']})
        corrected = deepcopy(first['episode'])
        corrected['revision_id'] = 'fictional-r2'
        corrected['chapters'][0]['spoken_text'] = 'Alder Bay will use corrected fictional delivery wording this spring.'

        class PausedSecondChapterProvider(FakeProvider):
            def __init__(self):
                super().__init__(failures={2, 3, 4})
                self.second_chapter_started = threading.Event()
                self.release_second_chapter = threading.Event()

            def synthesize(self, text, voice_name):
                if len(self.calls) == 1:
                    self.second_chapter_started.set()
                    if not self.release_second_chapter.wait(timeout=5):
                        raise RuntimeError('fictional provider fixture timed out')
                return super().synthesize(text, voice_name)

        partial_provider = PausedSecondChapterProvider()
        with ThreadPoolExecutor(max_workers=1) as workers:
            failed_render = workers.submit(
                render_episode_audio, corrected, corrected['profile_id'],
                provider=partial_provider, state_dir=self.state_dir)
            try:
                self.assertTrue(partial_provider.second_chapter_started.wait(timeout=5))
                staged = [row for row in asset_root.iterdir()
                          if row.is_dir() and row.name.startswith('.render-')]
                self.assertEqual(len(staged), 1)
                self.assertTrue((staged[0] / 'chapter-01.wav').is_file(),
                                'chapter one has been staged before chapter two begins')
                self.assertFalse((staged[0] / 'chapter-02.wav').exists())
                self.assertFalse((staged[0] / 'manifest.json').exists(),
                                 'a partial render has no ready manifest')
                in_progress_state, in_progress_manifest, in_progress_path = current_audio_asset(
                    corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE,
                    state_dir=self.state_dir)
                self.assertEqual((in_progress_state, in_progress_manifest, in_progress_path),
                                 ('stale', None, None),
                                 'the staged chapter cannot be returned as current audio')
                in_progress = add_chapter_timestamps(
                    {'episode': corrected}, corrected['profile_id'], voice_name=DEFAULT_VOICE,
                    state_dir=self.state_dir)
                self.assertEqual(in_progress['audio_state'], 'stale')
                self.assertNotIn('audio', in_progress['episode'])
                self.assertEqual({row.name for row in asset_root.iterdir()
                                  if row.is_dir() and not row.name.startswith('.')},
                                 {old_manifest['asset_key']},
                                 'only the old complete revision is published during synthesis')
            finally:
                partial_provider.release_second_chapter.set()
            with self.assertRaisesRegex(EpisodeAudioError, 'Chapter 2 failed after 3 attempts'):
                failed_render.result(timeout=10)

        self.assertEqual(len(partial_provider.calls), 4)
        self.assertNotEqual(partial_provider.calls[0][0], partial_provider.calls[1][0],
                            'chapter one is synthesized before chapter two retries fail')
        self.assertTrue(all(call[0] == partial_provider.calls[1][0]
                            for call in partial_provider.calls[1:]))

        old_state, old_manifest, _old_path = current_audio_asset(
            original, original['profile_id'], voice_name=DEFAULT_VOICE, state_dir=self.state_dir)
        corrected_state, corrected_manifest, corrected_path = current_audio_asset(
            corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE, state_dir=self.state_dir)
        self.assertEqual(old_state, 'ready', 'the immutable prior revision remains available to its revision')
        self.assertEqual(old_manifest['revision_id'], 'fictional-r1')
        self.assertEqual(old_path.read_bytes(), prior_audio)
        self.assertEqual((corrected_state, corrected_manifest, corrected_path), ('stale', None, None))
        self.assertEqual({row.name for row in asset_root.iterdir()
                          if row.is_dir() and not row.name.startswith('.')},
                         {old_manifest['asset_key']},
                         'failed correction staging must not publish a partial asset')

        stamped = add_chapter_timestamps({'episode': corrected}, corrected['profile_id'],
                                         voice_name=DEFAULT_VOICE, state_dir=self.state_dir)
        self.assertEqual(stamped['audio_state'], 'stale')
        self.assertNotIn('audio', stamped['episode'], 'old audio cannot be attached to a corrected transcript')
        self.assertTrue(all('audio_start_seconds' not in chapter and 'audio_duration_seconds' not in chapter
                            for chapter in stamped['episode']['chapters']))

        retry = render_episode_audio(corrected, corrected['profile_id'], provider=FakeProvider(),
                                     state_dir=self.state_dir)
        self.assertFalse(retry['cached'], 'a replacement is not ready until its own asset is committed')
        self.assertEqual(retry['manifest']['revision_id'], 'fictional-r2')
        self.assertEqual(retry['provider_calls'], len(corrected['chapters']))
        corrected_state, corrected_manifest, corrected_path = current_audio_asset(
            corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE, state_dir=self.state_dir)
        self.assertEqual(corrected_state, 'ready')
        self.assertEqual(corrected_manifest['revision_id'], 'fictional-r2')
        self.assertEqual(corrected_path, retry['directory'] / 'episode.wav')
        old_state, old_manifest, old_path = current_audio_asset(
            original, original['profile_id'], voice_name=DEFAULT_VOICE, state_dir=self.state_dir)
        self.assertEqual(old_state, 'ready')
        self.assertEqual(old_path.read_bytes(), prior_audio)
        self.assertEqual({row.name for row in asset_root.iterdir()
                          if row.is_dir() and not row.name.startswith('.')},
                         {old_manifest['asset_key'], corrected_manifest['asset_key']})

    def test_concurrent_identical_correction_renders_publish_one_complete_asset(self):
        original = read_fixture()
        first = render_episode_audio(original, original['profile_id'], provider=FakeProvider(),
                                     state_dir=self.state_dir)
        prior_audio = (first['directory'] / 'episode.wav').read_bytes()
        corrected = deepcopy(first['episode'])
        corrected['revision_id'] = 'fictional-r2'
        corrected['chapters'][0]['spoken_text'] = (
            'Alder Bay will use the corrected fictional delivery plan this spring.')

        class ConcurrentProvider(FakeProvider):
            def __init__(self):
                super().__init__()
                self.started = threading.Event()
                self.release = threading.Event()
                self.second_call = threading.Event()
                self.call_lock = threading.Lock()

            def synthesize(self, text, voice_name):
                with self.call_lock:
                    self.calls.append((text, voice_name))
                    call_number = len(self.calls)
                if call_number == 1:
                    self.started.set()
                    if not self.release.wait(timeout=5):
                        raise RuntimeError('fictional provider fixture timed out')
                else:
                    self.second_call.set()
                return TtsResult(wav_bytes(), 'audio/wav', 0.1, len(text), Decimal('0.0001'),
                                 DEFAULT_MODEL, voice_name)

        provider = ConcurrentProvider()
        start = threading.Barrier(3)

        def render():
            start.wait(timeout=5)
            return render_episode_audio(corrected, corrected['profile_id'], provider=provider,
                                        state_dir=self.state_dir)

        with ThreadPoolExecutor(max_workers=2) as workers:
            requests = [workers.submit(render) for _ in range(2)]
            start.wait(timeout=5)
            self.assertTrue(provider.started.wait(timeout=5), 'one request should begin rendering')
            duplicate_synthesis_started = provider.second_call.wait(timeout=0.25)
            during_state, during_manifest, during_path = current_audio_asset(
                corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE,
                state_dir=self.state_dir)
            during_episode = add_chapter_timestamps(
                {'episode': corrected}, corrected['profile_id'], voice_name=DEFAULT_VOICE,
                state_dir=self.state_dir)
            provider.release.set()
            results = [request.result(timeout=10) for request in requests]

        self.assertFalse(duplicate_synthesis_started,
                         'the second identical request must wait and reuse the completed asset')
        self.assertEqual(len(provider.calls), len(corrected['chapters']))
        self.assertEqual((during_state, during_manifest, during_path), ('stale', None, None),
                         'an in-progress correction must not be exposed as ready')
        self.assertEqual(during_episode['audio_state'], 'stale')
        self.assertNotIn('audio', during_episode['episode'])
        self.assertEqual(sum(not result['cached'] for result in results), 1)
        self.assertEqual(sum(result['cached'] for result in results), 1)
        self.assertEqual({result['manifest']['asset_key'] for result in results},
                         {results[0]['manifest']['asset_key']})
        self.assertTrue(all(result['manifest']['revision_id'] == 'fictional-r2'
                            for result in results))
        self.assertTrue(all(result['episode']['audio']['revision_id'] == 'fictional-r2'
                            and result['episode']['audio']['script_hash'] == result['manifest']['script_hash']
                            for result in results))
        corrected_state, corrected_manifest, corrected_path = current_audio_asset(
            corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE,
            state_dir=self.state_dir)
        self.assertEqual(corrected_state, 'ready')
        self.assertEqual(corrected_manifest['revision_id'], 'fictional-r2')
        self.assertEqual(corrected_path.read_bytes(), (results[0]['directory'] / 'episode.wav').read_bytes())
        self.assertEqual(prior_audio, (first['directory'] / 'episode.wav').read_bytes(),
                         'publishing the correction must preserve the prior revision asset')
        published = [row for row in first['directory'].parent.iterdir()
                     if row.is_dir() and not row.name.startswith('.')]
        self.assertEqual({row.name for row in published},
                         {first['manifest']['asset_key'], corrected_manifest['asset_key']})
        self.assertFalse(any(row.is_dir() and row.name.startswith('.render-')
                             for row in first['directory'].parent.iterdir()))

    def test_correction_renders_only_changed_chapter_restitches_and_retains_prior_revision(self):
        original = read_fixture()
        provider = CorrectionDurationProvider()
        first = render_episode_audio(original, original['profile_id'], provider=provider,
                                     state_dir=self.state_dir)
        corrected = deepcopy(original)
        corrected['revision_id'] = 'fictional-r2'
        corrected['chapters'][0]['spoken_text'] = 'Alder Bay published corrected wording for its spring locker trial.'
        before_replacement = add_chapter_timestamps(
            {'episode': corrected}, corrected['profile_id'], voice_name=DEFAULT_VOICE,
            state_dir=self.state_dir)
        self.assertEqual(before_replacement['audio_state'], 'stale')
        self.assertNotIn('audio', before_replacement['episode'])
        self.assertTrue(all('audio_start_seconds' not in chapter and 'audio_duration_seconds' not in chapter
                            for chapter in before_replacement['episode']['chapters']))

        updated = render_episode_audio(
            corrected, corrected['profile_id'], provider=provider, state_dir=self.state_dir,
            reuse_from_episode=original, changed_chapter_id='what-changed')
        self.assertEqual(len(provider.calls), 3, 'only the corrected chapter is synthesized again')
        self.assertEqual(provider.calls[2][0], corrected['chapters'][0]['spoken_text'])
        rows = updated['manifest']['chapters']
        self.assertEqual(rows[0]['duration_seconds'], 0.2)
        self.assertEqual(rows[1]['start_seconds'], 0.2, 'the later offset is recomputed')
        self.assertTrue(rows[1]['reused'])
        self.assertEqual(rows[1]['reused_from_revision_id'], 'fictional-r1')
        self.assertEqual(updated['manifest']['duration_seconds'], 0.3)
        stamped = add_chapter_timestamps({'episode': corrected}, corrected['profile_id'],
                                         voice_name=DEFAULT_VOICE, state_dir=self.state_dir)
        self.assertEqual(stamped['audio_state'], 'recorded')
        self.assertEqual(stamped['episode']['audio']['revision_id'], 'fictional-r2')
        self.assertEqual(stamped['episode']['audio']['script_hash'], updated['manifest']['script_hash'])
        self.assertEqual([chapter['audio_start_seconds'] for chapter in stamped['episode']['chapters']],
                         [0.0, 0.2], 'the player timing comes from the corrected manifest')
        self.assertEqual([chapter['audio_duration_seconds'] for chapter in stamped['episode']['chapters']],
                         [0.2, 0.1])

        old_state, old_manifest, old_path = current_audio_asset(
            original, original['profile_id'], voice_name=DEFAULT_VOICE, state_dir=self.state_dir)
        new_state, new_manifest, new_path = current_audio_asset(
            corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE, state_dir=self.state_dir)
        self.assertEqual(old_state, 'ready')
        self.assertEqual(new_state, 'ready')
        self.assertEqual(old_manifest['revision_id'], 'fictional-r1')
        self.assertEqual(new_manifest['revision_id'], 'fictional-r2')
        self.assertNotEqual(old_path, new_path, 'audio assets remain revision-addressed and immutable')

    def test_measured_offset_hash_and_partial_controls_preserve_prior_audio(self):
        original = read_fixture()
        provider = CorrectionDurationProvider()
        first = render_episode_audio(original, original['profile_id'], provider=provider,
                                     state_dir=self.state_dir)
        prior_bytes = (first['directory'] / 'episode.wav').read_bytes()
        corrected = deepcopy(original)
        corrected['revision_id'] = 'fictional-r2'
        corrected['chapters'][0]['spoken_text'] = (
            'Alder Bay published corrected wording for its spring locker trial.')
        updated = render_episode_audio(
            corrected, corrected['profile_id'], provider=provider, state_dir=self.state_dir,
            reuse_from_episode=original, changed_chapter_id='what-changed')
        manifest_path = updated['directory'] / 'manifest.json'
        current_manifest = deepcopy(updated['manifest'])
        self.assertEqual([row['duration_seconds'] for row in current_manifest['chapters']], [0.2, 0.1])
        self.assertEqual([row['start_seconds'] for row in current_manifest['chapters']], [0.0, 0.2])
        supported_state, supported_manifest, supported_path = current_audio_asset(
            corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE,
            state_dir=self.state_dir)
        self.assertEqual(supported_state, 'ready')
        self.assertEqual(supported_manifest['format_version'], 1)
        self.assertEqual(supported_path, updated['directory'] / 'episode.wav')

        numeric_version = deepcopy(current_manifest)
        numeric_version['format_version'] = 1.0
        manifest_path.write_text(json.dumps(numeric_version), encoding='utf-8')
        numeric_version_target_bytes = {path.name: path.read_bytes()
                                        for path in updated['directory'].iterdir()}
        numeric_state, numeric_manifest, numeric_path = current_audio_asset(
            corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE,
            state_dir=self.state_dir)
        self.assertEqual(numeric_state, 'ready',
                         'current Python equality policy accepts numeric format_version 1.0')
        self.assertIs(type(numeric_manifest['format_version']), float)
        self.assertEqual(numeric_manifest['format_version'], 1.0)
        self.assertEqual(numeric_path, updated['directory'] / 'episode.wav')
        self.assertEqual({path.name: path.read_bytes()
                          for path in updated['directory'].iterdir()}, numeric_version_target_bytes,
                         'numeric-version selection is read-only')
        manifest_path.write_text(json.dumps(current_manifest), encoding='utf-8')

        bad_offset = deepcopy(current_manifest)
        bad_offset['chapters'][1]['start_seconds'] = 0.1
        manifest_path.write_text(json.dumps(bad_offset), encoding='utf-8')
        offset_state, offset_manifest, offset_path = current_audio_asset(
            corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE,
            state_dir=self.state_dir)
        self.assertEqual((offset_state, offset_manifest, offset_path), ('stale', None, None),
                         'a start offset that disagrees with measured earlier WAV durations cannot play')

        non_finite_cases = (
            ('chapter-start-nan', ('chapters', 1, 'start_seconds'), float('nan')),
            ('chapter-start-infinity', ('chapters', 1, 'start_seconds'), float('inf')),
            ('chapter-duration-nan', ('chapters', 1, 'duration_seconds'), float('nan')),
            ('chapter-duration-infinity', ('chapters', 1, 'duration_seconds'), float('inf')),
            ('total-duration-nan', ('duration_seconds',), float('nan')),
            ('total-duration-infinity', ('duration_seconds',), float('inf')),
        )
        for case, path, value in non_finite_cases:
            with self.subTest(case=case):
                invalid_manifest = deepcopy(current_manifest)
                target = invalid_manifest
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                manifest_path.write_text(json.dumps(invalid_manifest), encoding='utf-8')
                invalid_state, invalid_audio, invalid_path = current_audio_asset(
                    corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE,
                    state_dir=self.state_dir)
                self.assertEqual((invalid_state, invalid_audio, invalid_path), ('stale', None, None),
                                 'non-finite manifest timing cannot make audio playable')

        bad_hash = deepcopy(current_manifest)
        bad_hash['script_hash'] = '0' * 64
        manifest_path.write_text(json.dumps(bad_hash), encoding='utf-8')
        hash_state, hash_manifest, hash_path = current_audio_asset(
            corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE,
            state_dir=self.state_dir)
        self.assertEqual((hash_state, hash_manifest, hash_path), ('stale', None, None),
                         'a manifest for another script hash cannot play')

        malformed_manifest = b'{"revision_id":'
        manifest_path.write_bytes(malformed_manifest)
        malformed_target_bytes = {path.name: path.read_bytes()
                                  for path in updated['directory'].iterdir()}
        malformed_state, malformed_audio, malformed_path = current_audio_asset(
            corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE,
            state_dir=self.state_dir)
        self.assertEqual((malformed_state, malformed_audio, malformed_path), ('stale', None, None),
                         'a malformed current manifest cannot make WAV bytes playable')
        self.assertEqual({path.name: path.read_bytes()
                          for path in updated['directory'].iterdir()}, malformed_target_bytes,
                         'manifest refusal does not rewrite or remove observed asset bytes')

        manifest_path.write_text('null', encoding='utf-8')
        null_target_bytes = {path.name: path.read_bytes()
                             for path in updated['directory'].iterdir()}
        null_state, null_manifest, null_path = current_audio_asset(
            corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE,
            state_dir=self.state_dir)
        self.assertEqual((null_state, null_manifest, null_path), ('stale', None, None),
                         'a JSON null manifest root cannot make WAV bytes playable')
        self.assertEqual({path.name: path.read_bytes()
                          for path in updated['directory'].iterdir()}, null_target_bytes,
                         'null-root refusal preserves all observed asset bytes')

        manifest_path.write_text('[]', encoding='utf-8')
        array_target_bytes = {path.name: path.read_bytes()
                              for path in updated['directory'].iterdir()}
        array_state, array_manifest, array_path = current_audio_asset(
            corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE,
            state_dir=self.state_dir)
        self.assertEqual((array_state, array_manifest, array_path), ('stale', None, None),
                         'a JSON array manifest root cannot make WAV bytes playable')
        self.assertEqual({path.name: path.read_bytes()
                          for path in updated['directory'].iterdir()}, array_target_bytes,
                         'array-root refusal preserves all observed asset bytes')

        unknown_version = deepcopy(current_manifest)
        unknown_version['format_version'] = 99
        unknown_version_bytes = json.dumps(unknown_version).encode('utf-8')
        manifest_path.write_bytes(unknown_version_bytes)
        unknown_version_target_bytes = {path.name: path.read_bytes()
                                        for path in updated['directory'].iterdir()}
        version_state, version_manifest, version_path = current_audio_asset(
            corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE,
            state_dir=self.state_dir)
        self.assertEqual((version_state, version_manifest, version_path), ('stale', None, None),
                         'an unknown manifest format version cannot make WAV bytes playable')
        self.assertEqual({path.name: path.read_bytes()
                          for path in updated['directory'].iterdir()}, unknown_version_target_bytes,
                         'version refusal preserves all observed asset bytes')

        missing_version = deepcopy(current_manifest)
        missing_version.pop('format_version')
        missing_version_bytes = json.dumps(missing_version).encode('utf-8')
        manifest_path.write_bytes(missing_version_bytes)
        missing_version_target_bytes = {path.name: path.read_bytes()
                                        for path in updated['directory'].iterdir()}
        missing_state, missing_manifest, missing_path = current_audio_asset(
            corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE,
            state_dir=self.state_dir)
        self.assertEqual((missing_state, missing_manifest, missing_path), ('stale', None, None),
                         'a missing manifest format version cannot make WAV bytes playable')
        self.assertEqual({path.name: path.read_bytes()
                          for path in updated['directory'].iterdir()}, missing_version_target_bytes,
                         'missing-version refusal preserves all observed asset bytes')

        string_version = deepcopy(current_manifest)
        string_version['format_version'] = '1'
        string_version_bytes = json.dumps(string_version).encode('utf-8')
        manifest_path.write_bytes(string_version_bytes)
        string_version_target_bytes = {path.name: path.read_bytes()
                                       for path in updated['directory'].iterdir()}
        string_state, string_manifest, string_path = current_audio_asset(
            corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE,
            state_dir=self.state_dir)
        self.assertEqual((string_state, string_manifest, string_path), ('stale', None, None),
                         'a string manifest format version cannot make WAV bytes playable')
        self.assertEqual({path.name: path.read_bytes()
                          for path in updated['directory'].iterdir()}, string_version_target_bytes,
                         'string-version refusal preserves all observed asset bytes')

        boolean_version = deepcopy(current_manifest)
        boolean_version['format_version'] = True
        manifest_path.write_text(json.dumps(boolean_version), encoding='utf-8')
        boolean_version_target_bytes = {path.name: path.read_bytes()
                                         for path in updated['directory'].iterdir()}
        boolean_state, _boolean_manifest, _boolean_path = current_audio_asset(
            corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE,
            state_dir=self.state_dir)
        boolean_files_after = {path.name: path.read_bytes()
                               for path in updated['directory'].iterdir()}
        self.assertTrue(
            boolean_state == 'stale' and boolean_files_after == boolean_version_target_bytes,
            'boolean format_version must fail closed without changing any observed asset bytes; '
            f'actual state={boolean_state!r}, files_unchanged=' +
            str(boolean_files_after == boolean_version_target_bytes))

        manifest_path.write_text(json.dumps(current_manifest), encoding='utf-8')
        approved_manifest_bytes = manifest_path.read_bytes()
        partial_chapter = updated['directory'] / 'chapter-02.wav'
        complete_later_chapter = partial_chapter.read_bytes()
        partial_chapter.write_bytes(complete_later_chapter[:-2])
        partial_state, partial_manifest, partial_path = current_audio_asset(
            corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE,
            state_dir=self.state_dir)
        self.assertEqual((partial_state, partial_manifest, partial_path), ('stale', None, None),
                         'a partial later chapter cannot play with the current transcript')

        partial_chapter.write_bytes(complete_later_chapter)
        partial_chapter.unlink()
        surviving_asset_bytes = {path.name: path.read_bytes()
                                 for path in updated['directory'].iterdir()}
        missing_chapter_state, missing_chapter_manifest, missing_chapter_path = current_audio_asset(
            corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE,
            state_dir=self.state_dir)
        self.assertEqual((missing_chapter_state, missing_chapter_manifest, missing_chapter_path),
                         ('stale', None, None),
                         'a missing later chapter cannot make the current transcript playable')
        self.assertEqual({path.name: path.read_bytes()
                          for path in updated['directory'].iterdir()}, surviving_asset_bytes,
                         'missing-chapter refusal leaves all surviving asset files unchanged')

        partial_chapter.write_bytes(complete_later_chapter)
        restored_state, restored_manifest, restored_path = current_audio_asset(
            corrected, corrected['profile_id'], voice_name=DEFAULT_VOICE,
            state_dir=self.state_dir)
        self.assertEqual((restored_state, restored_path),
                         ('ready', updated['directory'] / 'episode.wav'))
        self.assertEqual(restored_manifest, current_manifest)
        self.assertEqual(manifest_path.read_bytes(), approved_manifest_bytes,
                         'restoring the missing chapter makes selection ready without rewriting the manifest')

        old_state, old_manifest, old_path = current_audio_asset(
            original, original['profile_id'], voice_name=DEFAULT_VOICE,
            state_dir=self.state_dir)
        self.assertEqual(old_state, 'ready')
        self.assertEqual(old_manifest['revision_id'], 'fictional-r1')
        self.assertEqual(old_path.read_bytes(), prior_bytes,
                         'bad offsets, hash mismatch, and partial audio leave prior audio intact')

    def test_segment_retries_are_bounded_and_incomplete_audio_is_not_published(self):
        episode = read_fixture()
        provider = FakeProvider(failures={1, 2, 3})
        with self.assertRaisesRegex(EpisodeAudioError, 'failed after 3 attempts'):
            render_episode_audio(episode, episode['profile_id'], provider=provider,
                                 state_dir=self.state_dir)
        self.assertEqual(len(provider.calls), 3)
        root = self.state_dir / 'episodes' / 'profiles' / episode['profile_id'] / 'audio' / episode['id']
        self.assertFalse(any(row.is_dir() and not row.name.startswith('.render-') for row in root.iterdir()))

    def test_provider_failure_logs_only_the_sanitized_tts_reason(self):
        episode = read_fixture()

        class SanitizedFailureProvider:
            provider_name = 'gemini'
            model = DEFAULT_MODEL

            def synthesize(self, _text, _voice_name):
                raise TtsProviderError('Gemini API returned HTTP 503.')

        with self.assertLogs('dev.ingest.episodes_audio', level='WARNING') as captured:
            with self.assertRaisesRegex(EpisodeAudioError, 'failed after 3 attempts'):
                render_episode_audio(episode, episode['profile_id'], provider=SanitizedFailureProvider(),
                                     state_dir=self.state_dir)
        self.assertTrue(any('Gemini API returned HTTP 503.' in row for row in captured.output))
        self.assertFalse(any('fixture-key' in row for row in captured.output))

    def test_episode_tts_cap_refusal_is_not_wrapped_as_episode_audio_error(self):
        episode = read_fixture()
        now = datetime.now(ZoneInfo('America/New_York'))
        ledger = UsageLedger(ledger_dir=Path(self.temp.name) / 'cap-ledger')
        for _ in range(70):
            ledger.record_search('perplexity', now=now)
        key_file = Path(self.temp.name) / 'fictional-key'
        key_file.write_text('fictional-fixture-key', encoding='utf-8')
        requests = []
        provider = GeminiTtsProvider(key_path=key_file,
                                    transport=lambda *_args, **_kwargs: requests.append(True),
                                    usage_ledger=ledger)
        with self.assertRaises(UsageCapReached):
            render_episode_audio(episode, episode['profile_id'], provider=provider,
                                 state_dir=self.state_dir, voice_file=self.voice_file)
        self.assertEqual(requests, [], 'cap refusal occurs before transport')

    def test_cost_and_provider_counters_include_successful_retry_attempts(self):
        episode = read_fixture()
        provider = FakeProvider(failures={1})
        result = render_episode_audio(episode, episode['profile_id'], provider=provider,
                                     state_dir=self.state_dir)
        self.assertEqual(result['provider_calls'], 3)
        self.assertEqual(result['manifest']['chapters'][0]['attempts'], 2)
        self.assertEqual(result['manifest']['provider_calls'], 3)

    def test_symlinked_episode_directory_cannot_escape_state_tree(self):
        episode = read_fixture()
        profile_audio = self.state_dir / 'episodes' / 'profiles' / episode['profile_id'] / 'audio'
        profile_audio.mkdir(parents=True)
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        (profile_audio / episode['id']).symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'outside its profile state directory'):
            render_episode_audio(episode, episode['profile_id'], provider=FakeProvider(),
                                 state_dir=self.state_dir)

    def test_cli_renders_fixture_to_temp_state_and_writes_output_json(self):
        voice_file = Path(self.temp.name) / 'voice.json'
        voice_file.write_text('{"voice":"Orus"}', encoding='utf-8')
        provider = FakeProvider()
        output_episode = Path(self.temp.name) / 'rendered.json'
        output = StringIO()
        with redirect_stdout(output):
            status = main([str(FIXTURE), '--profile-id', read_fixture()['profile_id'],
                           '--voice-file', str(voice_file), '--state-dir', str(self.state_dir),
                           '--episode-output', str(output_episode)],
                          provider_factory=lambda: provider)
        self.assertEqual(status, 0)
        self.assertIn('Voice: Orus', output.getvalue())
        self.assertIn('estimated cost:', output.getvalue())
        self.assertEqual(len(provider.calls), len(read_fixture()['chapters']))
        rendered = json.loads(output_episode.read_text(encoding='utf-8'))
        self.assertEqual(rendered['chapters'][1]['audio_start_seconds'], 0.1)

if __name__ == '__main__':
    unittest.main()
