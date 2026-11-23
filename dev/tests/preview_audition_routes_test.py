import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dev import preview_server


class PreviewAuditionRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        from threading import Thread
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), preview_server.Handler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, path):
        return urlopen(self.base + path)

    def test_serves_labelled_page_and_audio_from_a_read_only_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = b'RIFF-test-audio'
            (root / 'sample.wav').write_bytes(audio)
            (root / 'manifest.json').write_text('{"format_version":1}', encoding='utf-8')
            with patch.object(preview_server, 'AUDITION_AUDIO_ROOT', root):
                page = self.request('/audition').read().decode('utf-8')
                script = self.request('/audition.js').read().decode('utf-8')
                style = self.request('/audition.css').read().decode('utf-8')
                response = self.request('/audition-audio/sample.wav')
                self.assertEqual(response.read(), audio)
                self.assertEqual(response.headers.get('Content-Type'), 'audio/x-wav')
                manifest = self.request('/audition-audio/manifest.json').read()
                self.assertEqual(manifest, b'{"format_version":1}')
                with self.assertRaises(HTTPError) as missing:
                    self.request('/audition-audio/%2e%2e%2fsecret.wav')
                with self.assertRaises(HTTPError) as post:
                    urlopen(Request(self.base + '/audition-audio/sample.wav', data=b'overwrite',
                                    method='POST'))
            self.assertIn('Sample A', page)
            self.assertIn('Sample F', page)
            self.assertIn('Which one would you listen to every morning?', page)
            self.assertIn('Use this voice', page)
            self.assertIn('src="/audition.js"', page)
            self.assertNotIn('<script>', page)
            self.assertIn("JSON.stringify({ letter })", script)
            self.assertIn('min-height: 44px', style)
            self.assertNotIn('Sulafat', page)
            self.assertEqual(missing.exception.code, 404)
            self.assertEqual(post.exception.code, 404)


if __name__ == '__main__':
    unittest.main()
