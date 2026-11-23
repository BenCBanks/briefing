"""Standalone fictional checks for the provider-free audio identity helper."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import subprocess
import sys
import unittest

from dev.ingest.audio_fingerprints import _fingerprints


class PublicAudioFingerprintTests(unittest.TestCase):
    @staticmethod
    def episode_and_chapters():
        episode = {
            'id': 'fictional-fingerprint-episode',
            'revision_id': 'fictional-fingerprint-r1',
        }
        chapters = [
            {'id': 'fictional-chapter-01',
             'text': 'Alder Bay tests a library delivery locker.'},
            {'id': 'fictional-chapter-02',
             'text': 'The fictional pilot will report results in June.'},
        ]
        return episode, chapters

    def test_fallback_fingerprint_matches_independent_fixed_vector(self):
        episode, chapters = self.episode_and_chapters()
        self.assertEqual(_fingerprints(
            episode, chapters, 'Sulafat', 'gemini', 'gemini-3.8-flash-lite-tts'), (
                '2fb547545e1ff1f3bcd432f2ad3bd23b96c0b80958e42342760579632d1a754b',
                '556292b5d15fe7c7883ffefc2af79fe65a77d27f01929fd7da3fd161ac663c04'))

    def test_frozen_and_bounded_fingerprints_match_independent_fixed_vectors(self):
        episode, chapters = self.episode_and_chapters()
        transcript = '\n\n'.join(chapter['text'] for chapter in chapters)
        frozen_episode = {
            **episode,
            'transcript': transcript,
            'script_hash': '397147fad551e53dc2d61a77bdecd4204f5b98cac2022bb6b27d00aad0262a5a',
        }
        args = (chapters, 'Sulafat', 'gemini', 'gemini-3.8-flash-lite-tts')
        self.assertEqual(_fingerprints(frozen_episode, *args), (
            '397147fad551e53dc2d61a77bdecd4204f5b98cac2022bb6b27d00aad0262a5a',
            'a165d2c14fd3953b279badb769bdafbe74be19f0d0546d9cb3c46474571d95a4'))
        self.assertEqual(_fingerprints(
            episode, *args, tts_output_token_limit=4096, max_segment_attempts=3), (
                '2fb547545e1ff1f3bcd432f2ad3bd23b96c0b80958e42342760579632d1a754b',
                '81181dc20bd90428cda244c65bb399f9da3547c2fc18b2457925b8e3e4b46fcd'))

    def test_fresh_helper_import_excludes_provider_state_and_approval_modules(self):
        script = '''
import sys
import dev.ingest.audio_fingerprints
allowed = {
    'dev.ingest',
    'dev.ingest.audio_fingerprints',
    'dev.ingest.tts',
    'dev.ingest.tts.base',
    'dev.ingest.tts.normalize',
}
loaded = {name for name in sys.modules
          if name == 'dev.ingest' or name.startswith('dev.ingest.')}
assert loaded == allowed, sorted(loaded - allowed)
'''
        result = subprocess.run(
            [sys.executable, '-c', script], check=False, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
