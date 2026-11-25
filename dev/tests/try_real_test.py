"""Fixture-only contract tests for the one-command real check runner."""
import os
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from dev.try_real import (_print_funnel, _print_result, assert_isolated_state, fictional_profile,
                          isolated_run, ledger_cost_delta)
from dev.ingest.usage import UsageCapReached, UsageLedger


class TryRealTests(unittest.TestCase):
    def test_episode_report_pairs_research_speed_with_evidence_counts(self):
        output = StringIO()
        result = {'word_count': 80, 'duration_minutes': 1, 'transcript': 'A fictional script.',
                  'sources_read': 7, 'audio': {'status': 'not_recorded'},
                  'timing': {'research_and_reading_seconds': 42.5},
                  'retrieval': {'research_budget': {
                      'research_elapsed_seconds': 42.5, 'research_budget_seconds': 165,
                      'slow_hosts_dropped': 2, 'read_timeouts': 3,
                      'read_funnel': {'attempted': 9, 'read': 7, 'skipped_deadline': 1,
                                      'skipped_host_gap': 0, 'failed': 1}}}}
        with redirect_stdout(output):
            _print_result('episode', result, {'daily': {'estimated_cost_usd': 0}}, 0)
        self.assertIn('Evidence: 7 sources read; 2 slow hosts dropped; 3 source timeouts; '
                      'reads attempted/read/skipped-deadline/skipped-host-gap/failed 9/7/1/0/1; '
                      'research 42.50s / 165s budget', output.getvalue())

    def test_episode_report_prints_requested_and_final_length_stage(self):
        output = StringIO()
        result = {'word_count': 400, 'duration_minutes': 2.67, 'transcript': 'Fictional words.',
                  'timing': {}, 'length_report': {
                      'report_line': 'requested 750 words, got 400, lost at stage writing'}}
        with redirect_stdout(output):
            _print_result('episode', result, {'daily': {'estimated_cost_usd': 0}}, 0)
        self.assertIn('Length: requested 750 words, got 400, lost at stage writing', output.getvalue())
        self.assertIn('Length stages: ', output.getvalue())

    def test_refuses_live_state_root_and_children(self):
        live = Path.home() / '.local' / 'state' / 'briefing'
        for path in (live, live / 'status.json'):
            with self.subTest(path=path), self.assertRaisesRegex(RuntimeError, 'refuses live'):
                assert_isolated_state(path)

    def test_accepts_temporary_state_outside_live_root(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(assert_isolated_state(root), Path(root).resolve())

    def test_profile_is_fictional_and_has_three_editorial_topics(self):
        profile = fictional_profile()
        self.assertEqual([row['label'] for row in profile['topics']],
                         ['AI chips', 'Climate policy', 'Space launches'])
        self.assertTrue(all(row['mustFollow'] is True for row in profile['topics']))

    def test_isolated_run_writes_fresh_state_and_restores_environment(self):
        before = os.environ.get('BRIEFING_STATE_DIR')
        before_flags = os.environ.get('BRIEFING_FLAGS_FILE')
        before_search = os.environ.get('BRIEFING_SEARCH_FIRST')
        os.environ['BRIEFING_FLAGS_FILE'] = '/not-a-real-profile/flags.json'
        os.environ['BRIEFING_SEARCH_FIRST'] = 'on'
        try:
            with isolated_run() as state:
                self.assertTrue((state / 'profile.json').is_file())
                self.assertEqual(os.environ.get('BRIEFING_EPISODES'), 'real')
                self.assertIsNone(os.environ.get('BRIEFING_FLAGS_FILE'))
                self.assertIsNone(os.environ.get('BRIEFING_SEARCH_FIRST'))
        finally:
            if before_flags is None:
                os.environ.pop('BRIEFING_FLAGS_FILE', None)
            else:
                os.environ['BRIEFING_FLAGS_FILE'] = before_flags
            if before_search is None:
                os.environ.pop('BRIEFING_SEARCH_FIRST', None)
            else:
                os.environ['BRIEFING_SEARCH_FIRST'] = before_search
        self.assertEqual(os.environ.get('BRIEFING_STATE_DIR'), before)
        self.assertEqual(os.environ.get('BRIEFING_FLAGS_FILE'), before_flags)
        self.assertEqual(os.environ.get('BRIEFING_SEARCH_FIRST'), before_search)

    def test_isolated_run_preserves_only_explicit_daily_outline_ab_override(self):
        before = os.environ.get('BRIEFING_DAILY_OUTLINE_WRITER')
        with patch.dict(os.environ, {'BRIEFING_DAILY_OUTLINE_WRITER': 'on',
                                     'BRIEFING_RETRIEVAL_FILL': 'on'}):
            with isolated_run() as state:
                self.assertEqual(Path(os.environ['BRIEFING_STATE_DIR']), state)
                self.assertEqual(os.environ.get('BRIEFING_DAILY_OUTLINE_WRITER'), 'on')
                self.assertIsNone(os.environ.get('BRIEFING_RETRIEVAL_FILL'))
            self.assertEqual(os.environ.get('BRIEFING_DAILY_OUTLINE_WRITER'), 'on')
        self.assertEqual(os.environ.get('BRIEFING_DAILY_OUTLINE_WRITER'), before)

    def test_search_first_run_option_is_explicit_and_restored(self):
        before = os.environ.get('BRIEFING_SEARCH_FIRST')
        with isolated_run(search_first=True) as state:
            self.assertEqual(os.environ.get('BRIEFING_SEARCH_FIRST'), 'on')
            self.assertEqual(Path(os.environ['BRIEFING_STATE_DIR']), state)
        self.assertEqual(os.environ.get('BRIEFING_SEARCH_FIRST'), before)

    def test_daily_real_run_report_includes_story_count(self):
        output = StringIO()
        edition = {'sections': [{'cluster_id': 'story-one'}, {'cluster_id': 'story-two'},
                                {'heading': 'not a story'}],
                   'spoken_words': 200,
                   'daily_briefing': {'briefing_script': [{'text': 'A fictional spoken script.'}]}}
        with redirect_stdout(output):
            _print_result('daily', edition, {'daily': {'estimated_cost_usd': 0}},
                          __import__('time').monotonic())
        self.assertIn('Stories: 2', output.getvalue())
        self.assertIn('Word count: 200', output.getvalue())

    def test_cost_delta_uses_normal_global_daily_ledger_bucket(self):
        before = {'daily': {'estimated_cost_usd': '1.25000'}}
        after = {'daily': {'estimated_cost_usd': '1.37500'}}
        self.assertEqual(ledger_cost_delta(before, after), 0.125)

    def test_funnel_report_separates_cluster_repeat_and_candidate_selection_stages(self):
        output = StringIO()
        with redirect_stdout(output):
            _print_funnel({'candidate_pool_diagnostics': {'ai': {
                'fetched': 255, 'topic_matched': 255, 'relevance_passed': 58,
                'clustered': 15, 'unseen': 14, 'candidate_clusters': 10}},
                'funnel_drop_reasons': {'ai': {'repeat_suppressed_cluster': 1}},
                'article_evidence_thresholds': {'single_source_chars': 300},
                'article_read_details': [{'topic_id': 'ai', 'candidate': 1,
                    'source_reads': [{'source_id': 'fixture-ai-source', 'chars': 321,
                                      'words': 49, 'body_sentences': 4}]}],
                'retrieval_fill': {'triggered': True, 'search_first': True,
                                   'topics_searched': 1, 'candidates_added': 2,
                                   'rejected': {'no_evidence': 1},
                                   'providers': {'gdelt': {'unavailable': 1},
                                                 'private-provider': {'ok': 5}}}}, {'ai': 0})
        report = output.getvalue()
        self.assertIn('| Fetched | Topic-matched | Relevance-passed | Clustered | Unseen | Candidate pool | Selected |', report)
        self.assertIn('| ai | 255 | 255 | 58 | 15 | 14 | 10 | 0 | repeat_suppressed_cluster: 1 |', report)
        self.assertIn('Open-web fallback:', report)
        self.assertIn('Article evidence thresholds:', report)
        self.assertIn('"words": 49', report)
        self.assertIn('"candidates_added": 2', report)
        self.assertIn('"gdelt": {"unavailable": 1}', report)
        self.assertNotIn('private-provider', report)

    def test_ledger_refuses_only_after_recorded_total_reaches_cap(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path('dev/ingest/usage-prices.json')
            config = json.loads(source.read_text(encoding='utf-8'))
            config['caps']['daily_usd'] = '0'
            config['caps']['monthly_usd'] = '1'
            config['caps']['daily_run_reserve_usd'] = '0'
            config_path = Path(root) / 'prices.json'
            config_path.write_text(json.dumps(config), encoding='utf-8')
            ledger = UsageLedger(config_path=config_path, ledger_dir=Path(root) / 'ledger')
            with self.assertRaises(UsageCapReached):
                ledger.preflight_episode()


if __name__ == '__main__':
    unittest.main()
