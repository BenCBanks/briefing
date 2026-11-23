"""Fixture-only contract tests for the one-command real check runner."""
import os
import unittest
from unittest.mock import patch

from dev.try_real import fictional_profile, isolated_run, ledger_cost_delta


class TryRealTests(unittest.TestCase):
    def test_profile_is_fictional_and_has_three_editorial_topics(self):
        profile = fictional_profile()
        self.assertEqual([row['label'] for row in profile['topics']],
                         ['AI chips', 'Climate policy', 'Space launches'])
        self.assertTrue(all(row['mustFollow'] is True for row in profile['topics']))

    def test_isolated_run_clears_acceptance_batch_gate_and_restores_environment(self):
        before = os.environ.get('BRIEFING_REQUIRE_USAGE_BATCH')
        os.environ['BRIEFING_REQUIRE_USAGE_BATCH'] = '1'
        try:
            with isolated_run() as state:
                self.assertTrue((state / 'profile.json').is_file())
                self.assertEqual(os.environ.get('BRIEFING_EPISODES'), 'real')
                self.assertNotIn('BRIEFING_REQUIRE_USAGE_BATCH', os.environ)
            self.assertEqual(os.environ.get('BRIEFING_REQUIRE_USAGE_BATCH'), '1')
        finally:
            if before is None:
                os.environ.pop('BRIEFING_REQUIRE_USAGE_BATCH', None)
            else:
                os.environ['BRIEFING_REQUIRE_USAGE_BATCH'] = before

    def test_cost_delta_uses_normal_global_daily_ledger_bucket(self):
        before = {'daily': {'estimated_cost_usd': '1.25000'}}
        after = {'daily': {'estimated_cost_usd': '1.37500'}}
        self.assertEqual(ledger_cost_delta(before, after), 0.125)


if __name__ == '__main__':
    unittest.main()
