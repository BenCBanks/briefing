import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import unittest

from dev.ingest.episodes.voice_quality import episode_voice_quality


OPENING = 'Instant payment networks move instructions between banks.'
BODY = 'Banks compare each instruction with account records before settlement.'
CLOSING = 'Shared rules coordinate how banks settle a payment.'


def episode(*, opening=OPENING, closing=CLOSING, title='How payment networks settle',
            claim='A sending bank forwards the payment instruction through a shared network.',
            chapter_title='Payment coordination', target_words=500, filler_count=40):
    middle = [BODY] * filler_count
    transcript = ' '.join([opening, claim, *middle, closing])
    return {'title': title, 'target_words': target_words, 'requested_minutes': 3,
            'word_count': len(transcript.split()), 'transcript': transcript,
            'chapters': [{'title': chapter_title, 'sentences': [
                {'text': opening, 'kind': 'opening'},
                {'text': claim, 'kind': 'claim'},
                *[{'text': line, 'kind': 'claim'} for line in middle],
                {'text': closing, 'kind': 'closing'},
            ]}]}


class EpisodeVoiceQualityTests(unittest.TestCase):
    def test_editorial_rules_have_table_driven_failures(self):
        cases = [
            ('direct template', episode(opening="Here's your five-minute briefing on payment systems."),
             {'direct_opening'}),
            ('rhetorical question', episode(opening='How do banks move payments between accounts?'),
             {'direct_opening', 'no_rhetorical_question_opener'}),
            ('disclaimer ending', episode(closing='This is not financial advice.'),
             {'no_disclaimer_ending'}),
            ('repeated chapter headline', episode(
                chapter_title='Payment networks settle bank transfers',
                claim='Payment networks settle bank transfers through shared clearing rules.'),
             {'no_repeated_headline'}),
            ('under target floor', episode(target_words=500, filler_count=3),
             {'minimum_words'}),
        ]
        for name, draft, expected_failed in cases:
            with self.subTest(name=name):
                report = episode_voice_quality(draft)
                self.assertTrue(expected_failed.issubset(set(report['failed'])), report)

    def test_supported_shape_at_minimum_floor_passes_without_provider_calls(self):
        report = episode_voice_quality(episode(target_words=500, filler_count=40))
        self.assertTrue(report['passed'], report)
        self.assertEqual(report['minimum_words'], 300)
        self.assertGreaterEqual(report['spoken_words'], 300)

