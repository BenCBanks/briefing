"""Fictional records only. Every HTTP connection is mocked and sockets fail closed."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from dev.ingest import core, discover
from dev.ingest.retrieval import merge_candidates
from dev.ingest.retrieval.models import Candidate, RetrievalBudget
from dev.ingest.retrieval.safety import fetch_candidate
from dev.ingest.retrieval.domain import (
    DomainRouter, DomainCandidate, EdgarAdapter, FredAdapter, EuropePmcAdapter,
    WikipediaAdapter, OpenAlexAdapter, CrossrefAdapter, BlsAdapter,
)

FIXTURES = json.loads((Path(__file__).parent / 'fixtures/domain/responses.json').read_text())
CLASSES = (EdgarAdapter, FredAdapter, EuropePmcAdapter, WikipediaAdapter,
           OpenAlexAdapter, CrossrefAdapter, BlsAdapter)


class Response(BytesIO):
    status = 200

    def __init__(self, url, data):
        self.url = url
        super().__init__(json.dumps(data).encode())

    def geturl(self):
        return self.url


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.network = patch('socket.socket.connect', side_effect=AssertionError('unmocked network'))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.waiter = patch.object(core, '_wait_for_host')
        self.waiter.start()
        self.addCleanup(self.waiter.stop)
        self.requests = []

    def adapter(self, cls, data=None):
        name = cls.capability.name
        def opener(request, timeout):
            self.requests.append((request, timeout))
            return Response(request.full_url, FIXTURES[name] if data is None else data)
        return cls(opener=opener, contact='test@example.test', api_key='fictional-key')

    def search(self, adapter, query='fictional research', **kwargs):
        return adapter.search(query, max_results=5, freshness=None,
                              budget=kwargs.pop('budget', RetrievalBudget()), **kwargs)

    def test_all_adapters_return_candidates_without_evidence(self):
        for cls in CLASSES:
            with self.subTest(provider=cls.capability.name):
                adapter = self.adapter(cls)
                rows = self.search(adapter, 'bls:LNS14000000' if cls is BlsAdapter else 'fictional research')
                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertIsInstance(row, Candidate)
                self.assertIsInstance(row, DomainCandidate)
                self.assertEqual(row.providers, (adapter.name,))
                self.assertEqual(row.metadata.retention, 'transient only')
                self.assertTrue(row.metadata.record_id)
                self.assertTrue(row.metadata.license_note)
                self.assertTrue(row.metadata.retrieved_at)
                serialized = json.dumps(asdict(row))
                self.assertNotIn('DO_NOT_RETAIN', serialized)
                self.assertNotIn('fictional-key', serialized)
                self.assertNotIn('?', row.metadata.api_url)
                self.assertIsNone(row.feed_body)
                request, timeout = self.requests[-1]
                self.assertIn('BriefingBot', request.get_header('User-agent'))
                self.assertIn('https://github.com/BenCBanks/briefing', request.get_header('User-agent'))
                self.assertLessEqual(timeout, 12)
                core._wait_for_host.assert_called_with(urlsplit(request.full_url).hostname,
                    adapter.capability.minimum_interval_seconds, unittest.mock.ANY)

    def test_representative_normalization(self):
        edgar = self.search(self.adapter(EdgarAdapter))[0]
        self.assertEqual(edgar.url, 'https://www.sec.gov/Archives/edgar/data/42/000000004226000001/0000000042-26-000001-index.html')
        self.assertEqual(edgar.published, '2026-01-12T00:00:00Z')
        self.assertEqual(edgar.metadata.jurisdiction, 'US')
        self.assertEqual(edgar.metadata.corrections, ('amended filing',))
        pmc = self.search(self.adapter(EuropePmcAdapter))[0]
        self.assertTrue(pmc.metadata.retracted)
        self.assertEqual(pmc.metadata.version, '2')
        self.assertEqual(pmc.metadata.corrections, ('Published Erratum',))
        wiki = self.search(self.adapter(WikipediaAdapter))[0]
        self.assertIsNone(wiki.published)  # revision time isn't a publication date
        self.assertEqual(wiki.metadata.version, '999999999')
        alex = self.search(self.adapter(OpenAlexAdapter))[0]
        self.assertTrue(alex.metadata.retracted)
        self.assertEqual(alex.metadata.version, 'publishedVersion')
        crossref = self.search(self.adapter(CrossrefAdapter))[0]
        self.assertEqual(crossref.metadata.corrections, ('correction: 10.0000/fictional-original',))
        self.assertEqual(crossref.metadata.license_urls, ('https://creativecommons.org/licenses/by/4.0/',))
        fred = self.search(self.adapter(FredAdapter))[0]
        self.assertEqual(fred.metadata.units, 'fictional index units')
        self.assertIsNone(fred.metadata.jurisdiction)  # FRED includes other countries
        self.assertEqual(fred.metadata.model_use, 'prohibited by this adapter')
        bls = self.search(self.adapter(BlsAdapter), 'bls:LNS14000000')[0]
        self.assertEqual(bls.metadata.units, 'percent')
        self.assertEqual(bls.metadata.jurisdiction, 'US')
        self.assertIsNone(bls.published)

    def test_requests_and_optional_openalex_key(self):
        adapter = self.adapter(OpenAlexAdapter)
        adapter.api_key = None
        self.assertTrue(self.search(adapter))
        params = parse_qs(urlsplit(self.requests[-1][0].full_url).query)
        self.assertNotIn('api_key', params)
        self.assertEqual(params['search'], ['fictional research'])
        self.search(self.adapter(WikipediaAdapter), 'widgets & toys')
        params = parse_qs(urlsplit(self.requests[-1][0].full_url).query)
        self.assertEqual(params['gsrsearch'], ['widgets & toys'])
        self.assertNotIn('content', params['rvprop'][0])

    def test_missing_configuration_does_not_spend_query(self):
        for adapter in (FredAdapter(), EdgarAdapter(), EdgarAdapter(contact='bad\ncontact')):
            budget = RetrievalBudget()
            self.assertEqual(self.search(adapter, budget=budget), [])
            self.assertEqual(budget.queries, 0)
        self.assertEqual(self.requests, [])

    def test_errors_are_sanitized_and_do_not_retry(self):
        errors = [TimeoutError('DO_NOT_RETAIN'), ValueError('DO_NOT_RETAIN'),
                  HTTPError('https://example.test/?secret=DO_NOT_RETAIN', 429, 'secret', {}, None)]
        for error in errors:
            calls = []
            def opener(*args, **kwargs):
                calls.append(1)
                raise error
            adapter = CrossrefAdapter(opener=opener)
            budget = RetrievalBudget()
            self.assertEqual(self.search(adapter, budget=budget), [])
            self.assertEqual(len(calls), 1)
            self.assertNotIn('DO_NOT_RETAIN', json.dumps(budget.statuses))

    def test_invalid_records_and_caps(self):
        good = FIXTURES['crossref']['message']['items'][0]
        for records in ([], [None, {}, {'title': []}], 'bad shape'):
            self.assertEqual(self.search(self.adapter(CrossrefAdapter, {'message': {'items': records}})), [])
        row = self.search(self.adapter(CrossrefAdapter, {'message': {'items': [{}, good, good]}}))
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0].rank, 2)
        adapter = self.adapter(CrossrefAdapter)
        for budget in (RetrievalBudget(max_queries=0), RetrievalBudget(max_results=0)):
            before = len(self.requests)
            self.assertEqual(self.search(adapter, budget=budget), [])
            self.assertEqual(len(self.requests), before)
        before = len(self.requests)
        self.assertEqual(self.search(self.adapter(BlsAdapter), 'bls:UNKNOWN'), [])
        self.assertEqual(len(self.requests), before)

    def test_year_only_does_not_invent_publication_day(self):
        row = dict(FIXTURES['crossref']['message']['items'][0], published={'date-parts': [[2026]]})
        result = self.search(self.adapter(CrossrefAdapter, {'message': {'items': [row]}}))[0]
        self.assertIsNone(result.published)
        self.assertEqual(result.metadata.publication_date, '2026')

    def test_unsafe_result_urls_rejected(self):
        row = dict(FIXTURES['wikipedia']['query']['pages'][0], fullurl='https://127.0.0.1/private')
        self.assertEqual(self.search(self.adapter(WikipediaAdapter, {'query': {'pages': [row]}})), [])

    def test_redirect_and_oversize_rejected(self):
        for opener in (lambda request, timeout: Response('https://example.test/redirect', {}),
                       lambda request, timeout: Response(request.full_url, {'body': 'x' * (core.MAX_RESPONSE_BYTES + 1)})):
            self.assertEqual(self.search(CrossrefAdapter(opener=opener)), [])

    def test_routing_and_unknown_or_ambiguous_entities(self):
        router = DomainRouter(fallback_providers=())
        for query in ('Apple', 'Mercury', 'Jaguar', 'second hand bicycles', 'local pottery workshops'):
            self.assertEqual(router.route(query).adapters, ())
            self.assertTrue(router.route(query).web_fallback)
        self.assertEqual(router.route('Fictional Co SEC filing 10-K').adapters, ('edgar',))
        self.assertIn('europe_pmc', router.route('Mercury clinical study').adapters)
        self.assertIn('wikipedia', router.route('history of Mercury').adapters)
        self.assertEqual(len(router.capabilities), 7)
        for bad in ('', None, 'x' * 501):
            with self.assertRaises(ValueError):
                router.route(bad)

    def test_fallback_reserved_and_failures_isolated(self):
        fallback = self.adapter(CrossrefAdapter)
        fallback.name = 'fictional_web'
        router = DomainRouter([FredAdapter()], fallback_providers=[fallback])
        budget = RetrievalBudget(max_queries=1)
        self.assertTrue(router.search('inflation', budget=budget))
        self.assertEqual(budget.queries, 1)
        self.assertEqual(budget.statuses['fred']['status'], 'domain key not configured')
        router = DomainRouter([self.adapter(EuropePmcAdapter, {})], fallback_providers=[fallback])
        budget = RetrievalBudget(max_queries=2)
        self.assertTrue(router.search('clinical study', budget=budget))
        self.assertEqual(budget.statuses['europe_pmc']['status'], 'invalid provider response')
        self.assertTrue(router.search('unknown domain', budget=RetrievalBudget()))

    def test_web_merge_preserves_licence_and_provenance(self):
        specialist = self.search(self.adapter(CrossrefAdapter))[0]
        bare = Candidate(specialist.url, 'Fictional web hint', 'web', None, 'web', 0)
        for batches in ([('web', [bare]), ('crossref', [specialist])],
                        [('crossref', [specialist]), ('web', [bare])]):
            result = merge_candidates(batches, max_results=2)[0]
            self.assertEqual(result.metadata, specialist.metadata)
            self.assertEqual(result.providers, ('crossref', 'web'))
            self.assertEqual(result.provider, 'crossref')

    def test_fred_cannot_be_fetched_as_model_evidence_even_as_web_hint(self):
        for url in ('https://fred.stlouisfed.org/series/FICTIONAL', 'https://api.stlouisfed.org/fred/series'):
            row = Candidate(url, 'Fictional', 'FRED', None, 'web', 1)
            with patch.object(core, 'fetch_article_text', side_effect=AssertionError('FRED evidence read')):
                diagnostics = []
                self.assertIsNone(fetch_candidate(row, budget=RetrievalBudget(), diagnostics=diagnostics))
                self.assertEqual(diagnostics, [{'reason': 'fred_discovery_only'}])

    def test_legacy_fred_watches_drop_retained_fields_before_interval_check(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry, state = root / 'registry.json', root / 'state.json'
            registry.write_text(json.dumps({'watched_pages': [{'id': 'fictional-fred',
                'url': 'https://fred.stlouisfed.org/series/FICTIONAL', 'interval_seconds': 604800}]}))
            state.write_text(json.dumps({'fictional-fred': {'checked_at': core.iso_date(core.utc_now()),
                'data_points': ['DO_NOT_RETAIN'], 'snippet': 'DO_NOT_RETAIN', 'digest': 'DO_NOT_RETAIN'}}))
            with patch.object(discover.core, '_robots_policy', side_effect=AssertionError('unexpected request')):
                discover.run_weekly_watches(registry, state)
            self.assertNotIn('DO_NOT_RETAIN', state.read_text())
            self.assertEqual(set(json.loads(state.read_text())['fictional-fred']), {'checked_at', 'status'})

    def test_rate_scheduler_is_shared_per_host_and_deadline_bound(self):
        self.waiter.stop()
        host = 'fictional-domain-api.example.test'
        with core._schedule_lock:
            core._last_request_by_host.pop(host, None)
        try:
            with patch.object(core.time, 'monotonic', return_value=100), patch.object(core.time, 'sleep') as sleep:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    list(pool.map(lambda _: core._wait_for_host(host, 1, 110), range(2)))
                sleep.assert_called_once_with(1)
                with self.assertRaises(TimeoutError):
                    core._wait_for_host(host, 1, 101.5)
        finally:
            with core._schedule_lock:
                core._last_request_by_host.pop(host, None)

    def test_domain_search_has_no_disk_side_effects(self):
        adapters = [self.adapter(cls) for cls in CLASSES]
        router = DomainRouter(adapters, fallback_providers=())
        # Includes credential reads, candidate queues, response caches and counters.
        with patch('builtins.open', side_effect=AssertionError('unexpected file access')), \
             patch('io.open', side_effect=AssertionError('unexpected file access')):
            result = router.search('clinical research history filings inflation bls:LNS14000000',
                                   budget=RetrievalBudget(max_queries=8, max_results=20))
        self.assertEqual(len(result), 7)

    def test_zero_budget_router_does_not_call_network(self):
        router = DomainRouter([self.adapter(CrossrefAdapter)], fallback_providers=())
        self.assertEqual(router.search('research', budget=RetrievalBudget(max_queries=0)), [])
        self.assertEqual(self.requests, [])

    def test_model_restriction_survives_non_fred_url(self):
        row = self.search(self.adapter(FredAdapter))[0]
        row = replace(row, url='https://fictional.example.test/statistic')
        with patch.object(core, 'fetch_article_text', side_effect=AssertionError('restricted evidence read')):
            self.assertIsNone(fetch_candidate(row, budget=RetrievalBudget()))
