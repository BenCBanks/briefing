import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.retrieval.planner import QUERY_BUDGETS, plan_queries, planning_prompt
from ingest.retrieval.multiround import FixtureResearch, ResearchLimits, research_chapters


FIXTURES = Path(__file__).parent / 'fixtures' / 'retrieval' / 'planner_topics.json'


class QueryPlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.topics = json.loads(FIXTURES.read_text(encoding='utf-8'))

    def test_ten_fictional_topics_get_unique_ordered_queries_with_length_budgets(self):
        self.assertEqual(len(self.topics), 10)
        for topic in self.topics:
            for length, expected in QUERY_BUDGETS.items():
                with self.subTest(topic=topic['id'], length=length):
                    plan = plan_queries(topic, style='balanced', length=length)
                    self.assertEqual(plan['query_budget'], expected)
                    self.assertEqual(len(plan['questions']), expected)
                    self.assertEqual(len(plan['queries']), expected)
                    self.assertEqual(len({query.casefold() for query in plan['queries']}), expected)
                    self.assertEqual(len({row['sub_question'].casefold()
                                          for row in plan['questions']}), expected)
                    self.assertTrue(all(row['query'].startswith(topic['label'])
                                        for row in plan['questions']))
                    self.assertFalse(plan['planner_used'])

    def test_deterministic_fallback_is_repeatable_and_styles_change_order(self):
        topic = self.topics[0]
        first = plan_queries(topic, style='balanced', length='long')
        self.assertEqual(first, plan_queries(topic, style='balanced', length='long'))
        context = plan_queries(topic, style='context', length='short')
        impact = plan_queries(topic, style='impact', length='short')
        self.assertEqual(context['questions'][0]['focus'], 'Relevant context')
        self.assertEqual(impact['questions'][0]['focus'], 'People and places affected')
        self.assertEqual(first['questions'][0]['focus'], 'Recent developments')

    def test_context_fallback_answers_four_factual_learning_angles_with_reference_queries(self):
        topic = {'label': 'Fictional parcel lockers',
                 'scope': 'library collection process and resident access'}
        plan = plan_queries(topic, style='context', length='medium')
        self.assertEqual([row['focus'] for row in plan['questions']], [
            'Relevant context', 'History and change', 'Comparison', 'Practical learning'])
        expected_terms = ('how it works', 'history', 'compare', 'practical')
        for row, term in zip(plan['questions'], expected_terms):
            self.assertIn(term, row['query'].casefold())
            self.assertNotIn('latest reported developments', row['query'].casefold())
            self.assertRegex(row['query'].casefold(), r'\b(?:reference|primary|guidance|evidence)\b')
        self.assertNotIn('recent context', plan['questions'][0]['sub_question'].casefold())
        self.assertNotIn('developments', plan['questions'][0]['sub_question'].casefold())
        self.assertIn('background', plan['questions'][0]['sub_question'].casefold())

        searches, documents = {}, {}
        for index, row in enumerate(plan['questions'], 1):
            url = f'https://fixture.example/reference-angle-{index}'
            searches[row['query']] = [[url]]
            documents[url] = {'passages': [
                {'text': f'Fictional reference record answers the {row["focus"].casefold()} question.',
                 'answers': [row['sub_question']]},
                {'text': f'Fictional primary evidence supplies a distinct supported fact for {row["focus"].casefold()}.',
                 'answers': [row['sub_question']]},
            ]}
        result = research_chapters(
            plan, fixtures=FixtureResearch(searches, documents),
            limits=ResearchLimits(max_rounds=1, max_queries=4, max_reads=4,
                                  min_sources=1, min_passages=2),
            created_at='2026-11-21T12:00:00Z')
        self.assertTrue(all(chapter['coverage']['met'] for chapter in result.chapters))
        self.assertEqual([chapter['question'] for chapter in result.chapters],
                         [row['sub_question'] for row in plan['questions']])
        self.assertEqual(result.usage['reads'], 4)

    def test_injected_planner_is_mockable_ordered_deduplicated_and_filled(self):
        topic = self.topics[1]
        mock_planner = Mock(return_value={
            'questions': [
                {'sub_question': 'What construction updates have been reported?',
                 'query': 'Fictional housing construction latest permits'},
                {'sub_question': 'What construction updates have been reported?',
                 'query': 'Fictional housing construction recent permits'},
                {'sub_question': 'Which places are affected by the work?',
                 'query': 'Fictional housing construction affected locations'},
            ]
        })
        result = plan_queries(topic, style='news', length='medium', planner=mock_planner)
        mock_planner.assert_called_once()
        prompt = mock_planner.call_args.args[0]
        self.assertIn('exactly 4 distinct useful rows', prompt)
        self.assertIn('untrusted data', prompt)
        self.assertIn('factual personal podcast', prompt)
        self.assertIn('official, primary, institutional, or reference sources', prompt)
        self.assertEqual(result['queries'][0], 'Fictional housing construction latest permits')
        self.assertEqual(len(result['questions']), 4)
        self.assertEqual(len(set(result['queries'])), 4)
        self.assertTrue(result['planner_used'])

    def test_failed_or_off_topic_mock_uses_deterministic_fallback(self):
        topic = self.topics[2]
        fallback = plan_queries(topic, length='short')
        self.assertEqual(plan_queries(topic, length='short', planner=Mock(side_effect=RuntimeError())), fallback)
        off_topic = Mock(return_value={'questions': [
            {'sub_question': 'What happened in a different place?', 'query': 'unrelated astronomy discovery'}
        ]})
        self.assertEqual(plan_queries(topic, length='short', planner=off_topic), fallback)

    def test_validation_and_input_caps(self):
        with self.assertRaisesRegex(ValueError, 'topic label'):
            plan_queries({'scope': 'missing label'})
        with self.assertRaisesRegex(ValueError, 'unsupported planning style'):
            plan_queries('fictional housing', style='speculation')
        with self.assertRaisesRegex(ValueError, 'unsupported planning length'):
            plan_queries('fictional housing', length='unlimited')
        long_topic = {'label': 'Fictional topic ' + ('x' * 300), 'scope': 'sample scope'}
        self.assertLessEqual(max(map(len, plan_queries(long_topic, length='short')['queries'])), 500)
        self.assertIn('Topic data:', planning_prompt('fictional transit'))


if __name__ == '__main__':
    unittest.main()
