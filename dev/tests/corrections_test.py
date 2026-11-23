import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch
import wave

from dev.ingest.corrections import (CorrectionAudioQueue, CorrectionError,
                                    CorrectionService, CorrectionStore, is_loopback_address)
from dev.ingest.episodes_audio import (DEFAULT_VOICE, current_audio_asset,
                                       render_episode_audio)
from dev.ingest.paths import profile_identifier
from dev.ingest.tts import TtsResult
from dev.ingest.tts.gemini import DEFAULT_MODEL


PROFILE = {'minutes': 12, 'topics': [{'id': 'fictional-topic', 'label': 'Fictional topic'}]}
EPISODE_ID = '1234567890abcdef1234567890abcdef'


def episode_job():
    return {'job_id': EPISODE_ID, 'status': 'done', 'episode': {
        'id': EPISODE_ID, 'revision_id': 'episode-r1', 'title': 'Fictional episode',
        'audio': {'state': 'recorded', 'file': 'episode.wav'},
        'chapters': [{'id': 'chapter-one', 'title': 'The update', 'sentences': [
            {'id': 's1', 'text': 'The fictional town changed its schedule.', 'source_numbers': [1]},
            {'id': 's2', 'text': 'The next review is planned for spring.', 'source_numbers': [1]},
        ]}], 'sources': [{'id': 'fictional-source', 'outlet': 'Fictional Desk'}],
    }}


def edition_payload():
    return {'generated': True, 'sources': [], 'edition': {
        'id': 'fictional-edition', 'kind': 'daily', 'revision_id': 'edition-r1',
        'audio': {'state': 'recorded'}, 'sections': [{
            'id': 'story-one', 'cluster_id': 'cluster-one', 'heading': 'A fictional update',
            'body': 'The fictional town changed its schedule.\n\nA second paragraph remains unchanged.',
            'audio_script': {'paragraphs': [
                {'section': 'what_happened', 'text': 'The fictional town changed its schedule.'},
                {'section': 'why_it_matters', 'text': 'A second paragraph remains unchanged.'},
            ]},
        }], 'corrections': [],
    }}


class FixedQueue:
    def __init__(self):
        self.rows = {}
        self.calls = []

    def enqueue(self, profile, profile_id, job_id, episode, correction_id,
                *, previous_episode=None, chapter_id=None):
        self.calls.append((profile_id, job_id, deepcopy(episode),
                           deepcopy(previous_episode), chapter_id))
        self.rows[(profile_id, correction_id)] = 'regenerating'

    def status(self, profile_id, correction_id, default='regenerating'):
        return self.rows.get((profile_id, correction_id), default)


def fixture_wav():
    output = BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(1000)
        audio.writeframes(b'\0\0' * 100)
    return output.getvalue()


class FictionalTtsProvider:
    provider_name = 'fictional-fixture'
    model = DEFAULT_MODEL

    def __init__(self, *, started=None, release=None):
        self.started = started
        self.release = release
        self.calls = []

    def synthesize(self, text, voice_name):
        self.calls.append(text)
        if self.started is not None:
            self.started.set()
            if not self.release.wait(timeout=5):
                raise RuntimeError('fictional delayed TTS fixture timed out')
        return TtsResult(fixture_wav(), 'audio/wav', 0.1, len(text),
                         Decimal('0.0001'), DEFAULT_MODEL, voice_name)


class CorrectionServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.job = episode_job()
        self.edition = edition_payload()
        self.queue = FixedQueue()
        self.service = CorrectionService(
            self.root,
            episode_reader=lambda _profile, _job_id: deepcopy(self.job),
            edition_reader=lambda _profile: deepcopy(self.edition),
            audio_queue=self.queue,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_episode_correction_appends_hashes_marks_chapter_stale_and_queues_mock_tts(self):
        payload = {'target_type': 'episode', 'target_id': EPISODE_ID,
                   'chapter_id': 'chapter-one', 'sentence_index': 0,
                   'old_text': 'The fictional town changed its schedule.',
                   'new_text': 'The fictional town changed its spring schedule.',
                   'what_changed': 'The schedule now includes the spring date.'}
        created = self.service.create(PROFILE, payload)
        correction = created['correction']
        self.assertEqual(correction['audio_status'], 'regenerating')
        self.assertEqual(correction['old_text_sha256'], sha256(payload['old_text'].encode()).hexdigest())
        self.assertEqual(correction['new_text_sha256'], sha256(payload['new_text'].encode()).hexdigest())
        self.assertNotIn('new_text', correction)
        self.assertEqual(created['audio_status'], 'regenerating')
        corrected = created['episode']
        self.assertEqual(corrected['chapters'][0]['sentences'][0]['text'], payload['new_text'])
        self.assertEqual(corrected['chapters'][0]['spoken_text'],
                         payload['new_text'] + ' The next review is planned for spring.')
        self.assertEqual(corrected['chapters'][0]['audio_status'], 'regenerating')
        self.assertTrue(corrected['updated'])
        self.assertNotEqual(corrected['revision_id'], 'episode-r1')
        self.assertEqual(len(self.queue.calls), 1)
        self.assertEqual(self.queue.calls[0][2]['chapters'][0]['spoken_text'], corrected['chapters'][0]['spoken_text'])
        self.assertEqual(self.queue.calls[0][3]['revision_id'], 'episode-r1')
        self.assertEqual(self.queue.calls[0][4], 'chapter-one')
        log_path = next((self.root / 'corrections').rglob('episode-*.jsonl'))
        self.assertEqual(len(log_path.read_text().splitlines()), 1)
        self.assertIn(payload['new_text'], log_path.read_text())

    def test_episode_corrections_replay_immutably_and_reject_stale_old_text(self):
        self.service.create(PROFILE, {'target_type': 'episode', 'target_id': EPISODE_ID,
            'chapter_id': 'chapter-one', 'sentence_index': 0,
            'old_text': 'The fictional town changed its schedule.',
            'new_text': 'The fictional town updated its schedule.', 'what_changed': 'The date was clarified.'})
        second = self.service.create(PROFILE, {'target_type': 'episode', 'target_id': EPISODE_ID,
            'chapter_id': 'chapter-one', 'sentence_index': 0,
            'old_text': 'The fictional town updated its schedule.',
            'new_text': 'The fictional town updated its spring schedule.', 'what_changed': 'The timing was clarified.'})
        self.assertEqual(second['episode']['chapters'][0]['sentences'][0]['text'],
                         'The fictional town updated its spring schedule.')
        self.assertEqual(len(self.service.list(PROFILE, target_type='episode')), 2)
        previous = self.service.revision(PROFILE, 'episode', EPISODE_ID, 'episode-r1')
        self.assertEqual(previous['chapters'][0]['sentences'][0]['text'],
                         'The fictional town changed its schedule.')
        self.assertEqual(previous['audio']['state'], 'recorded')
        with self.assertRaisesRegex(CorrectionError, 'has changed'):
            self.service.create(PROFILE, {'target_type': 'episode', 'target_id': EPISODE_ID,
                'chapter_id': 'chapter-one', 'sentence_index': 0,
                'old_text': 'The fictional town changed its schedule.',
                'new_text': 'Incorrect stale replacement.', 'what_changed': 'Stale correction.'})
        path = next((self.root / 'corrections').rglob('episode-*.jsonl'))
        self.assertEqual(len(path.read_text().splitlines()), 2)

    def test_story_correction_updates_readable_and_spoken_text_and_invalidates_edition_audio(self):
        created = self.service.create(PROFILE, {'target_type': 'story', 'target_id': 'story-one',
            'edition_id': 'fictional-edition', 'paragraph_index': 0,
            'old_text': 'The fictional town changed its schedule.',
            'new_text': 'The fictional town changed its spring schedule.',
            'what_changed': 'The schedule was clarified.',
            'why_it_matters': 'The earlier wording did not include the spring date.'})
        edition = created['edition']
        self.assertEqual(edition['sections'][0]['body'].split('\n\n')[0],
                         'The fictional town changed its spring schedule.')
        self.assertEqual(edition['sections'][0]['audio_script']['paragraphs'][0]['text'],
                         'The fictional town changed its spring schedule.')
        self.assertTrue(edition['updated'])
        self.assertTrue(edition['sections'][0]['updated'])
        self.assertEqual(edition['audio']['state'], 'superseded')
        self.assertEqual(edition['audio_status'], 'regenerating')
        self.assertEqual(edition['corrections'][0]['severity'], 'material')
        self.assertEqual(edition['corrections'][0]['previous_revision_id'], 'edition-r1')
        self.assertEqual(len(self.queue.calls), 1)
        self.assertEqual(self.queue.calls[0][4], 'story-one')
        self.assertEqual(self.queue.calls[0][3]['revision_id'], 'edition-r1')
        replayed = self.service.apply_edition(deepcopy(self.edition), PROFILE)['edition']
        self.assertEqual(replayed['sections'][0]['body'], edition['sections'][0]['body'])

    def test_daily_audio_status_becomes_ready_only_for_verified_current_manifest(self):
        self.edition['edition']['chapters'] = [{'id': 'story-one', 'audio_start_seconds': 42}]
        self.service.create(PROFILE, {'target_type': 'story', 'target_id': 'story-one',
            'edition_id': 'fictional-edition', 'paragraph_index': 0,
            'old_text': 'The fictional town changed its schedule.',
            'new_text': 'The fictional town changed its spring schedule.',
            'what_changed': 'The schedule was clarified.'})
        correction_id = self.service.list(PROFILE, target_type='story')[0]['id']
        self.queue.rows[(profile_identifier(PROFILE), correction_id)] = 'recorded'
        stamped = {'audio_state': 'recorded', 'episode': {
            'chapters': [{'id': 'story-one', 'audio_start_seconds': 2.5,
                          'audio_duration_seconds': 15.0}],
            'audio': {'state': 'recorded', 'duration_seconds': 30.0,
                      'url': '/api/episodes/fictional-edition/audio'}}}
        with patch('dev.ingest.episodes_audio.load_voice', return_value='FixtureVoice'), \
             patch('dev.ingest.episodes_audio.add_chapter_timestamps', return_value=stamped):
            edition = self.service.apply_edition(deepcopy(self.edition), PROFILE)['edition']
        self.assertEqual(edition['audio_status'], 'ready')
        self.assertEqual(edition['audio']['url'], '/api/daily/audio')
        self.assertEqual(edition['chapters'][0]['audio_start_seconds'], 2.5)
        self.assertEqual(edition['chapters'][0]['audio_duration_seconds'], 15.0)
        self.assertEqual(edition['sections'][0]['audio_status'], 'recorded')

    def test_validation_rejects_unknown_fields_empty_changes_and_path_traversal(self):
        good = {'target_type': 'episode', 'target_id': EPISODE_ID,
                'chapter_id': 'chapter-one', 'sentence_index': 0,
                'old_text': 'old', 'new_text': 'new', 'what_changed': 'changed'}
        for payload in ({**good, 'path': '../../secret'}, {**good, 'target_id': '../secret'},
                        {**good, 'what_changed': ''}, {**good, 'sentence_index': True}):
            with self.subTest(payload=payload), self.assertRaises(CorrectionError):
                self.service.create(PROFILE, payload)
        self.assertEqual(self.service.list(PROFILE), [])

    def test_correction_store_rejects_symlinked_log(self):
        store = CorrectionStore(self.root)
        profile_id = 'a' * 24
        directory = store._profile_dir(profile_id, create=True)
        outside = self.root / 'outside.jsonl'
        outside.write_text('')
        (directory / f'episode-{EPISODE_ID}.jsonl').symlink_to(outside)
        with self.assertRaisesRegex(CorrectionError, 'unavailable'):
            store.append({'profile_id': profile_id, 'target_type': 'episode',
                          'target_id': EPISODE_ID, 'id': 'f' * 32})
        self.assertEqual(outside.read_text(), '')

    def test_audio_queue_uses_injected_renderer_and_records_result_without_provider(self):
        store = CorrectionStore(self.root)
        calls = []
        queue = CorrectionAudioQueue(store, renderer=lambda episode, profile_id: calls.append((episode, profile_id)),
                                     start_worker=False)
        correction_id = 'f' * 32
        queue.enqueue(PROFILE, 'a' * 24, EPISODE_ID, episode_job()['episode'], correction_id)
        self.assertEqual(queue.status('a' * 24, correction_id), 'recorded')
        self.assertEqual(len(calls), 1)

    def test_late_r2_audio_result_stays_revision_bound_after_r3_correction(self):
        profile_id = profile_identifier(PROFILE)
        audio_state = self.root / 'fictional-audio'
        job = episode_job()
        r1 = job['episode']
        r1['chapters'][0]['spoken_text'] = ' '.join(
            row['text'] for row in r1['chapters'][0]['sentences'])
        r1_audio = render_episode_audio(
            r1, profile_id, provider=FictionalTtsProvider(), voice_name=DEFAULT_VOICE,
            state_dir=audio_state)
        job['episode'] = r1_audio['episode']

        old_tts_started = Event()
        release_old_tts = Event()
        old_provider = FictionalTtsProvider(started=old_tts_started, release=release_old_tts)
        current_provider = FictionalTtsProvider()
        workers = []
        rendered = {}
        slow_revision = {'id': None}

        class DeferredWorker:
            def __init__(self, target, args):
                self.target = target
                self.args = args

            def start(self):
                workers.append(self)

            def run(self):
                self.target(*self.args)

        def defer_worker(target, args, **_kwargs):
            return DeferredWorker(target, args)

        def render_correction(episode, _profile_id):
            provider = old_provider if episode['revision_id'] == slow_revision['id'] else current_provider
            rendered[episode['revision_id']] = render_episode_audio(
                episode, profile_id, provider=provider, voice_name=DEFAULT_VOICE,
                state_dir=audio_state)

        queue = CorrectionAudioQueue(
            CorrectionStore(self.root), renderer=render_correction,
            thread_factory=defer_worker)
        service = CorrectionService(
            self.root, episode_reader=lambda _profile, _job_id: deepcopy(job),
            audio_queue=queue)

        r2_result = service.create(PROFILE, {
            'target_type': 'episode', 'target_id': EPISODE_ID,
            'chapter_id': 'chapter-one', 'sentence_index': 0,
            'old_text': 'The fictional town changed its schedule.',
            'new_text': 'The fictional town revised its spring schedule.',
            'what_changed': 'The schedule was corrected.',
        })
        r2 = r2_result['episode']
        slow_revision['id'] = r2['revision_id']
        self.assertEqual(len(workers), 1)

        r3_result = service.create(PROFILE, {
            'target_type': 'episode', 'target_id': EPISODE_ID,
            'chapter_id': 'chapter-one', 'sentence_index': 0,
            'old_text': 'The fictional town revised its spring schedule.',
            'new_text': 'The fictional town set a summer schedule.',
            'what_changed': 'The schedule was corrected again.',
        })
        r3 = r3_result['episode']
        self.assertEqual(len(workers), 2)

        old_worker = Thread(target=workers[0].run)
        old_worker.start()
        try:
            self.assertTrue(old_tts_started.wait(timeout=5), 'r2 TTS should pause before returning its old WAV')
        finally:
            release_old_tts.set()
            old_worker.join(timeout=10)

        self.assertFalse(old_worker.is_alive(), 'the delayed r2 worker must finish within the fixture')
        self.assertEqual(queue.status(profile_id, r2_result['correction']['id']), 'recorded')
        self.assertEqual(queue.status(profile_id, r3_result['correction']['id']), 'regenerating')

        current_job = service.apply_job(job, PROFILE)
        current_episode = current_job['episode']
        self.assertEqual(current_episode['revision_id'], r3['revision_id'])
        self.assertEqual(current_job['audio_status'], 'regenerating',
                         'a completed r2 job cannot mark the newer r3 job ready')
        self.assertNotIn('url', current_episode['audio'])
        stale_state, stale_manifest, stale_path = current_audio_asset(
            current_episode, profile_id, voice_name=DEFAULT_VOICE, state_dir=audio_state,
            provider_name=FictionalTtsProvider.provider_name, model_name=DEFAULT_MODEL)
        self.assertEqual((stale_state, stale_manifest, stale_path), ('stale', None, None),
                         'the late r2 WAV is unavailable to the active r3 transcript')

        workers[1].run()
        self.assertEqual(queue.status(profile_id, r3_result['correction']['id']), 'recorded')

        current_job = service.apply_job(job, PROFILE)
        current_episode = current_job['episode']
        self.assertEqual(current_job['audio_status'], 'recorded')
        self.assertEqual(current_episode['revision_id'], r3['revision_id'])
        self.assertEqual(current_episode['chapters'][0]['sentences'][0]['text'],
                         'The fictional town set a summer schedule.')
        current_state, current_manifest, current_path = current_audio_asset(
            current_episode, profile_id, voice_name=DEFAULT_VOICE, state_dir=audio_state,
            provider_name=FictionalTtsProvider.provider_name, model_name=DEFAULT_MODEL)
        self.assertEqual(current_state, 'ready')
        self.assertEqual(current_manifest['revision_id'], r3['revision_id'])
        self.assertEqual(current_episode['script_hash'], current_manifest['script_hash'])
        self.assertEqual(current_manifest['script_hash'], rendered[r3['revision_id']]['manifest']['script_hash'])
        self.assertEqual(current_path, rendered[r3['revision_id']]['directory'] / 'episode.wav')

        old_state, old_manifest, old_path = current_audio_asset(
            r2, profile_id, voice_name=DEFAULT_VOICE, state_dir=audio_state,
            provider_name=FictionalTtsProvider.provider_name, model_name=DEFAULT_MODEL)
        self.assertEqual(old_state, 'ready', 'the late result remains tied to its immutable r2 transcript')
        self.assertEqual(old_manifest['revision_id'], r2['revision_id'])
        self.assertEqual(old_manifest['script_hash'], rendered[r2['revision_id']]['manifest']['script_hash'])
        self.assertNotEqual(old_path, current_path,
                            'the active r3 transcript cannot replay the late r2 recording')
        r1_state, r1_manifest, r1_path = current_audio_asset(
            r1, profile_id, voice_name=DEFAULT_VOICE, state_dir=audio_state,
            provider_name=FictionalTtsProvider.provider_name, model_name=DEFAULT_MODEL)
        self.assertEqual(r1_state, 'ready')
        self.assertEqual(r1_manifest['revision_id'], 'episode-r1')
        self.assertEqual(r1_path, r1_audio['directory'] / 'episode.wav')

    def test_selective_queue_estimates_before_worker_and_keeps_cost_cap_refusal_offline(self):
        store = CorrectionStore(self.root)
        profile_id = 'a' * 24
        correction_id = 'e' * 32
        job = episode_job()['episode']
        prior = deepcopy(job)
        job['revision_id'] = 'episode-r2'
        rendered = []
        estimates = []
        queue = CorrectionAudioQueue(
            store, chapter_renderer=lambda *args, **kwargs: rendered.append((args, kwargs)),
            cost_estimator=lambda _episode, chapter: estimates.append(chapter) or {
                'chapter_id': chapter, 'character_count': 123,
                'estimated_duration_seconds': 10, 'estimated_cost_usd': '0.00012345',
                'basis': 'fixture prices'}, start_worker=False)
        queue.enqueue(PROFILE, profile_id, EPISODE_ID, job, correction_id,
                      previous_episode=prior, chapter_id='chapter-one')
        path = queue._status_path(profile_id, correction_id)
        status = json.loads(path.read_text())
        self.assertEqual(estimates, ['chapter-one'])
        self.assertEqual(status['cost_estimate']['estimated_cost_usd'], '0.00012345')
        self.assertEqual(status['status'], 'recorded')
        self.assertEqual(len(rendered), 1)
        self.assertEqual(rendered[0][1]['previous_episode']['revision_id'], 'episode-r1')
        self.assertEqual(rendered[0][1]['chapter_id'], 'chapter-one')

        refused_id = 'f' * 32
        queue.cost_estimator = lambda *_args: (_ for _ in ()).throw(RuntimeError('cost_cap_reached'))
        queue.enqueue(PROFILE, profile_id, EPISODE_ID, job, refused_id,
                      previous_episode=prior, chapter_id='chapter-one')
        refused = json.loads(queue._status_path(profile_id, refused_id).read_text())
        self.assertEqual(refused['status'], 'failed')
        self.assertEqual(refused['error'], 'cost_cap_reached')
        self.assertEqual(len(rendered), 1, 'cap refusal must happen before the TTS worker')

    def test_loopback_auth_boundary(self):
        self.assertTrue(is_loopback_address('127.0.0.1'))
        self.assertTrue(is_loopback_address('::1'))
        self.assertFalse(is_loopback_address('192.0.2.3'))


if __name__ == '__main__':
    unittest.main()
