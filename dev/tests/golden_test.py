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
                            for _, _, question in golden.QUESTIONS))

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
                'scores': {'answers_question': 4, 'spoken': 5}, 'notes': 'Clear and grounded.',
                'lowest_sentences': [{'quote': 'One sentence.', 'score': 3, 'issue': 'Vague.'},
                                     {'quote': 'Another sentence.', 'score': 4, 'issue': 'Long.'}]}
        report = golden.render_report([case], Decimal('0.01234'), '2026-11-23')
        self.assertIn('2026-11-23', report)
        self.assertIn('$0.01234', report)
        self.assertIn('answers question: 4', report)
        self.assertIn('Two weakest sentences', report)
        self.assertIn('One plain paragraph.', report)

    def test_only_and_limit_select_the_requested_six_before_cases(self):
        judgment = {
            'scores': {name: 4 for name in golden.JUDGE_SCHEMA['properties']['scores']['required']},
            'overall': 4, 'notes': 'Fixture judgement.',
            'lowest_sentences': [{'quote': 'Weak one.', 'score': 3, 'issue': 'Flat.'},
                                 {'quote': 'Weak two.', 'score': 3, 'issue': 'Vague.'}],
        }
        fake_key = SimpleNamespace(is_file=lambda: True)
        selected = ['instant-payments', 'salary-negotiation', 'battery-recycling',
                    'nuclear-solar', 'europa-life', 'daily-ai-chips']
        with tempfile.TemporaryDirectory() as output:
            with patch.object(golden, 'KEY_FILE', fake_key), \
                 patch.object(golden, 'ledger_snapshot', return_value={'daily': {'estimated_cost_usd': '0'}}), \
                 patch.object(golden, 'ledger_cost_delta', return_value=Decimal('0')), \
                 patch.object(golden, 'judge', return_value=judgment), \
                 patch.object(golden, '_episode', return_value={'transcript': 'A fixture script.'}) as episode, \
                 patch.object(golden, '_daily', return_value={'transcript': 'A daily fixture.'}) as daily:
                report_path = golden.run('2026-11-23-before', report_dir=Path(output),
                                         limit=6, only=selected)
                report_text = report_path.read_text()
        self.assertEqual(episode.call_count, 5)
        daily.assert_called_once()
        self.assertEqual(report_path.name, '2026-11-23-before.md')
        self.assertEqual(report_text.count('## '), 6)

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
