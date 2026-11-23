import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
from contextlib import ExitStack
from copy import deepcopy
from decimal import Decimal
from io import BytesIO
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
from threading import Thread
import unittest
import wave
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dev import preview_server
from dev.ingest.corrections import CorrectionError, CorrectionService
from dev.ingest.daily_audio import daily_episode_from_edition
from dev.ingest.episodes_audio import EpisodeAudioError
from dev.ingest.episodes_audio import add_chapter_timestamps as _real_add_chapter_timestamps
from dev.ingest.episodes_audio import render_episode_audio
from dev.ingest.paths import profile_identifier
from dev.ingest.tts.base import TtsResult
from dev.ingest.tts.gemini import DEFAULT_MODEL


PROFILE = {'minutes': 8, 'topics': [{'id': 'fictional-town', 'label': 'Fictional town'}]}
EPISODE_ID = '1234567890abcdef1234567890abcdef'


class SyntheticTtsProvider:
    """Fixture provider identity with deterministic WAV bytes and no network."""
    provider_name = 'gemini'
    model = DEFAULT_MODEL

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def synthesize(self, text, voice_name):
        self.calls.append((text, voice_name))
        if self.fail:
            raise RuntimeError('fictional TTS failure')
        output = BytesIO()
        with wave.open(output, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(1000)
            wav_file.writeframes(b'\0\0' * 1000)
        return TtsResult(output.getvalue(), 'audio/wav', 1.0, len(text),
                         Decimal('0'), DEFAULT_MODEL, voice_name)


def edition_payload():
    return {'generated': True, 'edition': {
        'id': 'fictional-edition', 'kind': 'daily', 'revision_id': 'edition-r1',
        'sections': [{
            'id': 'fictional-story', 'heading': 'Town schedule update',
            'body': 'The fictional town changed its schedule.\n\nA second detail remains unchanged.',
            'audio_script': {'paragraphs': [
                {'text': 'The fictional town changed its schedule.'},
                {'text': 'A second detail remains unchanged.'},
            ]},
        }], 'corrections': [],
    }}


def episode_job():
    return {'job_id': EPISODE_ID, 'status': 'done', 'episode': {
        'id': EPISODE_ID, 'revision_id': 'episode-r1', 'title': 'Fictional episode',
        'chapters': [{'id': 'chapter-one', 'title': 'Schedule', 'sentences': [
            {'id': 'sentence-one', 'text': 'The fictional town changed its schedule.', 'source_numbers': [1]},
            {'id': 'sentence-two', 'text': 'A second detail remains unchanged.', 'source_numbers': [1]},
        ]}], 'sources': [{'id': 'source-one', 'outlet': 'Fictional Desk'}],
        'audio': {'state': 'recorded'},
    }}


class MemoryQueue:
    def __init__(self):
        self.calls = []

    def enqueue(self, profile, profile_id, job_id, episode, correction_id,
                *, previous_episode=None, chapter_id=None):
        self.calls.append((profile_id, job_id, deepcopy(episode), correction_id))

    def status(self, profile_id, correction_id, default='regenerating'):
        return 'regenerating'


class CorrectionsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), preview_server.Handler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.edition = edition_payload()
        self.job = episode_job()
        self.queue = MemoryQueue()
        self.service = CorrectionService(
            Path(self.temp.name),
            episode_reader=lambda _profile, job_id: deepcopy(self.job) if job_id == EPISODE_ID else None,
            edition_reader=lambda _profile: deepcopy(self.edition),
            audio_queue=self.queue,
        )
        self.patches = ExitStack()
        self.patches.enter_context(patch.object(preview_server, 'CORRECTIONS', self.service))
        self.patches.enter_context(patch.object(preview_server.episodes_audio, 'load_voice', return_value='fixture-voice'))
        self.patches.enter_context(patch.object(preview_server.episodes_audio, 'add_chapter_timestamps',
                                               side_effect=lambda job, *_a, **_k: job))
        self.patches.enter_context(patch.object(
            preview_server, 'profile_response', return_value=(200, {'profile': PROFILE})))
        self.patches.enter_context(patch.object(
            preview_server, 'edition_response', side_effect=lambda profile='active', **_kw: (200, deepcopy(self.edition))))
        self.patches.enter_context(patch.object(
            preview_server, 'EPISODE_JOBS', type('Jobs', (), {
                'get': lambda _self, _profile, job_id: deepcopy(self.job) if job_id == EPISODE_ID else None,
            })()))

    def tearDown(self):
        self.patches.close()
        self.temp.cleanup()

    def request(self, path, method='GET', payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        request = Request(self.base + path, data=body, method=method,
                          headers={'Content-Type': 'application/json'} if body is not None else {})
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def test_publication_lock_tracks_nested_modes_without_shared_upgrade(self):
        profile_id = profile_identifier(PROFILE)
        store = self.service.store

        with store.publication(profile_id, shared=True):
            with store.publication(profile_id, shared=True):
                self.assertTrue(store.publication_held(profile_id))
            with self.assertRaisesRegex(CorrectionError, 'upgrade'):
                with store.publication(profile_id):
                    self.fail('shared publication lock must not bypass exclusive acquisition')

        with store.publication(profile_id):
            with store.publication(profile_id, shared=True):
                self.assertTrue(store.publication_held(profile_id))

    def test_story_correction_is_readable_from_edition_and_listed_without_text_leak(self):
        old = 'The fictional town changed its schedule.'
        new = 'The fictional town changed its spring schedule.'
        status, created = self.request('/api/corrections', 'POST', {
            'target_type': 'story', 'target_id': 'fictional-story',
            'edition_id': 'fictional-edition', 'paragraph_index': 0,
            'old_text': old, 'new_text': new, 'what_changed': 'The timing was clarified.',
        })
        self.assertEqual(status, 201)
        self.assertNotIn('new_text', created['correction'])
        self.assertEqual(created['edition']['sections'][0]['body'].split('\n\n')[0], new)

        status, response = self.request('/api/edition?profile=active&history=0')
        self.assertEqual(status, 200)
        story = response['edition']['sections'][0]
        self.assertEqual(story['body'].split('\n\n')[0], new)
        self.assertEqual(story['audio_status'], 'regenerating')
        self.assertTrue(response['edition']['updated'])
        self.assertTrue(response['edition']['corrected'])
        self.assertEqual(response['edition']['corrected_at'], created['correction']['corrected_at'])
        self.assertEqual(response['edition']['correction_note'], 'The timing was clarified.')
        self.assertEqual(response['edition']['audio_status'], 'regenerating')
        self.assertTrue(story['corrected'])
        self.assertEqual(story['corrected_at'], created['correction']['corrected_at'])
        self.assertEqual(story['correction_note'], 'The timing was clarified.')

        status, old = self.request('/api/revisions/edition/fictional-edition/edition-r1')
        self.assertEqual(status, 200)
        self.assertEqual(old['edition']['sections'][0]['body'].split('\n\n')[0],
                         'The fictional town changed its schedule.')
        self.assertFalse(old['playback_allowed'])
        self.assertEqual(old['edition']['audio']['state'], 'stale')
        status, missing = self.request('/api/revisions/edition/fictional-edition/missing')
        self.assertEqual((status, missing['error']), (404, 'revision_not_found'))

        status, response = self.request('/api/corrections?target_type=story&target_id=fictional-story')
        self.assertEqual(status, 200)
        self.assertEqual(len(response['corrections']), 1)
        self.assertEqual(response['corrections'][0]['what_changed'], 'The timing was clarified.')
        self.assertNotIn('new_text', response['corrections'][0])
        for private_field in ('id', 'profile_id', 'old_text_sha256', 'new_text_sha256', 'evidence_sha256', 'source_ids'):
            self.assertNotIn(private_field, response['corrections'][0])
        self.assertEqual(response['corrections'][0]['target_id'], 'fictional-story')
        self.assertEqual(response['corrections'][0]['target_type'], 'story')
        self.assertEqual(response['corrections'][0]['audio_status'], 'regenerating')

        with patch.object(preview_server.episodes_audio, 'current_audio_asset',
                          return_value=('stale', None, None)):
            status, audio = self.request('/api/daily/audio')
        self.assertEqual(status, 409)
        self.assertEqual(audio['error'], 'audio_stale')
        self.assertEqual(audio['audio_status'], 'superseded')

    def test_active_daily_audio_requires_current_corrected_manifest_identity(self):
        audio_state = Path(self.temp.name) / 'daily-audio-state'
        story = self.edition['edition']['sections'][0]
        story['cluster_id'] = 'fictional-story-cluster'
        story['audio_script'] = {'text': story['body']}
        old_episode = daily_episode_from_edition(self.edition['edition'])
        old_render = render_episode_audio(
            old_episode, profile_identifier(PROFILE), provider=SyntheticTtsProvider(),
            voice_name='Kore', state_dir=audio_state)
        old_manifest = old_render['manifest']
        self.assertEqual(old_manifest['revision_id'], 'edition-r1')
        self.edition['edition']['audio'] = {
            'audio_status': 'ready', 'url': '/api/daily/audio',
            'revision_id': old_manifest['revision_id'], 'script_hash': old_manifest['script_hash'],
            'voice_name': 'Kore', 'duration_seconds': old_manifest['duration_seconds'],
        }

        with patch.object(preview_server, 'EPISODE_AUDIO_STATE_DIR', audio_state), \
                patch.object(preview_server.episodes_audio, 'load_voice', return_value='Kore'):
            status, created = self.request('/api/corrections', 'POST', {
                'target_type': 'story', 'target_id': 'fictional-story',
                'edition_id': 'fictional-edition', 'paragraph_index': 0,
                'old_text': 'The fictional town changed its schedule.',
                'new_text': 'The fictional town changed its spring schedule.',
                'what_changed': 'The fictional timing was clarified.',
            })
            self.assertEqual(status, 201)
            corrected_revision = created['edition']['revision_id']
            self.assertNotEqual(corrected_revision, old_manifest['revision_id'])

            status, pending = self.request('/api/edition?profile=active&history=0')
            self.assertEqual(status, 200)
            self.assertEqual(pending['edition']['revision_id'], corrected_revision)
            self.assertIn('spring schedule', pending['edition']['sections'][0]['body'])
            self.assertEqual(pending['edition']['audio_status'], 'regenerating')

            status, stale = self.request('/api/daily/audio')
            self.assertEqual(status, 409)
            self.assertEqual(stale['error'], 'audio_stale')
            self.assertEqual(stale['audio_status'], 'superseded')

            current_episode = daily_episode_from_edition(pending['edition'])
            self.assertIn('spring schedule', current_episode['chapters'][0]['spoken_text'])
            current_render = render_episode_audio(
                current_episode, profile_identifier(PROFILE), provider=SyntheticTtsProvider(),
                voice_name='Kore', state_dir=audio_state)
            current_manifest = current_render['manifest']
            self.assertEqual(current_manifest['revision_id'], corrected_revision)
            self.assertNotEqual(current_manifest['script_hash'], old_manifest['script_hash'])

            def add_current_timestamps(job, profile_id, *, voice_name, **_kwargs):
                return _real_add_chapter_timestamps(
                    job, profile_id, voice_name=voice_name, state_dir=audio_state)

            with patch.object(self.queue, 'status', return_value='recorded'), \
                    patch.object(preview_server.episodes_audio, 'add_chapter_timestamps',
                                 side_effect=add_current_timestamps):
                status, active = self.request('/api/edition?profile=active&history=0')
                self.assertEqual(status, 200)
                active_edition = active['edition']
                self.assertEqual(active_edition['revision_id'], current_manifest['revision_id'])
                self.assertEqual(active_edition['audio']['revision_id'], current_manifest['revision_id'])
                self.assertEqual(active_edition['audio']['script_hash'], current_manifest['script_hash'])
                self.assertEqual(active_edition['audio']['audio_status'], 'ready')

                with urlopen(self.base + '/api/daily/audio', timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get('Content-Type'), 'audio/wav')
                    self.assertTrue(response.read().startswith(b'RIFF'))

                manifest_path = Path(current_render['directory']) / 'manifest.json'
                for field, wrong_value in (
                    ('revision_id', old_manifest['revision_id']),
                    ('script_hash', old_manifest['script_hash']),
                ):
                    with self.subTest(mismatched_field=field):
                        mismatched = deepcopy(current_manifest)
                        mismatched[field] = wrong_value
                        manifest_path.write_text(json.dumps(mismatched), encoding='utf-8')
                        status, body = self.request('/api/edition?profile=active&history=0')
                        self.assertEqual(status, 200)
                        self.assertNotEqual(body['edition']['audio_status'], 'ready')
                        self.assertNotIn('url', body['edition']['audio'])
                        status, body = self.request('/api/daily/audio')
                        self.assertEqual(status, 409)
                        self.assertEqual(body['error'], 'audio_stale')
                        self.assertEqual(body['audio_status'], 'superseded')
                        manifest_path.write_text(json.dumps(current_manifest), encoding='utf-8')

    def test_active_edition_keeps_corrected_text_when_current_tts_fails(self):
        audio_state = Path(self.temp.name) / 'failed-daily-audio-state'
        story = self.edition['edition']['sections'][0]
        story['cluster_id'] = 'fictional-story-cluster'
        story['audio_script'] = {'text': story['body']}
        old_episode = daily_episode_from_edition(self.edition['edition'])
        old_render = render_episode_audio(
            old_episode, profile_identifier(PROFILE), provider=SyntheticTtsProvider(),
            voice_name='Kore', state_dir=audio_state)
        old_manifest = old_render['manifest']
        self.edition['edition']['audio'] = {
            'audio_status': 'ready', 'url': '/api/daily/audio',
            'revision_id': old_manifest['revision_id'], 'script_hash': old_manifest['script_hash'],
            'voice_name': 'Kore', 'duration_seconds': old_manifest['duration_seconds'],
        }

        with patch.object(preview_server, 'EPISODE_AUDIO_STATE_DIR', audio_state), \
                patch.object(preview_server.episodes_audio, 'load_voice', return_value='Kore'):
            status, created = self.request('/api/corrections', 'POST', {
                'target_type': 'story', 'target_id': 'fictional-story',
                'edition_id': 'fictional-edition', 'paragraph_index': 0,
                'old_text': 'The fictional town changed its schedule.',
                'new_text': 'The fictional town changed its spring schedule.',
                'what_changed': 'The fictional timing was clarified.',
            })
            self.assertEqual(status, 201)
            corrected_revision = created['edition']['revision_id']

            status, corrected = self.request('/api/edition?profile=active&history=0')
            self.assertEqual(status, 200)
            self.assertEqual(corrected['edition']['revision_id'], corrected_revision)
            self.assertIn('spring schedule', corrected['edition']['sections'][0]['body'])
            self.assertEqual(corrected['edition']['audio_status'], 'regenerating')

            current_episode = daily_episode_from_edition(corrected['edition'])
            failing_provider = SyntheticTtsProvider(fail=True)
            with self.assertRaises(EpisodeAudioError):
                render_episode_audio(
                    current_episode, profile_identifier(PROFILE), provider=failing_provider,
                    voice_name='Kore', state_dir=audio_state)
            self.assertGreater(len(failing_provider.calls), 0)
            state, manifest, audio_path = preview_server.episodes_audio.current_audio_asset(
                current_episode, profile_identifier(PROFILE), voice_name='Kore', state_dir=audio_state)
            self.assertEqual((state, manifest, audio_path), ('stale', None, None))

            with patch.object(self.queue, 'status', return_value='failed'):
                status, available = self.request('/api/edition?profile=active&history=0')
                self.assertEqual(status, 200)
                current = available['edition']
                self.assertEqual(current['revision_id'], corrected_revision)
                self.assertIn('spring schedule', current['sections'][0]['body'])
                self.assertEqual(current['audio_status'], 'failed')
                self.assertEqual(current['audio']['audio_status'], 'failed')
                self.assertEqual(current['audio']['state'], 'superseded')
                for field in ('url', 'audio_url', 'revision_id', 'script_hash',
                               'duration_seconds', 'chapter_offsets'):
                    self.assertNotIn(field, current['audio'])

                status, unavailable = self.request('/api/daily/audio')
                self.assertEqual(status, 409)
                self.assertEqual(unavailable['error'], 'audio_stale')
                self.assertEqual(unavailable['audio_status'], 'superseded')

    def test_episode_correction_queues_mock_and_audio_route_refuses_stale_revision(self):
        status, created = self.request('/api/corrections', 'POST', {
            'target_type': 'episode', 'target_id': EPISODE_ID,
            'chapter_id': 'chapter-one', 'sentence_index': 0,
            'old_text': 'The fictional town changed its schedule.',
            'new_text': 'The fictional town changed its spring schedule.',
            'what_changed': 'The timing was clarified.',
        })
        self.assertEqual(status, 201)
        self.assertEqual(created['audio_status'], 'regenerating')
        self.assertEqual(len(self.queue.calls), 1)
        self.assertEqual(self.queue.calls[0][2]['chapters'][0]['sentences'][0]['text'],
                         'The fictional town changed its spring schedule.')

        status, job = self.request(f'/api/episodes/{EPISODE_ID}')
        self.assertEqual(status, 200)
        self.assertEqual(job['episode']['chapters'][0]['sentences'][0]['text'],
                         'The fictional town changed its spring schedule.')
        self.assertEqual(job['audio_status'], 'regenerating')
        self.assertEqual(job['corrected_at'], created['correction']['corrected_at'])
        self.assertEqual(job['correction_note'], 'The timing was clarified.')
        self.assertEqual(job['episode']['corrected_at'], created['correction']['corrected_at'])
        self.assertEqual(job['episode']['audio_status'], 'regenerating')
        self.assertEqual(job['episode']['chapters'][0]['audio_status'], 'regenerating')
        self.assertTrue(job['episode']['corrected'])
        self.assertEqual(job['episode']['correction_note'], 'The timing was clarified.')
        status, old = self.request(f'/api/revisions/episode/{EPISODE_ID}/episode-r1')
        self.assertEqual(status, 200)
        self.assertIn('changed its schedule', old['episode']['chapters'][0]['sentences'][0]['text'])
        self.assertFalse(old['playback_allowed'])

        status, response = self.request(f'/api/episodes/{EPISODE_ID}/audio')
        self.assertEqual(status, 409)
        self.assertEqual(response['error'], 'audio_stale')
        self.assertEqual(response['audio_status'], 'regenerating')

    def test_malformed_and_stale_correction_requests_are_rejected(self):
        status, response = self.request('/api/corrections', 'POST', {'target_type': 'story'})
        self.assertEqual(status, 400)
        self.assertEqual(response['error'], 'invalid_correction')
        payload = {'target_type': 'story', 'target_id': 'fictional-story',
                   'edition_id': 'fictional-edition', 'paragraph_index': 0,
                   'old_text': 'outdated text', 'new_text': 'replacement text',
                   'what_changed': 'The text was clarified.'}
        status, response = self.request('/api/corrections', 'POST', payload)
        self.assertEqual(status, 409)
        self.assertEqual(response['error'], 'correction_revision_conflict')

    def test_corrections_routes_reject_non_loopback_clients(self):
        with patch.object(preview_server.api_access, 'is_api_request_allowed', return_value=False):
            status, response = self.request('/api/profile/export')
            self.assertEqual(status, 403)
            self.assertEqual(response['error'], 'local_only')
            status, response = self.request('/api/corrections', 'POST', {})
            self.assertEqual(status, 403)
            self.assertEqual(response['error'], 'local_only')

    def test_corrections_list_is_public_safe_while_post_requires_loopback(self):
        self.service.create(PROFILE, {
            'target_type': 'story', 'target_id': 'fictional-story',
            'edition_id': 'fictional-edition', 'paragraph_index': 0,
            'old_text': 'The fictional town changed its schedule.',
            'new_text': 'The fictional town changed its spring schedule.',
            'what_changed': 'The timing was clarified.',
        })
        access = lambda method, _path, _ip: method == 'GET'
        with patch.object(preview_server.api_access, 'is_api_request_allowed', side_effect=access):
            status, response = self.request('/api/corrections')
            self.assertEqual(status, 200)
            self.assertEqual(len(response['corrections']), 1)
            status, response = self.request('/api/corrections', 'POST', {})
            self.assertEqual(status, 403)
            self.assertEqual(response['error'], 'local_only')


if __name__ == '__main__':
    unittest.main()
