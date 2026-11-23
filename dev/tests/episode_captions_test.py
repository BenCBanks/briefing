"""Fixture-backed WebVTT route checks for completed fictional episodes."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from copy import deepcopy
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dev import preview_server


FIXTURE = Path(__file__).parent / 'fixtures' / 'episode-captions.json'
EPISODE = json.loads(FIXTURE.read_text(encoding='utf-8'))
EPISODE_ID = EPISODE['id']
PROFILE = {'minutes': 12, 'topics': [{'id': 'fictional', 'label': 'Fictional delivery'}]}


class FixedJobs:
    def __init__(self, job):
        self.job = job

    def get(self, _profile, episode_id):
        return deepcopy(self.job) if episode_id == self.job['job_id'] else None


class FixedCorrections:
    @staticmethod
    def apply_job(job, _profile):
        return deepcopy(job)


class EpisodeCaptionsRouteTests(unittest.TestCase):
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
        self.job = {'job_id': EPISODE_ID, 'status': 'done', 'episode': deepcopy(EPISODE)}
        self.patches = ExitStack()
        self.patches.enter_context(patch.object(preview_server, 'EPISODE_JOBS', FixedJobs(self.job)))
        self.patches.enter_context(patch.object(preview_server, 'CORRECTIONS', FixedCorrections()))
        self.patches.enter_context(patch.object(
            preview_server, 'profile_response', lambda: (200, {'profile': PROFILE})))

    def tearDown(self):
        self.patches.close()
        self.temp.cleanup()

    def request(self, path):
        try:
            with urlopen(Request(self.base + path), timeout=3) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            return error.code, error.headers, error.read()

    def test_vtt_uses_global_chapter_offsets_and_sentence_timings(self):
        status, headers, body = self.request(f'/api/episodes/{EPISODE_ID}/captions.vtt')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'text/vtt; charset=utf-8')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(body.decode(), '''WEBVTT

00:00:00.000 --> 00:00:02.400
Alder Bay will test shared parcel lockers this spring.

00:00:02.400 --> 00:00:04.900
Residents can collect packages during one trip downtown.

00:00:05.000 --> 00:00:06.500
The town will review the trial before deciding whether to continue.

00:00:06.500 --> 00:00:08.000
The review will include resident feedback.
''')

    def test_vtt_escapes_cue_markup_and_rejects_bad_timing(self):
        self.job['episode']['chapters'][0]['sentences'][0]['text'] = 'The <pilot> & review continues.'
        status, _headers, body = self.request(f'/api/episodes/{EPISODE_ID}/captions.vtt')
        self.assertEqual(status, 200)
        self.assertIn(b'The &lt;pilot&gt; &amp; review continues.', body)

        self.job['episode']['chapters'][0]['sentences'][1]['start_seconds'] = 1
        status, _headers, body = self.request(f'/api/episodes/{EPISODE_ID}/captions.vtt')
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)['error'], 'captions_unavailable')

    def test_stale_correction_unfinished_job_and_unknown_id_are_not_exported(self):
        self.job.update(updated='2026-11-18T16:00:00Z', audio_status='regenerating')
        status, _headers, body = self.request(f'/api/episodes/{EPISODE_ID}/captions.vtt')
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)['error'], 'audio_stale')
        self.job.update(updated=None, status='reading')
        status, _headers, body = self.request(f'/api/episodes/{EPISODE_ID}/captions.vtt')
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)['error'], 'episode_not_ready')
        status, _headers, body = self.request('/api/episodes/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/captions.vtt')
        self.assertEqual(status, 404)


if __name__ == '__main__':
    unittest.main()
