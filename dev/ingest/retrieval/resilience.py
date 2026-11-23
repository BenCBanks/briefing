"""Process-local availability state. Never stores queries, credentials or results."""
from contextlib import contextmanager
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import hashlib
import math
from threading import Lock
import time
from urllib.error import HTTPError, URLError


class ProviderUnavailable(Exception):
    """Contains only an application-owned status code, never upstream content."""


def failure_status(error):
    if isinstance(error, ProviderUnavailable):
        return str(error)
    if isinstance(error, HTTPError):
        return 'provider rate limited' if error.code == 429 else 'provider HTTP error'
    if isinstance(error, TimeoutError):
        return 'provider timeout'
    if isinstance(error, (OSError, URLError)):
        return 'provider unavailable'
    return 'invalid provider response'


@dataclass
class Health:
    failures: int = 0
    retry_at: float = 0
    score: int = 100


class Resilience:
    """Retry on a later search, never sleep through a fallback's time allowance.

    One attempt per search/provider; failures back off 5, 10, 20…300 seconds.
    Retry-After may extend that delay. One serialized recovery probe is allowed
    after cooldown. Success (including valid empty results) restores health.
    """
    def __init__(self, *, clock=time.monotonic, wall_clock=time.time, ttl=60, capacity=256):
        self.clock, self.wall_clock = clock, wall_clock
        self.ttl, self.capacity = ttl, capacity
        self._lock = Lock()
        self._provider_locks = {}
        self._negative, self._health = {}, {}

    @staticmethod
    def identity(provider, query, count, freshness):
        # Configuration and feed contents scope misses; a new feed snapshot or
        # credentials cannot inherit another instance's empty-result entry.
        scope = tuple(getattr(provider, name, None) for name in (
            'config', 'api_key', 'contact', 'items', 'registry', 'refresh', 'max_feeds'))
        return hashlib.sha256(repr((provider.name, scope, query.strip(), count, freshness)).encode()).hexdigest()

    def _prune(self):
        now = self.clock()
        for key, (expiry, _) in list(self._negative.items()):
            if expiry <= now:
                del self._negative[key]

    def blocked(self, name, identity):
        with self._lock:
            self._prune()
            if identity in self._negative:
                return self._negative[identity][1]
            health = self._health.get(name, Health())
            return 'provider backoff' if health.retry_at > self.clock() else None

    def priority(self, name):
        with self._lock:
            health = self._health.get(name, Health())
            # Once eligible, give one recovery probe normal priority. Otherwise
            # a healthy fallback would permanently starve a recovered provider.
            score = 100 if health.retry_at <= self.clock() else health.score
            return (health.retry_at > self.clock(), -score)

    def health(self, name):
        with self._lock:
            row = self._health.get(name, Health())
            return {'score': row.score, 'failures': row.failures,
                    'retry_in': max(0, row.retry_at - self.clock())}

    @contextmanager
    def _serial(self, locks, key, budget):
        with self._lock:
            lock = locks.setdefault(key, Lock())
        if not lock.acquire(timeout=budget.remaining()):
            raise TimeoutError('retrieval deadline')
        try:
            budget.timeout()
            yield
        finally:
            lock.release()

    @contextmanager
    def host_slot(self, host, budget):
        # Hold through the existing shared host scheduler AND response read so
        # delayed concurrent starts cannot bunch after a scheduler wake-up.
        from .. import core
        lock = core._host_request_lock(host)
        if not lock.acquire(timeout=budget.remaining()):
            raise TimeoutError('retrieval deadline')
        try:
            budget.timeout()
            yield
        finally:
            lock.release()

    def _retry_after(self, error):
        if not isinstance(error, HTTPError):
            return 0
        value = (error.headers or {}).get('Retry-After', '')
        try:
            delay = float(value)
        except (ValueError, TypeError):
            try:
                delay = parsedate_to_datetime(value).timestamp() - self.wall_clock()
            except (ValueError, TypeError, OverflowError, AttributeError):
                return 0
        return max(0, delay) if math.isfinite(delay) else 0

    def call(self, name, identity, budget, operation):
        with self._serial(self._provider_locks, name, budget):
            status = self.blocked(name, identity)
            if status == 'no results':
                return []
            if status:
                raise ProviderUnavailable(status)
            try:
                rows = operation()
            except (ProviderUnavailable, HTTPError, TimeoutError, OSError, URLError,
                    ValueError, TypeError, KeyError, AttributeError) as error:
                status = failure_status(error)
                if status == 'query budget exhausted':
                    raise
                if status in {'explicit BLS series required', 'unsupported BLS series',
                              'daily query cap reached', 'usage counter unavailable'}:
                    # A query/configuration-local refusal is not a provider outage.
                    self._remember(identity, status)
                    raise
                delay = self._retry_after(error)
                with self._lock:
                    health = self._health.setdefault(name, Health())
                    health.failures = min(health.failures + 1, 16)
                    health.score = max(0, 100 - 25 * health.failures)
                    health.retry_at = self.clock() + max(delay, min(300, 5 * 2 ** (health.failures - 1)))
                self._remember(identity, status, min(self.ttl, health.retry_at - self.clock()))
                raise
            with self._lock:
                self._health[name] = Health()
            if not rows:
                self._remember(identity, 'no results')
            return rows

    def _remember(self, identity, status, ttl=None):
        with self._lock:
            self._prune()
            if len(self._negative) >= self.capacity:
                self._negative.pop(next(iter(self._negative)))
            self._negative[identity] = (self.clock() + (self.ttl if ttl is None else ttl), status)


DEFAULT = Resilience()
