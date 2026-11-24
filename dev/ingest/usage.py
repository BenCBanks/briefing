"""Count-only provider usage and cost ledger.

Check recorded totals before a provider call, then add its measured usage after
it returns. The ledger stores no content, queries, URLs, or credentials.
"""
from __future__ import annotations

from contextvars import ContextVar
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import RLock
from uuid import uuid4
from zoneinfo import ZoneInfo
import fcntl
import json
import os

EASTERN = ZoneInfo('America/New_York')
PRICE_CONFIG = Path(__file__).with_name('usage-prices.json')
TEST_PRICE_CONFIG = Path(__file__).resolve().parents[1] / 'tests/fixtures/usage-prices.json'
GLOBAL_LEDGER_DIR = Path.home() / '.local/state/briefing-usage'
_PROCESS_LOCK = RLock()
_CALLER = ContextVar('briefing_usage_caller', default='other')
_JOB = ContextVar('briefing_usage_job', default=None)


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


@contextmanager
def episode_usage(job_id, cancelled):
    token = _JOB.set((job_id, cancelled))
    try:
        yield
    finally:
        _JOB.reset(token)


def usage_caller(name):
    if name not in {'other', 'daily'}:
        raise ValueError('invalid usage caller')
    return _CALLER.set(name)


def reset_usage_caller(token):
    _CALLER.reset(token)


