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
    def test_strict_text_reservation_requires_explicit_input_ceiling(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(config_path=batch_prices_file(directory),
                                 ledger_dir=Path(directory) / 'ledger')
            batch_id = 'fictional-explicit-input-ceiling'
            ledger.reserve_batch_budget(batch_id, general_usd='0.01', now=NOW)
            with patch.dict(os.environ, {'BRIEFING_REQUIRE_USAGE_BATCH': '1'}), \
                    ledger.usage_batch(batch_id):
                with self.assertRaises(UsageCapReached) as missing:
                    ledger.reserve_text_call(
                        20, 20, 100, model='gemini-3.8-flash', now=NOW)
                self.assertEqual(missing.exception.period, 'batch')
                reservation = ledger.reserve_text_call(
                    20, 20, 100, model='gemini-3.8-flash', now=NOW,
                    input_token_limit=321)
            pending = json.loads(ledger.path.read_text())['pending'][reservation]
            self.assertEqual(pending['token_input_limit'], 321)
            self.assertEqual(pending['token_output_limit'], 100)

    def test_persistent_batch_envelope_is_idempotent_and_releases_only_when_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            config = batch_prices_file(directory)
            ledger_dir = Path(directory) / 'ledger'
            first_ledger = UsageLedger(config_path=config, ledger_dir=ledger_dir)
            batch_id = 'fictional-release-batch-17'
            first = first_ledger.reserve_batch_budget(
                batch_id, general_usd='0.20', daily_usd='0.10', now=NOW)
            self.assertEqual(first['general_remaining_usd'], '0.20000000')
            self.assertEqual(first['daily_remaining_usd'], '0.10000000')
            self.assertEqual(first_ledger.summary(NOW)['daily']['available_to_other_callers_usd'],
                             '0.05000000')

            # A second process/instance observes the same durable envelope and
            # repeating the same preflight does not reserve it twice.
            second_ledger = UsageLedger(config_path=config, ledger_dir=ledger_dir)
            repeated = second_ledger.reserve_batch_budget(
                batch_id, general_usd='0.20', daily_usd='0.10', now=NOW)
            self.assertEqual(repeated, first)
            with second_ledger.usage_batch(batch_id):
                second_ledger.record_search('perplexity', now=NOW, caller='other')
            caller_token = usage_caller('daily')
            try:
                with second_ledger.usage_batch(batch_id):
                    second_ledger.record_search('perplexity', now=NOW)
            finally:
                reset_usage_caller(caller_token)
            status = first_ledger.batch_budget(batch_id)
            self.assertEqual(status['general_remaining_usd'], '0.19500000')
            self.assertEqual(status['daily_remaining_usd'], '0.09500000')
            self.assertEqual(first_ledger.summary(NOW)['daily']['batch_reserved_usd'], '0.29000000')

            closed = second_ledger.close_batch_budget(batch_id)
            self.assertEqual(closed['status'], 'closed')
            self.assertEqual(first_ledger.summary(NOW)['daily']['batch_reserved_usd'], '0.00000000')
            self.assertEqual(first_ledger.summary(NOW)['daily']['available_to_other_callers_usd'],
                             '0.34000000')
            self.assertEqual(second_ledger.close_batch_budget(batch_id), closed)
            with self.assertRaisesRegex(UsageError, 'identity or ceiling changed'):
                second_ledger.reserve_batch_budget(
                    batch_id, general_usd='0.10', daily_usd='0.20', now=NOW)

    def test_general_batch_ceiling_cannot_use_daily_reserve(self):
        with tempfile.TemporaryDirectory() as directory:
            config = DEFAULT_PRICES
            general_ledger = UsageLedger(config_path=config,
                                         ledger_dir=Path(directory) / 'general-ledger')
            daily_cap = general_ledger.config['_daily_cap']
            protected_reserve = general_ledger.config['_daily_reserve']
            general_ceiling = daily_cap - protected_reserve

            with self.assertRaises(UsageCapReached) as caught:
                general_ledger.reserve_batch_budget(
                    'fictional-general-over-reserve',
                    general_usd=general_ceiling + Decimal('0.00000001'), now=NOW)
            self.assertEqual(caught.exception.period, 'daily')
            self.assertIsNone(general_ledger.batch_budget('fictional-general-over-reserve'))
            self.assertEqual(Decimal(general_ledger.summary(NOW)['daily'][
                'available_to_other_callers_usd']), general_ceiling)

            split_ledger = UsageLedger(config_path=config,
                                       ledger_dir=Path(directory) / 'split-ledger')
            split = split_ledger.reserve_batch_budget(
                'fictional-full-daily-split', general_usd=general_ceiling,
                daily_usd=protected_reserve, now=NOW)
            self.assertEqual(Decimal(split['general_ceiling_usd']), general_ceiling)
            self.assertEqual(Decimal(split['daily_ceiling_usd']), protected_reserve)
            self.assertEqual(Decimal(split['general_ceiling_usd']) +
                             Decimal(split['daily_ceiling_usd']), daily_cap)

            # An ordinary pending attempt consumes global headroom alongside
            # both batch slices; it neither borrows nor double-counts them.
            mixed = UsageLedger(config_path=config, ledger_dir=Path(directory) / 'mixed-ledger')
            batch_id = 'fictional-batch-with-ordinary-text'
            batch = mixed.reserve_batch_budget(batch_id, general_usd='0.20',
                                                daily_usd='0.10', now=NOW)
            ordinary = mixed.reserve_text_call(0, 0, 16_000, model='gemini-3.5-flash',
                                               now=NOW, caller='other')
            prior = json.loads(mixed.path.read_text())
            self.assertEqual(prior['pending'][ordinary]['reserved_cost_usd'], '0.04800000')
            self.assertNotIn('batch_id', prior['pending'][ordinary])
            self.assertEqual(mixed.summary(NOW)['daily']['available_to_other_callers_usd'],
                             '0.00200000')
            transports = []
            with self.assertRaises(UsageCapReached) as denied:
                mixed.reserve_text_call(0, 0, 1000, model='gemini-3.5-flash',
                                        now=NOW, caller='other')
                transports.append('general')
            self.assertEqual(denied.exception.period, 'daily')
            refused = json.loads(mixed.path.read_text())
            for key in ('pending', 'batch_reservations', 'days', 'months'):
                self.assertEqual(refused[key], prior[key],
                                 'denial may record a cap counter but cannot alter funds or holds')
            daily = mixed.reserve_text_call(0, 0, 1000, model='gemini-3.5-flash',
                                           now=NOW, caller='daily')
            transports.append('daily')
            state = json.loads(mixed.path.read_text())
            self.assertEqual(transports, ['daily'])
            self.assertEqual(set(state['pending']), {ordinary, daily})
            self.assertEqual(state['pending'][ordinary], prior['pending'][ordinary])
            self.assertEqual(state['pending'][daily]['caller'], 'daily')
            self.assertNotIn('batch_id', state['pending'][daily])
            self.assertEqual(mixed.batch_budget(batch_id), batch)
            summary = mixed.summary(NOW)
            for period in ('daily', 'monthly'):
                self.assertEqual(summary[period]['estimated_cost_usd'], '0.05100000')
                self.assertEqual(summary[period]['batch_reserved_usd'], '0.30000000')
            self.assertEqual(summary['daily']['remaining_usd'], '0.14900000')
            self.assertEqual(summary['daily']['available_to_other_callers_usd'], '0.00000000')
            self.assertEqual(mixed.config['_daily_cap'], daily_cap)
            self.assertEqual(mixed.config['_daily_reserve'], protected_reserve)

    def test_batch_allowance_denial_happens_before_transport_and_preserves_hold(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(config_path=batch_prices_file(directory),
                                 ledger_dir=Path(directory) / 'ledger')
            batch_id = 'fictional-too-small-batch'
            ledger.reserve_batch_budget(batch_id, general_usd='0.0049', now=NOW)
            transport_calls = []

            def transport():
                ledger.record_search('perplexity', now=NOW, caller='other')
                transport_calls.append('sent')

            with ledger.usage_batch(batch_id):
                with self.assertRaises(UsageCapReached) as caught:
                    transport()
            self.assertEqual(caught.exception.period, 'batch')
            self.assertEqual(transport_calls, [], 'the denied provider attempt never reaches transport')
            self.assertEqual(ledger.batch_budget(batch_id)['general_remaining_usd'], '0.00490000')
            self.assertEqual(ledger.summary(NOW)['daily']['search_queries'], {})

    def test_batch_closed_after_request_validation_refuses_worker_before_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(config_path=batch_prices_file(directory),
                                 ledger_dir=Path(directory) / 'ledger')
            batch_id = 'fictional-close-between-validation-and-worker'
            ledger.reserve_batch_budget(batch_id, general_usd='0.020', now=NOW)

            # The request-side check sees an open envelope. Closing it before
            # worker dispatch must still make the atomic usage reservation fail.
            validated = ledger.batch_budget(batch_id)
            self.assertEqual(validated['status'], 'open')
            closed = ledger.close_batch_budget(batch_id)
            closed_bytes = ledger.path.read_bytes()

            # A later runner reusing the same ID/ceiling must not reopen it.
            restarted = UsageLedger(config_path=ledger.config_path,
                                    ledger_dir=Path(directory) / 'ledger')
            self.assertEqual(restarted.reserve_batch_budget(
                batch_id, general_usd='0.020', now=NOW), closed)
            ledger = restarted

            rejected, transport_calls = [], []

            def generation_worker():
                try:
                    with ledger.usage_batch(batch_id):
                        ledger.record_search('perplexity', now=NOW, caller='other')
                        transport_calls.append('sent')
                except UsageCapReached as error:
                    rejected.append(error.period)

            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(generation_worker).result(timeout=5)

            self.assertEqual(rejected, ['batch'])
            self.assertEqual(transport_calls, [], 'the closed batch is refused before transport')
            self.assertEqual(ledger.summary(NOW)['daily']['search_queries'], {})
            self.assertEqual(ledger.batch_budget(batch_id)['status'], 'closed')
            self.assertEqual(ledger.path.read_bytes(), closed_bytes)

    def test_batch_envelope_refuses_concurrent_reservations_before_any_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            config = batch_prices_file(directory)
            ledger_dir = Path(directory) / 'ledger'
            ledgers = [UsageLedger(config_path=config, ledger_dir=ledger_dir) for _ in range(2)]
            transports = []

            def reserve(index):
                try:
                    ledgers[index % 2].reserve_batch_budget(
                        f'fictional-contender-{index}', general_usd='0.20', now=NOW)
                except UsageCapReached:
                    return False
                transports.append(index)
                return True

            with ThreadPoolExecutor(max_workers=8) as pool:
                admitted = list(pool.map(reserve, range(8)))
            self.assertEqual(sum(admitted), 1)
            self.assertEqual(len(transports), 1)
            self.assertEqual(ledgers[0].summary(NOW)['daily']['batch_reserved_usd'], '0.20000000')
            self.assertEqual(ledgers[0].summary(NOW)['daily']['available_to_other_callers_usd'],
                             '0.15000000')

    def test_concurrent_batch_attempts_cannot_exceed_shared_hold(self):
        with tempfile.TemporaryDirectory() as directory:
            config = batch_prices_file(directory)
            ledger_dir = Path(directory) / 'ledger'
            ledgers = [UsageLedger(config_path=config, ledger_dir=ledger_dir) for _ in range(2)]
            batch_id = 'fictional-concurrent-attempt-batch'
            ledgers[0].reserve_batch_budget(batch_id, general_usd='0.010', now=NOW)
            transports = []

            def attempt(index):
                ledger = ledgers[index % 2]
                try:
                    with ledger.usage_batch(batch_id):
                        ledger.record_search('perplexity', now=NOW, caller='other')
                        transports.append(index)
                    return True
                except UsageCapReached:
                    return False

            with ThreadPoolExecutor(max_workers=8) as pool:
                admitted = list(pool.map(attempt, range(8)))
            self.assertEqual(sum(admitted), 2)
            self.assertEqual(len(transports), 2)
            self.assertEqual(ledgers[0].batch_budget(batch_id)['general_remaining_usd'], '0.00000000')
            self.assertEqual(ledgers[0].summary(NOW)['daily']['search_queries']['perplexity'], 2)

            # Repeating preflight after two accepted attempts cannot fund a third.
            depleted = ledgers[0].batch_budget(batch_id)
            depleted_bytes = ledgers[0].path.read_bytes()
            restarted = UsageLedger(config_path=config, ledger_dir=ledger_dir)
            self.assertEqual(restarted.reserve_batch_budget(
                batch_id, general_usd='0.010', now=NOW), depleted)
            with restarted.usage_batch(batch_id):
                with self.assertRaises(UsageCapReached) as caught:
                    restarted.record_search('perplexity', now=NOW, caller='other')
                    transports.append('postdeploy')
            self.assertEqual(caught.exception.period, 'batch')
            self.assertEqual(len(transports), 2)
            self.assertEqual(restarted.batch_budget(batch_id), depleted)
            self.assertEqual(restarted.path.read_bytes(), depleted_bytes)

    def test_batch_settlement_returns_only_confirmed_unused_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(config_path=batch_prices_file(directory),
                                 ledger_dir=Path(directory) / 'ledger')
            batch_id = 'fictional-settlement-batch'
            ledger.reserve_batch_budget(batch_id, general_usd='0.010', now=NOW)
            with ledger.usage_batch(batch_id):
                reservation = ledger.reserve_text_call(
                    0, 0, 1000, model='gemini-3.5-flash', now=NOW, caller='other')
            self.assertEqual(ledger.batch_budget(batch_id)['general_remaining_usd'], '0.00700000')
            with self.assertRaisesRegex(UsageError, 'unsettled calls'):
                ledger.close_batch_budget(batch_id)

            ledger.settle_text_call(reservation, model='gemini-3.5-flash',
                                    prompt_tokens=0, candidates_tokens=25, thoughts_tokens=0)
            self.assertEqual(ledger.batch_budget(batch_id)['general_remaining_usd'], '0.00992500')
            closed = ledger.close_batch_budget(batch_id)
            self.assertEqual(closed['status'], 'closed')
            self.assertEqual(ledger.summary(NOW)['daily']['estimated_cost_usd'], '0.00007500')

    def test_daily_batch_settlement_overage_debits_only_daily_slice(self):
        for output_tokens, actual_cost, remaining in (
                (133, '0.00049950', '0.00151050'),
                (600, '0.00225075', '0.00000000')):
            with self.subTest(output_tokens=output_tokens), tempfile.TemporaryDirectory() as directory:
                config = batch_prices_file(directory)
                ledger = UsageLedger(config_path=config,
                                     ledger_dir=Path(directory) / 'ledger')
                batch_id = 'fictional-daily-overrun-slice'
                ledger.reserve_batch_budget(batch_id, general_usd='0.00201',
                                            daily_usd='0.00201', now=NOW)
                with ledger.usage_batch(batch_id):
                    reservation = ledger.reserve_text_call(
                        0, 1, 10, model='gemini-3.8-flash', now=NOW, caller='daily')

                self.assertEqual(
                    json.loads(ledger.path.read_text())['pending'][reservation]['reserved_cost_usd'],
                    '0.00003825')

                if remaining == '0.00000000':
                    # Settle the confirmed overrun in a fresh process.  The
                    # child has no inherited ContextVars; it must use the
                    # persisted reservation's original day/month/caller.
                    env = {'PATH': os.defpath, 'BRIEFING_TESTING': '1',
                           'BRIEFING_STATE_DIR': str(Path(directory) / 'child-state'),
                           'BRIEFING_TEST_LEDGER_DIR': str(ledger.state_dir),
                           'PYTHONDONTWRITEBYTECODE': '1'}
                    child = subprocess.run(
                        [sys.executable, '-c', FRESH_SETTLEMENT_PROBE,
                         str(config), str(ledger.state_dir), reservation, batch_id,
                         str(output_tokens), NOW.isoformat()],
                        cwd=Path(__file__).resolve().parents[2], env=env,
                        capture_output=True, text=True, timeout=20)
                    self.assertEqual(child.returncode, 0, child.stderr)
                    result = json.loads(child.stdout)
                    self.assertEqual(result['outcome'], 'batch')
                    self.assertEqual(result['pending'], [])
                    self.assertEqual(result['batch']['general_remaining_usd'], '0.00201000')
                    self.assertEqual(result['batch']['daily_remaining_usd'], '0.00000000')
                    self.assertEqual(result['daily']['estimated_cost_usd'], actual_cost)
                    self.assertEqual(result['monthly']['estimated_cost_usd'], actual_cost)
                    self.assertFalse(result['unchanged'])
                    settled_bytes = ledger.path.read_bytes()
                    for replay_output in (output_tokens, 0):
                        replay = subprocess.run(
                            [sys.executable, '-c', FRESH_SETTLEMENT_PROBE,
                             str(config), str(ledger.state_dir), reservation, batch_id,
                             str(replay_output), NOW.isoformat()],
                            cwd=Path(__file__).resolve().parents[2], env=env,
                            capture_output=True, text=True, timeout=20)
                        self.assertEqual(replay.returncode, 0, replay.stderr)
                        replayed = json.loads(replay.stdout)
                        self.assertEqual(replayed['outcome'], 'ok')
                        self.assertTrue(replayed['unchanged'],
                                        'settled replay cannot debit twice or refund exhausted headroom')
                        for key in ('batch', 'daily', 'monthly', 'pending'):
                            self.assertEqual(replayed[key], result[key])
                        self.assertEqual(ledger.path.read_bytes(), settled_bytes)
                    ledger = UsageLedger(config_path=config,
                                         ledger_dir=Path(directory) / 'ledger')
                else:
                    ledger.settle_text_call(reservation, model='gemini-3.8-flash',
                                            prompt_tokens=1, candidates_tokens=output_tokens,
                                            thoughts_tokens=0)

                batch = ledger.batch_budget(batch_id)
                self.assertEqual(batch['general_remaining_usd'], '0.00201000',
                                 'daily overage must never consume or release general headroom')
                self.assertEqual(batch['daily_remaining_usd'], remaining)
                self.assertEqual(json.loads(ledger.path.read_text())['pending'], {},
                                 'confirmed overage must not leave a phantom ambiguous hold')
                for period in ('daily', 'monthly'):
                    self.assertEqual(ledger.summary(NOW)[period]['estimated_cost_usd'], actual_cost)

                if remaining == '0.00000000':
                    before = ledger.path.read_bytes()
                    self.assertEqual(ledger.reserve_batch_budget(
                        batch_id, general_usd='0.00201', daily_usd='0.00201', now=NOW), batch)
                    transports = []
                    with ledger.usage_batch(batch_id), self.assertRaises(UsageCapReached) as denied:
                        ledger.reserve_text_call(0, 1, 10, model='gemini-3.8-flash',
                                                 now=NOW, caller='daily')
                        transports.append('retry')
                    self.assertEqual(denied.exception.period, 'batch')
                    self.assertEqual(transports, [])
                    self.assertEqual(ledger.path.read_bytes(), before)

                closed = ledger.close_batch_budget(batch_id)
                self.assertEqual(closed['status'], 'closed')
                closed_bytes = ledger.path.read_bytes()
                self.assertEqual(ledger.reserve_batch_budget(
                    batch_id, general_usd='0.00201', daily_usd='0.00201', now=NOW), closed)
                self.assertEqual(ledger.close_batch_budget(batch_id), closed)
                self.assertEqual(ledger.path.read_bytes(), closed_bytes)
                for period in ('daily', 'monthly'):
                    self.assertEqual(ledger.summary(NOW)[period]['estimated_cost_usd'], actual_cost)
                self.assertEqual(ledger.summary(NOW)['daily']['batch_reserved_usd'], '0.00000000')
                self.assertEqual(Decimal(ledger.summary(NOW)['daily']['available_to_other_callers_usd']),
                                 Decimal('0.35') - Decimal(actual_cost),
                                 'closure releases unused holds, never the confirmed expense')

    def test_batch_partial_usage_keeps_pending_attempt_and_full_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(config_path=batch_prices_file(directory),
                                 ledger_dir=Path(directory) / 'ledger')
            batch_id = 'fictional-ambiguous-settlement-batch'
            ledger.reserve_batch_budget(batch_id, general_usd='0.010', daily_usd='0.003', now=NOW)
            transports = []
            with ledger.usage_batch(batch_id):
                reservation = ledger.reserve_text_call(
                    0, 0, 1000, model='gemini-3.5-flash', now=NOW, caller='other')
                transports.append('first')

            # One usage field missing is not enough to release any of the hard
            # ceiling, even if the text-derived estimate would be smaller.
            ledger.settle_text_call(reservation, model='gemini-3.5-flash',
                                    prompt_tokens=0, candidates_tokens=25)
            state = json.loads(ledger.path.read_text(encoding='utf-8'))
            self.assertIn(reservation, state['pending'])
            self.assertEqual(ledger.batch_budget(batch_id)['general_remaining_usd'], '0.00700000')
            self.assertEqual(ledger.summary(NOW)['daily']['estimated_cost_usd'], '0.00300000')
            with self.assertRaisesRegex(UsageError, 'unsettled calls'):
                ledger.close_batch_budget(batch_id)

            # Three planned builds share the envelope: the first retries once,
            # the second is daily, and the third must refuse its fourth attempt.
            with ledger.usage_batch(batch_id):
                retry = ledger.reserve_text_call(
                    0, 0, 1000, model='gemini-3.5-flash', now=NOW, caller='other')
                transports.append('build-1-retry')
                ledger.settle_text_call(retry, model='gemini-3.5-flash',
                                        prompt_tokens=0, candidates_tokens=1000, thoughts_tokens=0)
                daily = ledger.reserve_text_call(
                    0, 0, 1000, model='gemini-3.5-flash', now=NOW, caller='daily')
                transports.append('build-2-daily')
            state = json.loads(ledger.path.read_text())
            self.assertEqual(set(state['pending']), {reservation, daily})
            self.assertEqual(state['pending'][daily]['caller'], 'daily')
            for row in state['pending'].values():
                self.assertEqual(row['batch_id'], batch_id)
                self.assertEqual(row['reserved_cost_usd'], '0.00300000')
            self.assertEqual(ledger.batch_budget(batch_id)['daily_remaining_usd'], '0.00000000')
            self.assertEqual(ledger.summary(NOW)['daily']['estimated_cost_usd'], '0.00900000')
            # Global headroom remains, but the original general slice is too
            # small. Refusal cannot release either ambiguous pending attempt.
            before_denial = ledger.path.read_bytes()
            with ledger.usage_batch(batch_id):
                with self.assertRaises(UsageCapReached) as caught:
                    ledger.reserve_text_call(
                        0, 0, 2000, model='gemini-3.5-flash', now=NOW, caller='other')
                    transports.append('build-3')
            self.assertEqual(caught.exception.period, 'batch')
            self.assertEqual(transports, ['first', 'build-1-retry', 'build-2-daily'])
            self.assertEqual(ledger.path.read_bytes(), before_denial)
            self.assertEqual(json.loads(before_denial)['pending'], state['pending'])
            self.assertEqual(ledger.batch_budget(batch_id)['general_remaining_usd'], '0.00400000')
            with self.assertRaisesRegex(UsageError, 'unsettled calls'):
                ledger.close_batch_budget(batch_id)
            self.assertEqual(ledger.path.read_bytes(), before_denial)

            sealed = ledger.finalize_batch_budget(batch_id)
            self.assertEqual(sealed['status'], 'sealed')
            self.assertEqual(sealed['sealed_call_count'], 2)
            self.assertEqual(sealed['sealed_reserved_usd'], '0.00600000')
            self.assertEqual(sealed['usage_basis'], 'reserved_ceiling')
            finalized = json.loads(ledger.path.read_text())
            self.assertEqual(finalized['pending'], {})
            self.assertEqual(ledger.summary(NOW)['daily']['estimated_cost_usd'], '0.00900000')
            self.assertEqual(ledger.summary(NOW)['daily']['batch_reserved_usd'], '0.00000000')
            sealed_bytes = ledger.path.read_bytes()
            self.assertEqual(ledger.finalize_batch_budget(batch_id), sealed)
            self.assertEqual(ledger.close_batch_budget(batch_id), sealed)
            self.assertEqual(ledger.path.read_bytes(), sealed_bytes)

    def test_finalize_ambiguous_token_call_preserves_pool_ceiling_without_token_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(config_path=batch_prices_file(directory),
                                 ledger_dir=Path(directory) / 'ledger')
            batch_id = 'fictional-sealed-token-batch'
            ledger.reserve_batch_budget(batch_id, general_usd='0.010', now=NOW)
            with patch.dict(os.environ, {'BRIEFING_REQUIRE_USAGE_BATCH': '1'}), \
                    ledger.usage_batch(batch_id):
                ledger.reserve_text_call(
                    0, 0, 1000, model='gemini-3.5-flash', input_token_limit=321, now=NOW)

            sealed = ledger.finalize_batch_budget(batch_id)
            state = json.loads(ledger.path.read_text())
            pool = next(iter(state['token_pools'].values()))
            self.assertEqual(sealed['sealed_input_token_ceiling'], 321)
            self.assertEqual(sealed['sealed_output_token_ceiling'], 1000)
            self.assertEqual(pool['reserved_usd'], '0.00000000')
            self.assertEqual(pool['reserved_output_tokens'], 0)
            self.assertEqual(pool['sealed_usd'], sealed['sealed_reserved_usd'])
            self.assertEqual(pool['sealed_output_token_ceiling'], 1000)
            self.assertEqual(pool['sealed_call_count'], 1)
            self.assertNotIn('settled_output_tokens', sealed,
                             'a reservation ceiling must not be reported as confirmed token usage')
            with patch.dict(os.environ, {'BRIEFING_REQUIRE_USAGE_BATCH': '1'}), \
                    self.assertRaises(UsageCapReached):
                with ledger.usage_batch(batch_id):
                    ledger.reserve_text_call(
                        0, 0, 1, model='gemini-3.5-flash', input_token_limit=1, now=NOW)

    def test_token_ceiling_reservations_share_a_batch_pool_and_retain_ambiguous_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            config = prices_file(directory, daily='0.50', monthly='5.00')
            value = json.loads(config.read_text())
            value['caps']['daily_run_reserve_usd'] = '0.15'
            config.write_text(json.dumps(value))
            ledger = UsageLedger(config_path=config, ledger_dir=Path(directory) / 'ledger')
            batch_id = 'fictional-two-run-2026-11-18'
            pool_limit = Decimal('0.06144')
            transport_calls = []

            def transport_after_reservation(reservation):
                pending = json.loads(ledger.path.read_text(encoding='utf-8'))['pending']
                self.assertIn(reservation, pending)
                transport_calls.append(reservation)

            self.assertEqual(ledger.estimate_token_call_cost(
                8192, 3072, model='gemini-3.8-flash-lite-tts', modality='tts'),
                Decimal('0.022528'))
            first = ledger.reserve_tts_token_call(
                8192, 3072, 'gemini-3.8-flash-lite-tts', batch_id=batch_id,
                batch_limit_usd=pool_limit, caller='daily', now=NOW)
            transport_after_reservation(first)
            raw = json.loads(ledger.path.read_text(encoding='utf-8'))
            self.assertEqual(raw['pending'][first]['token_input_limit'], 8192)
            self.assertEqual(raw['pending'][first]['token_output_limit'], 3072)
            self.assertEqual(raw['token_pools'][batch_id]['reserved_usd'], '0.02252800')

            ledger.settle_tts_token_call(first, 6000, 1000, 'gemini-3.8-flash-lite-tts')
            second = ledger.reserve_tts_token_call(
                8192, 3072, 'gemini-3.8-flash-lite-tts', batch_id=batch_id,
                batch_limit_usd=pool_limit, caller='daily', now=NOW)
            transport_after_reservation(second)
            # Ambiguous usage remains pending and occupies its full token-cost ceiling.
            with self.assertRaises(UsageCapReached) as caught:
                ledger.reserve_tts_token_call(
                    8192, 3072, 'gemini-3.8-flash-lite-tts', batch_id=batch_id,
                    batch_limit_usd=pool_limit, caller='daily', now=NOW)
            self.assertEqual(caught.exception.period, 'batch_tokens')
            self.assertEqual(len(transport_calls), 2,
                             'a denied retry never reaches the transport')
            raw = json.loads(ledger.path.read_text(encoding='utf-8'))
            self.assertIn(second, raw['pending'])
            self.assertEqual(raw['token_pools'][batch_id]['reserved_usd'], '0.02252800')
            self.assertEqual(raw['token_pools'][batch_id]['settled_usd'], '0.00900000')
            self.assertEqual(raw['token_pools'][batch_id]['reserved_output_tokens'], 3072)
            self.assertEqual(raw['token_pools'][batch_id]['settled_output_tokens'], 1000)

    def test_token_ceiling_reservation_refuses_general_daily_and_monthly_caps_before_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            config = prices_file(directory, daily='0.50', monthly='5.00')
            value = json.loads(config.read_text())
            value['caps']['daily_run_reserve_usd'] = '0.15'
            config.write_text(json.dumps(value))
            ledger = UsageLedger(config_path=config, ledger_dir=Path(directory) / 'ledger')
            for _ in range(70):
                ledger.record_search('perplexity', now=NOW)
            transports = []

            with self.assertRaises(UsageCapReached) as daily:
                reservation = ledger.reserve_token_call(
                    0, 1000, model='gemini-3.5-flash', pool_id='fictional-daily-cap',
                    pool_limit_usd='0.10', caller='other', now=NOW)
                transports.append(reservation)
            self.assertEqual(daily.exception.period, 'daily')
            self.assertEqual(transports, [])

            monthly_config = prices_file(directory, daily='2.00', monthly='0.001')
            monthly_ledger = UsageLedger(config_path=monthly_config,
                                         ledger_dir=Path(directory) / 'monthly-ledger')
            with self.assertRaises(UsageCapReached) as monthly:
                reservation = monthly_ledger.reserve_token_call(
                    1000, 1000, model='gemini-3.5-flash', pool_id='fictional-monthly-cap',
                    pool_limit_usd='0.10', now=NOW)
                transports.append(reservation)
            self.assertEqual(monthly.exception.period, 'monthly')
            self.assertEqual(transports, [])

    def test_two_build_four_logical_call_text_envelope_reserves_each_retry_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            config = batch_prices_file(directory)
            ledger = UsageLedger(config_path=config, ledger_dir=Path(directory) / 'ledger')
            batch_id = 'fictional-four-call-two-build-text-batch'
            token_pool_id = 'fictional-four-call-two-build-text-pool'
            model = 'gemini-3.5-flash'
            input_limit = 16_384
            initial_output_limit = 1_024
            retry_output_limit = 1_536
            attempt_pair_cost = sum((
                ledger.estimate_token_call_cost(input_limit, initial_output_limit, model=model),
                ledger.estimate_token_call_cost(input_limit, retry_output_limit, model=model),
            ), Decimal(0))
            two_build_cost = attempt_pair_cost * 4 * 2
            total_output_limit = (initial_output_limit + retry_output_limit) * 4 * 2

            self.assertEqual(attempt_pair_cost, Decimal('0.024064'))
            self.assertEqual(two_build_cost, Decimal('0.192512'))
            self.assertLess(two_build_cost, Decimal('0.35'))
            ledger.reserve_batch_budget(batch_id, general_usd=two_build_cost, now=NOW)
            transports = []
            logical_kinds = ('story_writer', 'story_writer', 'story_writer', 'claim_repair')

            for build_number in range(2):
                logical_calls = 0
                with ledger.usage_batch(batch_id):
                    for logical_kind in logical_kinds:
                        logical_calls += 1
                        self.assertLessEqual(logical_calls, 4)
                        for output_limit in (initial_output_limit, retry_output_limit):
                            reservation = ledger.reserve_token_call(
                                input_limit, output_limit, model=model,
                                pool_id=token_pool_id, pool_limit_usd=two_build_cost,
                                pool_output_token_limit=total_output_limit,
                                modality='text', now=NOW, caller='other')
                            pending = json.loads(ledger.path.read_text(encoding='utf-8'))['pending']
                            self.assertIn(reservation, pending,
                                          'full token ceilings are persisted before fictional transport')
                            self.assertEqual(pending[reservation]['token_input_limit'], input_limit)
                            self.assertEqual(pending[reservation]['token_output_limit'], output_limit)
                            transports.append((build_number, logical_calls, logical_kind, output_limit))
                            ledger.settle_token_call(reservation, input_limit, output_limit,
                                                     model=model, modality='text')

            self.assertEqual(len(transports), 16,
                             'two builds reserve both attempts for four logical calls per build')
            self.assertEqual(sum(1 for _build, _logical, _kind, cap in transports if cap == 1_024), 8)
            self.assertEqual(sum(1 for _build, _logical, _kind, cap in transports if cap == 1_536), 8)
            self.assertEqual(sum(1 for _build, _logical, kind, _cap in transports
                                 if kind == 'claim_repair'), 4)
            self.assertEqual(ledger.batch_budget(batch_id)['general_remaining_usd'], '0.00000000')
            self.assertEqual(ledger.summary(NOW)['daily']['estimated_cost_usd'], '0.19251200')
            self.assertEqual(ledger.summary(NOW)['daily']['available_to_other_callers_usd'],
                             '0.15748800')
            state = json.loads(ledger.path.read_text(encoding='utf-8'))
            pool = state['token_pools'][token_pool_id]
            self.assertEqual(pool['settled_usd'], '0.19251200')
            self.assertEqual(pool['settled_output_tokens'], total_output_limit)
            self.assertEqual(pool['reserved_output_tokens'], 0)

    def test_strict_batch_refuses_unpriced_text_model_before_transport(self):
        cases = (
            (True, 'gemini-99-flash', None),
            (True, 'gemini-3.8-flash-fictional-price-drift', None),
            (True, 'gemini-3.8-flash-lite-tts', None),
            (True, 'gemini-3.5-flash-fictional-price-drift', None),
            (True, 'gemini-3.5-flash', '0.00035000'),
            (True, 'gemini-3.8-flash', '0.00045000'),
            (False, 'gemini-99-flash', '0.00035000'),
            (False, 'gemini-3.8-flash-fictional-price-drift', '0.00045000'),
        )
        for strict, model, expected_cost in cases:
            with self.subTest(strict=strict, model=model), tempfile.TemporaryDirectory() as directory:
                ledger = UsageLedger(config_path=batch_prices_file(directory),
                                     ledger_dir=Path(directory) / 'ledger')
                batch_id = 'fictional-strict-model-price-batch'
                ledger.reserve_batch_budget(batch_id, general_usd='0.10', now=NOW)
                before = ledger.path.read_bytes()
                transports = []

                def reserve_then_transport():
                    reservation = ledger.reserve_token_call(
                        100, 100, model=model, pool_id='fictional-model-pool', pool_limit_usd='0.10',
                        pool_output_token_limit=100, modality='text', now=NOW, caller='other')
                    transports.append(model)
                    return reservation

                with patch.dict(os.environ, {'BRIEFING_REQUIRE_USAGE_BATCH': '1' if strict else '0'}):
                    with ledger.usage_batch(batch_id):
                        if expected_cost is None:
                            with self.assertRaises(UsageCapReached) as caught:
                                reserve_then_transport()
                            self.assertEqual(caught.exception.period, 'model_price')
                            self.assertEqual(transports, [], 'unpriced identity must not reach transport')
                            self.assertEqual(ledger.path.read_bytes(), before,
                                             'refusal must not consume the batch or create pending usage')
                        else:
                            reservation = reserve_then_transport()
                            self.assertEqual(transports, [model])
                            pending = json.loads(ledger.path.read_text())['pending'][reservation]
                            self.assertEqual(pending['reserved_cost_usd'], expected_cost)
                            ledger.settle_token_call(reservation, 100, 100, model=model, modality='text')
                            self.assertEqual(ledger.summary(NOW)['daily']['estimated_cost_usd'], expected_cost)

    def test_token_pool_reservations_are_atomic_across_ledger_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            config = prices_file(directory)
            ledger_dir = Path(directory) / 'shared-ledger'
            ledgers = [UsageLedger(config_path=config, ledger_dir=ledger_dir) for _ in range(2)]
            transports = []

            def attempt(index):
                ledger = ledgers[index % len(ledgers)]
                try:
                    reservation = ledger.reserve_token_call(
                        1000, 1000, model='gemini-3.5-flash', pool_id='fictional-atomic-batch',
                        pool_limit_usd='0.0035', now=NOW)
                except UsageCapReached:
                    return False
                transports.append(reservation)
                return True

            with ThreadPoolExecutor(max_workers=8) as pool:
                accepted = list(pool.map(attempt, range(8)))
            self.assertEqual(sum(accepted), 1)
            self.assertEqual(len(transports), 1,
                             'the shared batch pool admits only one pre-transport reservation')
            state = json.loads(ledger_dir.joinpath('ledger.json').read_text(encoding='utf-8'))
            self.assertEqual(len(state['pending']), 1)

    def test_token_settlement_records_and_rejects_usage_above_the_reserved_ceiling(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(ledger_dir=Path(directory) / 'ledger')
            reservation = ledger.reserve_token_call(
                1000, 1000, model='gemini-3.5-flash', pool_id='fictional-over-ceiling',
                pool_limit_usd='0.007', now=NOW)
            transports = ['initial']
            with self.assertRaises(UsageCapReached) as caught:
                ledger.settle_token_call(reservation, 1001, 1000,
                                         model='gemini-3.5-flash', modality='text')
            self.assertEqual(caught.exception.period, 'token_ceiling')
            self.assertEqual(ledger.summary(NOW)['daily']['estimated_cost_usd'], '0.00350050')
            settled_bytes = ledger.path.read_bytes()
            state = json.loads(settled_bytes)
            self.assertNotIn(reservation, state['pending'])
            pool = state['token_pools']['fictional-over-ceiling']
            self.assertEqual(pool['reserved_usd'], '0.00000000')
            self.assertEqual(pool['settled_usd'], '0.00350050')
            self.assertEqual(pool['reserved_output_tokens'], 0)
            self.assertEqual(pool['settled_output_tokens'], 1000)

            # Two estimated $0.0035 attempts would fit this fictional pool.
            # A fresh client must see the confirmed first-attempt overrun and
            # refuse the retry, even though global daily/monthly caps permit it.
            restarted = UsageLedger(ledger_dir=Path(directory) / 'ledger')
            with self.assertRaises(UsageCapReached) as retry:
                restarted.reserve_token_call(
                    1000, 1000, model='gemini-3.5-flash', pool_id='fictional-over-ceiling',
                    pool_limit_usd='0.007', now=NOW)
                transports.append('retry')
            self.assertEqual(retry.exception.period, 'batch')
            self.assertEqual(transports, ['initial'])
            self.assertEqual(restarted.path.read_bytes(), settled_bytes)

    def test_token_pool_rejects_changed_limit_for_an_existing_batch_id(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(ledger_dir=Path(directory) / 'ledger')
            ledger.reserve_token_call(
                100, 100, model='gemini-3.5-flash', pool_id='fictional-stable-batch',
                pool_limit_usd='0.01', now=NOW)
            with self.assertRaisesRegex(UsageError, 'pool identity or limit changed'):
                ledger.reserve_token_call(
                    100, 100, model='gemini-3.5-flash', pool_id='fictional-stable-batch',
                    pool_limit_usd='0.02', now=NOW)

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

    def test_daily_audio_budget_includes_ordinary_pending_tts_once_with_settled_and_batch_holds(self):
        with tempfile.TemporaryDirectory() as directory:
            config = batch_prices_file(directory)
            ledger = UsageLedger(config_path=config, ledger_dir=Path(directory) / 'ledger')

            def query_in_fresh_process():
                before_query = ledger.path.read_bytes()
                env = {'PATH': os.defpath, 'BRIEFING_TESTING': '1',
                       'BRIEFING_STATE_DIR': str(Path(directory) / 'child-state'),
                       'BRIEFING_TEST_LEDGER_DIR': str(ledger.state_dir),
                       'PYTHONDONTWRITEBYTECODE': '1'}
                child = subprocess.run(
                    [sys.executable, '-c', DAILY_AUDIO_RESERVE_QUERY,
                     str(config), str(ledger.state_dir), NOW.isoformat()],
                    cwd=Path(__file__).resolve().parents[2], env=env,
                    capture_output=True, text=True, timeout=20)
                self.assertEqual(child.returncode, 0, child.stderr)
                result = json.loads(child.stdout)
                self.assertTrue(result['unchanged'])
                self.assertEqual(ledger.path.read_bytes(), before_query)
                return result

            caller_token = usage_caller('daily')
            try:
                chars_per_second = Decimal(ledger.config['_tts_chars_per_second'])
                settled_chars = 3_000
                settled_seconds = Decimal(settled_chars) / chars_per_second
                settled_reservation = ledger.reserve_tts_call(
                    settled_chars, model='fictional-tts', now=NOW)
                ledger.settle_tts_call(settled_reservation, settled_chars, settled_seconds)

                ledger.reserve_batch_budget(
                    'fictional-audio-reserve', general_usd='0', daily_usd='0.02', now=NOW)
                pending_chars = 3_000
                pending_reservation = ledger.reserve_tts_call(
                    pending_chars, model='fictional-tts', now=NOW)
                state = json.loads(ledger.path.read_text(encoding='utf-8'))
                pending_cost = Decimal(
                    state['pending'][pending_reservation]['reserved_cost_usd'])
                settled_cost = ledger.estimate_tts_cost(settled_chars, settled_seconds)
                batch_hold = Decimal('0.02')
                expected_remaining = Decimal('0.15') - settled_cost - batch_hold - pending_cost

                pending_row = state['pending'][pending_reservation]
                self.assertEqual(pending_row['caller'], 'daily')
                self.assertEqual(pending_row['kind'], 'tts')
                self.assertEqual(ledger.daily_audio_budget_remaining(NOW), expected_remaining)
                restarted_pending = query_in_fresh_process()
                self.assertEqual(restarted_pending['pending_ids'], [pending_reservation])
                self.assertEqual(Decimal(restarted_pending['remaining']), expected_remaining,
                                 'a fresh process cannot recover funds held by ambiguous daily TTS')

                pending_seconds = Decimal(pending_chars) / chars_per_second
                ledger.settle_tts_call(pending_reservation, pending_chars, pending_seconds)
                self.assertEqual(ledger.daily_audio_budget_remaining(NOW), expected_remaining,
                                 'settlement replaces the pending hold instead of counting both')
                restarted_settled = query_in_fresh_process()
                self.assertEqual(restarted_settled['pending_ids'], [])
                self.assertEqual(Decimal(restarted_settled['remaining']), expected_remaining,
                                 'confirmed equal-cost settlement stays charged exactly once')
                self.assertEqual(ledger.batch_budget('fictional-audio-reserve')['daily_remaining_usd'],
                                 '0.02000000', 'the independent batch hold remains counted once')
                self.assertEqual(ledger.config['_daily_cap'], Decimal('0.50'))
                self.assertEqual(ledger.config['_daily_reserve'], Decimal('0.15'))
            finally:
                reset_usage_caller(caller_token)

    def test_synthetic_two_run_writer_batch_reserves_before_transport_and_stops_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(ledger_dir=Path(directory) / 'ledger')
            # A fictional prior daily spend leaves a small, explicit general-caller allowance.
            ledger.reserve_text_call(0, 0, 107_000, model='gemini-3.5-flash', now=NOW, caller='daily')
            calls = []
            results = []
            phases = ('writer', 'writer', 'writer', 'verifier', 'retrieval')

            def reserved_transport(run_number, phase, candidate=None):
                output_caps = {'writer': 800, 'verifier': 400, 'retrieval': 800}
                try:
                    reservation = ledger.reserve_text_call(
                        100, 100, output_caps[phase], model='gemini-3.5-flash', now=NOW, caller='other')
                    if phase == 'retrieval':
                        ledger.record_search('gemini', now=NOW, caller='other')
                except UsageCapReached:
                    return False
                state = json.loads(ledger.path.read_text(encoding='utf-8'))
                self.assertIn(reservation, state['pending'], 'transport requires a durable reservation')
                if phase == 'retrieval':
                    self.assertEqual(ledger.summary(NOW)['daily']['search_queries'].get('gemini'), 1)
                calls.append((run_number, phase, candidate))
                return True

            for run_number in (1, 2):
                passed_candidates = 0
                incomplete = False
                for candidate in range(1, 4):
                    if not reserved_transport(run_number, 'writer', candidate):
                        incomplete = True
                        break
                    passed_candidates += 1
                if not incomplete and reserved_transport(run_number, 'verifier'):
                    pass
                else:
                    incomplete = True
                if not incomplete and reserved_transport(run_number, 'retrieval'):
                    pass
                else:
                    incomplete = True
                results.append({'run': run_number, 'passed_candidates': passed_candidates,
                                'status': 'incomplete' if incomplete or passed_candidates < 3 else 'complete'})
                if run_number == 1:
                    # TTS runs between acceptance builds and consumes the shared daily cap.
                    ledger.reserve_tts_call(1_200, model='fictional-tts', now=NOW,
                                            caller='daily')

            self.assertEqual([row['status'] for row in results], ['complete', 'incomplete'])
            self.assertEqual(results[0]['passed_candidates'], 3)
            self.assertLess(results[1]['passed_candidates'], 3)
            self.assertEqual(calls, [
                (1, 'writer', 1), (1, 'writer', 2), (1, 'writer', 3),
                (1, 'verifier', None), (1, 'retrieval', None),
                (2, 'writer', 1),
            ], 'calls denied by the ledger never reach the mock transport')

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

    def test_reserves_and_settles_counts_only_and_estimated_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(ledger_dir=directory)
            reservation = ledger.reserve_text_call(100, 80, 100, model='gemini-fictional', now=NOW)
            ledger.settle_text_call(reservation, 100, 80, 60, model='gemini-fictional')
            ledger.record_search('gemini', now=NOW)
            tts_reservation = ledger.reserve_tts_call(120, model='gemini-fictional-tts', now=NOW)
            ledger.settle_tts_call(tts_reservation, 120, 8)
            ledger.record_fetch(now=NOW)

            summary = ledger.summary(NOW)
            self.assertEqual(summary['daily']['text_calls'], 1)
            self.assertEqual(summary['daily']['search_queries'], {'gemini': 1})
            self.assertEqual(summary['daily']['tts_characters'], 120)
            self.assertEqual(summary['daily']['fetches'], 1)
            self.assertGreater(float(summary['daily']['estimated_cost_usd']), 0)
            self.assertEqual(summary['cap_status'], 'within_caps')

            raw = (Path(directory) / 'ledger.json').read_text(encoding='utf-8')
            for forbidden in ('fictional reader text', 'https://', 'API key', 'spoken paragraph'):
                self.assertNotIn(forbidden, raw)
            self.assertEqual((Path(directory) / 'ledger.json').stat().st_mode & 0o777, 0o600)

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

    def test_strict_gemini_grounding_overage_requires_and_debits_general_batch(self):
        from contextlib import nullcontext

        for batch_amount, admitted in ((None, False), ('0.010', False), ('0.020', True)):
            with self.subTest(batch_amount=batch_amount), tempfile.TemporaryDirectory() as directory:
                config = prices_file(directory)
                value = json.loads(config.read_text())
                value['prices']['gemini_grounded_search'] = {
                    'free_allowance': 1, 'overage_usd_per_query': '0.014'}
                config.write_text(json.dumps(value))
                ledger = UsageLedger(config_path=config, ledger_dir=Path(directory) / 'ledger')
                ledger.record_search('gemini', now=NOW)  # consume the fictional free query
                batch_id = 'fictional-grounding-overage-batch'
                if batch_amount is not None:
                    ledger.reserve_batch_budget(batch_id, general_usd=batch_amount, now=NOW)
                before = ledger.path.read_bytes()
                transports = []

                def mocked_search_transport():
                    ledger.record_search('gemini', now=NOW, caller='other')
                    transports.append('sent')

                batch_context = ledger.usage_batch(batch_id) if batch_amount is not None else nullcontext()
                with patch.dict(os.environ, {'BRIEFING_REQUIRE_USAGE_BATCH': '1'}), batch_context:
                    if admitted:
                        mocked_search_transport()
                    else:
                        with self.assertRaises(UsageCapReached) as caught:
                            mocked_search_transport()
                        self.assertEqual(caught.exception.period, 'batch')

                self.assertEqual(transports, ['sent'] if admitted else [])
                self.assertEqual(ledger.summary(NOW)['daily']['search_queries']['gemini'],
                                 2 if admitted else 1)
                if admitted:
                    self.assertEqual(ledger.batch_budget(batch_id)['general_remaining_usd'],
                                     '0.00600000')
                    self.assertEqual(ledger.batch_budget(batch_id)['daily_remaining_usd'],
                                     '0.00000000')
                else:
                    self.assertEqual(ledger.path.read_bytes(), before)

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

    def test_daily_cap_reservations_are_atomic_across_workers(self):
        with tempfile.TemporaryDirectory() as directory:
            config = prices_file(directory, daily='0.015', monthly='1.00')
            ledger = UsageLedger(config_path=config, ledger_dir=directory)
            def attempt(_):
                try:
                    ledger.record_search('perplexity', now=NOW)
                    return True
                except UsageCapReached as error:
                    self.assertEqual(error.period, 'daily')
                    return False
            with ThreadPoolExecutor(max_workers=12) as pool:
                outcomes = list(pool.map(attempt, range(40)))
            self.assertEqual(sum(outcomes), 3)
            summary = ledger.summary(NOW)
            self.assertEqual(summary['daily']['search_queries'], {'perplexity': 3})
            self.assertEqual(summary['cap_status'], 'daily_cap_reached')
            self.assertEqual(summary['cap_denials']['daily'], 37)

    def test_concurrent_reservations_gate_failed_retries_and_keep_actual_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            config = prices_file(directory, daily='0.015', monthly='1.00')
            ledger_dir = Path(directory) / 'global-ledger'
            ledgers = [UsageLedger(config_path=config, ledger_dir=ledger_dir) for _ in range(2)]
            provider_calls = []
            model = 'fictional-model'

            def failing_provider(identifier):
                provider_calls.append(identifier)
                raise RuntimeError('fictional provider failure')

            def failed_attempt(index):
                ledger = ledgers[index % len(ledgers)]
                try:
                    reservation = ledger.reserve_text_call(
                        100, 100, 2_000, model=model, now=NOW)
                except UsageCapReached:
                    return None
                try:
                    failing_provider(index)
                except RuntimeError:
                    return reservation

            with ThreadPoolExecutor(max_workers=8) as pool:
                reservations = list(pool.map(failed_attempt, range(8)))
            reservations = [row for row in reservations if row is not None]
            self.assertEqual(len(reservations), 2)
            self.assertEqual(len(provider_calls), 2,
                             'provider access follows successful reservations only')

            reserved = ledgers[0].summary(NOW)['daily']
            self.assertEqual(reserved['estimated_cost_usd'], '0.01205000')
            self.assertEqual((reserved['prompt_tokens'], reserved['candidates_tokens'],
                              reserved['thoughts_tokens']), (0, 0, 0))
            self.assertEqual(len(json.loads(ledgers[0].path.read_text())['pending']), 2)

            def retry_after_failure(index):
                try:
                    ledgers[index % len(ledgers)].reserve_text_call(
                        100, 100, 2_000, model=model, now=NOW)
                except UsageCapReached:
                    return False
                try:
                    failing_provider(f'retry-{index}')
                except RuntimeError:
                    return True

            with ThreadPoolExecutor(max_workers=2) as pool:
                retries = list(pool.map(retry_after_failure, range(2)))
            self.assertEqual(retries, [False, False])
            self.assertEqual(len(provider_calls), 2,
                             'over-cap retries are refused before provider access')

            ledgers[1].settle_text_call(
                reservations[0], model=model, prompt_tokens=50,
                candidates_tokens=30, thoughts_tokens=10)
            settled = ledgers[0].summary(NOW)['daily']
            self.assertEqual((settled['prompt_tokens'], settled['candidates_tokens'],
                              settled['thoughts_tokens']), (50, 30, 10))
            self.assertEqual(settled['text_calls'], 2)
            self.assertEqual(settled['estimated_cost_usd'], '0.00617000')
            self.assertEqual(len(json.loads(ledgers[0].path.read_text())['pending']), 1)

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
