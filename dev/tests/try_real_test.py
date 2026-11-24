"""Fixture-only contract tests for the one-command real check runner."""
import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dev.try_real import (assert_isolated_state, fictional_profile, isolated_run,
                          ledger_cost_delta)
from dev.ingest.usage import UsageCapReached, UsageLedger


class TryRealTests(unittest.TestCase):
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
        with isolated_run() as state:
            self.assertTrue((state / 'profile.json').is_file())
            self.assertEqual(os.environ.get('BRIEFING_EPISODES'), 'real')
        self.assertEqual(os.environ.get('BRIEFING_STATE_DIR'), before)

    def test_cost_delta_uses_normal_global_daily_ledger_bucket(self):
        before = {'daily': {'estimated_cost_usd': '1.25000'}}
        after = {'daily': {'estimated_cost_usd': '1.37500'}}
        self.assertEqual(ledger_cost_delta(before, after), 0.125)

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
