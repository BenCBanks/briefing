"""Fixture-only tests for the opt-in real-output quality harness."""

from decimal import Decimal
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from dev import golden


class GoldenHarnessTests(unittest.TestCase):
    def test_fixed_question_and_profile_sets_are_generic(self):
        self.assertEqual(len(golden.QUESTIONS), 12)
        self.assertEqual([row[0] for row in golden.DAILY_PROFILES],
                         ['AI chips', 'Climate policy', 'Space launches'])
        self.assertTrue(all('fictional' not in question.casefold()
                            for _, question in golden.QUESTIONS))

    def test_profile_uses_one_generic_topic(self):
        profile = golden._profile('AI chips', 'digital', 'AI chip design')
        self.assertEqual(len(profile['topics']), 1)
        self.assertEqual(profile['topics'][0]['label'], 'AI chips')
        self.assertTrue(profile['topics'][0]['mustFollow'])

    def test_isolated_state_restores_environment_without_test_ledger_override(self):
        old = {key: os.environ.get(key) for key in
               ('BRIEFING_STATE_DIR', 'BRIEFING_EPISODES', 'BRIEFING_TESTING',
                'BRIEFING_TEST_LEDGER_DIR')}
        try:
            os.environ['BRIEFING_TEST_LEDGER_DIR'] = '/tmp/unchanged-golden-ledger'
            with golden.isolated_state() as state:
                self.assertEqual(os.environ['BRIEFING_STATE_DIR'], str(state))
                self.assertEqual(os.environ['BRIEFING_EPISODES'], 'real')
                self.assertNotIn('BRIEFING_TESTING', os.environ)
                self.assertEqual(os.environ['BRIEFING_TEST_LEDGER_DIR'],
                                 '/tmp/unchanged-golden-ledger')
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_word_count_handles_spoken_contractions_and_hyphens(self):
        self.assertEqual(golden.word_count("That's a well-known system."), 4)

    def test_report_includes_scores_cost_and_full_script(self):
        case = {'kind': 'episode', 'name': 'example', 'question': 'How does it work?',
                'script': 'One plain paragraph.', 'words': 3, 'seconds': 4.2,
                'cost_usd': '0.01234', 'overall': 4,
                'scores': {'answers_question': 4, 'spoken': 5}, 'notes': 'Clear and grounded.'}
        report = golden.render_report([case], Decimal('0.01234'), '2026-11-23')
        self.assertIn('2026-11-23', report)
        self.assertIn('$0.01234', report)
        self.assertIn('answers question: 4', report)
        self.assertIn('One plain paragraph.', report)

    def test_judge_rejects_invalid_fixture_scores(self):
        result = {'scores': {name: 4 for name in golden.JUDGE_SCHEMA['properties']['scores']['required']},
                  'overall': 7, 'notes': 'Invalid overall'}
        fake_key = SimpleNamespace(is_file=lambda: True,
                                   read_text=lambda **_kwargs: 'fictional-key')
        with patch.object(golden, 'KEY_FILE', fake_key), \
             patch.object(golden, 'call_gemini_json', return_value=result):
            with self.assertRaisesRegex(ValueError, '1–5'):
                golden.judge('Question?', 'Script.', [])


if __name__ == '__main__':
    unittest.main()
