"""Mocked any-topic planner/retrieval sanity over unusual professional topics."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
from pathlib import Path
import unittest

from dev.ingest.retrieval.multiround import FixtureResearch, ResearchLimits, research_chapters
from dev.ingest.retrieval.planner import plan_queries

FIXTURE = Path(__file__).parent / 'fixtures' / 'retrieval' / 'odd_topics.json'
PROFESSIONAL_LIBRARY = Path(__file__).parent / 'fixtures' / 'professional-topic-library.json'


class MockTopicPlanner:
    """Return topic-specific mock results based on the prompt's data payload."""
    def __init__(self, topics):
        self.topics = {row['label']: row for row in topics}
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        topic = json.loads(prompt.split('Topic data: ', 1)[1])
        fixture = self.topics[topic['label']]
        questions = []
        for angle in fixture['angles']:
            questions.append({
                'sub_question': f"What evidence is available about {angle} for {fixture['label']}?",
                'query': f"{fixture['label']} {fixture['scope']} {angle} official reports",
            })
        return {'questions': questions}


class AnyTopicSanityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.topics = json.loads(FIXTURE.read_text(encoding='utf-8'))

    def test_fifteen_odd_topics_plan_specific_questions_and_retrieve_fixture_evidence(self):
        self.assertEqual(len(self.topics), 15)
        planner = MockTopicPlanner(self.topics)
        for topic in self.topics:
            with self.subTest(topic=topic['label']):
                plan = plan_queries(topic, style='balanced', length='short', planner=planner)
                self.assertEqual(len(plan['questions']), 3)
                self.assertTrue(plan['planner_used'])
                self.assertEqual(len({row['sub_question'].casefold() for row in plan['questions']}), 3)
                self.assertEqual(len({row['query'].casefold() for row in plan['questions']}), 3)
                for row, angle in zip(plan['questions'], topic['angles']):
                    self.assertIn(topic['label'].casefold(), row['sub_question'].casefold())
                    self.assertIn(angle.casefold(), row['sub_question'].casefold())
                    self.assertIn(topic['label'].casefold(), row['query'].casefold())
                    self.assertIn(topic['scope'].casefold(), row['query'].casefold())
                    self.assertLessEqual(len(row['query']), 500)
                    self.assertNotIn('\n', row['query'])

                searches, documents = {}, {}
                for index, row in enumerate(plan['questions'], 1):
                    url = f"https://fixture.example/{topic['label'].encode('utf-8').hex()}/{index}"
                    searches[row['query']] = [[url]]
                    documents[url] = {'passages': [{
                        'text': f"Fictional source reports evidence about {topic['angles'][index - 1]}.",
                        'answers': [row['sub_question']],
                    }]}
                backend = FixtureResearch(searches, documents)
                result = research_chapters(
                    plan, fixtures=backend,
                    limits=ResearchLimits(max_rounds=1, min_sources=1, min_passages=1),
                    created_at='2026-11-19T12:00:00Z',
                )
                self.assertEqual(result.usage['queries'], 3)
                self.assertEqual(result.usage['reads'], 3)
                self.assertTrue(all(chapter['coverage']['met'] for chapter in result.chapters))
                self.assertEqual([row['query'] for row in plan['questions']],
                                 [row['query'] for row in plan['questions'] if row['query'] in searches])
        self.assertEqual(len(planner.prompts), 15)

    def test_shared_professional_library_plans_and_retrieves_all_ten_topics(self):
        library = json.loads(PROFESSIONAL_LIBRARY.read_text(encoding='utf-8'))
        self.assertTrue(library['fixture_only'])
        self.assertEqual(len(library['topics']), 10)
        for topic in library['topics']:
            with self.subTest(topic=topic['id']):
                url = topic['source']['url']
                planner_rows = [{**row, 'query': f"{topic['label']} {row['query']}"}
                                for row in topic['questions']]
                raw = {'questions': planner_rows}
                plan = plan_queries(topic, style='balanced', length='short', planner=lambda _prompt: raw)
                self.assertTrue(plan['planner_used'])
                self.assertEqual(len(plan['questions']), 3)
                self.assertEqual([row['sub_question'] for row in plan['questions']],
                                 [row['sub_question'] for row in topic['questions']])
                searches = {row['query']: [[url]] for row in plan['questions']}
                documents = {url: {'passages': [
                    {'text': row['text'], 'answers': [row['sub_question']]}
                    for row in topic['questions']]}}
                researched = research_chapters(
                    plan, fixtures=FixtureResearch(searches, documents),
                    limits=ResearchLimits(max_rounds=1, min_sources=1, min_passages=1),
                    created_at='2026-11-19T12:00:00Z')
                self.assertEqual(len(researched.chapters), 3)
                self.assertTrue(all(row['coverage']['met'] for row in researched.chapters))
                self.assertEqual(researched.usage['reads'], 1, 'the three fixture angles share one source read')

if __name__ == '__main__':
    unittest.main()
