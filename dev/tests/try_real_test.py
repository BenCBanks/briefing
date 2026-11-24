"""Fixture-only contract tests for the one-command real check runner."""
import os
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from dev.try_real import _print_funnel, fictional_profile, isolated_run, ledger_cost_delta
from dev.ingest.usage import UsageCapReached, UsageLedger


class TryRealTests(unittest.TestCase):
    def test_profile_is_fictional_and_has_three_editorial_topics(self):
        profile = fictional_profile()
        self.assertEqual([row['label'] for row in profile['topics']],
                         ['AI chips', 'Climate policy', 'Space launches'])
        self.assertTrue(all(row['mustFollow'] is True for row in profile['topics']))

    def test_isolated_run_writes_fresh_state_and_restores_environment(self):
        before = os.environ.get('BRIEFING_STATE_DIR')
        with isolated_run() as state:
            self.assertTrue((state / 'profile.json').is_file())
            self.assertEqual(os.environ.get('BRIEFING_EPISODES'), 'real')
        self.assertEqual(os.environ.get('BRIEFING_STATE_DIR'), before)

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
                'funnel_drop_reasons': {'ai': {'repeat_suppressed_cluster': 1}}}, {'ai': 0})
        report = output.getvalue()
        self.assertIn('| Fetched | Topic-matched | Relevance-passed | Clustered | Unseen | Candidate pool | Selected |', report)
        self.assertIn('| ai | 255 | 255 | 58 | 15 | 14 | 10 | 0 | repeat_suppressed_cluster: 1 |', report)

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
