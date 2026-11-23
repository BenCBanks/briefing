"""Count-only provider usage and estimated cost ledger.

The ledger never accepts content, queries, URLs, or credentials. Reservations
are written before network calls, so concurrent callers share the same caps.
"""

from __future__ import annotations

from contextvars import ContextVar
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import RLock
from uuid import uuid4
from zoneinfo import ZoneInfo
import fcntl
import hashlib
import json
import os
import re

EASTERN = ZoneInfo('America/New_York')
PRICE_CONFIG = Path(__file__).with_name('usage-prices.json')
# Tests use fixed caps so changing the real caps never changes test results.
TEST_PRICE_CONFIG = Path(__file__).resolve().parents[1] / 'tests/fixtures/usage-prices.json'
GLOBAL_LEDGER_DIR = Path.home() / '.local/state/briefing-usage'
_PROCESS_LOCK = RLock()
_CALLER = ContextVar('briefing_usage_caller', default='other')
_JOB = ContextVar('briefing_usage_job', default=None)
_BATCH = ContextVar('briefing_usage_batch', default=None)
_RELEASE_USAGE = ContextVar('briefing_release_usage', default=None)
# The active proxy model is intentionally priced by the configured text fallback.
# Pin that one model explicitly so strict batches cannot silently price a future
# unknown model drift at the same generic fallback rate.
STRICT_BATCH_FALLBACK_TEXT_MODELS = frozenset({'gemini-3.5-flash'})


@contextmanager
def episode_usage(job_id, cancelled):
    token = _JOB.set((job_id, cancelled))
    try:
        yield
    finally:
        _JOB.reset(token)


def usage_caller(name):
    """Set the caller class for one synchronous generation job."""
    if name not in {'other', 'daily'}:
        raise ValueError('invalid usage caller')
    return _CALLER.set(name)


def reset_usage_caller(token):
    _CALLER.reset(token)


@contextmanager
def usage_batch(batch_id):
    """Charge provider reservations against one previously reserved batch envelope."""
    token = _BATCH.set(UsageLedger._batch_id(batch_id))
    try:
        yield
    finally:
        _BATCH.reset(token)


@dataclass(frozen=True)
class ReleaseUsageRef:
    """Internal trusted-launch reference, never populated from reader requests."""

    ledger_dir: Path
    batch_id: str

    def __post_init__(self):
        object.__setattr__(self, 'ledger_dir', Path(self.ledger_dir))
        UsageLedger._batch_id(self.batch_id)


def _release_ledger(ref, factory):
    if type(ref) is not ReleaseUsageRef:
        raise UsageError('trusted usage reference required')
    directory = ref.ledger_dir
    if (not directory.is_absolute() or not directory.is_dir() or
            directory.resolve() != directory or not (directory / 'ledger.json').is_file()):
        raise UsageError('trusted usage ledger is unavailable')
    ledger = factory(ledger_dir=directory)

    def check(state):
        # Lookup only: never recreate an absent ledger or reserve a new envelope.
        if not ledger.path.is_file():
            raise UsageError('trusted usage ledger is unavailable')
        batch = state['batch_reservations'].get(ref.batch_id)
        if (not isinstance(batch, dict) or batch.get('status') != 'open' or
                (batch.get('day'), batch.get('month')) != ledger._periods()):
            raise UsageCapReached('batch')
        return None, False

    ledger._transaction(check)
    return ledger


@contextmanager
def trusted_usage_batch(ref):
    """Enter an existing envelope locally; its coordinator alone owns closure."""
    _release_ledger(ref, UsageLedger)
    token = _RELEASE_USAGE.set(ref)
    try:
        with usage_batch(ref.batch_id):
            yield
    finally:
        _RELEASE_USAGE.reset(token)


class UsageError(RuntimeError):
    """Usage state or configuration is invalid."""


class UsageCancelled(UsageError):
    pass


class UsageCapReached(UsageError):
    def __init__(self, period):
        self.period = period
        super().__init__('cost_cap_reached')


def _decimal(value, name):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise UsageError(f'invalid usage price: {name}') from None
    if not result.is_finite() or result < 0:
        raise UsageError(f'invalid usage price: {name}')
    return result


def _money(value):
    return f'{Decimal(value).quantize(Decimal("0.00000001")):.8f}'


def _new_bucket():
    return {'text_calls': 0, 'search_queries': {}, 'tts_characters': 0, 'fetches': 0,
            'prompt_tokens': 0, 'candidates_tokens': 0, 'thoughts_tokens': 0,
            'estimated_cost_usd': '0.00000000', 'by_model': {}}


