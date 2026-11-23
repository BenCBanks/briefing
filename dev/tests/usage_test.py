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
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from dev.ingest.usage import UsageCapReached, UsageError, UsageLedger, reset_usage_caller, usage_caller


ET = ZoneInfo('America/New_York')
NOW = datetime(2026, 11, 18, 9, tzinfo=ET)
DEFAULT_PRICES = Path(__file__).resolve().parent / 'fixtures' / 'usage-prices.json'


DAILY_AUDIO_RESERVE_QUERY = r'''
import json
import sys
from datetime import datetime
from dev.test_guard import enforce_test_environment
enforce_test_environment()
from dev.ingest.usage import UsageLedger

ledger = UsageLedger(config_path=sys.argv[1], ledger_dir=sys.argv[2])
before = ledger.path.read_bytes()
remaining = ledger.daily_audio_budget_remaining(datetime.fromisoformat(sys.argv[3]))
print(json.dumps({'remaining': str(remaining),
                  'pending_ids': sorted(json.loads(before)['pending']),
                  'unchanged': ledger.path.read_bytes() == before}))
'''


FRESH_SETTLEMENT_PROBE = r'''
import json
import sys
from datetime import datetime
from dev.test_guard import enforce_test_environment
enforce_test_environment()
from dev.ingest.usage import UsageCapReached, UsageLedger

config_path, ledger_dir, reservation_id, batch_id, output_tokens, when = sys.argv[1:]
ledger = UsageLedger(config_path=config_path, ledger_dir=ledger_dir)
before = ledger.path.read_bytes()
try:
    ledger.settle_text_call(
        reservation_id, model='gemini-3.8-flash', prompt_tokens=1,
        candidates_tokens=int(output_tokens), thoughts_tokens=0)
except UsageCapReached as error:
    outcome = error.period
else:
    outcome = 'ok'
state = json.loads(ledger.path.read_text(encoding='utf-8'))
print(json.dumps({
    'outcome': outcome,
    'batch': ledger.batch_budget(batch_id),
    'daily': ledger.summary(datetime.fromisoformat(when))['daily'],
    'monthly': ledger.summary(datetime.fromisoformat(when))['monthly'],
    'pending': sorted(state['pending']),
    'unchanged': ledger.path.read_bytes() == before,
}))
'''


def prices_file(directory, *, daily='2.00', monthly='30.00'):
    config = json.loads(DEFAULT_PRICES.read_text(encoding='utf-8'))
    config['caps'] = {'daily_usd': daily, 'monthly_usd': monthly}
    path = Path(directory) / 'prices.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    return path


def batch_prices_file(directory):
    path = prices_file(directory, daily='0.50', monthly='5.00')
    config = json.loads(path.read_text(encoding='utf-8'))
    config['caps']['daily_run_reserve_usd'] = '0.15'
    path.write_text(json.dumps(config), encoding='utf-8')
    return path


