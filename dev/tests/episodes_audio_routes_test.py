import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
import wave
import base64
from copy import deepcopy
from contextlib import ExitStack
from decimal import Decimal
import hashlib
from http.server import ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
import tempfile
import subprocess
import textwrap
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dev import preview_server
from dev.ingest.corrections import CorrectionService
from dev.ingest.episodes_audio import (DEFAULT_VOICE, approve_bounded_audio_candidate,
                                       render_episode_audio)
from dev.ingest.tts.gemini import GeminiTtsProvider
from dev.ingest.jobs import EpisodeJobStore, fake_episode
from dev.ingest.paths import profile_identifier
from dev.tests.episodes_audio_test import (CorrectionDurationProvider, FakeProvider,
                                            read_fixture, wav_bytes)
from dev.ingest.usage import UsageLedger


PROFILE = {'minutes': 12, 'topics': [{'id': 'payments', 'label': 'Payments'}]}
EPISODE_ID = read_fixture()['id']


class FixedJobs:
    def __init__(self, job):
        self.job = job

    def get(self, _profile, job_id):
        return deepcopy(self.job) if job_id == self.job['job_id'] else None


class EpisodeAudioRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), preview_server.Handler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / 'state'
        self.voice = Path(self.temp.name) / 'voice.json'
        self.voice.write_text(json.dumps({'voice_name': DEFAULT_VOICE}), encoding='utf-8')
        episode = read_fixture()
        self.job = {'job_id': EPISODE_ID, 'status': 'done', 'episode': episode}
        self.store = FixedJobs(self.job)
        self.patches = ExitStack()
        self.patches.enter_context(patch.object(preview_server, 'EPISODE_JOBS', self.store))
        self.patches.enter_context(patch.object(
            preview_server, 'profile_response', return_value=(200, {'profile': PROFILE})))
        self.patches.enter_context(patch.object(preview_server, 'EPISODE_AUDIO_STATE_DIR', self.state))
        self.patches.enter_context(patch.object(preview_server, 'EPISODE_VOICE_FILE', self.voice))
        render_episode_audio(episode, profile_identifier(PROFILE), provider=FakeProvider(),
                             voice_name=DEFAULT_VOICE, state_dir=self.state)

    def tearDown(self):
        self.patches.close()
        self.temp.cleanup()

    def request(self, path, headers=None, method='GET', payload=None):
        data = None if payload is None else json.dumps(payload).encode('utf-8')
        request_headers = dict(headers or {})
        if data is not None:
            request_headers['Content-Type'] = 'application/json'
        request = Request(self.base + path, data=data, method=method, headers=request_headers)
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            return error.code, error.headers, error.read()

    def test_audio_serves_full_and_byte_ranges_including_suffix(self):
        status, headers, audio = self.request(f'/api/episodes/{EPISODE_ID}/audio')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'audio/wav')
        self.assertEqual(headers['Accept-Ranges'], 'bytes')
        self.assertEqual(int(headers['Content-Length']), len(audio))
        self.assertTrue(audio.startswith(b'RIFF'))

        status, headers, partial = self.request(
            f'/api/episodes/{EPISODE_ID}/audio', {'Range': 'bytes=10-19'})
        self.assertEqual(status, 206)
        self.assertEqual(headers['Content-Type'], 'audio/wav')
        self.assertEqual(headers['Content-Range'], f'bytes 10-19/{len(audio)}')
        self.assertEqual(partial, audio[10:20])

        status, headers, suffix = self.request(
            f'/api/episodes/{EPISODE_ID}/audio', {'Range': 'bytes=-8'})
        self.assertEqual(status, 206)
        self.assertEqual(headers['Content-Range'],
                         f'bytes {len(audio) - 8}-{len(audio) - 1}/{len(audio)}')
        self.assertEqual(int(headers['Content-Length']), len(suffix))
        self.assertEqual(suffix, audio[-8:])

    def test_missing_current_wav_fails_closed_for_full_and_range_requests(self):
        state, _manifest, audio_path = preview_server.episodes_audio.current_audio_asset(
            self.job['episode'], profile_identifier(PROFILE), voice_name=DEFAULT_VOICE,
            state_dir=self.state)
        self.assertEqual(state, 'ready')
        target = audio_path.parent
        audio_path.unlink()
        preserved_files = {path.name: path.read_bytes() for path in target.iterdir()}
        manifest_bytes = (target / 'manifest.json').read_bytes()

        for headers in ({}, {'Range': 'bytes=0-9'}):
            with self.subTest(headers=headers):
                status, _response_headers, body = self.request(
                    f'/api/episodes/{EPISODE_ID}/audio', headers)
                self.assertEqual(status, 409)
                self.assertEqual(json.loads(body)['error'], 'audio_invalidated')
                self.assertNotIn(b'RIFF', body)
                self.assertNotIn(str(target).encode(), body)

        self.assertEqual((target / 'manifest.json').read_bytes(), manifest_bytes)
        self.assertEqual({path.name: path.read_bytes() for path in target.iterdir()}, preserved_files)

    def test_shortened_current_wav_fails_closed_without_file_mutation(self):
        state, _manifest, audio_path = preview_server.episodes_audio.current_audio_asset(
            self.job['episode'], profile_identifier(PROFILE), voice_name=DEFAULT_VOICE,
            state_dir=self.state)
        self.assertEqual(state, 'ready')
        target = audio_path.parent
        manifest_bytes = (target / 'manifest.json').read_bytes()
        original_wav = audio_path.read_bytes()
        self.assertGreater(len(original_wav), 1)
        audio_path.write_bytes(original_wav[:-1])
        shortened_asset_bytes = {path.name: path.read_bytes() for path in target.iterdir()}

        for headers in ({}, {'Range': 'bytes=0-9'}):
            with self.subTest(headers=headers):
                status, _response_headers, body = self.request(
                    f'/api/episodes/{EPISODE_ID}/audio', headers)
                self.assertEqual(status, 409)
                self.assertEqual(json.loads(body)['error'], 'audio_invalidated')
                self.assertNotIn(b'RIFF', body)
                self.assertNotIn(str(target).encode(), body)
                self.assertEqual({path.name: path.read_bytes() for path in target.iterdir()},
                                 shortened_asset_bytes,
                                 'route refusal leaves the mismatched approved asset untouched')

        self.assertEqual((target / 'manifest.json').read_bytes(), manifest_bytes)

    def test_malformed_current_manifest_fails_full_and_range_with_safe_error(self):
        state, _manifest, audio_path = preview_server.episodes_audio.current_audio_asset(
            self.job['episode'], profile_identifier(PROFILE), voice_name=DEFAULT_VOICE,
            state_dir=self.state)
        self.assertEqual(state, 'ready')
        target = audio_path.parent
        approved_wav_bytes = audio_path.read_bytes()
        manifest_path = target / 'manifest.json'
        approved_manifest_bytes = manifest_path.read_bytes()
        manifest_path.write_bytes(b'{"format_version":')
        observed_asset_bytes = {path.name: path.read_bytes() for path in target.iterdir()}

        safe_response_signature = None
        for headers in ({}, {'Range': 'bytes=0-9'}):
            with self.subTest(headers=headers):
                status, response_headers, body = self.request(
                    f'/api/episodes/{EPISODE_ID}/audio', headers)
                self.assertEqual(status, 409)
                self.assertEqual(response_headers['Content-Type'], 'application/json; charset=utf-8')
                self.assertEqual(response_headers['Cache-Control'], 'no-store')
                self.assertEqual(json.loads(body), {
                    'error': 'audio_invalidated', 're_render_required': True,
                })
                self.assertNotIn(b'RIFF', body)
                self.assertNotIn(str(target).encode(), body)
                self.assertEqual({path.name: path.read_bytes() for path in target.iterdir()},
                                 observed_asset_bytes)
                signature = (status, body, *(response_headers.get(name) for name in (
                    'Content-Type', 'Cache-Control', 'X-Content-Type-Options')))
                if safe_response_signature is None:
                    safe_response_signature = signature
                else:
                    self.assertEqual(signature, safe_response_signature,
                                     'full and Range failures use the same safe response')

        self.assertEqual(manifest_path.read_bytes(), observed_asset_bytes['manifest.json'])
        manifest_path.write_bytes(approved_manifest_bytes)
        restored_state, _restored_manifest, restored_path = preview_server.episodes_audio.current_audio_asset(
            self.job['episode'], profile_identifier(PROFILE), voice_name=DEFAULT_VOICE,
            state_dir=self.state)
        self.assertEqual((restored_state, restored_path), ('ready', audio_path))
        self.assertEqual(manifest_path.read_bytes(), approved_manifest_bytes,
                         'selecting the restored good asset leaves its exact manifest bytes unchanged')
        for headers in ({}, {'Range': 'bytes=0-9'}):
            with self.subTest(restored_request_headers=headers):
                status, response_headers, body = self.request(
                    f'/api/episodes/{EPISODE_ID}/audio', headers)
                if headers:
                    self.assertEqual(status, 206)
                    self.assertEqual(response_headers['Content-Type'], 'audio/wav')
                    self.assertEqual(response_headers['Content-Range'],
                                     f'bytes 0-9/{len(approved_wav_bytes)}')
                    self.assertEqual(body, approved_wav_bytes[:10])
                else:
                    self.assertEqual(status, 200)
                    self.assertEqual(response_headers['Content-Type'], 'audio/wav')
                    self.assertEqual(body, approved_wav_bytes)
        self.assertEqual(manifest_path.read_bytes(), approved_manifest_bytes,
                         'restored full and Range reads leave the approved manifest unchanged')
        status, _response_headers, body = self.request(
            f'/api/episodes/{EPISODE_ID}/audio', {'Range': 'bytes=999999-'})
        self.assertEqual((status, body), (416, b''),
                         'an invalid Range after restoration serves no stale WAV bytes')
        self.assertEqual(_response_headers['Content-Range'],
                         f'bytes */{len(approved_wav_bytes)}')
        self.assertEqual(manifest_path.read_bytes(), approved_manifest_bytes,
                         'an invalid Range after restoration leaves the approved manifest unchanged')

    def test_wrong_profile_full_and_range_audio_requests_preserve_asset(self):
        other_profile = deepcopy(PROFILE)
        other_profile['topics'][0]['id'] = 'fictional-other-profile'

        class ProfileScopedJobs:
            def get(_self, profile, job_id):
                if profile != PROFILE or job_id != EPISODE_ID:
                    return None
                return deepcopy(self.job)

        state, _manifest, audio_path = preview_server.episodes_audio.current_audio_asset(
            self.job['episode'], profile_identifier(PROFILE), voice_name=DEFAULT_VOICE,
            state_dir=self.state)
        self.assertEqual(state, 'ready')
        target = audio_path.parent
        target_bytes = {path.name: path.read_bytes() for path in target.iterdir()}

        with patch.object(preview_server, 'EPISODE_JOBS', ProfileScopedJobs()), \
                patch.object(preview_server, 'profile_response',
                             return_value=(200, {'profile': other_profile})), \
                patch.object(preview_server.episodes_audio, 'current_audio_asset') as selector:
            for headers in ({}, {'Range': 'bytes=0-9'}):
                status, _response_headers, body = self.request(
                    f'/api/episodes/{EPISODE_ID}/audio', headers)
                self.assertEqual(status, 404)
                self.assertNotIn(b'RIFF', body)
            selector.assert_not_called()

        self.assertEqual({path.name: path.read_bytes() for path in target.iterdir()}, target_bytes)

    def test_ordinary_recorded_audio_projection_keeps_unbounded_route_selector(self):
        episode = read_fixture()
        rendered = render_episode_audio(
            episode, profile_identifier(PROFILE), provider=FakeProvider(),
            voice_name=DEFAULT_VOICE, state_dir=self.state)
        self.job['episode'] = rendered['episode']
        self.job['audio_status'] = 'recorded'
        self.assertNotIn('asset_key', rendered['episode']['audio'])
        self.assertNotIn('tts_output_token_limit', rendered['episode']['audio'])
        self.assertNotIn('max_segment_attempts', rendered['episode']['audio'])
        self.assertNotIn('coverage_status', rendered['episode']['audio'])
        self.job['episode']['audio']['asset_key'] = rendered['manifest']['asset_key']

        status, _headers, audio = self.request(f'/api/episodes/{EPISODE_ID}/audio')
        self.assertEqual(status, 200)
        self.assertEqual(audio, (rendered['directory'] / 'episode.wav').read_bytes())

    def test_fresh_process_bounded_reference_requires_exact_batch_and_revision(self):
        episode = read_fixture()
        episode['transcript'] = '\n\n'.join(row['spoken_text'] for row in episode['chapters'])
        episode['script_hash'] = hashlib.sha256(episode['transcript'].encode()).hexdigest()
        self.assertEqual(len(episode['chapters']), 2)
        key = Path(self.temp.name) / 'fictional-key'
        key.write_text('fictional-key', encoding='utf-8')
        job_state = Path(self.temp.name) / 'restart-jobs'
        jobs = EpisodeJobStore(job_state, generator=fake_episode, stage_delay=0,
                               ledger=UsageLedger(ledger_dir=Path(self.temp.name) / 'job-ledger'))
        status, submitted = jobs.submit(PROFILE, {
            'topic': 'Explain fictional Alder Bay parcel pickup.', 'length': 3,
            'style': 'explainer'}, start=False)
        self.assertEqual(status, 202)
        job_id = submitted['job_id']
        episode['id'] = job_id
        render_episode_audio(episode, profile_identifier(PROFILE), provider=FakeProvider(),
                             voice_name=DEFAULT_VOICE, state_dir=self.state)
        directory = jobs._profile_dir(PROFILE)
        persisted = jobs._path(directory, job_id)

        def persist_fixture(job):
            # Seed terminal approval states with the real store serializer. The
            # worker-to-approval write transition is outside this restart proof.
            with jobs._profile_lock(directory):
                row = jobs._read(persisted)
                row.update(job)
                jobs._write(persisted, row)

        def request_persisted(job, headers=None):
            persist_fixture(job)
            # The child imports the real route and reads only disposable files; no
            # renderer/approval object or parent monkeypatch can supply identity.
            child = subprocess.run([
                _briefing_sys.executable, '-c', textwrap.dedent('''
                    import base64, json, sys
                    from pathlib import Path
                    from http.server import ThreadingHTTPServer
                    from threading import Thread
                    from urllib.error import HTTPError
                    from urllib.request import Request, urlopen
                    from dev.test_guard import enforce_test_environment
                    enforce_test_environment()
                    from dev import preview_server
                    from dev.ingest.jobs import EpisodeJobStore, fake_episode
                    from dev.ingest.corrections import CorrectionService
                    from dev.ingest.usage import UsageLedger

                    job_state, job_id, state, voice, profile_json, headers = sys.argv[1:]
                    profile = json.loads(profile_json)
                    store = EpisodeJobStore(
                        Path(job_state), generator=fake_episode,
                        ledger=UsageLedger(ledger_dir=Path(job_state) / 'read-ledger'))
                    recovered = store.startup(start=False)
                    job = store.get(profile, job_id)
                    assert job is not None and job['status'] == 'done'
                    preview_server.EPISODE_JOBS = store
                    preview_server.CORRECTIONS = CorrectionService(
                        Path(job_state) / 'corrections', episode_reader=store.get)
                    preview_server.profile_response = lambda: (200, {'profile': profile})
                    preview_server.EPISODE_AUDIO_STATE_DIR = Path(state)
                    preview_server.EPISODE_VOICE_FILE = Path(voice)
                    server = ThreadingHTTPServer(('127.0.0.1', 0), preview_server.Handler)
                    thread = Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    try:
                        url = f'http://127.0.0.1:{server.server_address[1]}/api/episodes/{job["job_id"]}/audio'
                        with urlopen(url.removesuffix('/audio'), timeout=3) as detail_response:
                            detail = json.loads(detail_response.read())
                        request = Request(url, headers=json.loads(headers))
                        try:
                            response = urlopen(request, timeout=3)
                        except HTTPError as error:
                            response = error
                        with response:
                            print(json.dumps({'status': response.code,
                                              'persisted_audio': job['episode']['audio'], 'detail': detail,
                                              'recovered': recovered,
                                              'content_range': response.headers.get('Content-Range'),
                                              'content_length': response.headers.get('Content-Length'),
                                              'body': base64.b64encode(response.read()).decode()}))
                    finally:
                        server.shutdown()
                        server.server_close()
                        thread.join(timeout=2)
                '''), str(job_state), job_id, str(self.state), str(self.voice),
                json.dumps(PROFILE), json.dumps(headers or {})],
                capture_output=True, text=True, timeout=10)
            self.assertEqual(child.returncode, 0, child.stderr)
            response = json.loads(child.stdout)
            self.assertEqual(response['recovered'], 0)
            self.assertEqual(response['persisted_audio'], job['episode']['audio'])
            body = base64.b64decode(response['body'])
            if response['status'] == 206:
                self.assertEqual(
                    response['content_range'],
                    f"bytes 17-41/{len(approved_bytes['episode.wav'])}")
            if response['status'] == 200:
                self.assertEqual(response['content_length'],
                                 str(len(approved_bytes['episode.wav'])))
                current = response['detail']['episode']
                self.assertEqual(response['detail']['audio_state'], 'recorded')
                transcript = '\n\n'.join(row['spoken_text'] for row in current['chapters'])
                self.assertEqual(current['transcript'], transcript)
                self.assertEqual(current['audio']['script_hash'], hashlib.sha256(transcript.encode()).hexdigest())
                with wave.open(BytesIO(body), 'rb') as recording:
                    duration = recording.getnframes() / recording.getframerate()
                self.assertAlmostEqual(duration, 0.3)
                self.assertAlmostEqual(current['audio']['duration_seconds'], duration)
                self.assertEqual([row['audio_start_seconds'] for row in current['chapters']], [0.0, 0.1])
                self.assertEqual([row['audio_duration_seconds'] for row in current['chapters']], [0.1, 0.2])
            return response['status'], body

        transport_calls = []

        def transport(_request, timeout):
            transport_calls.append(_request)
            return BytesIO(json.dumps({
                'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{
                    'inlineData': {'mimeType': 'audio/wav',
                                   'data': base64.b64encode(wav_bytes(frames=100 * len(transport_calls))).decode('ascii')}
                }]}}], 'usageMetadata': {'promptTokenCount': 1, 'candidatesTokenCount': 1}
            }).encode())

        provider = GeminiTtsProvider(
            key_path=key, transport=transport,
            usage_ledger=UsageLedger(ledger_dir=Path(self.temp.name) / 'bounded-ledger'))
        identity = {'voice_name': DEFAULT_VOICE, 'state_dir': self.state,
                    'tts_output_token_limit': 4096, 'max_segment_attempts': 1}
        candidate = render_episode_audio(
            episode, profile_identifier(PROFILE), provider=provider, **identity)
        pending_episode = json.loads((candidate['candidate_directory'] / 'episode.json').read_text())
        self.assertNotIn('url', pending_episode['audio'])
        pending_candidate_bytes = {
            path.name: path.read_bytes() for path in candidate['candidate_directory'].iterdir()}
        pending_status, _body = request_persisted({
            'job_id': job_id, 'status': 'done', 'audio_status': 'pending_human_listening',
            'episode': pending_episode})
        self.assertEqual(pending_status, 404)
        pending_range_status, pending_range_body = request_persisted({
            'job_id': job_id, 'status': 'done', 'audio_status': 'pending_human_listening',
            'episode': pending_episode}, {'Range': 'bytes=0-9'})
        self.assertEqual(pending_range_status, 404)
        self.assertNotIn(b'RIFF', pending_range_body)
        self.assertEqual({path.name: path.read_bytes()
                          for path in candidate['candidate_directory'].iterdir()},
                         pending_candidate_bytes)
        approved = approve_bounded_audio_candidate(
            episode, profile_identifier(PROFILE), batch_id=candidate['batch_id'],
            approved_revision_id=episode['revision_id'],
            approved_script_hash=candidate['script_hash'], human_approved=True,
            **identity)  # Fictional attestation only.
        approved_bytes = {p.name: p.read_bytes() for p in approved['directory'].iterdir()}
        persist_fixture({
            'job_id': job_id, 'status': 'done', 'audio_status': 'recorded',
            'episode': approved['episode']})
        reloaded = jobs.get(PROFILE, job_id)
        self.job.clear()
        self.job.update(deepcopy(reloaded))
        status, audio = request_persisted(self.job)
        self.assertEqual((status, audio), (200, approved_bytes['episode.wav']))
        current_wav = approved_bytes['episode.wav']
        status, ranged_audio = request_persisted(self.job, {'Range': 'bytes=17-41'})
        self.assertEqual(status, 206)
        self.assertEqual(ranged_audio, current_wav[17:42],
                         'a recovered current identity serves the exact WAV range')

        for case in ('wrong-batch', 'missing-batch', 'revision-only', 'wrong-episode',
                     'wrong-key', 'wrong-pool', 'wrong-attempts', 'wrong-approval',
                     'wrong-voice', 'wrong-provider', 'wrong-model'):
            with self.subTest(case=case):
                self.job.clear()
                self.job.update(deepcopy(reloaded))
                current = self.job['episode']
                if case == 'wrong-batch':
                    current['audio']['batch_id'] = 'fictional-other-approved-batch'
                elif case == 'missing-batch':
                    current['audio'].pop('batch_id')
                elif case == 'revision-only':
                    current['revision_id'] = 'fictional-identical-script-r2'
                elif case == 'wrong-episode':
                    current['audio']['episode_id'] = 'fictional-other-episode'
                elif case == 'wrong-key':
                    current['audio']['asset_key'] = '0' * 64
                elif case == 'wrong-pool':
                    current['audio']['tts_output_token_limit'] = 2048
                elif case == 'wrong-attempts':
                    current['audio']['max_segment_attempts'] = 2
                elif case == 'wrong-voice':
                    current['audio']['voice_name'] = 'fictional-unsupported-voice'
                elif case == 'wrong-provider':
                    current['audio']['provider'] = 'fictional-unsupported-provider'
                elif case == 'wrong-model':
                    current['audio']['model'] = 'fictional-unsupported-model'
                else:
                    current['audio']['human_listening_approval']['script_hash'] = '0' * 64
                self.assertEqual(current['chapters'], reloaded['episode']['chapters'])
                self.assertEqual(current['audio']['script_hash'],
                                 reloaded['episode']['audio']['script_hash'])
                status, body = request_persisted(self.job, {'Range': 'bytes=0-9'})
                self.assertEqual(status, 409)
                self.assertNotIn(b'RIFF', body,
                                 'stale bounded identity is denied before WAV bytes are returned')
                self.assertIn(json.loads(body)['error'], ('audio_stale', 'audio_script_unavailable'))
                self.assertEqual({p.name: p.read_bytes() for p in approved['directory'].iterdir()},
                                 approved_bytes)

        self.job.clear()
        self.job.update(deepcopy(reloaded))
        status, audio = request_persisted(self.job)
        self.assertEqual((status, audio), (200, approved_bytes['episode.wav']))

    def test_invalid_ranges_and_traversal_are_rejected(self):
        _state, _manifest, audio_path = preview_server.episodes_audio.current_audio_asset(
            self.job['episode'], profile_identifier(PROFILE), voice_name=DEFAULT_VOICE,
            state_dir=self.state)
        target = audio_path.parent
        asset_bytes = {path.name: path.read_bytes() for path in target.iterdir()}
        for value in ('bytes=999999-', 'bytes=5-2', 'bytes=0-1,3-4', 'items=0-1', 'bytes=-0'):
            with self.subTest(value=value):
                status, headers, body = self.request(
                    f'/api/episodes/{EPISODE_ID}/audio', {'Range': value})
                self.assertEqual(status, 416)
                self.assertEqual(headers['Content-Range'].split('/')[0], 'bytes *')
                self.assertEqual(headers['Cache-Control'], 'no-store')
                self.assertEqual(body, b'')
                self.assertNotIn(str(target), str(headers))
                self.assertNotIn(str(target).encode(), body)
                self.assertEqual({path.name: path.read_bytes() for path in target.iterdir()},
                                 asset_bytes, 'unsatisfiable ranges leave the approved asset unchanged')
        status, _headers, _body = self.request('/api/episodes/%2e%2e/audio')
        self.assertEqual(status, 404)
        with patch.object(preview_server.episodes_audio, 'current_audio_asset') as current:
            status, _headers, body = self.request(
                '/api/episodes/%2e%2e/audio', {'Range': 'bytes=0-9'})
        self.assertEqual(status, 404)
        self.assertNotIn(b'RIFF', body)
        current.assert_not_called()

    def test_head_audio_request_is_rejected_without_reading_wav(self):
        with patch.object(preview_server.episodes_audio, 'current_audio_asset') as current:
            status, _headers, body = self.request(
                f'/api/episodes/{EPISODE_ID}/audio', method='HEAD')
        self.assertEqual(status, 501)
        self.assertNotIn(b'RIFF', body)
        current.assert_not_called()

    def test_audio_disabled_job_never_serves_an_existing_asset(self):
        for audio_status in ('disabled', 'pending_human_listening'):
            with self.subTest(audio_status=audio_status), patch.object(
                    preview_server.episodes_audio, 'current_audio_asset') as current:
                self.job['audio_status'] = audio_status
                status, headers, body = self.request(f'/api/episodes/{EPISODE_ID}/audio')
                self.assertEqual(status, 404)
                self.assertEqual(headers['Content-Type'], 'application/json; charset=utf-8')
                self.assertEqual(json.loads(body), {
                    'error': 'audio_not_rendered', 'audio_status': audio_status,
                }, 'safe audio response contains no filesystem or traceback diagnostics')
                current.assert_not_called()

    def test_episode_get_includes_current_chapter_times_and_correction_hides_them(self):
        status, _headers, body = self.request(f'/api/episodes/{EPISODE_ID}')
        self.assertEqual(status, 200)
        episode_job = json.loads(body)
        self.assertEqual(episode_job['audio_state'], 'recorded')
        self.assertEqual(episode_job['episode']['chapters'][0]['audio_start_seconds'], 0.0)
        self.assertIn('audio_duration_seconds', episode_job['episode']['chapters'][0])

        self.job['episode']['revision_id'] = 'fictional-corrected-r2'
        self.job['episode']['chapters'][0]['spoken_text'] = (
            'Alder Bay will use a corrected fictional delivery schedule this spring.')
        status, _headers, body = self.request(f'/api/episodes/{EPISODE_ID}/audio')
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)['error'], 'audio_stale')
        self.assertTrue(json.loads(body)['re_render_required'])

        status, _headers, body = self.request(f'/api/episodes/{EPISODE_ID}')
        corrected = json.loads(body)
        self.assertEqual(corrected['audio_state'], 'stale')
        self.assertNotIn('audio_start_seconds', corrected['episode']['chapters'][0])
        self.assertNotIn('audio', corrected['episode'])

    def test_audio_route_serves_only_the_corrected_revision_after_selective_rebuild(self):
        original = deepcopy(self.job['episode'])
        corrected = deepcopy(original)
        corrected['revision_id'] = 'fictional-corrected-r2'
        corrected['chapters'][0]['spoken_text'] = 'Alder Bay published corrected wording for the spring locker trial.'
        rebuilt = render_episode_audio(
            corrected, profile_identifier(PROFILE), provider=CorrectionDurationProvider(),
            voice_name=DEFAULT_VOICE, state_dir=self.state, reuse_from_episode=original,
            changed_chapter_id=corrected['chapters'][0]['id'])
        old_state, old_manifest, old_path = preview_server.episodes_audio.current_audio_asset(
            original, profile_identifier(PROFILE), voice_name=DEFAULT_VOICE, state_dir=self.state)
        self.assertEqual((old_state, old_manifest['revision_id']), ('ready', original['revision_id']))
        self.job['episode'] = corrected
        status, _headers, current_bytes = self.request(
            f'/api/episodes/{EPISODE_ID}/audio?revision_id={original["revision_id"]}')
        self.assertEqual(status, 200)
        self.assertEqual(current_bytes, rebuilt['directory'].joinpath('episode.wav').read_bytes())
        self.assertNotEqual(current_bytes, old_path.read_bytes(),
                            'the current route must not select retained historical audio by query')

    def test_on_demand_job_audio_route_serves_its_persisted_current_script(self):
        audio_state = self.state / 'on-demand-audio'

        def generate(topic, length, style, **_kwargs):
            episode = fake_episode({'job_id': current_job_id[0], 'topic': topic,
                                    'length': length, 'style': style})
            episode['revision_id'] = 'fictional-on-demand-r1'
            for chapter in episode['chapters']:
                chapter['spoken_text'] = ' '.join(row['text'] for row in chapter['sentences'])
            episode['transcript'] = '\n\n'.join(
                chapter['spoken_text'] for chapter in episode['chapters'])
            episode['script_hash'] = hashlib.sha256(episode['transcript'].encode()).hexdigest()
            episode.update(source_count=len(episode['sources']),
                           sources_read=len(episode['sources']),
                           shorter_than_requested=False,
                           quality_rubric={'passed': True},
                           verification={'passed': True})
            return episode

        current_job_id = ['']

        def render(episode, profile_id, **kwargs):
            return render_episode_audio(
                episode, profile_id, provider=FakeProvider(), voice_name=DEFAULT_VOICE,
                state_dir=audio_state, **{key: value for key, value in kwargs.items()
                                          if key in {'cancelled', 'progress'}})

        with patch.dict('os.environ', {'BRIEFING_EPISODES': 'real', 'BRIEFING_TTS': '1'}):
            jobs = EpisodeJobStore(
                Path(self.temp.name) / 'on-demand-jobs', stage_delay=0,
                real_generator=generate, audio_renderer=render,
                ledger=UsageLedger(ledger_dir=Path(self.temp.name) / 'usage'),
                key_available=lambda: True, tts_enabled=True)
            corrections = CorrectionService(
                Path(self.temp.name) / 'corrections',
                episode_reader=lambda profile, job_id: jobs.get(profile, job_id))
            with patch.object(preview_server, 'EPISODE_JOBS', jobs), \
                    patch.object(preview_server, 'EPISODE_AUDIO_STATE_DIR', audio_state), \
                    patch.object(preview_server, 'CORRECTIONS', corrections):
                status, created = jobs.submit(
                    PROFILE, {'topic': 'How does fictional Alder Bay parcel pickup work?',
                              'length': 3, 'style': 'explainer'}, start=False)
                self.assertEqual(status, 202)
                current_job_id[0] = created['job_id']
                jobs.run_pending(PROFILE, created['job_id'])

                persisted = jobs.get(PROFILE, created['job_id'])
                episode = persisted['episode']
                self.assertTrue(episode['quality_rubric']['passed'])
                self.assertTrue(episode['verification']['passed'])
                self.assertEqual(persisted['audio_status'], 'recorded')
                self.assertEqual(episode['audio']['status'], 'recorded')
                expected_script = '\n\n'.join(chapter['spoken_text'] for chapter in episode['chapters'])
                expected_hash = hashlib.sha256(expected_script.encode()).hexdigest()
                self.assertEqual(episode['audio']['script_hash'], expected_hash)
                self.assertEqual(episode['audio']['revision_id'], episode['revision_id'])
                self.assertEqual(episode['audio']['url'],
                                 f"/api/episodes/{created['job_id']}/audio")

                route_status, _headers, playable = self.request(episode['audio']['url'])
                self.assertEqual(route_status, 200)
                self.assertTrue(playable.startswith(b'RIFF'))

                old_state, old_manifest, old_path = preview_server.episodes_audio.current_audio_asset(
                    episode, profile_identifier(PROFILE), voice_name=DEFAULT_VOICE,
                    state_dir=audio_state)
                self.assertEqual(old_state, 'ready')
                prior_files = {path.name: path.read_bytes() for path in old_path.parent.iterdir()}
                corrected_text = 'Alder Bay will use a corrected fictional parcel schedule this spring.'
                correction_status, _headers, correction_body = self.request(
                    '/api/corrections', method='POST', payload={
                        'target_type': 'episode', 'target_id': created['job_id'],
                        'chapter_id': episode['chapters'][0]['id'], 'sentence_index': 0,
                        'old_text': episode['chapters'][0]['sentences'][0]['text'],
                        'new_text': corrected_text,
                        'what_changed': 'The fictional schedule was corrected.',
                    })
                correction = json.loads(correction_body)
                self.assertEqual(correction_status, 201)
                self.assertEqual(correction['audio_status'], 'regenerating')
                self.assertEqual(correction['episode']['chapters'][0]['spoken_text'],
                                 ' '.join([corrected_text, episode['chapters'][0]['sentences'][1]['text']]))

                corrected_status, _headers, corrected_body = self.request(
                    f"/api/episodes/{created['job_id']}")
                self.assertEqual(corrected_status, 200)
                corrected_job = json.loads(corrected_body)
                current_episode = corrected_job['episode']
                self.assertIn(corrected_text, current_episode['transcript'])
                self.assertEqual(current_episode['revision_id'], correction['episode']['revision_id'])
                self.assertNotIn('audio', current_episode)
                self.assertNotIn('audio_start_seconds', current_episode['chapters'][0])
                self.assertNotIn('audio_duration_seconds', current_episode['chapters'][0])

                stale_status, _headers, stale_body = self.request(episode['audio']['url'])
                self.assertEqual(stale_status, 409)
                self.assertEqual(json.loads(stale_body)['error'], 'audio_stale')
                preserved_state, preserved_manifest, preserved_path = \
                    preview_server.episodes_audio.current_audio_asset(
                        episode, profile_identifier(PROFILE), voice_name=DEFAULT_VOICE,
                        state_dir=audio_state)
                self.assertEqual((preserved_state, preserved_manifest, preserved_path),
                                 ('ready', old_manifest, old_path))
                self.assertEqual({path.name: path.read_bytes() for path in preserved_path.parent.iterdir()},
                                 prior_files, 'the prior WAV and manifest remain byte-for-byte intact')

    def test_daily_audio_route_serves_range_requests_from_active_edition(self):
        audio_path = self.state / 'daily.wav'
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(wav_bytes())
        edition = {'id': 'daily-fixture', 'revision_id': 'r1', 'local_date': '2026-11-18',
                   'sections': [{'id': 'story-one', 'cluster_id': 'one', 'heading': 'Fictional story',
                                 'audio_script': 'A fully fictional update. [1]'}]}
        payload = {'generated': True, 'edition': edition, 'sources': [], 'history': []}
        with patch.object(preview_server, 'edition_response', return_value=(200, payload)), \
                patch.object(preview_server.CORRECTIONS, 'apply_edition', return_value=payload), \
                patch.object(preview_server.episodes_audio, 'current_audio_asset',
                             return_value=('ready', {'stitched_sha256': 'a' * 64}, audio_path)):
            status, headers, body = self.request('/api/daily/audio', {'Range': 'bytes=0-3'})
        self.assertEqual(status, 206)
        self.assertEqual(headers['Accept-Ranges'], 'bytes')
        self.assertEqual(body, audio_path.read_bytes()[:4])
        with patch.object(preview_server, 'edition_response', return_value=(200, payload)), \
                patch.object(preview_server.CORRECTIONS, 'apply_edition', return_value=payload), \
                patch.object(preview_server.episodes_audio, 'current_audio_asset',
                             return_value=('stale', None, None)):
            status, _headers, body = self.request('/api/daily/audio')
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)['error'], 'audio_stale')

    def test_daily_audio_route_exposes_pending_and_failed_without_attempting_playback(self):
        for audio_status in ('pending', 'failed', 'pending_human_listening'):
            edition = {'id': 'daily-fixture', 'revision_id': 'r1', 'local_date': '2026-11-18',
                       'audio': {'audio_status': audio_status},
                       'sections': [{'id': 'story-one', 'cluster_id': 'one',
                                     'heading': 'Fictional story',
                                     'audio_script': 'A fully fictional update.'}]}
            payload = {'generated': True, 'edition': edition, 'sources': [], 'history': []}
            with patch.object(preview_server, 'edition_response', return_value=(200, payload)), \
                    patch.object(preview_server.episodes_audio, 'current_audio_asset') as current:
                status, _headers, body = self.request('/api/daily/audio')
            self.assertEqual(status, 404)
            self.assertEqual(json.loads(body)['audio_status'], audio_status)
            current.assert_not_called()


if __name__ == '__main__':
    unittest.main()
