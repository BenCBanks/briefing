"""Fictional responses, injected clocks and blocked sockets; no provider calls."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from dev.ingest import core
from dev.ingest.retrieval import (
    FeedProvider, GdeltProvider, GeminiGroundingProvider, PerplexitySearchProvider,
    RetrievalBudget, RetrievalConfig, retrieve,
)
from dev.ingest.retrieval.domain import (
    BlsAdapter, CrossrefAdapter, EdgarAdapter, EuropePmcAdapter, FredAdapter,
    OpenAlexAdapter, WikipediaAdapter,
)
from dev.ingest.retrieval.provider import BaseProvider, candidate
from dev.ingest.retrieval.resilience import Resilience


class Clock:
    def __init__(self):
        self.now = 100.0
    def __call__(self):
        return self.now
    def advance(self, seconds):
        self.now += seconds


class Stub(BaseProvider):
    name = 'fictional-search'
    def __init__(self, result=None):
        self.calls = 0
        self.result = result
    def _search(self, *args):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result or []


class Response(BytesIO):
    status = 200
    def __init__(self, url, data):
        self.url = url
        super().__init__(json.dumps(data).encode())
    def geturl(self):
        return self.url


class ResilienceTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.policy = Resilience(clock=self.clock, wall_clock=lambda: 0)
        for target in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            guard = patch(target, side_effect=AssertionError('network forbidden'))
            guard.start()
            self.addCleanup(guard.stop)

    def budget(self, **kwargs):
        return RetrievalBudget(resilience=self.policy, clock=self.clock, **kwargs)

    def search(self, provider, query='fictional battery', **kwargs):
        budget = self.budget(**kwargs)
        rows = provider.search(query, max_results=10, freshness=None, budget=budget)
        return rows, budget

    def test_negative_cache_shared_expiry_bounded_and_no_retained_text(self):
        self.policy.capacity = 2
        first, second = Stub(), Stub()
        self.search(first)
        _, cached = self.search(second)
        self.assertEqual((first.calls, second.calls, cached.queries), (1, 0, 0))
        self.clock.advance(60)
        self.search(second)
        self.assertEqual(second.calls, 1)
        for query in ('private fictional request A', 'private fictional request B'):
            self.search(second, query)
        self.assertEqual(len(self.policy._negative), 2)
        self.assertNotIn('private', repr(vars(self.policy)))
        self.search(second)  # Oldest miss was evicted.
        self.assertEqual(second.calls, 4)

    def test_backoff_doubles_caps_and_recovers_without_sleep(self):
        provider = Stub(URLError('PRIVATE upstream response'))
        for delay in (5, 10, 20, 40, 80, 160, 300, 300):
            self.search(provider)
            self.assertEqual(self.policy.health(provider.name)['retry_in'], delay)
            calls = provider.calls
            _, skipped = self.search(provider, 'different topic')
            self.assertEqual((provider.calls, skipped.queries), (calls, 0))
            self.assertEqual(skipped.statuses[provider.name]['status'], 'provider backoff')
            self.clock.advance(delay)
        provider.result = [candidate('https://fictional.example.test/a', 'Fictional battery', provider.name, 1)]
        rows, _ = self.search(provider)
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.policy.health(provider.name), {'score': 100, 'failures': 0, 'retry_in': 0})
        self.assertNotIn('PRIVATE', repr(vars(self.policy)))

    def test_retry_after_seconds_dates_and_malformed_values(self):
        for header, expected in [('90', 90), ('Thu, 01 Jan 1970 00:02:00 GMT', 120),
                                 ('NaN', 5), ('inf', 5), ('-1', 5), ('bad', 5)]:
            self.policy = Resilience(clock=self.clock, wall_clock=lambda: 0)
            error = HTTPError('https://fictional.example.test/?private', 429, 'PRIVATE',
                              {'Retry-After': header}, BytesIO(b'PRIVATE'))
            _, budget = self.search(Stub(error))
            self.assertTrue(error.fp.closed)
            self.assertEqual(self.policy.health(Stub.name)['retry_in'], expected)
            self.assertNotIn('PRIVATE', repr(budget.statuses))
            self.assertEqual(self.policy.health(Stub.name)['score'], 75)

    def test_fallback_skips_down_or_cached_provider_without_spending_slot(self):
        failed = Stub(TimeoutError('PRIVATE'))
        self.search(failed)
        healthy = Stub([candidate('https://fictional.example.test/a', 'Battery', 'backup', 1)])
        healthy.name = 'backup'
        budget = self.budget(max_queries=1)
        self.assertEqual(len(retrieve('another query', [failed, healthy], budget)), 1)
        self.assertEqual((failed.calls, healthy.calls, budget.queries), (1, 1, 1))
        self.assertEqual(budget.statuses[failed.name]['status'], 'provider backoff')
        self.clock.advance(5)
        failed.result = healthy.result
        retrieve('another query', [failed, healthy], self.budget(max_queries=1))
        self.assertEqual(failed.calls, 2)  # Eligible primary gets a recovery probe.
        self.assertEqual(self.policy.health(failed.name)['score'], 100)
        self.policy = Resilience(clock=self.clock)
        failed.result = []
        self.search(failed)
        budget = self.budget(max_queries=1, freshness=None)
        self.assertTrue(retrieve('fictional battery', [failed, healthy], budget))
        self.assertEqual(budget.queries, 1)

    def test_concurrent_identical_failures_make_one_attempt(self):
        providers = [Stub(URLError('offline')) for _ in range(12)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(self.search, providers))
        self.assertEqual(sum(p.calls for p in providers), 1)
        self.assertEqual(sum(budget.queries for _, budget in results), 1)
        self.assertEqual(self.policy.health(Stub.name)['failures'], 1)

    def test_shared_host_rate_limit_and_deadline_across_instances(self):
        host = 'api.gdeltproject.org'
        with core._schedule_lock:
            previous = core._last_request_by_host.pop(host, None)
        self.addCleanup(core._last_request_by_host.pop, host, None)
        if previous is not None:
            self.addCleanup(core._last_request_by_host.__setitem__, host, previous)
        starts = []
        def opener(request, timeout):
            starts.append(self.clock())
            return Response(request.full_url, {'articles': [
                {'url': 'https://fictional.example.test/a', 'title': 'Battery'}]})
        with patch.object(core.time, 'monotonic', self.clock), patch.object(core.time, 'sleep', self.clock.advance):
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda _: self.search(GdeltProvider(opener=opener)), range(2)))
            self.assertEqual(starts, [100, 105])
            _, budget = self.search(GdeltProvider(opener=opener), timeout_seconds=2)
            self.assertEqual(starts, [100, 105])
            self.assertEqual(budget.statuses['gdelt']['status'], 'provider timeout')

    def test_all_api_adapters_share_failure_policy_and_paid_attempt_counts(self):
        calls = []
        def offline(request, timeout):
            calls.append(request.full_url)
            raise HTTPError('', 503, 'PRIVATE', {}, None)
        with tempfile.TemporaryDirectory() as temp:
            key = Path(temp) / 'fictional-search-key'
            key.write_text('fictional-key')
            key.chmod(0o600)
            config = RetrievalConfig(state_dir=Path(temp), gemini_key_file=key, perplexity_key_file=key)
            providers = [GdeltProvider(opener=offline), GeminiGroundingProvider(config, opener=offline),
                         PerplexitySearchProvider(config, opener=offline)]
            providers += [cls(opener=offline, api_key='fictional-key', contact='test@example.test')
                          for cls in (BlsAdapter, CrossrefAdapter, EdgarAdapter, EuropePmcAdapter,
                                      FredAdapter, OpenAlexAdapter, WikipediaAdapter)]
            with patch.object(core, '_wait_for_host'):
                for provider in providers:
                    query = 'bls:LNS14000000' if provider.name == 'bls' else 'fictional battery'
                    before = len(calls)
                    self.search(provider, query)
                    _, cached = self.search(provider, query)
                    self.assertEqual(len(calls), before + 1, provider.name)
                    self.assertEqual(cached.queries, 0, provider.name)
                    self.assertEqual(self.policy.health(provider.name)['score'], 75)
            counts = json.loads((Path(temp) / 'retrieval-counts.json').read_text())['counts']
            self.assertEqual(counts, {'gemini': 1, 'perplexity': 1})

    def test_feed_miss_scope_refresh_failure_and_partial_success(self):
        self.search(FeedProvider())
        item = {'title': 'Fictional battery', 'url': 'https://fictional.example.test/a'}
        self.assertTrue(self.search(FeedProvider([item]))[0])
        source = {'id': 'fictional', 'enabled': True, 'robots_ok': True, 'format': 'rss'}
        provider = FeedProvider(registry=[source], refresh=True)
        with patch.object(core, 'fetch_source', side_effect=TimeoutError('PRIVATE')) as fetch:
            self.search(provider)
            self.search(provider)
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.policy.health('feeds')['score'], 75)
        self.clock.advance(5)
        provider.items = [dict(item, source_id='fictional')]
        with patch.object(core, 'fetch_source', side_effect=TimeoutError('PRIVATE')):
            self.assertTrue(self.search(provider)[0])
        self.assertEqual(self.policy.health('feeds')['score'], 100)

    def test_disabled_flag_and_zero_budget(self):
        with patch.dict('os.environ', {'BRIEFING_RETRIEVAL_RESILIENCE': '0'}):
            self.assertIsNone(RetrievalBudget().resilience)
        with patch.dict('os.environ', {'BRIEFING_RETRIEVAL_RESILIENCE': '1'}):
            self.assertIs(RetrievalBudget().resilience, RetrievalBudget().resilience)
        provider = Stub()
        _, budget = self.search(provider, max_queries=0)
        self.assertEqual((provider.calls, budget.queries), (0, 0))
        self.assertFalse(self.policy._negative)
        self.assertEqual(self.policy.health(provider.name)['score'], 100)

    def test_local_series_refusal_does_not_disable_valid_domain_queries(self):
        calls = []
        def opener(request, timeout):
            calls.append(1)
            return Response(request.full_url, {'status': 'REQUEST_SUCCEEDED', 'Results': {'series': []}})
        provider = BlsAdapter(opener=opener)
        self.search(provider, 'bls:UNKNOWN')
        self.assertEqual(self.policy.health('bls')['score'], 100)
        with patch.object(core, '_wait_for_host'):
            self.search(provider, 'bls:LNS14000000')
        self.assertEqual(calls, [1])


if __name__ == '__main__':
    unittest.main()
