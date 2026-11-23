"""Route-level golden shape checks for the browser's stable API contract."""

import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from contextlib import ExitStack
import http.client
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

from dev import preview_server


FIXTURE = Path(__file__).parent / 'fixtures' / 'api_contract_shapes.json'
EPISODE_ID = 'a' * 32


def _load_fixture():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


def _assert_required_paths(testcase, payload, paths):
    """Require each dotted path; [] means a non-empty array and checks all rows."""
    for path in paths:
        with testcase.subTest(path=path):
            parts = path.split('.')
            current = [payload]
            for index, part in enumerate(parts):
                array = part.endswith('[]')
                key = part[:-2] if array else part
                next_values = []
                for value in current:
                    testcase.assertIsInstance(value, dict, f'{path}: expected object before {key}')
                    testcase.assertIn(key, value, f'{path}: required field removed')
                    found = value[key]
                    if array:
                        testcase.assertIsInstance(found, list, f'{path}: expected array')
                        if index < len(parts) - 1:
                            testcase.assertTrue(found, f'{path}: expected a fixture row')
                        next_values.extend(found)
                    else:
                        next_values.append(found)
                current = next_values


class EpisodeJobsFixture:
    def __init__(self, responses):
        self.responses = responses

    def list(self, _profile):
        return self.responses['episodes']

    def get(self, _profile, job_id):
        if job_id == EPISODE_ID:
            return self.responses['episode']
        return None


class ApiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), preview_server.Handler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.fixture = _load_fixture()
        self.responses = self.fixture['responses']
        self.profile = {'minutes': 5, 'topics': [{'id': 'fictional-town', 'label': 'Fictional town'}]}
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.voice_file = root / 'state' / 'voice.json'
        self.manifest = root / 'manifest.json'
        self.manifest.write_text(json.dumps({'samples': [
            {'label': letter, 'voice': f'FixtureVoice{letter}'} for letter in 'ABCDEF'
        ]}), encoding='utf-8')
        self.jobs = EpisodeJobsFixture(self.responses)
        self.patches = ExitStack()
        self.patches.enter_context(patch.object(preview_server, 'profile_response',
                                                return_value=(200, {'profile': self.profile})))
        self.patches.enter_context(patch.object(preview_server, 'edition_response',
                                                return_value=(200, self.responses['edition'])))
        self.patches.enter_context(patch.object(preview_server.CORRECTIONS, 'apply_edition',
                                                side_effect=lambda edition, _profile: edition))
        self.patches.enter_context(patch.object(preview_server.CORRECTIONS, 'list',
                                                return_value=self.responses['corrections']))
        self.patches.enter_context(patch.object(preview_server.CORRECTIONS, 'apply_job',
                                                side_effect=lambda job, _profile: job))
        self.patches.enter_context(patch.object(preview_server, 'EPISODE_JOBS', self.jobs))
        self.patches.enter_context(patch.object(preview_server, 'status_response',
                                                return_value=(200, self.responses['status'])))
        self.patches.enter_context(patch.object(preview_server, 'EPISODE_VOICE_FILE', self.voice_file))
        self.patches.enter_context(patch.object(preview_server, 'EPISODE_AUDITION_MANIFEST', self.manifest))

    def tearDown(self):
        self.patches.close()
        self.temp.cleanup()

    def request(self, path):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        connection.request('GET', path)
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        return response.status, payload

    def assert_contract(self, path, response_name):
        status, payload = self.request(path)
        self.assertEqual(status, 200, path)
        _assert_required_paths(self, payload, self.fixture['required_paths'][path])
        return payload

    def test_public_browser_routes_match_golden_required_shapes(self):
        edition = self.assert_contract('/api/edition?profile=active&history=1', 'edition')
        episodes = self.assert_contract('/api/episodes', 'episodes')
        episode = self.assert_contract(f'/api/episodes/{EPISODE_ID}', 'episode')
        voice = self.assert_contract('/api/voice', 'voice')
        corrections = self.assert_contract('/api/corrections', 'corrections')
        status = self.assert_contract('/api/status', 'status')
        profile = self.assert_contract('/api/profile', 'profile')

        # Golden fixtures document shape, not exact dynamic values or additive fields.
        self.assertEqual(edition['edition']['id'], 'fixture-edition-current')
        self.assertEqual(episodes['jobs'][0]['job_id'], EPISODE_ID)
        self.assertEqual(episode['episode']['id'], 'fixture-episode-one')
        self.assertEqual(voice, {'letter': 'A'})
        self.assertNotIn('internal_id', corrections['corrections'][0])
        self.assertNotIn('new_text', corrections['corrections'][0])
        self.assertEqual(status['ok'], True)
        self.assertEqual(profile['profile']['topics'][0]['id'], 'fictional-town')


if __name__ == '__main__':
    unittest.main()
