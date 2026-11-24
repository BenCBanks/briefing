import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from dev.ingest.usage import UsageCapReached, UsageLedger, reset_usage_caller, usage_caller

ET = ZoneInfo('America/New_York')
NOW = datetime(2026, 11, 18, 9, tzinfo=ET)
DEFAULT_PRICES = Path(__file__).resolve().parent / 'fixtures' / 'usage-prices.json'


def prices_file(directory, *, daily='2.00', monthly='30.00', reserve='0.15'):
    config = json.loads(DEFAULT_PRICES.read_text(encoding='utf-8'))
    config['caps'] = {'daily_usd': daily, 'monthly_usd': monthly,
                      'daily_run_reserve_usd': reserve}
    path = Path(directory) / 'prices.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    return path


class UsageLedgerTests(unittest.TestCase):
    def test_default_ledger_is_global_across_state_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'briefing-usage'
            with patch('dev.ingest.usage.GLOBAL_LEDGER_DIR', root):
                with patch.dict(os.environ, {'BRIEFING_TEST_LEDGER_DIR': str(root)}):
                    first = UsageLedger('/fictional/state/a')
                    second = UsageLedger('/fictional/state/b')
            self.assertEqual(first.path, root / 'ledger.json')
            self.assertEqual(second.path, first.path)
            first.record_search('perplexity', now=NOW)
            self.assertEqual(second.summary(NOW)['daily']['search_queries'], {'perplexity': 1})

    def test_other_callers_are_refused_at_daily_total_minus_six_am_reserve(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(config_path=prices_file(directory, daily='0.50'),
                                 ledger_dir=Path(directory) / 'ledger')
            for _ in range(70):
                ledger.record_search('perplexity', now=NOW)
            self.assertEqual(ledger.summary(NOW)['daily']['estimated_cost_usd'], '0.35000000')
            with self.assertRaises(UsageCapReached) as caught:
                ledger.preflight_episode(now=NOW)
            self.assertEqual(caught.exception.period, 'daily')
            self.assertEqual(ledger.summary(NOW)['daily']['available_to_other_callers_usd'], '0.00000000')

    def test_daily_caller_can_use_the_protected_reserve_until_the_full_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(config_path=prices_file(directory, daily='0.50'),
                                 ledger_dir=Path(directory) / 'ledger')
            for _ in range(70):
                ledger.record_search('perplexity', now=NOW)
            token = usage_caller('daily')
            try:
                ledger.preflight_episode(now=NOW)
                for _ in range(30):
                    ledger.record_search('perplexity', now=NOW)
                self.assertEqual(ledger.summary(NOW)['daily']['estimated_cost_usd'], '0.50000000')
                with self.assertRaises(UsageCapReached):
                    ledger.preflight_episode(now=NOW)
            finally:
                reset_usage_caller(token)

    def test_actual_provider_tokens_and_cost_are_recorded_after_call_even_if_it_crosses_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(config_path=prices_file(directory, daily='0.50'),
                                 ledger_dir=Path(directory) / 'ledger')
            ledger.preflight_episode(now=NOW)
            ledger.record_text_call(1_000_000, 118, 1079,
                                    model='gemini-3.6-flash', now=NOW)
            daily = ledger.summary(NOW)['daily']
            self.assertEqual(daily['prompt_tokens'], 1_000_000)
            self.assertEqual(daily['candidates_tokens'], 118)
            self.assertEqual(daily['thoughts_tokens'], 1079)
            self.assertEqual(daily['estimated_cost_usd'], '0.75448875')
            with self.assertRaises(UsageCapReached) as caught:
                ledger.preflight_episode(now=NOW)
            self.assertEqual(caught.exception.period, 'daily')

    def test_monthly_cap_refuses_before_a_new_provider_call(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(config_path=prices_file(directory, daily='2.00', monthly='0.005'),
                                 ledger_dir=Path(directory) / 'ledger')
            ledger.record_search('perplexity', now=NOW)
            with self.assertRaises(UsageCapReached) as caught:
                ledger.preflight_episode(now=NOW)
            self.assertEqual(caught.exception.period, 'monthly')
            self.assertEqual(ledger.summary(NOW)['cap_status'], 'monthly_cap_reached')
            december = datetime(2026, 12, 1, 9, tzinfo=ET)
            ledger.preflight_episode(now=december)
            ledger.record_search('perplexity', now=december)
            self.assertEqual(ledger.summary(december)['monthly']['search_queries'], {'perplexity': 1})

    def test_daily_audio_budget_tracks_measured_usage_without_pending_reservations(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(ledger_dir=directory)
            self.assertEqual(ledger.daily_audio_budget_remaining(NOW), Decimal('0.50'))
            self.assertEqual(ledger.estimate_tts_cost(12_000, 60), Decimal('0.0105'))
            ledger.record_tts_usage(10_000, 60, model='fictional-tts', now=NOW)
            spent = Decimal(ledger.summary(NOW)['daily']['estimated_cost_usd'])
            self.assertEqual(ledger.daily_audio_budget_remaining(NOW), Decimal('0.50') - spent)
            state = json.loads(ledger.path.read_text(encoding='utf-8'))
            self.assertNotIn('pending', state)
            self.assertNotIn('batch_reservations', state)

    def test_daily_audio_uses_remaining_cap_after_episode_tts_spend(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(config_path=prices_file(directory, daily='0.50', reserve='0.15'),
                                 ledger_dir=Path(directory) / 'ledger')
            for _ in range(70):
                ledger.record_search('perplexity', now=NOW)
            ledger.record_tts_usage(100_000, 300, model='fictional-tts', now=NOW)
            self.assertEqual(ledger.summary(NOW)['daily']['estimated_cost_usd'], '0.40750000')
            self.assertEqual(ledger.daily_audio_budget_remaining(NOW), Decimal('0.0925'))
            self.assertLess(ledger.estimate_tts_cost(3_000, 300),
                            ledger.daily_audio_budget_remaining(NOW))

    def test_gemini_grounded_search_free_allowance_then_overage(self):
        with tempfile.TemporaryDirectory() as directory:
            config = prices_file(directory)
            value = json.loads(config.read_text())
            value['prices']['gemini_grounded_search'] = {
                'free_allowance': 2, 'overage_usd_per_query': '0.014'}
            config.write_text(json.dumps(value))
            ledger = UsageLedger(config_path=config, ledger_dir=Path(directory) / 'ledger')
            for _ in range(2):
                ledger.record_search('gemini', now=NOW)
            self.assertEqual(ledger.summary(NOW)['monthly']['estimated_cost_usd'], '0.00000000')
            for _ in range(2):
                ledger.record_search('gemini', now=NOW)
            month = ledger.summary(NOW)['monthly']
            self.assertEqual(month['search_queries'], {'gemini': 4})
            self.assertEqual(month['estimated_cost_usd'], '0.02800000')

    def test_gemini_free_allowance_is_atomic_across_workers(self):
        with tempfile.TemporaryDirectory() as directory:
            config = prices_file(directory)
            value = json.loads(config.read_text())
            value['prices']['gemini_grounded_search'] = {
                'free_allowance': 2, 'overage_usd_per_query': '0.014'}
            config.write_text(json.dumps(value))
            ledger = UsageLedger(config_path=config, ledger_dir=Path(directory) / 'ledger')
            with ThreadPoolExecutor(max_workers=12) as pool:
                list(pool.map(lambda _: ledger.record_search('gemini', now=NOW), range(20)))
            month = ledger.summary(NOW)['monthly']
            self.assertEqual(month['search_queries'], {'gemini': 20})
            self.assertEqual(month['estimated_cost_usd'], '0.25200000')

    def test_month_and_day_buckets_roll_over(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(ledger_dir=directory)
            ledger.record_search('perplexity', now=NOW)
            december = datetime(2026, 12, 1, 9, tzinfo=ET)
            ledger.record_search('perplexity', now=december)
            self.assertEqual(ledger.summary(december)['daily']['search_queries'], {'perplexity': 1})
            self.assertEqual(ledger.summary(december)['monthly']['search_queries'], {'perplexity': 1})


if __name__ == '__main__':
    unittest.main()
