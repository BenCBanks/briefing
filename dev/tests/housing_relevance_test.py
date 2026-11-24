"""Fixture-only tests for the opt-in candidate relevance check."""

import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dev.ingest.write_edition import (
    RELEVANCE_SCHEMA, RELEVANCE_SYSTEM_PROMPT, SYSTEM_PROMPT, WRITER_SCHEMA,
    _cluster_story, _quick_hit_items,
)

FIXTURE = ROOT / 'dev/tests/fixtures/housing-relevance-candidates.json'


def _evidence_for(candidate):
    evidence, ids = {}, []
    for number, text in enumerate(candidate['excerpt'], 1):
        evidence_id = f"{candidate['id']}-e{number}"
        evidence[evidence_id] = {'id': evidence_id, 'source_number': 1, 'text': text, 'kind': 'news'}
        ids.append(evidence_id)
    excerpt_chars = sum(len(candidate['excerpt'][i]) for i in range(len(candidate['excerpt'])))
    citation = {
        'number': 1, 'registry_id': 'fictional-housing-source', 'publisher': 'Fictional Housing Ledger',
        'title': candidate['title'], 'url': f"https://housing.example.test/{candidate['id']}",
        'published': '2026-11-18T12:00:00Z', 'fetched_at': '2026-11-18T12:00:00Z',
        'source_class': 'news', 'owner': 'fictional housing ledger', 'official': False,
        'evidence_depth': 'full-text', 'evidence_chars': excerpt_chars,
        'source_sentence_count': len(ids), 'source_sentences': ids,
    }
    return evidence, [citation]


def _mock_response(candidate, evidence, enabled):
    first_id = next(iter(evidence))
    first_text = evidence[first_id]['text']
    reason = ('The event matches the housing topic and geography.' if candidate['expected_relevant'] else
              'The excerpt concerns a different domain; shared wording does not make it housing news. ' * 8)
    output = {
        'relevant': True,
        'heading': candidate['title'],
        'claims': [{'section': 'what_happened', 'text': first_text, 'evidence_ids': [first_id]}],
        'quote': None, 'quote_attribution': None, 'quote_evidence_ids': [],
        'background_text': None, 'background_section': None,
    }
    if enabled:
        output['candidate_relevant'] = candidate['expected_relevant']
        output['candidate_relevance_reason'] = reason
    else:
        # Verify the feature is truly off: this new opt-in field is ignored.
        output['candidate_relevant'] = False
        output['candidate_relevance_reason'] = 'feature disabled'
    return output


class HousingRelevanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(FIXTURE.read_text(encoding='utf-8'))
        cls.topic_by_id = {topic['id']: topic for topic in cls.data['profile']['topics']}

    def run_candidate(self, candidate, enabled):
        evidence, citations = _evidence_for(candidate)
        calls = []

        def mock_model(prompt, _payload, schema, _api_key, **_kwargs):
            calls.append((prompt, schema))
            return _mock_response(candidate, evidence, enabled)

        env = {'BRIEFING_RELEVANCE': 'on'} if enabled else {}
        with patch.dict(os.environ, env, clear=False):
            if not enabled:
                os.environ.pop('BRIEFING_RELEVANCE', None)
            result = _cluster_story({'id': candidate['id'], 'rows': []},
                self.topic_by_id[candidate['topic_id']], lambda *_args: None, mock_model,
                'fictional-test-key', extracted=(citations, evidence))
        return result, calls

    def test_opt_in_relevance_yield_on_fictional_housing_set(self):
        positives = negatives = accepted = rejected = calls_total = 0
        for candidate in self.data['candidates']:
            (story, _evidence, _dropped, _citations), calls = self.run_candidate(candidate, enabled=True)
            self.assertEqual(len(calls), 1, f"{candidate['id']} must use only the existing writer call")
            self.assertIs(calls[0][0], RELEVANCE_SYSTEM_PROMPT)
            self.assertIs(calls[0][1], RELEVANCE_SCHEMA)
            self.assertIn('candidate_relevant', calls[0][1]['properties'])
            calls_total += len(calls)
            if candidate['expected_relevant']:
                positives += 1
                self.assertTrue(story and story.get('claims'), candidate['title'])
                self.assertLessEqual(len(story.get('candidate_relevance_reason', '')), 240)
                accepted += 1
            else:
                negatives += 1
                self.assertEqual(story['drop_reason'], 'semantic_irrelevance', candidate['title'])
                self.assertLessEqual(len(story['relevance_reason']), 240)
                rejected += 1

        precision = accepted / (accepted + (negatives - rejected))
        recall = accepted / positives
        yield_rate = accepted / len(self.data['candidates'])
        print(f'housing relevance on: {accepted}/{positives} on-topic passed; '
              f'{rejected}/{negatives} off-topic rejected; precision={precision:.0%}; '
              f'recall={recall:.0%}; candidate yield={yield_rate:.0%}; calls={calls_total}')
        self.assertEqual(accepted, positives)
        self.assertEqual(rejected, negatives)
        self.assertEqual(calls_total, len(self.data['candidates']))

    def test_default_off_keeps_legacy_prompt_and_schema(self):
        candidate = next(row for row in self.data['candidates'] if not row['expected_relevant'])
        (story, _evidence, _dropped, _citations), calls = self.run_candidate(candidate, enabled=False)
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][0], SYSTEM_PROMPT)
        self.assertIs(calls[0][1], WRITER_SCHEMA)
        self.assertTrue(story and story.get('claims'), 'candidate_relevant is ignored while flag is off')
        self.assertNotIn('candidate_relevant', WRITER_SCHEMA['properties'])

    def test_rejected_candidate_is_not_rendered_as_a_quick_hit(self):
        candidate = next(row for row in self.data['candidates'] if row['id'] == 'off-01')
        source = {'id': 'fictional-housing-source', 'name': 'Fictional Housing Ledger'}
        item = {'source_id': source['id'], 'title': candidate['title'],
                'url': f"https://housing.example.test/{candidate['id']}",
                'snippet': candidate['excerpt'][0]}
        clusters = {'home-building-construction': [{'id': candidate['id'],
                    'rows': [{'item': item, 'source': source}]}]}
        hits = _quick_hit_items(clusters, set(), {candidate['id']}, {'__source_ids__': {source['id']: source}})
        self.assertEqual(hits, [])

    def test_fixture_contains_requested_profile_and_known_distractors(self):
        words = self.data['profile']['words'].casefold()
        for phrase in ('fernvale pv', 'northpines sp', 'home building', 'construction', 'affordable housing'):
            self.assertIn(phrase, words)
        titles = {candidate['title'] for candidate in self.data['candidates']}
        self.assertTrue({'Building a Permanent Home for a Space Shuttle',
                         'CMS parents push for play-based kindergarten',
                         'Stonebridge Building Collapse'} <= titles)
        self.assertGreater(sum(row['expected_relevant'] for row in self.data['candidates']), 0)


if __name__ == '__main__':
    unittest.main()
