import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import copy
import json
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from dev.ingest.audio_script import _speak_text
from dev.ingest.episodes_audio import render_episode_audio
from dev.ingest.tts.base import TtsResult
from dev.ingest.tts.gemini import DEFAULT_MODEL
from dev.ingest.tts.normalize import NORMALIZER_VERSION, normalize_narration
from dev.tests.episodes_audio_test import read_fixture, wav_bytes


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests' / 'fixtures' / 'narration-normalizer.json'


class FixtureProvider:
    provider_name = 'fixture'
    model = DEFAULT_MODEL

    def __init__(self):
        self.calls = []

    def synthesize(self, text, voice_name):
        self.calls.append((text, voice_name))
        return TtsResult(wav_bytes(), 'audio/wav', 0.1, len(text), Decimal('0'),
                         DEFAULT_MODEL, voice_name)


class NarrationNormalizerTests(unittest.TestCase):
    def test_fixture_cases_are_normalized_deterministically(self):
        cases = json.loads(FIXTURE.read_text(encoding='utf-8'))
        for case in cases:
            with self.subTest(case=case['name']):
                expected = case['expected']
                self.assertEqual(normalize_narration(case['input']), expected)
                self.assertEqual(normalize_narration(normalize_narration(case['input'])), expected)
                self.assertNotRegex(expected, r'\d|\[[0-9]|https?://|www\.')

    def test_unavailable_or_non_text_input_is_not_silently_coerced(self):
        with self.assertRaises(TypeError):
            normalize_narration(None)
        self.assertEqual(normalize_narration('A scale near 10^6 is useful.'),
                         'A scale near ten to the power of six is useful.')

    def test_common_pronounceable_names_and_acronyms_reach_tts_as_words(self):
        self.assertEqual(normalize_narration('NASA tested FedNow and ACH.'),
                         'NASA tested FedNow and ACH.')

    def test_parenthesized_citation_links_leave_money_and_date_punctuation_clean(self):
        cited = (
            'The fictional agency approved $2.5 billion ([1](https://example.test/amount)). '
            'The terms begin on Nov 12, 2026 ([2](https://example.test/date)).')
        self.assertEqual(
            normalize_narration(cited),
            'The fictional agency approved two and a half billion dollars. '
            'The terms begin on November twelfth, twenty twenty-six.')

    def test_audio_path_synthesizes_normalized_text_but_keeps_transcript_and_offsets(self):
        episode = copy.deepcopy(read_fixture())
        raw = ('The SEC reported $2.5 billion on Nov 12 '
               '[1](https://example.test/fictional-source).\n\nAI and PV rates rose 3–5%.')
        episode['chapters'][0]['spoken_text'] = raw
        provider = FixtureProvider()
        with tempfile.TemporaryDirectory() as temp:
            result = render_episode_audio(episode, episode['profile_id'], provider=provider,
                                          state_dir=Path(temp) / 'state')
        self.assertEqual(provider.calls[0][0],
                         'The S-E-C reported two and a half billion dollars on November twelfth.\n\n'
                         'A-I and P-V rates rose three to five percent.')
        self.assertEqual(result['episode']['chapters'][0]['spoken_text'], raw,
                         'on-screen transcript stays separate from the synthesis-only normalization')
        self.assertEqual(result['manifest']['chapters'][1]['start_seconds'], 0.1,
                         'chapter offsets remain based on measured audio durations')
        self.assertEqual(result['manifest']['normalizer_version'], NORMALIZER_VERSION)

    def test_existing_story_speech_builder_uses_the_same_normalizer(self):
        self.assertEqual(normalize_narration(_speak_text('Payment guidance changed on Nov 12. The SEC issued it.')),
                         'Payment guidance changed on November twelfth. The S-E-C issued it.')


if __name__ == '__main__':
    unittest.main()