class UsageLedger:
    def __init__(self, state_dir=None, config_path=PRICE_CONFIG, *, ledger_dir=None):
        # state_dir remains accepted for source compatibility, but never scopes
        # the global spend ledger. Tests may inject an isolated ledger_dir.
        if os.environ.get('BRIEFING_TESTING') == '1' and ledger_dir is None:
            ledger_dir = os.environ.get('BRIEFING_TEST_LEDGER_DIR')
            if not ledger_dir:
                raise UsageError('test ledger path required')
        if os.environ.get('BRIEFING_TESTING') == '1' and Path(config_path) == PRICE_CONFIG:
            config_path = TEST_PRICE_CONFIG
        self.state_dir = Path(ledger_dir).expanduser() if ledger_dir is not None else GLOBAL_LEDGER_DIR
        self.path = self.state_dir / 'ledger.json'
        self.lock_path = self.state_dir / 'ledger.lock'
        self.config_path = Path(config_path)
        self.config = self._load_config()

    def _load_config(self):
        try:
            config = json.loads(self.config_path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            raise UsageError('usage price configuration is unavailable or invalid') from None
        if not isinstance(config, dict) or config.get('currency') != 'USD':
            raise UsageError('usage price configuration must use USD')
        caps, prices = config.get('caps'), config.get('prices')
        if not isinstance(caps, dict) or not isinstance(prices, dict):
            raise UsageError('usage price configuration is incomplete')
        daily = _decimal(caps.get('daily_usd'), 'daily cap')
        monthly = _decimal(caps.get('monthly_usd'), 'monthly cap')
        reserve = _decimal(caps.get('daily_run_reserve_usd', '0'), 'daily run reserve')
        if reserve > daily:
            raise UsageError('daily run reserve exceeds daily cap')
        text = prices.get('gemini_text_fallback') or prices.get('gemini_text')
        tts = prices.get('tts')
        searches = prices.get('search_usd_per_query')
        if not isinstance(text, dict) or not isinstance(tts, dict) or not isinstance(searches, dict):
            raise UsageError('usage price configuration is incomplete')
        grounded_search = prices.get('gemini_grounded_search')
        if not isinstance(grounded_search, dict):
            raise UsageError('usage price configuration is incomplete')
        free_allowance = grounded_search.get('free_allowance')
        if type(free_allowance) is not int or free_allowance < 0:
            raise UsageError('invalid Gemini grounded-search free allowance')
        result = deepcopy(config)
        result['_daily_cap'] = daily
        result['_monthly_cap'] = monthly
        result['_daily_reserve'] = reserve
        result['_text_input_rate'] = _decimal(text.get('input_usd_per_million_tokens'), 'Gemini text input')
        result['_text_output_rate'] = _decimal(text.get('output_usd_per_million_tokens'), 'Gemini text output')
        by_model = prices.get('gemini_text_by_model', {})
        if not isinstance(by_model, dict):
            raise UsageError('usage price configuration is incomplete')
        result['_text_rates_by_model'] = {
            str(prefix): (_decimal(rates.get('input_usd_per_million_tokens'), f'{prefix} input'),
                          _decimal(rates.get('output_usd_per_million_tokens'), f'{prefix} output'))
            for prefix, rates in by_model.items() if isinstance(rates, dict)}
        tts_tokens = prices.get('gemini_tts_lite')
        try:
            if not isinstance(tts_tokens, dict):
                raise UsageError('Gemini TTS token rates are unavailable')
            result['_tts_token_input_rate'] = _decimal(
                tts_tokens.get('text_usd_per_million_tokens'), 'Gemini TTS input tokens')
            result['_tts_token_output_rate'] = _decimal(
                tts_tokens.get('audio_usd_per_million_tokens'), 'Gemini TTS output tokens')
        except UsageError:
            # Keep older/custom price files valid for existing character/time TTS paths.
            result['_tts_token_input_rate'] = None
            result['_tts_token_output_rate'] = None
        result['_text_chars_per_token'] = self._positive_int(
            prices.get('characters_per_token', text.get('characters_per_token')), 'characters per token')
        result['_search_prices'] = {str(provider): _decimal(price, f'{provider} search')
                                    for provider, price in searches.items()}
        result['_grounded_search_free_allowance'] = free_allowance
        result['_grounded_search_overage_price'] = _decimal(
            grounded_search.get('overage_usd_per_query'), 'Gemini grounded-search overage')
        result['_fetch_price'] = _decimal(prices.get('fetch_usd_each'), 'fetch')
        result['_tts_char_rate'] = _decimal(tts.get('usd_per_million_characters'), 'TTS characters')
        result['_tts_minute_rate'] = _decimal(tts.get('usd_per_minute'), 'TTS duration')
        result['_tts_chars_per_second'] = self._positive_int(
            tts.get('estimated_characters_per_second'), 'TTS estimated characters per second')
        return result
    @staticmethod
    def _positive_int(value, name):
        if type(value) is not int or value <= 0:
            raise UsageError(f'invalid usage price: {name}')
        return value

    def _empty_state(self):
        return {'schema_version': 1, 'days': {}, 'months': {}, 'pending': {},
                'cap_denials': {'days': {}, 'months': {}}, 'token_pools': {},
                'batch_reservations': {}}

    def _read(self):
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return self._empty_state()
        except (OSError, ValueError, TypeError):
            raise UsageError('usage ledger is unavailable or invalid') from None
        if (not isinstance(value, dict) or value.get('schema_version') != 1 or
                not isinstance(value.get('days'), dict) or not isinstance(value.get('months'), dict) or
                not isinstance(value.get('pending'), dict) or
                not isinstance(value.get('cap_denials'), dict) or
                not isinstance(value['cap_denials'].get('days'), dict) or
                not isinstance(value['cap_denials'].get('months'), dict)):
            raise UsageError('usage ledger is unavailable or invalid')
        if 'token_pools' not in value:
            value['token_pools'] = {}
        if not isinstance(value['token_pools'], dict):
            raise UsageError('usage ledger token pools are invalid')
        if 'batch_reservations' not in value:
            value['batch_reservations'] = {}
        if not isinstance(value['batch_reservations'], dict):
            raise UsageError('usage ledger batch reservations are invalid')
        return value

    def _write(self, state):
        temporary = self.path.with_name(f'.{self.path.name}.{os.getpid()}.{uuid4().hex}.tmp')
        try:
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump(state, output, separators=(',', ':'), sort_keys=True)
                output.write('\n')
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(self.path)
            os.chmod(self.path, 0o600)
        finally:
            temporary.unlink(missing_ok=True)

    def _transaction(self, operation):
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink() or self.lock_path.is_symlink():
            raise UsageError('usage ledger path may not be a symlink')
        os.chmod(self.state_dir, 0o700)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with _PROCESS_LOCK, os.fdopen(fd, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                state = self._read()
                result, changed = operation(state)
                if changed:
                    self._write(state)
                return result
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    def _periods(now=None):
        current = (now or datetime.now(EASTERN)).astimezone(EASTERN)
        return current.date().isoformat(), current.strftime('%Y-%m')

    @staticmethod
    def _bucket(container, key):
        value = container.get(key)
        if value is None:
            value = _new_bucket()
            container[key] = value
        if not isinstance(value, dict):
            raise UsageError('usage ledger aggregate is invalid')
        return value

    @staticmethod
    def _increment(bucket, kind, provider, model, units, cost):
        if kind == 'text_call':
            bucket['text_calls'] += 1
        elif kind == 'search_query':
            counts = bucket['search_queries']
            counts[provider] = counts.get(provider, 0) + units
        elif kind == 'tts':
            bucket['tts_characters'] += units
        elif kind == 'fetch':
            bucket['fetches'] += units
        else:
            raise UsageError('unknown usage category')
        bucket['estimated_cost_usd'] = _money(_decimal(bucket['estimated_cost_usd'], 'ledger total') + cost)
        if model:
            model_bucket = bucket['by_model'].setdefault(model, {'text_calls': 0, 'tts_characters': 0,
                                                                  'prompt_tokens': 0,
                                                                  'candidates_tokens': 0,
                                                                  'thoughts_tokens': 0})
            if kind == 'text_call':
                model_bucket['text_calls'] += 1
            elif kind == 'tts':
                model_bucket['tts_characters'] += units

    @staticmethod
    def _batch_id(value):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', value):
            raise UsageError('usage batch id is invalid')
        return value

    @staticmethod
    def _batch_holds(state, periods, *, monthly=False):
        total = Decimal(0)
        batches = state.get('batch_reservations', {})
        for batch in batches.values():
            if not isinstance(batch, dict) or batch.get('status') != 'open':
                continue
            period_matches = (batch.get('month') == periods[1] if monthly else
                              batch.get('day') == periods[0] and batch.get('month') == periods[1])
            if period_matches:
                total += _decimal(batch.get('general_remaining_usd'), 'general batch reservation')
                total += _decimal(batch.get('daily_remaining_usd'), 'daily batch reservation')
        return total

    @staticmethod
    def _take_batch_allowance(state, batch_id, caller, amount, periods):
        if not batch_id:
            return None
        batch = state.get('batch_reservations', {}).get(batch_id)
        if (not isinstance(batch, dict) or batch.get('status') != 'open' or
                batch.get('day') != periods[0] or batch.get('month') != periods[1]):
            raise UsageCapReached('batch')
        field = 'daily_remaining_usd' if caller == 'daily' else 'general_remaining_usd'
        remaining = _decimal(batch.get(field), 'batch allowance')
        if amount > remaining:
            raise UsageCapReached('batch')
        batch[field] = _money(remaining - amount)
        return batch

    def reserve_batch_budget(self, batch_id, *, general_usd, daily_usd='0', now=None):
        """Persist an idempotent worst-case envelope before starting a multi-build batch.

        ``general_usd`` must cover every provider attempt charged to non-daily
        callers; ``daily_usd`` must cover every attempt charged to the protected
        daily caller. Values are hard ceilings derived from enforced request
        limits, never expected or average costs. Individual call paths must run
        inside ``usage_batch(batch_id)`` so each normal ledger reservation moves
        its ceiling from this hold before transport.
        """
        batch_id = self._batch_id(batch_id)
        general = _decimal(general_usd, 'general batch ceiling')
        daily = _decimal(daily_usd, 'daily batch ceiling')
        if general + daily <= 0:
            raise UsageError('batch reservation must be positive')
        periods = self._periods(now)

        def update(state):
            batches = state.setdefault('batch_reservations', {})
            current = batches.get(batch_id)
            identity = {'day': periods[0], 'month': periods[1],
                        'general_limit_usd': _money(general), 'daily_limit_usd': _money(daily)}
            if current is not None:
                if not isinstance(current, dict) or any(current.get(key) != value
                                                        for key, value in identity.items()):
                    raise UsageError('usage batch identity or ceiling changed')
                return self._batch_summary(current), False

            # The general slice must fit below the protected daily-run reserve;
            # the combined envelope must also fit the absolute daily/monthly caps.
            if general:
                self._cost(state, periods, general, caller='other')
            self._cost(state, periods, general + daily, caller='daily')
            batch = {**identity, 'status': 'open',
                     'general_remaining_usd': _money(general),
                     'daily_remaining_usd': _money(daily)}
            batches[batch_id] = batch
            return self._batch_summary(batch), True

        try:
            return self._transaction(update)
        except UsageCapReached as error:
            if error.period in {'daily', 'monthly'}:
                self._record_cap_denial(periods, error.period)
            raise

    def usage_batch(self, batch_id):
        """Return a context manager that charges this ledger's calls to a batch envelope."""
        return usage_batch(batch_id)

    @staticmethod
    def _batch_summary(batch):
        result = {'status': batch['status'], 'day': batch['day'], 'month': batch['month'],
                  'general_ceiling_usd': batch['general_limit_usd'],
                  'daily_ceiling_usd': batch['daily_limit_usd'],
                  'general_remaining_usd': batch['general_remaining_usd'],
                  'daily_remaining_usd': batch['daily_remaining_usd']}
        if batch.get('status') == 'sealed':
            result.update({
                'sealed_call_count': batch.get('sealed_call_count', 0),
                'sealed_reserved_usd': batch.get('sealed_reserved_usd', '0.00000000'),
                'sealed_input_token_ceiling': batch.get('sealed_input_token_ceiling', 0),
                'sealed_output_token_ceiling': batch.get('sealed_output_token_ceiling', 0),
                'usage_basis': 'reserved_ceiling',
            })
        return result

    def batch_budget(self, batch_id):
        """Read the count-only persisted batch envelope, or return None if unknown."""
        batch_id = self._batch_id(batch_id)

        def read(state):
            batch = state.get('batch_reservations', {}).get(batch_id)
            return self._batch_summary(batch) if isinstance(batch, dict) else None, False
        return self._transaction(read)

    def close_batch_budget(self, batch_id):
        """Release unused headroom after all calls charged to this batch are settled."""
        batch_id = self._batch_id(batch_id)

        def update(state):
            batch = state.get('batch_reservations', {}).get(batch_id)
            if not isinstance(batch, dict):
                raise UsageError('usage batch is unavailable')
            if batch.get('status') in {'closed', 'sealed'}:
                return self._batch_summary(batch), False
            if any(row.get('batch_id') == batch_id for row in state['pending'].values()):
                raise UsageError('usage batch has unsettled calls')
            batch['status'] = 'closed'
            batch['general_remaining_usd'] = '0.00000000'
            batch['daily_remaining_usd'] = '0.00000000'
            return self._batch_summary(batch), True
        return self._transaction(update)

    def finalize_batch_budget(self, batch_id):
        """Close a batch, conservatively sealing ambiguous calls at reservation ceilings.

        Confirmed calls are already settled normally. If provider usage is
        incomplete, its full reservation has already been charged to the
        day/month buckets. Move that reservation from pending into count-only
        sealed metadata without inventing token counts, then release only the
        batch headroom that no call consumed. This operation is atomic and
        idempotent; a sealed batch can never be re-entered as trusted usage.
        """
        batch_id = self._batch_id(batch_id)

        def update(state):
            batch = state.get('batch_reservations', {}).get(batch_id)
            if not isinstance(batch, dict):
                raise UsageError('usage batch is unavailable')
            if batch.get('status') in {'closed', 'sealed'}:
                return self._batch_summary(batch), False
            rows = [(reservation_id, row) for reservation_id, row in state['pending'].items()
                    if isinstance(row, dict) and row.get('batch_id') == batch_id]
            if not rows:
                batch['status'] = 'closed'
                batch['general_remaining_usd'] = '0.00000000'
                batch['daily_remaining_usd'] = '0.00000000'
                return self._batch_summary(batch), True

            sealed_cost = Decimal(0)
            input_ceiling = 0
            output_ceiling = 0
            for reservation_id, pending in rows:
                reserved = _decimal(pending.get('reserved_cost_usd'), 'reservation')
                sealed_cost += reserved
                input_ceiling += max(0, int(pending.get('token_input_limit') or 0))
                output_ceiling += max(0, int(pending.get('token_output_limit') or 0))
                pool_id = pending.get('token_pool_id')
                if pool_id:
                    pool = state.get('token_pools', {}).get(pool_id)
                    if not isinstance(pool, dict):
                        raise UsageError('token reservation pool is unavailable')
                    pool['reserved_usd'] = _money(max(
                        Decimal(0), _decimal(pool.get('reserved_usd'), 'token pool reservations') - reserved))
                    pool['reserved_output_tokens'] = max(
                        0, int(pool.get('reserved_output_tokens') or 0) -
                        int(pending.get('token_output_limit') or 0))
                    pool['sealed_usd'] = _money(
                        _decimal(pool.get('sealed_usd', '0'), 'sealed token pool usage') + reserved)
                    pool['sealed_output_token_ceiling'] = (
                        int(pool.get('sealed_output_token_ceiling') or 0) +
                        int(pending.get('token_output_limit') or 0))
                    pool['sealed_call_count'] = int(pool.get('sealed_call_count') or 0) + 1
                del state['pending'][reservation_id]

            batch.update({
                'status': 'sealed',
                'general_remaining_usd': '0.00000000',
                'daily_remaining_usd': '0.00000000',
                'sealed_call_count': len(rows),
                'sealed_reserved_usd': _money(sealed_cost),
                'sealed_input_token_ceiling': input_ceiling,
                'sealed_output_token_ceiling': output_ceiling,
            })
            return self._batch_summary(batch), True
        return self._transaction(update)

    def _cost(self, state, periods, cost, caller='other'):
        total = _decimal(cost, 'estimated cost')
        day = self._bucket(state['days'], periods[0])
        month = self._bucket(state['months'], periods[1])
        daily_total = _decimal(day['estimated_cost_usd'], 'daily total')
        monthly_total = _decimal(month['estimated_cost_usd'], 'monthly total')
        available_daily = self.config['_daily_cap']
        if caller != 'daily':
            available_daily -= self.config['_daily_reserve']
        if daily_total + total > available_daily:
            raise UsageCapReached('daily')
        if monthly_total + total > self.config['_monthly_cap']:
            raise UsageCapReached('monthly')

    def _record(self, kind, *, provider='', model='', units=1, cost, now=None, reservation=False,
                caller=None, token_pool=None, token_limits=None, token_counts=None):
        if type(units) is not int or units < 0:
            raise UsageError('usage units must be a nonnegative integer')
        amount = _decimal(cost, 'estimated cost')
        job = _JOB.get()
        if job and job[1]():
            raise UsageCancelled('cancelled')
        periods = self._periods(now)
        caller = caller or _CALLER.get()
        reservation_id = uuid4().hex if reservation else None
        batch_id = _BATCH.get()
        def update(state):
            pool = None
            if token_pool is not None:
                pools = state.setdefault('token_pools', {})
                pool = pools.get(token_pool['id'])
                expected = {'limit_usd': _money(token_pool['limit_usd']),
                            'output_token_limit': token_pool['output_token_limit'],
                            'day': periods[0], 'month': periods[1]}
                if pool is None:
                    pool = {**expected, 'reserved_usd': '0.00000000',
                            'settled_usd': '0.00000000',
                            'reserved_output_tokens': 0, 'settled_output_tokens': 0}
                    pools[token_pool['id']] = pool
                elif any(pool.get(key) != value for key, value in expected.items()):
                    raise UsageError('token reservation pool identity or limit changed')
                used = (_decimal(pool['reserved_usd'], 'token pool reservations') +
                        _decimal(pool['settled_usd'], 'token pool settled usage'))
                if used + amount > _decimal(pool['limit_usd'], 'token pool limit'):
                    raise UsageCapReached('batch')
                output_tokens = token_limits['output_tokens']
                if (pool['output_token_limit'] is not None and
                        pool['reserved_output_tokens'] + pool['settled_output_tokens'] +
                        output_tokens > pool['output_token_limit']):
                    raise UsageCapReached('batch_tokens')
            self._cost(state, periods, amount, caller)
            for key, period in (('days', periods[0]), ('months', periods[1])):
                bucket = self._bucket(state[key], period)
                self._increment(bucket, kind, provider, model, units, amount)
                for name, value in (token_counts or {}).items():
                    bucket[name] = bucket.get(name, 0) + max(0, int(value))
                    if model:
                        model_bucket = bucket['by_model'].setdefault(
                            model, {'text_calls': 0, 'tts_characters': 0, 'prompt_tokens': 0,
                                    'candidates_tokens': 0, 'thoughts_tokens': 0})
                        model_bucket[name] = model_bucket.get(name, 0) + max(0, int(value))
            if job:
                bucket = self._bucket(state.setdefault('jobs', {}), job[0])
                self._increment(bucket, kind, provider, model, units, amount)
            if reservation_id:
                state['pending'][reservation_id] = {
                    'day': periods[0], 'month': periods[1], 'kind': kind,
                    'provider': provider, 'model': model, 'units': units,
                    'reserved_cost_usd': _money(amount), 'job_id': job[0] if job else None,
                    'caller': caller,
                }
                if batch_id:
                    state['pending'][reservation_id]['batch_id'] = batch_id
                if pool is not None:
                    pool['reserved_usd'] = _money(
                        _decimal(pool['reserved_usd'], 'token pool reservations') + amount)
                    pool['reserved_output_tokens'] += token_limits['output_tokens']
                    state['pending'][reservation_id].update({
                        'token_pool_id': token_pool['id'],
                        'token_input_limit': token_limits['input_tokens'],
                        'token_output_limit': token_limits['output_tokens'],
                        'token_modality': token_limits['modality'],
                    })
            return reservation_id, True

        try:
            return self._transaction(update)
        except UsageCapReached as error:
            if error.period in {'daily', 'monthly'}:
                self._record_cap_denial(periods, error.period)
            raise

    def _record_cap_denial(self, periods, period_name):
        def update(state):
            index = 0 if period_name == 'daily' else 1
            bucket = state['cap_denials']['days' if index == 0 else 'months']
            key = periods[index]
            bucket[key] = bucket.get(key, 0) + 1
            return None, True
        self._transaction(update)

    def reserve_text_call(self, system_chars, input_chars, max_output_tokens, *, model, now=None,
                          caller=None, input_token_limit=None):
        input_tokens = (max(0, int(system_chars) + int(input_chars)) +
                        self.config['_text_chars_per_token'] - 1) // self.config['_text_chars_per_token']
        output_tokens = max(0, int(max_output_tokens))
        if os.environ.get('BRIEFING_REQUIRE_USAGE_BATCH') == '1':
            # Character-to-token ratios are estimates, not enforceable ceilings.
            # Strict release calls supply a conservative byte-length ceiling for
            # the complete serialized request before transport.
            if type(input_token_limit) is not int or input_token_limit < 0:
                raise UsageCapReached('batch')
            input_tokens = input_token_limit
            batch_id = _BATCH.get()
            if not batch_id:
                raise UsageCapReached('batch')
            batch = self.batch_budget(batch_id)
            if not isinstance(batch, dict) or batch.get('status') != 'open':
                raise UsageCapReached('batch')
            active_caller = caller or _CALLER.get()
            limit_key = 'daily_ceiling_usd' if active_caller == 'daily' else 'general_ceiling_usd'
            pool_id = 'text-' + hashlib.sha256(
                f'{batch_id}:{active_caller}'.encode('utf-8')).hexdigest()[:32]
            return self.reserve_token_call(
                input_tokens, output_tokens, model=model, pool_id=pool_id,
                pool_limit_usd=batch[limit_key], modality='text', now=now,
                caller=active_caller)
        input_rate, output_rate = self._text_rates(model)
        cost = ((Decimal(input_tokens) * input_rate + Decimal(output_tokens) * output_rate) /
                Decimal(1_000_000))
        return self._record('text_call', model=model, cost=cost, now=now, reservation=True, caller=caller)

    @staticmethod
    def _token_limit(value, name):
        if type(value) is not int or value < 0:
            raise UsageError(f'{name} must be a nonnegative integer')
        return value

    @staticmethod
    def _token_pool_id(value):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', value):
            raise UsageError('token reservation pool id is invalid')
        return value

    def estimate_token_call_cost(self, input_tokens, output_tokens, *, model, modality='text'):
        """Price explicit token ceilings without reserving them."""
        input_tokens = self._token_limit(input_tokens, 'input token ceiling')
        output_tokens = self._token_limit(output_tokens, 'output token ceiling')
        if modality == 'text':
            input_rate, output_rate = self._text_rates(model)
        elif modality == 'tts':
            input_rate = self.config['_tts_token_input_rate']
            output_rate = self.config['_tts_token_output_rate']
            if input_rate is None or output_rate is None:
                raise UsageError('Gemini TTS token rates are unavailable')
        else:
            raise UsageError('token modality must be text or tts')
        return ((Decimal(input_tokens) * input_rate + Decimal(output_tokens) * output_rate) /
                Decimal(1_000_000))

    def reserve_token_call(self, input_tokens, output_tokens, *, model, pool_id, pool_limit_usd,
                           pool_output_token_limit=None, modality='text', now=None, caller=None):
        """Atomically reserve input/output token maxima against ledger and shared batch pool.

        Call this before transport. Every retry must reserve its own ceilings in the
        same pool. If usage is ambiguous, leave the reservation pending; only a
        confirmed token report should be settled to release unused headroom.
        """
        input_tokens = self._token_limit(input_tokens, 'input token ceiling')
        output_tokens = self._token_limit(output_tokens, 'output token ceiling')
        pool_id = self._token_pool_id(pool_id)
        pool_limit = _decimal(pool_limit_usd, 'token reservation pool limit')
        if (pool_output_token_limit is not None and
                (type(pool_output_token_limit) is not int or pool_output_token_limit < 0)):
            raise UsageError('token pool output limit must be a nonnegative integer')
        if modality not in {'text', 'tts'}:
            raise UsageError('token modality must be text or tts')
        if not isinstance(model, str) or not model:
            raise UsageError('token reservation model is invalid')
        cost = self.estimate_token_call_cost(input_tokens, output_tokens, model=model, modality=modality)
        kind = 'text_call' if modality == 'text' else 'tts'
        return self._record(
            kind, model=model, units=0, cost=cost, now=now, reservation=True, caller=caller,
            token_pool={'id': pool_id, 'limit_usd': pool_limit,
                        'output_token_limit': pool_output_token_limit},
            token_limits={'input_tokens': input_tokens, 'output_tokens': output_tokens,
                          'modality': modality})

    def reserve_tts_token_call(self, input_token_limit, output_token_limit, model, *, batch_id,
                               batch_output_token_limit=4096, batch_limit_usd, now=None, caller=None):
        """Reserve one TTS attempt's full token ceilings in its shared batch pool."""
        return self.reserve_token_call(
            input_token_limit, output_token_limit, model=model, pool_id=batch_id,
            pool_limit_usd=batch_limit_usd,
            pool_output_token_limit=batch_output_token_limit, modality='tts', now=now, caller=caller)

    def settle_token_call(self, reservation_id, input_tokens, output_tokens, *, model, modality,
                          prompt_tokens=None, candidates_tokens=None, thoughts_tokens=None):
        """Settle a token-ceiling reservation only when both actual counts are known."""
        input_tokens = self._token_limit(input_tokens, 'actual input tokens')
        output_tokens = self._token_limit(output_tokens, 'actual output tokens')
        if modality not in {'text', 'tts'} or not isinstance(model, str) or not model:
            raise UsageError('token settlement details are invalid')
        breakdown = (prompt_tokens, candidates_tokens, thoughts_tokens)
        if any(value is not None for value in breakdown):
            if modality != 'text' or any(type(value) is not int or value < 0
                                         for value in breakdown):
                raise UsageError('text token settlement breakdown is invalid')
            if prompt_tokens != input_tokens or candidates_tokens + thoughts_tokens != output_tokens:
                raise UsageError('text token settlement breakdown does not match totals')
            recorded_counts = {
                'prompt_tokens': prompt_tokens, 'candidates_tokens': candidates_tokens,
                'thoughts_tokens': thoughts_tokens, 'model': model,
            }
        else:
            recorded_counts = {'input_tokens': input_tokens, 'output_tokens': output_tokens,
                               'model': model}
        cost = self.estimate_token_call_cost(input_tokens, output_tokens, model=model, modality=modality)
        self._settle(reservation_id, cost,
                     token_counts=recorded_counts, enforce_caps=True,
                     token_details={'model': model, 'modality': modality,
                                    'input_tokens': input_tokens, 'output_tokens': output_tokens},
                     usage_confirmed=True)

    def settle_tts_token_call(self, reservation_id, input_tokens, output_tokens, model):
        """Settle confirmed TTS token usage; leave ambiguous attempts pending."""
        return self.settle_token_call(reservation_id, input_tokens, output_tokens,
                                      model=model, modality='tts')

    def settle_text_call(self, reservation_id, system_chars=0, input_chars=0, output_chars=0, *, model,
                         prompt_tokens=None, candidates_tokens=None, thoughts_tokens=None):
        chars_per_token = self.config['_text_chars_per_token']
        has_actual = prompt_tokens is not None or candidates_tokens is not None or thoughts_tokens is not None
        if has_actual:
            input_tokens = max(0, int(prompt_tokens or 0))
            output_tokens = max(0, int(candidates_tokens or 0))
            thinking_tokens = max(0, int(thoughts_tokens or 0))
            billed_output = output_tokens + thinking_tokens
        else:
            input_tokens = (max(0, int(system_chars) + int(input_chars)) + chars_per_token - 1) // chars_per_token
            output_tokens = (max(0, int(output_chars)) + chars_per_token - 1) // chars_per_token
            thinking_tokens = 0
            billed_output = output_tokens
        input_rate, output_rate = self._text_rates(model)
        cost = ((Decimal(input_tokens) * input_rate + Decimal(billed_output) * output_rate) /
                Decimal(1_000_000))
        usage_confirmed = (prompt_tokens is not None and candidates_tokens is not None and
                           thoughts_tokens is not None)
        self._settle(reservation_id, cost, token_counts={
            'prompt_tokens': input_tokens, 'candidates_tokens': output_tokens,
            'thoughts_tokens': thinking_tokens, 'model': model}, usage_confirmed=usage_confirmed)

    def _text_rates(self, model):
        candidates = sorted(self.config['_text_rates_by_model'], key=len, reverse=True)
        for prefix in candidates:
            if str(model).startswith(prefix):
                return self.config['_text_rates_by_model'][prefix]
        return self.config['_text_input_rate'], self.config['_text_output_rate']

    def reserve_tts_call(self, characters, *, model, now=None, caller=None):
        chars = max(0, int(characters))
        seconds = Decimal(chars) / Decimal(self.config['_tts_chars_per_second'])
        cost = (Decimal(chars) * self.config['_tts_char_rate'] / Decimal(1_000_000) +
                seconds * self.config['_tts_minute_rate'] / Decimal(60))
        return self._record('tts', model=model, units=chars, cost=cost, now=now, reservation=True, caller=caller)

    def settle_tts_call(self, reservation_id, characters, duration_seconds):
        chars = max(0, int(characters))
        seconds = max(0, Decimal(str(duration_seconds)))
        cost = (Decimal(chars) * self.config['_tts_char_rate'] / Decimal(1_000_000) +
                seconds * self.config['_tts_minute_rate'] / Decimal(60))
        self._settle(reservation_id, cost, enforce_caps=True, usage_confirmed=True)

    def _settle(self, reservation_id, actual_cost, token_counts=None, *, enforce_caps=False,
                token_details=None, usage_confirmed=False):
        amount = _decimal(actual_cost, 'settled estimated cost')

        def update(state):
            pending = state['pending'].get(reservation_id)
            if pending is None:
                if token_details:
                    raise UsageError('token reservation is unavailable')
                return None, False
            if pending.get('batch_id') and not usage_confirmed:
                # Missing or partial provider usage leaves the full batch attempt
                # pending and reserved; estimates cannot release hard headroom.
                return None, False
            if token_details and (pending.get('model') != token_details['model'] or
                                  pending.get('token_modality') != token_details['modality']):
                raise UsageError('token settlement does not match its reservation')
            reserved = _decimal(pending['reserved_cost_usd'], 'reservation')
            delta = amount - reserved
            buckets = [self._bucket(state[key], period) for key, period in
                       (('days', pending['day']), ('months', pending['month']))]
            overrun = None
            if (token_details and
                    (token_details.get('input_tokens', 0) > pending.get('token_input_limit', -1) or
                     token_details.get('output_tokens', 0) > pending.get('token_output_limit', -1))):
                overrun = 'token_ceiling'
            if enforce_caps:
                projected_daily = _decimal(buckets[0]['estimated_cost_usd'], 'daily total') + delta
                projected_monthly = _decimal(buckets[1]['estimated_cost_usd'], 'monthly total') + delta
                available_daily = self.config['_daily_cap']
                if pending.get('caller', 'other') != 'daily':
                    available_daily -= self.config['_daily_reserve']
                if projected_daily > available_daily:
                    overrun = overrun or 'daily'
                elif projected_monthly > self.config['_monthly_cap']:
                    overrun = overrun or 'monthly'
            batch_id = pending.get('batch_id')
            pool_id = pending.get('token_pool_id')
            if pool_id:
                pool = state.get('token_pools', {}).get(pool_id)
                if not isinstance(pool, dict):
                    raise UsageError('token reservation pool is unavailable')
                pool_reserved = _decimal(pool.get('reserved_usd'), 'token pool reservations')
                pool_settled = _decimal(pool.get('settled_usd'), 'token pool settled usage')
                next_pool_reserved = max(Decimal(0), pool_reserved - reserved)
                next_reserved_output = max(0, pool['reserved_output_tokens'] -
                                           pending['token_output_limit'])
                if next_pool_reserved + pool_settled + amount > _decimal(pool.get('limit_usd'),
                                                                        'token pool limit'):
                    overrun = overrun or 'batch'
                actual_output = token_details.get('output_tokens', 0) if token_details else 0
                output_limit = pool.get('output_token_limit')
                if (output_limit is not None and
                        next_reserved_output + pool['settled_output_tokens'] + actual_output > output_limit):
                    overrun = overrun or 'batch_tokens'
                pool['reserved_usd'] = _money(next_pool_reserved)
                pool['settled_usd'] = _money(pool_settled + amount)
                pool['reserved_output_tokens'] = next_reserved_output
                pool['settled_output_tokens'] += actual_output
            if pending.get('job_id'):
                buckets.append(self._bucket(state.setdefault('jobs', {}), pending['job_id']))
            if batch_id and amount != reserved:
                batch = state.get('batch_reservations', {}).get(batch_id)
                if not isinstance(batch, dict) or batch.get('status') != 'open':
                    raise UsageError('usage batch is unavailable for settlement')
                field = ('daily_remaining_usd' if pending.get('caller') == 'daily'
                         else 'general_remaining_usd')
                remaining = _decimal(batch[field], 'batch allowance')
                if amount < reserved:
                    batch[field] = _money(remaining + reserved - amount)
                else:
                    confirmed_overage = amount - reserved
                    if confirmed_overage > remaining:
                        batch[field] = '0.00000000'
                        overrun = overrun or 'batch'
                    else:
                        batch[field] = _money(remaining - confirmed_overage)
            for bucket in buckets:
                bucket['estimated_cost_usd'] = _money(
                    max(Decimal(0), _decimal(bucket['estimated_cost_usd'], 'period total') + delta))
                if token_counts:
                    model = token_counts.get('model')
                    for name, token_value in token_counts.items():
                        if name == 'model':
                            continue
                        bucket[name] = bucket.get(name, 0) + token_value
                        if isinstance(model, str) and model:
                            model_bucket = bucket['by_model'].setdefault(
                                model, {'text_calls': 0, 'tts_characters': 0, 'prompt_tokens': 0,
                                        'candidates_tokens': 0, 'thoughts_tokens': 0})
                            model_bucket[name] = model_bucket.get(name, 0) + token_value
            del state['pending'][reservation_id]
            return overrun, True

        overrun = self._transaction(update)
        if overrun:
            # The provider has already returned the WAV. Keep measured usage in
            # the ledger, but prevent the caller from publishing over-cap audio.
            raise UsageCapReached(overrun)

    def preflight_episode(self, estimate, now=None):
        """Refuse only when the recorded daily/monthly total is already capped."""
        periods = self._periods(now)
        def read(state):
            day = self._bucket(state['days'], periods[0])
            month = self._bucket(state['months'], periods[1])
            if _decimal(day['estimated_cost_usd'], 'daily total') >= self.config['_daily_cap'] - self.config['_daily_reserve']:
                raise UsageCapReached('daily')
            if _decimal(month['estimated_cost_usd'], 'monthly total') >= self.config['_monthly_cap']:
                raise UsageCapReached('monthly')
            return None, False
        return self._transaction(read)

    def record_text_call(self, prompt_tokens, candidates_tokens, thoughts_tokens=0, *, model,
                         caller=None, now=None):
        """Record measured provider tokens after a completed text call."""
        prompt_tokens = max(0, int(prompt_tokens or 0))
        candidates_tokens = max(0, int(candidates_tokens or 0))
        thoughts_tokens = max(0, int(thoughts_tokens or 0))
        input_rate, output_rate = self._text_rates(model)
        cost = ((Decimal(prompt_tokens) * input_rate +
                 Decimal(candidates_tokens + thoughts_tokens) * output_rate) / Decimal(1_000_000))
        return self._record('text_call', model=model, cost=cost, now=now, caller=caller,
                            token_counts={'prompt_tokens': prompt_tokens,
                                          'candidates_tokens': candidates_tokens,
                                          'thoughts_tokens': thoughts_tokens})

    def record_text_estimate(self, system_chars, input_chars, output_tokens, *, model,
                             caller=None, now=None):
        """Record a bounded estimate when a provider omits usage metadata."""
        chars_per_token = self.config['_text_chars_per_token']
        input_tokens = (max(0, int(system_chars) + int(input_chars)) + chars_per_token - 1) // chars_per_token
        input_rate, output_rate = self._text_rates(model)
        cost = ((Decimal(input_tokens) * input_rate + Decimal(max(0, int(output_tokens))) * output_rate) /
                Decimal(1_000_000))
        return self._record('text_call', model=model, cost=cost, now=now, caller=caller,
                            token_counts={'prompt_tokens': input_tokens})

    def episode_record(self, job_id):
        def read(state):
            bucket = deepcopy(state.get('jobs', {}).get(job_id, _new_bucket()))
            bucket['pending_calls'] = sum(row.get('job_id') == job_id for row in state['pending'].values())
            bucket['basis'] = 'Ledger: actual text tokens, priced searches and estimated TTS from measured seconds; pending calls retain reservations.'
            return bucket, False
        return self._transaction(read)

    def record_search(self, provider, *, now=None, caller=None):
        job = _JOB.get()
        if job and job[1]():
            raise UsageCancelled('cancelled')
        cost = self.config['_search_prices'].get(provider)
        if cost is None:
            raise UsageError('no configured search price for provider')
        if provider == 'gemini':
            # The monthly query count and its price must be read and updated in
            # one ledger transaction, so concurrent workers cannot overspend
            # the free allowance.
            periods = self._periods(now)
            caller = caller or _CALLER.get()

            def update(state):
                month = self._bucket(state['months'], periods[1])
                prior_queries = month['search_queries'].get(provider, 0)
                if type(prior_queries) is not int or prior_queries < 0:
                    raise UsageError('usage ledger search count is invalid')
                price = (Decimal(0) if prior_queries < self.config['_grounded_search_free_allowance']
                         else self.config['_grounded_search_overage_price'])
                batch_id = _BATCH.get()
                if (price > 0 and os.environ.get('BRIEFING_REQUIRE_USAGE_BATCH') == '1' and
                        not batch_id):
                    # Grounded overage uses this atomic counter path rather
                    # than _record; it must still require its reserved batch.
                    raise UsageCapReached('batch')
                if batch_id:
                    self._take_batch_allowance(state, batch_id, caller, price, periods)
                self._cost(state, periods, price, caller)
                for key, period in (('days', periods[0]), ('months', periods[1])):
                    self._increment(self._bucket(state[key], period), 'search_query', provider, '', 1, price)
                return None, True

            try:
                self._transaction(update)
            except UsageCapReached as error:
                if error.period in {'daily', 'monthly'}:
                    self._record_cap_denial(periods, error.period)
                raise
            return
        self._record('search_query', provider=provider, cost=cost, now=now, caller=caller)

    def record_fetch(self, *, now=None):
        self._record('fetch', cost=self.config['_fetch_price'], now=now)

    def record_tts_usage(self, characters, duration_seconds, *, model, now=None, caller=None):
        """Record measured TTS characters and duration after synthesis."""
        chars = max(0, int(characters))
        seconds = max(Decimal(0), Decimal(str(duration_seconds)))
        cost = (Decimal(chars) * self.config['_tts_char_rate'] / Decimal(1_000_000) +
                seconds * self.config['_tts_minute_rate'] / Decimal(60))
        return self._record('tts', model=model, units=chars, cost=cost, now=now, caller=caller,
                            token_counts={})

    def _summary_bucket(self, bucket):
        return {**bucket, 'estimated_cost_usd': _money(_decimal(bucket['estimated_cost_usd'], 'summary total'))}

    def summary(self, now=None):
        day_key, month_key = self._periods(now)

        def read(state):
            day = self._summary_bucket(self._bucket(state['days'], day_key))
            month = self._summary_bucket(self._bucket(state['months'], month_key))
            day_cap = self.config['_daily_cap']
            month_cap = self.config['_monthly_cap']
            daily_holds = self._batch_holds(state, (day_key, month_key))
            monthly_holds = self._batch_holds(state, (day_key, month_key), monthly=True)
            day['cap_usd'] = _money(day_cap)
            month['cap_usd'] = _money(month_cap)
            day['batch_reserved_usd'] = _money(daily_holds)
            month['batch_reserved_usd'] = _money(monthly_holds)
            day['remaining_usd'] = _money(max(
                Decimal(0), day_cap - _decimal(day['estimated_cost_usd'], 'daily total') - daily_holds))
            month['remaining_usd'] = _money(max(
                Decimal(0), month_cap - _decimal(month['estimated_cost_usd'], 'monthly total') - monthly_holds))
            day['available_to_other_callers_usd'] = _money(max(Decimal(0), day_cap - self.config['_daily_reserve'] -
                                                    _decimal(day['estimated_cost_usd'], 'daily total') - daily_holds))
            day['reserved_for_daily_run_usd'] = _money(self.config['_daily_reserve'])
            denials = {
                'daily': state['cap_denials']['days'].get(day_key, 0),
                'monthly': state['cap_denials']['months'].get(month_key, 0),
            }
            reached = ('daily' if denials['daily'] or
                       _decimal(day['estimated_cost_usd'], 'daily summary') + daily_holds >= day_cap else
                       'monthly' if denials['monthly'] or
                       _decimal(month['estimated_cost_usd'], 'monthly summary') + monthly_holds >= month_cap else None)
            return {'ok': True, 'date': day_key, 'month': month_key, 'daily': day,
                    'monthly': month, 'cap_status': f'{reached}_cap_reached' if reached else 'within_caps',
                    'cap_denials': denials}, False

        return self._transaction(read)

    def daily_audio_budget_remaining(self, now=None):
        """Remaining TTS budget inside the portion protected for the daily run."""
        day_key, _month_key = self._periods(now)

        def read(state):
            bucket = self._bucket(state['days'], day_key)
            chars = max(0, int(bucket.get('tts_characters', 0)))
            seconds = Decimal(chars) / Decimal(self.config['_tts_chars_per_second'])
            spent = self.estimate_tts_cost(chars, seconds)
            daily_holds = sum(
                _decimal(batch.get('daily_remaining_usd'), 'daily batch reservation')
                for batch in state.get('batch_reservations', {}).values()
                if isinstance(batch, dict) and batch.get('status') == 'open' and batch.get('day') == day_key)
            return max(Decimal(0), self.config['_daily_reserve'] - spent - daily_holds), False

        return self._transaction(read)

    def estimate_tts_cost(self, characters, duration_seconds):
        chars = max(0, int(characters))
        seconds = max(Decimal(0), Decimal(str(duration_seconds)))
        return (Decimal(chars) * self.config['_tts_char_rate'] / Decimal(1_000_000) +
                seconds * self.config['_tts_minute_rate'] / Decimal(60))


def process_ledger(factory=UsageLedger):
    """Honor an internal launch reference, otherwise retain normal ledger selection."""
    ref = _RELEASE_USAGE.get()
    if ref is not None:
        return _release_ledger(ref, factory)
    if os.environ.get('BRIEFING_TESTING') == '1':
        target = os.environ.get('BRIEFING_TEST_LEDGER_DIR')
        if not target:
            raise UsageError('test ledger path required')
        return factory(ledger_dir=target)
    return factory()
