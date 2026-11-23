import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dev import preview_server
from dev.ingest.episodes_audio import load_voice


class VoiceSelectionRouteTests(unittest.TestCase):
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
        root = Path(self.temp.name)
        self.voice_file = root / 'state' / 'voice.json'
        self.manifest = root / 'manifest.json'
        self.voice_ids = [f'FixtureVoice{letter}' for letter in 'ABCDEF']
        self.manifest.write_text(json.dumps({'samples': [
            {'label': letter, 'voice': voice_id} for letter, voice_id in zip('ABCDEF', self.voice_ids)
        ]}), encoding='utf-8')
        self.patches = [
            patch.object(preview_server, 'EPISODE_VOICE_FILE', self.voice_file),
            patch.object(preview_server, 'EPISODE_AUDITION_MANIFEST', self.manifest),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def request(self, path, method='GET', payload=None):
        data = None if payload is None else json.dumps(payload).encode('utf-8')
        request = Request(self.base + path, data=data, method=method,
                          headers={'Content-Type': 'application/json'} if data is not None else {})
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def test_get_post_and_audio_renderer_use_the_letter_mapped_voice_id(self):
        status, initial = self.request('/api/voice')
        self.assertEqual((status, initial), (200, {'letter': 'A'}))
        self.assertNotIn(self.voice_ids[0], json.dumps(initial))

        status, selected = self.request('/api/voice', 'POST', {'letter': 'D'})
        self.assertEqual((status, selected), (200, {'letter': 'D'}))
        saved = json.loads(self.voice_file.read_text(encoding='utf-8'))
        self.assertEqual(saved, {'voice_id': self.voice_ids[3]})
        self.assertEqual(load_voice(self.voice_file, manifest_path=self.manifest), self.voice_ids[3])

        status, current = self.request('/api/voice')
        self.assertEqual((status, current), (200, {'letter': 'D'}))
        self.assertNotIn(self.voice_ids[3], json.dumps(current))

    def test_manifest_voice_id_compatibility_never_returns_provider_name(self):
        status, selected = self.request('/api/voice', 'POST', {'voice_name': self.voice_ids[2]})
        self.assertEqual((status, selected), (200, {'letter': 'C'}))
        self.assertNotIn(self.voice_ids[2], json.dumps(selected))
        self.assertEqual(json.loads(self.voice_file.read_text())['voice_id'], self.voice_ids[2])

    def test_invalid_or_extra_voice_fields_are_rejected_without_writing(self):
        for payload in ({'letter': 'G'}, {'letter': 'A', 'voice_name': self.voice_ids[0]},
                        {'voice_name': 'NotInManifest'}, {'letter': '../A'}):
            with self.subTest(payload=payload):
                status, response = self.request('/api/voice', 'POST', payload)
                self.assertEqual(status, 400)
                self.assertEqual(response['error'], 'invalid_voice_selection' if
                                 set(payload) == {'letter'} or set(payload) == {'voice_name'} else 'invalid_request')
                self.assertFalse(self.voice_file.exists())


if __name__ == '__main__':
    unittest.main()
