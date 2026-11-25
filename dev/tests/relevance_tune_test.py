"""Mocked relevance-tune regression cases; no provider/network access."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
import os
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dev.ingest import write_edition

FIXTURE = ROOT / 'dev/tests/fixtures/relevance-tune.json'


class RelevanceTuneTests(unittest.TestCase):
    def setUp(self):
        self.cases = json.loads(FIXTURE.read_text(encoding='utf-8'))['cases']

    @staticmethod
    def _run_case(case, enabled=True, daily_word_target=None):
        sentences = [row.strip() for row in re.split(r'(?<=[.!?])\s+', case['excerpt']) if row.strip()]
        if not sentences:
            sentences = [case['excerpt']]
        evidence = {}
        sentence_ids = []
        for index, sentence in enumerate(sentences, 1):
            sentence_id = f"fixture-{case['id']}-{index}"
            sentence_ids.append(sentence_id)
            evidence[sentence_id] = {'id': sentence_id, 'source_number': 1,
                                     'text': sentence, 'kind': 'news'}
        citation = {'number': 1, 'registry_id': 'fixture-outlet', 'publisher': 'Fictional Desk',
                    'title': case['title'], 'url': 'https://fixture.example.test/story',
                    'published': '2026-11-18T10:00:00Z', 'source_class': 'news',
                    'evidence_depth': 'full-text', 'evidence_chars': 500,
                    'source_sentence_count': 3, 'source_sentences': sentence_ids}
        cluster = {'id': case['id'], 'rows': [{'item': {'title': case['title']},
                                               'source': {'id': 'fixture-outlet'}}]}
        prompts = []

        def model(system, user, schema, *_args, **_kwargs):
            prompts.append((system, user, schema))
            claims = ([{'section': 'what_happened', 'text': sentences[0],
                        'evidence_ids': [sentence_ids[0]]}] if case['expected'] else [])
            return {'relevant': case['expected'],
                    'relevance_reason': 'The fixture event matches the selected scope.' if case['expected']
                    else 'The fixture event is outside the selected scope.',
                    'heading': case['title'], 'claims': claims, 'quote': None,
                    'quote_attribution': None, 'quote_evidence_ids': [],
                    'background_text': None, 'background_section': None}

        env = {'BRIEFING_RELEVANCE_TUNE': 'on' if enabled else 'off', 'BRIEFING_RELEVANCE': 'off'}
        with patch.dict(os.environ, env), patch.object(write_edition, '_background_sentences',
                                                       return_value=([], {}, [])):
            story, _evidence, _dropped, _citations = write_edition._cluster_story(
                cluster, case['topic'], lambda *_args: None, model, 'fictional-test-key',
                extracted=([citation], evidence), daily_word_target=daily_word_target)
        return story, prompts

    def test_domain_examples_pass_and_acronym_or_housing_false_positives_fail(self):
        for case in self.cases:
            with self.subTest(case=case['id']):
                story, prompts = self._run_case(case)
                self.assertEqual(bool(story.get('drop_reason') != 'semantic_irrelevance'), case['expected'])
                self.assertEqual(len(prompts), 1)
                self.assertIn(write_edition.RELEVANCE_TUNE_PROMPT, prompts[0][0])
                self.assertIn('preserve all profile exclusions', prompts[0][0].lower())
                if case['topic']['id'] == 'health':
                    scoped = write_edition._matches_topic_scope(
                        case['topic'], case['title'] + ' ' + case['excerpt'])
                    self.assertEqual(scoped, case['expected'], case['id'])
                if case['id'] == 'ai-bank-risk-report':
                    self.assertTrue(write_edition._matches_topic_scope(
                        case['topic'], case['title'] + ' ' + case['excerpt']))

    def test_tuning_is_opt_in_and_default_prompt_is_unchanged(self):
        case = next(row for row in self.cases if row['id'] == 'medicaid-national-policy')
        _story, prompts = self._run_case(case, enabled=False)
        self.assertEqual(prompts[0][0], write_edition.SYSTEM_PROMPT)
        self.assertNotIn(write_edition.RELEVANCE_TUNE_PROMPT, prompts[0][0])

    def test_daily_story_prompt_expands_only_with_cited_retrieved_context(self):
        case = next(row for row in self.cases if row['id'] == 'medicaid-national-policy')
        _story, prompts = self._run_case(case, enabled=False, daily_word_target=165)
        system, user, _schema = prompts[0]
        payload = json.loads(user)
        self.assertIn('aim for about 165 spoken words', system)
        self.assertIn('do not pad with generic filler', system.lower())
        self.assertIn('one thing to watch', system)
        self.assertIn('first body sentence must add information beyond the headline', system)
        self.assertEqual(payload['daily_story_word_target'], 165)
        self.assertTrue(payload['source_sentences_only'])


if __name__ == '__main__':
    unittest.main()
