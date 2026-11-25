"""Provider-free regressions for evidence-linked, fictional spoken context."""
from copy import deepcopy
import unittest

from dev.test_guard import enforce_test_environment
enforce_test_environment()

from dev.ingest.daily_narration import (
    _deterministically_supported, _review_writer_draft,
)


class LinkedContextTests(unittest.TestCase):
    def setUp(self):
        self.evidence = [{
            'id': 'fictional-context', 'outlet': 'Fictional Transit Desk',
            'title': 'Fictional route planning',
            'text': 'Published timetables help passengers compare routes and plan connections.',
        }]
        self.sentence = {
            'text': 'For context, published timetables help passengers compare routes and plan connections.',
            'evidence_ids': ['fictional-context'], 'general_context': True,
        }

    def test_linked_context_preserves_deterministic_support_checks(self):
        self.assertTrue(_deterministically_supported(self.sentence, self.evidence))
        for changes in (
            {'evidence_ids': ['unknown']},
            {'text': 'For context, timetables serve 900 passengers.'},
            {'text': 'For context, passengers use Zorvex timetables to plan connections.'},
            {'text': 'For context, volcanic eruptions scatter molten rock.'},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(_deterministically_supported(
                    {**self.sentence, **changes}, self.evidence))

    def test_semantic_rejection_still_removes_linked_context(self):
        before = deepcopy(self.sentence)
        accepted, findings, reason = _review_writer_draft(
            {'fictional-story': [self.sentence]}, {('fictional-story', 0): False},
            {'fictional-story': self.evidence}, target_words=120, allow_soft_style=True)
        self.assertEqual(accepted, {'fictional-story': []})
        self.assertEqual(reason, 'narration_style_or_evidence_check')
        self.assertIn({'section_id': 'fictional-story', 'sentence_index': 0,
                       'code': 'unsupported_sentence', 'gate': 'hard', 'general_context': True,
                       'verifier_supported': False,
                       'deterministic_supported': True}, findings)
        self.assertEqual(self.sentence, before)