class UsageLedger:
    """Persist actual usage, with daily-run reserve and daily/monthly limits."""

    def __init__(self, state_dir=None, config_path=PRICE_CONFIG, *, ledger_dir=None):
        if os.environ.get('BRIEFING_TESTING') == '1' and ledger_dir is None:
            ledger_dir = os.environ.get('BRIEFING_TEST_LEDGER_DIR')
            if not ledger_dir:
                raise UsageError('test ledger path required')
        if os.environ.get('BRIEFING_TESTING') == '1' and Path(config_path) == PRICE_CONFIG:
            config_path = TEST_PRICE_CONFIG
        self.state_dir = Path(ledger_dir).expanduser() if ledger_dir is not None else GLOBAL_LEDGER_DIR
        self.path = self.state_dir / 'ledger.json'
        self.lock_path = self.state_dir / 'ledger.lock'
        self.config = self._load_config(Path(config_path))

    @staticmethod
    def _load_config(path):
        try:
            config = json.loads(path.read_text(encoding='utf-8'))
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
        tts, search = prices.get('tts'), prices.get('search_usd_per_query')
        grounded = prices.get('gemini_grounded_search')
        by_model = prices.get('gemini_text_by_model', {})
        if not all(isinstance(value, dict) for value in (text, tts, search, grounded, by_model)):
            raise UsageError('usage price configuration is incomplete')
        free = grounded.get('free_allowance')
        if type(free) is not int or free < 0:
            raise UsageError('invalid Gemini grounded-search free allowance')
        token_tts = prices.get('gemini_tts_lite') or {}
        config.update({
            '_daily_cap': daily, '_monthly_cap': monthly, '_daily_reserve': reserve,
            '_text_input_rate': _decimal(text.get('input_usd_per_million_tokens'), 'Gemini text input'),
            '_text_output_rate': _decimal(text.get('output_usd_per_million_tokens'), 'Gemini text output'),
            '_text_rates_by_model': {
                str(prefix): (_decimal(rates.get('input_usd_per_million_tokens'), f'{prefix} input'),
                              _decimal(rates.get('output_usd_per_million_tokens'), f'{prefix} output'))
                for prefix, rates in by_model.items()},
            '_text_chars_per_token': UsageLedger._positive_int(
                prices.get('characters_per_token', text.get('characters_per_token')), 'characters per token'),
            '_search_prices': {str(provider): _decimal(price, f'{provider} search')
                               for provider, price in search.items()},
            '_grounded_search_free_allowance': free,
            '_grounded_search_overage_price': _decimal(
                grounded.get('overage_usd_per_query'), 'grounded search overage'),
            '_tts_token_input_rate': (_decimal(token_tts.get('text_usd_per_million_tokens'), 'TTS text tokens')
                                      if token_tts else None),
            '_tts_token_output_rate': (_decimal(token_tts.get('audio_usd_per_million_tokens'), 'TTS audio tokens')
                                       if token_tts else None),
            '_tts_char_rate': _decimal(tts.get('usd_per_million_characters'), 'TTS characters'),
            '_tts_minute_rate': _decimal(tts.get('usd_per_minute'), 'TTS duration'),
            '_tts_chars_per_second': UsageLedger._positive_int(
                tts.get('estimated_characters_per_second'), 'TTS characters per second'),
            '_fetch_price': _decimal(prices.get('fetch_usd_each'), 'fetch'),
        })
        return config

    @staticmethod
    def _positive_int(value, name):
        if type(value) is not int or value <= 0:
            raise UsageError(f'invalid usage price: {name}')
        return value

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

    def _empty_state(self):
        return {'schema_version': 1, 'days': {}, 'months': {}, 'jobs': {}}

    def _read(self):
        try:
            old = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return self._empty_state()
        except (OSError, ValueError, TypeError):
            raise UsageError('usage ledger is unavailable or invalid') from None
        if (not isinstance(old, dict) or old.get('schema_version') != 1 or
                not isinstance(old.get('days'), dict) or not isinstance(old.get('months'), dict)):
            raise UsageError('usage ledger is unavailable or invalid')
        return {'schema_version': 1, 'days': old['days'], 'months': old['months'],
                'jobs': old.get('jobs', {}) if isinstance(old.get('jobs', {}), dict) else {}}

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
    def _increment(bucket, kind, provider, model, units, cost, token_counts=None):
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
        for name, value in (token_counts or {}).items():
            bucket[name] = bucket.get(name, 0) + value
        if model:
            model_bucket = bucket['by_model'].setdefault(model, {
                'text_calls': 0, 'tts_characters': 0, 'prompt_tokens': 0,
                'candidates_tokens': 0, 'thoughts_tokens': 0})
            if kind == 'text_call':
                model_bucket['text_calls'] += 1
            elif kind == 'tts':
                model_bucket['tts_characters'] += units
            for name, value in (token_counts or {}).items():
                model_bucket[name] = model_bucket.get(name, 0) + value

    def _record(self, kind, *, provider='', model='', units=1, cost, now=None, token_counts=None):
        if type(units) is not int or units < 0:
            raise UsageError('usage units must be a nonnegative integer')
        job = _JOB.get()
        if job and job[1]():
            raise UsageCancelled('cancelled')
        periods = self._periods(now)
        amount = _decimal(cost, 'actual cost')

        def update(state):
            for key, period in (('days', periods[0]), ('months', periods[1])):
                self._increment(self._bucket(state[key], period), kind, provider, model,
                                units, amount, token_counts)
            if job:
                self._increment(self._bucket(state.setdefault('jobs', {}), job[0]), kind,
                                provider, model, units, amount, token_counts)
            return None, True
        return self._transaction(update)

    def _text_rates(self, model):
        for prefix in sorted(self.config['_text_rates_by_model'], key=len, reverse=True):
            if str(model).startswith(prefix):
                return self.config['_text_rates_by_model'][prefix]
        return self.config['_text_input_rate'], self.config['_text_output_rate']

    def preflight_episode(self, now=None):
        """Refuse only when recorded spend has reached a caller's cap."""
        periods = self._periods(now)
        caller = _CALLER.get()

        def read(state):
            day = self._bucket(state['days'], periods[0])
            month = self._bucket(state['months'], periods[1])
            daily_limit = self.config['_daily_cap'] - (0 if caller == 'daily' else self.config['_daily_reserve'])
            if _decimal(day['estimated_cost_usd'], 'daily total') >= daily_limit:
                raise UsageCapReached('daily')
            if _decimal(month['estimated_cost_usd'], 'monthly total') >= self.config['_monthly_cap']:
                raise UsageCapReached('monthly')
            return None, False
        return self._transaction(read)

    def record_text_call(self, prompt_tokens, candidates_tokens, thoughts_tokens=0, *, model,
                         caller=None, now=None):
        prompt_tokens, candidates_tokens, thoughts_tokens = (
            max(0, int(value or 0)) for value in (prompt_tokens, candidates_tokens, thoughts_tokens))
        input_rate, output_rate = self._text_rates(model)
        cost = (Decimal(prompt_tokens) * input_rate +
                Decimal(candidates_tokens + thoughts_tokens) * output_rate) / Decimal(1_000_000)
        return self._record('text_call', model=model, cost=cost, now=now,
                            token_counts={'prompt_tokens': prompt_tokens,
                                          'candidates_tokens': candidates_tokens,
                                          'thoughts_tokens': thoughts_tokens})

    def record_text_estimate(self, system_chars, input_chars, output_tokens, *, model,
                             caller=None, now=None):
        cpt = self.config['_text_chars_per_token']
        input_tokens = (max(0, int(system_chars) + int(input_chars)) + cpt - 1) // cpt
        output_tokens = max(0, int(output_tokens))
        input_rate, output_rate = self._text_rates(model)
        cost = (Decimal(input_tokens) * input_rate + Decimal(output_tokens) * output_rate) / Decimal(1_000_000)
        return self._record('text_call', model=model, cost=cost, now=now,
                            token_counts={'prompt_tokens': input_tokens,
                                          'candidates_tokens': output_tokens})

    def record_tts_tokens(self, characters, prompt_tokens, audio_tokens, *, model, now=None):
        if self.config['_tts_token_input_rate'] is None or self.config['_tts_token_output_rate'] is None:
            raise UsageError('Gemini TTS token rates are unavailable')
        characters = max(0, int(characters))
        prompt_tokens, audio_tokens = max(0, int(prompt_tokens)), max(0, int(audio_tokens))
        cost = (Decimal(prompt_tokens) * self.config['_tts_token_input_rate'] +
                Decimal(audio_tokens) * self.config['_tts_token_output_rate']) / Decimal(1_000_000)
        return self._record('tts', units=characters, model=model, cost=cost, now=now,
                            token_counts={'prompt_tokens': prompt_tokens,
                                          'candidates_tokens': audio_tokens})

    def record_search(self, provider, *, now=None, caller=None):
        job = _JOB.get()
        if job and job[1]():
            raise UsageCancelled('cancelled')
        if provider not in self.config['_search_prices']:
            raise UsageError('no configured search price for provider')
        periods = self._periods(now)

        def update(state):
            month = self._bucket(state['months'], periods[1])
            count = month['search_queries'].get(provider, 0)
            cost = (self.config['_grounded_search_overage_price']
                    if provider == 'gemini' and count >= self.config['_grounded_search_free_allowance']
                    else self.config['_search_prices'][provider])
            for key, period in (('days', periods[0]), ('months', periods[1])):
                self._increment(self._bucket(state[key], period), 'search_query', provider, '', 1, cost)
            if job:
                self._increment(self._bucket(state.setdefault('jobs', {}), job[0]),
                                'search_query', provider, '', 1, cost)
            return None, True
        return self._transaction(update)

    def record_fetch(self, *, now=None):
        return self._record('fetch', cost=self.config['_fetch_price'], now=now)

    def record_tts_usage(self, characters, duration_seconds, *, model, now=None, caller=None):
        chars = max(0, int(characters))
        seconds = max(Decimal(0), Decimal(str(duration_seconds)))
        cost = (Decimal(chars) * self.config['_tts_char_rate'] / Decimal(1_000_000) +
                seconds * self.config['_tts_minute_rate'] / Decimal(60))
        return self._record('tts', units=chars, model=model, cost=cost, now=now)

    def estimate_tts_cost(self, characters, duration_seconds):
        chars = max(0, int(characters))
        seconds = max(Decimal(0), Decimal(str(duration_seconds)))
        return (Decimal(chars) * self.config['_tts_char_rate'] / Decimal(1_000_000) +
                seconds * self.config['_tts_minute_rate'] / Decimal(60))

    def daily_audio_budget_remaining(self, now=None):
        day_key, _ = self._periods(now)
        def read(state):
            bucket = self._bucket(state['days'], day_key)
            spent = _decimal(bucket.get('estimated_cost_usd', '0'), 'daily total')
            # Daily audio is part of the daily caller, which may use the
            # protected reserve. Subtracting prior TTS usage from the reserve
            # double-counted episode audio and could refuse the scheduled daily
            # render even while the ledger still had room under the hard cap.
            return max(Decimal(0), self.config['_daily_cap'] - spent), False
        return self._transaction(read)

    def episode_record(self, job_id):
        def read(state):
            bucket = deepcopy(state.get('jobs', {}).get(job_id, _new_bucket()))
            bucket['basis'] = 'Ledger: actual provider usage recorded after each completed call.'
            return bucket, False
        return self._transaction(read)

    def summary(self, now=None):
        day_key, month_key = self._periods(now)
        def read(state):
            day = deepcopy(self._bucket(state['days'], day_key))
            month = deepcopy(self._bucket(state['months'], month_key))
            day_total = _decimal(day['estimated_cost_usd'], 'daily total')
            month_total = _decimal(month['estimated_cost_usd'], 'monthly total')
            day_cap, month_cap = self.config['_daily_cap'], self.config['_monthly_cap']
            day.update(cap_usd=_money(day_cap), remaining_usd=_money(max(Decimal(0), day_cap - day_total)),
                       available_to_other_callers_usd=_money(max(
                           Decimal(0), day_cap - self.config['_daily_reserve'] - day_total)),
                       reserved_for_daily_run_usd=_money(self.config['_daily_reserve']))
            month.update(cap_usd=_money(month_cap),
                         remaining_usd=_money(max(Decimal(0), month_cap - month_total)))
            reached = ('daily' if day_total >= day_cap else
                       'monthly' if month_total >= month_cap else None)
            return {'ok': True, 'date': day_key, 'month': month_key, 'daily': day,
                    'monthly': month, 'cap_status': f'{reached}_cap_reached' if reached else 'within_caps',
                    }, False
        return self._transaction(read)


def process_ledger(factory=UsageLedger):
    if os.environ.get('BRIEFING_TESTING') == '1':
        target = os.environ.get('BRIEFING_TEST_LEDGER_DIR')
        if not target:
            raise UsageError('test ledger path required')
        return factory(ledger_dir=target)
    return factory()
