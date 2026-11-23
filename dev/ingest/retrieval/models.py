"""Portable URL hints and cooperative, thread-safe retrieval limits."""
from dataclasses import dataclass, field
from threading import Lock
from typing import Protocol
import math
import os
import time


@dataclass(frozen=True)
class Candidate:
    url: str
    title: str
    outlet: str
    published: str | None
    provider: str
    rank: int
    # Unique discovery providers, not independent reporting origins.
    providers: tuple[str, ...] = ()
    credibility: dict | None = field(default=None, compare=False)
    # RSS evidence only, never Gemini text; retained in memory for this retrieval.
    source_id: str | None = None
    feed_body: str | None = field(default=None, repr=False, compare=False)


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, *, max_results: int, freshness: str | None,
               budget: 'RetrievalBudget') -> list[Candidate]: ...


@dataclass
class RetrievalBudget:
    timeout_seconds: float = 20
    max_queries: int = 4
    max_results: int = 20
    per_provider_results: int = 10
    freshness: str | None = 'week'
    max_workers: int = 4
    domain_tiers: dict | None = None
    before_gemini_call: object | None = field(default=None, repr=False)
    # Explicit injection for fixture clocks; production opt-in shares one process policy.
    resilience: object | None = field(default=None, repr=False)
    clock: object = field(default_factory=lambda: time.monotonic, repr=False)
    deadline: float = field(init=False)
    queries: int = field(default=0, init=False)
    grounding_requests: int = field(default=0, init=False)
    statuses: dict = field(default_factory=dict, init=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self):
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 120:
            raise ValueError('timeout_seconds must be in (0, 120]')
        for name, upper in [('max_queries', 16), ('max_results', 250),
                            ('per_provider_results', 250), ('max_workers', 4)]:
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= upper:
                raise ValueError(f'invalid {name}')
        if self.max_workers == 0:
            raise ValueError('max_workers must be positive')
        if self.freshness not in {None, 'hour', 'day', 'week', 'month', 'year'}:
            raise ValueError('invalid freshness')
        if self.resilience is None and os.environ.get('BRIEFING_RETRIEVAL_RESILIENCE') == '1':
            from .resilience import DEFAULT
            self.resilience = DEFAULT
        self.deadline = self.clock() + self.timeout_seconds

    def remaining(self):
        return max(0.0, self.deadline - self.clock())

    def timeout(self):
        if self.remaining() <= 0:
            raise TimeoutError('retrieval deadline')
        return min(12.0, self.remaining())

    def claim(self):
        with self._lock:
            if self.remaining() <= 0 or self.queries >= self.max_queries:
                return False
            self.queries += 1
            return True

    def report(self, provider, status, count=0, elapsed=0):
        # Never record queries, response bodies, suggestions, URLs or exception text.
        with self._lock:
            self.statuses[provider] = {'status': status, 'count': count,
                                       'latency_ms': round(elapsed * 1000)}