class UsageLedgerTests(unittest.TestCase):
    # Reservation and batch-envelope tests were removed with that behavior.

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

    def test_non_daily_callers_cannot_spend_reserved_daily_run_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            config = prices_file(directory, daily='0.50', monthly='5.00')
            value = json.loads(config.read_text())
            value['caps']['daily_run_reserve_usd'] = '0.15'
            config.write_text(json.dumps(value))
            ledger = UsageLedger(config_path=config, ledger_dir=Path(directory) / 'ledger')
            for _ in range(70):
                ledger.record_search('perplexity', now=NOW)
            with self.assertRaises(UsageCapReached) as caught:
                ledger.record_search('perplexity', now=NOW)
            self.assertEqual(str(caught.exception), 'cost_cap_reached')
            self.assertEqual(caught.exception.period, 'daily')
            self.assertEqual(ledger.summary(NOW)['daily']['available_to_other_callers_usd'], '0.00000000')
            for _ in range(30):
                ledger.record_search('perplexity', now=NOW, caller='daily')
            self.assertEqual(ledger.summary(NOW)['daily']['estimated_cost_usd'], '0.50000000')

    def test_daily_audio_budget_is_limited_to_the_reserved_amount(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(ledger_dir=directory)
            self.assertEqual(ledger.daily_audio_budget_remaining(NOW), Decimal('0.15'))
            self.assertEqual(ledger.estimate_tts_cost(12_000, 60), Decimal('0.0105'))
            reservation = ledger.reserve_tts_call(10_000, model='fictional-tts', now=NOW)
            ledger.settle_tts_call(reservation, 10_000, 60)
            self.assertLess(ledger.daily_audio_budget_remaining(NOW), Decimal('0.15'))



    def test_tts_settlement_records_measured_overrun_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(ledger_dir=directory)
            for _ in range(99):
                ledger.record_search('perplexity', now=NOW, caller='daily')
            reservation = ledger.reserve_tts_call(
                100, model='fictional-tts', now=NOW, caller='daily')
            self.assertEqual(ledger.summary(NOW)['daily']['estimated_cost_usd'], '0.49626250')

            with self.assertRaises(UsageCapReached) as caught:
                ledger.settle_tts_call(reservation, 100, 60)

            self.assertEqual(caught.exception.period, 'daily')
            daily = ledger.summary(NOW)['daily']
            self.assertEqual(daily['tts_characters'], 100)
            self.assertEqual(daily['estimated_cost_usd'], '0.50401250',
                             'settlement records the measured 60-second audio cost')
            self.assertEqual(ledger.summary(NOW)['cap_status'], 'daily_cap_reached')
            self.assertEqual(len(json.loads(ledger.path.read_text())['pending']), 0)

    def test_real_token_usage_counts_thinking_tokens_at_model_price(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(ledger_dir=directory)
            reservation = ledger.reserve_text_call(100, 100, 100, model='gemini-3.6-flash', now=NOW)
            ledger.settle_text_call(reservation, model='gemini-3.6-flash', prompt_tokens=128,
                                    candidates_tokens=118, thoughts_tokens=1079)
            daily = ledger.summary(NOW)['daily']
            self.assertEqual(daily['prompt_tokens'], 128)
            self.assertEqual(daily['candidates_tokens'], 118)
            self.assertEqual(daily['thoughts_tokens'], 1079)
            self.assertEqual(daily['estimated_cost_usd'], '0.00458475')
            self.assertEqual(daily['by_model']['gemini-3.6-flash']['thoughts_tokens'], 1079)


    def test_gemini_grounded_search_free_allowance_then_overage(self):
        with tempfile.TemporaryDirectory() as directory:
            config = prices_file(directory)
            value = json.loads(config.read_text())
            value['prices']['gemini_grounded_search'] = {
                'free_allowance': 2, 'overage_usd_per_query': '0.014'}
            config.write_text(json.dumps(value))
            ledger = UsageLedger(config_path=config, ledger_dir=Path(directory) / 'ledger')

            ledger.record_search('gemini', now=NOW)
            ledger.record_search('gemini', now=NOW)
            self.assertEqual(ledger.summary(NOW)['monthly']['estimated_cost_usd'], '0.00000000')
            ledger.record_search('gemini', now=NOW)
            ledger.record_search('gemini', now=NOW)
            summary = ledger.summary(NOW)
            self.assertEqual(summary['monthly']['search_queries'], {'gemini': 4})
            self.assertEqual(summary['monthly']['estimated_cost_usd'], '0.02800000')


    def test_default_grounded_search_allowance_is_unverified_and_covers_three_queries(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(ledger_dir=Path(directory) / 'ledger')
            self.assertEqual(ledger.config['_grounded_search_free_allowance'], 1500)
            self.assertIn('unverified', ledger.config['grounded_search_price_status'])
            for _ in range(3):
                ledger.record_search('gemini', now=NOW)
            self.assertEqual(ledger.summary(NOW)['daily']['search_queries'], {'gemini': 3})
            self.assertEqual(ledger.summary(NOW)['daily']['estimated_cost_usd'], '0.00000000')

    def test_gemini_grounded_search_allowance_rolls_over_by_month(self):
        with tempfile.TemporaryDirectory() as directory:
            config = prices_file(directory)
            value = json.loads(config.read_text())
            value['prices']['gemini_grounded_search'] = {
                'free_allowance': 1, 'overage_usd_per_query': '0.014'}
            config.write_text(json.dumps(value))
            ledger = UsageLedger(config_path=config, ledger_dir=Path(directory) / 'ledger')
            ledger.record_search('gemini', now=NOW)
            ledger.record_search('gemini', now=NOW)
            self.assertEqual(ledger.summary(NOW)['monthly']['estimated_cost_usd'], '0.01400000')

            december = datetime(2026, 12, 1, 9, tzinfo=ET)
            ledger.record_search('gemini', now=december)
            self.assertEqual(ledger.summary(december)['monthly']['search_queries'], {'gemini': 1})
            self.assertEqual(ledger.summary(december)['monthly']['estimated_cost_usd'], '0.00000000')

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



    def test_monthly_cap_is_reported_and_usage_buckets_roll_over(self):
        with tempfile.TemporaryDirectory() as directory:
            config = prices_file(directory, daily='0.05', monthly='0.006')
            ledger = UsageLedger(config_path=config, ledger_dir=directory)
            ledger.record_search('perplexity', now=NOW)
            with self.assertRaises(UsageCapReached) as caught:
                ledger.record_search('perplexity', now=NOW)
            self.assertEqual(caught.exception.period, 'monthly')
            self.assertEqual(ledger.summary(NOW)['cap_status'], 'monthly_cap_reached')
            december = datetime(2026, 12, 1, 9, tzinfo=ET)
            ledger.record_search('perplexity', now=december)
            self.assertEqual(ledger.summary(december)['daily']['search_queries'], {'perplexity': 1})
            self.assertEqual(ledger.summary(december)['monthly']['search_queries'], {'perplexity': 1})

if __name__ == '__main__':
    unittest.main()
