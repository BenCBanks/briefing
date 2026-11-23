"""Fictional multi-round research: no provider clients, sockets or live state."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from dev.ingest.evidence_packs import validate_evidence_pack
from dev.ingest.retrieval.multiround import FixtureResearch, ResearchLimits, research_chapters


class MultiRoundTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((Path(__file__).parent / 'fixtures/multiround-research.json').read_text())
        self.backend = FixtureResearch(self.fixture['searches'], self.fixture['documents'])
        self.plan = self.fixture['plan']
        self.limits = ResearchLimits()

    def run_research(self, **kwargs):
        return research_chapters(self.plan, fixtures=self.backend, limits=self.limits,
                                 created_at=self.fixture['created_at'], **kwargs)

    def test_multiple_rounds_and_valid_chapter_packs_without_io(self):
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), \
                patch('builtins.open', side_effect=AssertionError('disk forbidden')):
            result = self.run_research()
        self.assertEqual(result.usage, {'rounds': 2, 'queries': 6, 'reads': 6,
                                       'chars': sum(len('\n\n'.join(p['text'] for p in doc['passages']))
                                                    for doc in self.fixture['documents'].values())})
        for chapter in result.chapters:
            self.assertEqual(chapter['stop_reason'], 'coverage_met')
            self.assertEqual(chapter['coverage'], {'sources': 2, 'passages': 4, 'met': True})
            self.assertFalse(chapter['missing_answers'])
            self.assertTrue(validate_evidence_pack(chapter['evidence_pack'].data))
            self.assertEqual(len(chapter['evidence_pack'].runtime_evidence()), 4)
        metadata = json.dumps(result.metadata())
        self.assertNotIn('Aster passengers reserve', metadata)
        self.assertNotIn('excerpt"', metadata)
        self.assertEqual(result.metadata(), self.run_research().metadata())

    def test_covered_questions_are_not_searched_again(self):
        query = self.plan['questions'][0]['query']
        rounds = self.backend.searches[query]
        rounds[0] += rounds[1]
        result = self.run_research()
        self.assertEqual(result.usage['queries'], 5)
        self.assertFalse(any(row['chapter'] == 'chapter-1' and row['round'] == 2 for row in result.trace))

    def test_ranked_round_robin_reading_and_budget(self):
        for rounds in self.backend.searches.values():
            rounds[0] += rounds[1]
        self.limits = replace(self.limits, max_reads=3)
        reads = []
        original = self.backend.read
        with patch.object(self.backend, 'read', side_effect=lambda url: (reads.append(url), original(url))[1]):
            result = self.run_research()
        self.assertEqual(reads, [rounds[0][0] for rounds in self.backend.searches.values()])
        self.assertTrue(all(c['coverage']['sources'] == 1 for c in result.chapters))
        self.assertTrue(all(c['stop_reason'] == 'read_budget' for c in result.chapters))
        self.assertEqual(result.usage['reads'], 3)

    def test_query_round_and_character_budgets(self):
        for limits, reason in [(replace(self.limits, max_queries=3), 'query_budget'),
                               (replace(self.limits, max_rounds=1), 'round_budget'),
                               (replace(self.limits, max_chars=1), 'character_budget')]:
            with self.subTest(reason=reason):
                self.limits = limits
                result = self.run_research()
                self.assertTrue(all(c['stop_reason'] == reason for c in result.chapters))
                self.assertLessEqual(result.usage['chars'], limits.max_chars)
                self.assertLessEqual(result.usage['queries'], limits.max_queries)
                self.assertTrue(all(c['missing_answers'] for c in result.chapters))

    def test_unreadable_and_irrelevant_pages_do_not_cover_a_question(self):
        query = self.plan['questions'][0]['query']
        url = self.backend.searches[query][0][0]
        self.backend.documents[url]['passages'][0]['answers'] = []
        self.backend.documents[url]['passages'][1]['answers'] = []
        self.backend.searches[query][0].insert(0, 'https://blocked.example/page')
        result = self.run_research()
        chapter = result.chapters[0]
        self.assertEqual(chapter['coverage']['sources'], 1)
        self.assertEqual(chapter['stop_reason'], 'round_budget')
        self.assertEqual(len(chapter['evidence_pack'].runtime_evidence()), 2)
        self.assertTrue(any(row['status'] == 'unreadable' for row in result.trace))

    def test_canonical_duplicates_shared_reads_and_question_scope(self):
        rows = self.plan['questions']
        first = self.backend.searches[rows[0]['query']][0][0]
        self.backend.searches[rows[0]['query']][0] += [first + '?utm_source=fixture#top', first]
        self.backend.searches[rows[1]['query']][0] = [first]
        self.backend.documents[first]['passages'][0]['answers'].append(rows[1]['sub_question'])
        self.limits = replace(self.limits, max_rounds=1)
        result = self.run_research()
        self.assertEqual(result.usage['reads'], 2)
        self.assertEqual(len(result.chapters[0]['evidence_pack'].runtime_evidence()), 2)
        self.assertEqual(len(result.chapters[1]['evidence_pack'].runtime_evidence()), 1)
        self.assertEqual(set(result.chapters[0]['evidence_pack'].runtime_evidence()) &
                         set(result.chapters[1]['evidence_pack'].runtime_evidence()),
                         set(result.chapters[1]['evidence_pack'].runtime_evidence()))

    def test_mirrored_documents_and_repeated_passages_do_not_inflate_coverage(self):
        query = self.plan['questions'][0]['query']
        first, second = [r[0] for r in self.backend.searches[query]]
        self.backend.documents[second] = deepcopy(self.backend.documents[first])
        result = self.run_research()
        self.assertEqual(result.chapters[0]['coverage'], {'sources': 1, 'passages': 2, 'met': False})
        # A repeated paragraph within a different document also does not add depth.
        self.backend.documents[second]['passages'].append(deepcopy(self.backend.documents[first]['passages'][0]))
        self.assertEqual(self.run_research().chapters[0]['coverage']['passages'], 2)

    def test_errors_are_bounded_and_sanitized(self):
        with patch.object(self.backend, 'search', side_effect=RuntimeError('SECRET payload')):
            result = self.run_research()
        self.assertEqual(result.usage['queries'], 9)
        self.assertEqual(result.usage['reads'], 0)
        self.assertNotIn('SECRET', json.dumps(result.metadata()))
        with patch.object(self.backend, 'read', side_effect=RuntimeError('PRIVATE body')):
            result = self.run_research()
        self.assertEqual(result.usage['reads'], 6)
        self.assertNotIn('PRIVATE', json.dumps(result.metadata()))
        self.assertTrue(all(not c['coverage']['met'] for c in result.chapters))

    def test_late_reads_are_discarded_and_cancellation_stops_calls(self):
        now = [0]
        original = self.backend.read

        def late(url):
            now[0] = 31
            return original(url)

        with patch.object(self.backend, 'read', side_effect=late):
            result = self.run_research(clock=lambda: now[0])
        self.assertEqual(result.usage['reads'], 1)
        self.assertEqual(result.usage['chars'], 0)
        self.assertTrue(all(c['stop_reason'] == 'deadline' for c in result.chapters))
        result = self.run_research(cancelled=lambda: True)
        self.assertEqual(result.usage['queries'], 0)
        self.assertTrue(all(c['stop_reason'] == 'cancelled' for c in result.chapters))

    def test_bad_inputs_fail_before_research(self):
        for key, value in [('max_reads', 0), ('max_queries', True), ('timeout_seconds', float('nan'))]:
            with self.assertRaises(ValueError):
                replace(self.limits, **{key: value})
        with self.assertRaises(TypeError):
            research_chapters(self.plan, fixtures=None, created_at=self.fixture['created_at'])
        self.plan['questions'][1] = deepcopy(self.plan['questions'][0])
        with patch.object(self.backend, 'search') as search, self.assertRaises(ValueError):
            self.run_research()
        search.assert_not_called()

    def test_query_cap_still_allows_discovered_pages_to_be_read(self):
        for rounds in self.backend.searches.values():
            rounds[0] += rounds[1]
        self.limits = replace(self.limits, max_queries=3, reads_per_question_round=1)
        result = self.run_research()
        self.assertEqual(result.usage['queries'], 3)
        self.assertEqual(result.usage['reads'], 6)
        self.assertTrue(all(c['coverage']['met'] for c in result.chapters))

    def test_empty_searches_produce_valid_empty_packs(self):
        self.backend.searches = {}
        result = self.run_research()
        self.assertEqual(result.usage['reads'], 0)
        for row in result.metadata()['chapters']:
            self.assertEqual(row['stop_reason'], 'round_budget')
            self.assertTrue(validate_evidence_pack(row['evidence_pack']))
            self.assertEqual(row['evidence_pack']['sources'], [])
            self.assertEqual(row['missing_answers'], [row['question']])

    def test_capped_results_and_unsafe_hints_never_reach_reader(self):
        first = self.plan['questions'][0]['query']
        self.backend.searches[first][0] = ['http://localhost/private',
                                          'https://127.0.0.1/private',
                                          self.backend.searches[first][0][0]]
        self.limits = replace(self.limits, results_per_query=2, max_rounds=1)
        with patch.object(self.backend, 'read', wraps=self.backend.read) as read:
            result = self.run_research()
        self.assertEqual(result.usage['reads'], 2)
        self.assertFalse(result.chapters[0]['evidence_pack'].runtime_evidence())
        self.assertTrue(all('.example/' in c.args[0] for c in read.call_args_list))

    def test_cancel_during_search_discards_result_and_skips_reads(self):
        cancel = [False]
        original = self.backend.search

        def search(*args):
            cancel[0] = True
            return original(*args)

        with patch.object(self.backend, 'search', side_effect=search):
            result = self.run_research(cancelled=lambda: cancel[0])
        self.assertEqual(result.usage['queries'], 1)
        self.assertEqual(result.usage['reads'], 0)
        self.assertTrue(all(c['stop_reason'] == 'cancelled' for c in result.chapters))

    def test_changed_document_has_new_passage_identity(self):
        before = self.run_research().chapters[0]['evidence_pack']
        url = self.backend.searches[self.plan['questions'][0]['query']][0][0]
        self.backend.documents[url]['passages'][0]['text'] += ' This fixture is revised.'
        after = self.run_research().chapters[0]['evidence_pack']
        self.assertNotEqual(before.data['pack_id'], after.data['pack_id'])
        self.assertEqual(len(set(before.runtime_evidence()) & set(after.runtime_evidence())), 2)


if __name__ == '__main__':
    unittest.main()
