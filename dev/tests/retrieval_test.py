"""All retrieval HTTP is mocked; no credentials or live state are used."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dev.ingest import core
from dev.ingest.retrieval.models import Candidate, RetrievalBudget
from dev.ingest.retrieval.safety import canonical_url
from dev.ingest.retrieval.usage import DailyCounter
from dev.ingest.usage import UsageCapReached, UsageError, UsageLedger


class FoundationsTest(unittest.TestCase):
    def test_canonical_url(self):
        self.assertEqual(canonical_url('https://Example.COM:443/a?utm_source=x&id=4#here'),
                         'https://example.com/a?id=4')
        for url in ['http://example.com/a', 'https://127.0.0.1/', 'https://[::1]/',
                    'https://user:pass@example.com', 'https://localhost/',
                    'https://x.local/', 'https://example.com:80/', 'https://example.com/\nx']:
            with self.subTest(url=url):
                self.assertIsNone(canonical_url(url))

    def test_budget_and_atomic_counter(self):
        budget = RetrievalBudget(max_queries=2)
        self.assertTrue(budget.claim())
        self.assertTrue(budget.claim())
        self.assertFalse(budget.claim())
        with tempfile.TemporaryDirectory() as directory:
            counter = DailyCounter(directory)
            with ThreadPoolExecutor(max_workers=8) as pool:
                outcomes = list(pool.map(lambda _: counter.claim('gemini', 3), range(20)))
            self.assertEqual(sum(outcomes), 3)
            self.assertEqual(json.loads(counter.path.read_text())['counts'], {'gemini': 3})
            self.assertTrue(counter.claim('gemini', 3, datetime(2099, 1, 1, tzinfo=timezone.utc)))
            counter.path.write_text('broken')
            with self.assertRaises(ValueError):
                counter.claim('gemini', 3)

    def test_memory_robots_cache_never_reads_or_writes_disk(self):
        from io import BytesIO
        class Response(BytesIO):
            status = 200
            def geturl(self): return 'https://example.com/robots.txt'
        core._transient_robots_cache.clear()
        with patch.object(Path, 'read_text', side_effect=AssertionError('disk read')), \
                patch.object(core, '_save_robots_cache', side_effect=AssertionError('disk write')), \
                patch.object(core, '_wait_for_host'), \
                patch.object(core, '_safe_https_open', return_value=Response(b'User-agent: *\nDisallow: /private')) as http:
            self.assertEqual(core._robots_policy('https://example.com/public', cache_path=None), (True, None))
            self.assertEqual(core._robots_policy('https://example.com/private', cache_path=None), (False, None))
            self.assertEqual(http.call_count, 1)
        core._transient_robots_cache.clear()

# Intentionally synthetic fixtures. Nothing here came from a grounded response.
from dataclasses import replace
from io import BytesIO, StringIO
import socket
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from dev.ingest.retrieval import (FeedProvider, GdeltProvider, GeminiGroundingProvider,
    PerplexitySearchProvider, RetrievalConfig, build_providers, merge_candidates, retrieve)
from dev.ingest.retrieval.provider import request_json
from dev.ingest.retrieval import gdelt
from dev.ingest.retrieval.safety import (_Redirect, fetch_candidate, resolve_google_hint, source_for)

FIXTURES = Path(__file__).parent / 'fixtures' / 'retrieval'


class Response(BytesIO):
    def __init__(self, body, url, headers=None, status=200):
        super().__init__(body)
        self.url, self.headers, self.status = url, headers or {}, status
    def geturl(self):
        return self.url


class AdapterTest(unittest.TestCase):
    def setUp(self):
        gdelt._negative_cache.clear()
        self.addCleanup(gdelt._negative_cache.clear)
        self.sleeper = patch.object(gdelt.time, 'sleep')
        self.sleeper.start()
        self.addCleanup(self.sleeper.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.key = self.directory / '.briefing_gemini_search_key'
        self.key.write_text('synthetic-key-for-tests')
        self.key.chmod(0o600)
        self.config = RetrievalConfig(gemini_key_file=self.key, perplexity_key_file=self.key,
                                      state_dir=self.directory, result_cap=10)
        ledger = UsageLedger(ledger_dir=self.directory / 'global-usage')
        self.usage_patchers = [patch('dev.ingest.retrieval.gemini.UsageLedger', return_value=ledger),
                               patch('dev.ingest.retrieval.perplexity.UsageLedger', return_value=ledger)]
        for patcher in self.usage_patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.requests = []
        self.network = patch.object(socket, 'create_connection', side_effect=AssertionError('real HTTP forbidden'))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.dns = patch.object(socket, 'getaddrinfo', side_effect=AssertionError('real DNS forbidden'))
        self.dns.start()
        self.addCleanup(self.dns.stop)
        self.waiter = patch.object(core, '_wait_for_host')
        self.wait = self.waiter.start()
        self.addCleanup(self.waiter.stop)
        core._transient_robots_cache.clear()
        self.addCleanup(core._transient_robots_cache.clear)

    def http(self, fixture):
        def open_request(request, timeout):
            self.requests.append((request, timeout))
            return Response((FIXTURES / f'{fixture}.json').read_bytes(), request.full_url)
        return open_request

    def search(self, provider, count=10, freshness='week', budget=None):
        self.budget = budget or RetrievalBudget()
        return provider.search('battery research', max_results=count, freshness=freshness, budget=self.budget)

    def test_gemini_request_metadata_and_transient_boundary(self):
        provider = GeminiGroundingProvider(self.config, opener=self.http('gemini'),
            resolver=lambda url, budget: 'https://www.energy.example/research' if 'redirect' in str(url) else url)
        rows = self.search(provider)
        self.assertEqual([r.url for r in rows], ['https://science.example/paper', 'https://www.energy.example/research'])
        req, timeout = self.requests[0]
        body = json.loads(req.data)
        self.assertEqual(body['tools'], [{'google_search': {}}])
        self.assertEqual(body['generationConfig'], {'maxOutputTokens': 2048,
                         'thinkingConfig': {'thinkingLevel': 'low'}})
        self.assertIn(self.config.gemini_model + ':generateContent', req.full_url)
        self.assertIn('x-goog-api-key', {k.lower() for k, _ in req.header_items()})
        self.assertNotIn('key=', req.full_url)
        self.assertLessEqual(timeout, 12)
        persisted = self.directory.joinpath('retrieval-counts.json').read_text()
        self.assertEqual(json.loads(persisted)['counts'], {'gemini': 1})
        for marker in ['FICTIONAL', 'battery', 'https://', 'synthetic-key']:
            self.assertNotIn(marker, persisted)
            self.assertNotIn(marker, repr(self.budget.statuses))
        self.assertFalse(any(name in vars(provider) for name in ['response', 'answer', 'suggestions', 'api_key']))
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()),
                         ['.briefing_gemini_search_key', 'global-usage', 'retrieval-counts.json',
                          'retrieval-counts.lock'])

    def test_grounding_tokens_query_meter_daily_context_and_six_call_cap(self):
        from dev.ingest.retrieval import retrieve
        from dev.ingest.usage import usage_caller, reset_usage_caller
        config_path = self.directory / 'daily-only.json'
        config = json.loads((Path(__file__).resolve().parents[1] / 'ingest/usage-prices.json').read_text())
        config['caps'] = {'daily_usd': '0.50', 'monthly_usd': '5', 'daily_run_reserve_usd': '0.50'}
        config_path.write_text(json.dumps(config))
        ledger = UsageLedger(config_path=config_path, ledger_dir=self.directory / 'daily-usage')
        def opener(request, timeout):
            self.requests.append((request, timeout))
            return Response(json.dumps({'usageMetadata': {'promptTokenCount': 120,
                'candidatesTokenCount': 20, 'thoughtsTokenCount': 5}, 'candidates': []}).encode(), request.full_url)
        provider = GeminiGroundingProvider(replace(self.config, gemini_daily_cap=6),
                                           opener=opener, usage_ledger=ledger)
        token = usage_caller('daily')
        try:
            for _ in range(7):
                budget = RetrievalBudget()
                retrieve('fictional research', [provider], budget)
        finally:
            reset_usage_caller(token)
        self.assertEqual(len(self.requests), 6)
        self.assertEqual(budget.statuses['gemini']['status'], 'daily query cap reached')
        totals = ledger.summary()['daily']
        self.assertEqual(totals['text_calls'], 6)
        self.assertEqual(totals['search_queries'], {'gemini': 6})
        self.assertEqual(totals['prompt_tokens'], 720)
        self.assertEqual(totals['candidates_tokens'], 120)
        self.assertEqual(totals['thoughts_tokens'], 30)
        persisted = ledger.path.read_text()
        self.assertNotIn('fictional research', persisted)
        self.assertNotIn('synthetic-key', persisted)
        self.assertEqual(json.loads(persisted)['pending'], {})

    def test_strict_grounding_rejects_missing_usage_and_keeps_reservation_pending(self):
        ledger = UsageLedger(ledger_dir=self.directory / 'strict-grounding-ledger')
        batch_id = 'fictional-strict-grounding-batch'
        ledger.reserve_batch_budget(batch_id, general_usd='0.02')
        response = {'candidates': []}
        provider = GeminiGroundingProvider(
            replace(self.config, gemini_model='gemini-3.8-flash'),
            opener=lambda request, timeout: Response(
                json.dumps(response).encode(), request.full_url),
            usage_ledger=ledger)
        with patch.dict(os.environ, {'BRIEFING_REQUIRE_USAGE_BATCH': '1'}), \
                ledger.usage_batch(batch_id):
            self.assertEqual(self.search(provider), [])
        self.assertEqual(self.budget.statuses['gemini']['status'],
                         'usage metadata unavailable')
        self.assertEqual(len(json.loads(ledger.path.read_text())['pending']), 1)
        with self.assertRaises(UsageError):
            ledger.close_batch_budget(batch_id)

    def test_separate_missing_key_no_fallback_or_counter(self):
        self.key.unlink()
        # A writer key exists, but the adapter must not use it.
        main_key = self.directory / '.briefing_gemini_key'
        main_key.write_text('never-read')
        main_key.chmod(0o600)
        rows = self.search(GeminiGroundingProvider(self.config, opener=self.http('gemini')))
        self.assertEqual(rows, [])
        self.assertEqual(self.budget.statuses['gemini']['status'], 'search key not configured')
        self.assertEqual(self.requests, [])
        self.assertFalse((self.directory / 'retrieval-counts.json').exists())
        rows = self.search(GeminiGroundingProvider(replace(self.config, gemini_key_file=main_key)))
        self.assertEqual(rows, [])
        self.assertEqual(self.budget.statuses['gemini']['status'], 'separate search key required')

    def test_perplexity_current_search_schema_and_dates(self):
        rows = self.search(PerplexitySearchProvider(self.config, opener=self.http('perplexity')))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].published, '2026-11-18T00:00:00Z')
        self.assertIsNone(rows[1].published)
        request, _ = self.requests[0]
        self.assertEqual(request.full_url, 'https://api.perplexity.ai/search')
        self.assertEqual(json.loads(request.data), {'query': 'battery research', 'max_results': 10,
            'search_type': 'web', 'search_recency_filter': 'week'})
        self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-key-for-tests')
        self.assertNotIn('snippet', repr(rows))

    def test_perplexity_missing_key_disabled_and_fast_switch(self):
        absent = replace(self.config, perplexity_key_file=self.directory / 'missing')
        self.assertEqual(self.search(PerplexitySearchProvider(absent)), [])
        self.assertEqual(self.budget.statuses['perplexity']['status'], 'search key not configured')
        fast = replace(self.config, perplexity_search_type='fast', result_cap=100)
        rows = self.search(PerplexitySearchProvider(fast, opener=self.http('perplexity')), count=100,
                           freshness=None, budget=RetrievalBudget(per_provider_results=100, max_results=100))
        payload = json.loads(self.requests[-1][0].data)
        self.assertEqual(payload, {'query': 'battery research', 'max_results': 20, 'search_type': 'fast'})
        self.assertEqual(len(rows), 2)

    def test_gdelt_params_interval_and_domain_title_dedupe(self):
        rows = self.search(GdeltProvider(opener=self.http('gdelt')))
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0].published)
        params = parse_qs(urlsplit(self.requests[0][0].full_url).query)
        self.assertEqual(params['mode'], ['artlist'])
        self.assertEqual(params['timespan'], ['7d'])
        self.assertEqual(params['query'], ['battery research'])
        self.assertEqual(params['maxrecords'], ['10'])
        self.assertEqual(self.wait.call_args.args[1], 5)

    def test_feeds_relevance_freshness_and_disabled_registry(self):
        items = json.loads((FIXTURES / 'feeds.json').read_text())['items']
        with patch.object(core, 'utc_now', return_value=datetime(2026, 11, 18, 12, tzinfo=timezone.utc)):
            rows = self.search(FeedProvider(items))
            self.assertEqual([r.url for r in rows], ['https://science.example/paper'])
            rows = self.search(FeedProvider(items), freshness=None)
            self.assertEqual(len(rows), 2)
            registry = [{'id': 'science-fixture', 'name': 'Science', 'enabled': False, 'robots_ok': True}]
            self.assertEqual(self.search(FeedProvider(items, registry=registry)), [])
        self.assertEqual(self.requests, [])

    def test_feed_candidate_filter_preserves_older_item_within_existing_result_cap(self):
        now = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
        source = {'id': 'fictional-payments-feed', 'name': 'Fictional Payments Feed',
                  'enabled': True, 'robots_ok': True, 'article_access': 'full-text',
                  'yield_estimate': {'items_per_week': 20, 'summary_chars': 240}}
        recent_items = [
            {'source_id': source['id'], 'title': f'Payments settlement update {index}',
             'url': f'https://payments.example.test/recent-{index}',
             'published': (now - timedelta(days=index % 3)).isoformat()}
            for index in range(12)
        ]
        older_item = {'source_id': source['id'], 'title': 'Payments settlement process explained',
                      'url': 'https://payments.example.test/older-70-days',
                      'published': (now - timedelta(days=70)).isoformat()}
        rejected_recent_urls = {row['url'] for row in recent_items}
        provider = FeedProvider([*recent_items, older_item], registry=[source], result_cap=12)
        provider.candidate_filter = lambda item, _source: item['url'] not in rejected_recent_urls
        budget = RetrievalBudget(max_results=12, per_provider_results=12)

        rows = provider.search('payments settlement', max_results=12, freshness=None, budget=budget)

        self.assertEqual([row.url for row in rows], [older_item['url']])
        self.assertEqual((now - datetime.fromisoformat(older_item['published'])).days, 70)

    def test_daily_refresh_prefers_relevant_recent_items_without_rejecting_91_day_supply(self):
        from dev.ingest import credibility
        from dev.ingest import repeats
        from dev.ingest.retrieval_fill import _feed_candidate_rejection
        from dev.ingest.write_edition import cluster_items

        now = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
        source = {'id': 'fictional-daily-payments', 'name': 'Fictional Daily Payments',
                  'url': 'https://daily.example.test/feed', 'topics': ['payments'],
                  'tier': 'trade', 'format': 'rss', 'enabled': True, 'robots_ok': True,
                  'reuse_terms': 'OBSERVED: fictional fixture license', 'article_access': 'feed-full'}
        cached = {'source_id': source['id'], 'title': 'Payments settlement banking policy update',
                  'url': 'https://daily.example.test/cached-30-day',
                  'published': (now - timedelta(days=30)).isoformat()}
        seen_item = {'source_id': source['id'], 'title': 'Payments settlement report already selected',
                     'url': 'https://daily.example.test/already-seen',
                     'published': (now - timedelta(days=7)).isoformat()}
        junk_item = {'source_id': source['id'], 'title': 'Top 7 payments settlement tips for your account',
                     'url': 'https://daily.example.test/listicle',
                     'published': (now - timedelta(days=7)).isoformat()}
        requests = []
        rss = (b'<rss><channel>'
               b'<item><title>Payments settlement analysis explains regional-bank timing</title>'
               b'<link>https://daily.example.test/refreshed-89-day</link>'
               b'<pubDate>Mon, 24 Aug 2026 12:00:00 GMT</pubDate></item>'
               b'<item><title>Payments settlement outlook from outside window</title>'
               b'<link>https://daily.example.test/refreshed-91-day</link>'
               b'<pubDate>Sat, 22 Aug 2026 12:00:00 GMT</pubDate></item>'
               b'</channel></rss>')

        def mocked_open(request, timeout):
            requests.append(request.full_url)
            if request.full_url.endswith('/robots.txt'):
                return Response(b'User-agent: *\nAllow: /', request.full_url)
            return Response(rss, request.full_url)

        maps = {'__source_ids__': {source['id']: source}}
        topic = {'id': 'payments', 'label': 'Payments', 'scope': 'banking settlement'}
        seen = {repeats.canonical_url(seen_item['url'])}

        def real_daily_filter(item, current_source):
            return _feed_candidate_rejection(
                item, current_source, topic, now=now, prior=[], seen=seen, maps=maps,
                rights_doc=f"`{source['id']}`", cluster_items_fn=cluster_items,
                require_date=True) is None

        provider = FeedProvider([cached, seen_item, junk_item], registry=[source], refresh=True, opener=mocked_open,
                                max_feeds=1, result_cap=5)
        provider.candidate_filter = real_daily_filter
        budget = RetrievalBudget(max_results=5, per_provider_results=5, freshness=None)
        tiers = dict(credibility._DEFAULT_TIERS)
        tiers['daily.example.test'] = {'tier': 'trade', 'reviewed_at': '2026-11-18'}
        with patch.object(core, 'utc_now', return_value=now), \
                patch.object(credibility, '_DEFAULT_TIERS', tiers):
            rows = provider.search('payments settlement', max_results=5,
                                   freshness=None, budget=budget)

        self.assertEqual([row.url for row in rows], [cached['url'],
                         'https://daily.example.test/refreshed-89-day',
                         'https://daily.example.test/refreshed-91-day'])
        self.assertNotIn(seen_item['url'], [row.url for row in rows])
        self.assertNotIn(junk_item['url'], [row.url for row in rows])
        published = [core.parse_date(row.published) for row in rows]
        self.assertEqual(published, sorted(published, reverse=True))
        self.assertEqual(requests, ['https://daily.example.test/robots.txt', source['url']])

    def test_cached_and_refreshed_tracking_variants_dedupe_and_keep_89_day_fallback(self):
        now = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
        source = {'id': 'fictional-canonical-feed', 'name': 'Fictional Canonical Feed',
                  'url': 'https://canonical.example.test/feed', 'topics': ['payments'],
                  'format': 'rss', 'enabled': True, 'robots_ok': True}
        canonical_story = 'https://canonical.example.test/settlement-timing'
        cached_duplicate = {'source_id': source['id'],
                            'title': 'Payments settlement timing changes for merchants',
                            'url': canonical_story + '?utm_source=cached',
                            'published': (now - timedelta(days=7)).isoformat()}
        fallback = {'source_id': source['id'],
                    'title': 'Payments settlement procedures explained for regional banks',
                    'url': 'https://canonical.example.test/older-89-day-explainer',
                    'published': (now - timedelta(days=89)).isoformat()}
        seen = {'source_id': source['id'], 'title': 'Payments settlement update already received',
                'url': 'https://canonical.example.test/seen-story',
                'published': (now - timedelta(days=3)).isoformat()}
        requests = []

        def mocked_open(request, timeout):
            requests.append(request.full_url)
            if request.full_url.endswith('/robots.txt'):
                return Response(b'User-agent: *\nAllow: /', request.full_url)
            rss = (b'<rss><channel><item>'
                   b'<title>Payments settlement timing changes for merchants</title>'
                   b'<link>' + (canonical_story + '?utm_source=refreshed').encode() + b'</link>'
                   b'<pubDate>Sun, 15 Nov 2026 12:00:00 GMT</pubDate>'
                   b'</item></channel></rss>')
            return Response(rss, request.full_url)

        seen_urls = {canonical_url(seen['url'])}
        provider = FeedProvider([cached_duplicate, fallback, seen], registry=[source],
                                refresh=True, opener=mocked_open, max_feeds=1, result_cap=2)
        provider.candidate_filter = lambda item, _source: canonical_url(item.get('url')) not in seen_urls
        budget = RetrievalBudget(max_results=2, per_provider_results=2, freshness=None)
        with patch.object(core, 'utc_now', return_value=now):
            rows = provider.search('payments settlement', max_results=2,
                                   freshness=None, budget=budget)

        self.assertEqual([row.url for row in rows], [canonical_story, fallback['url']])
        self.assertEqual(sum(row.url == canonical_story for row in rows), 1)
        self.assertNotIn(seen['url'], [row.url for row in rows])
        self.assertEqual(requests, ['https://canonical.example.test/robots.txt', source['url']])
        self.assertEqual(budget.statuses['feeds']['count'], 2)

    def test_caps_charge_failed_attempts_and_no_retries(self):
        def unavailable(*args, **kwargs):
            self.requests.append(None)
            raise URLError('FICTIONAL_SECRET_RESPONSE')
        config = replace(self.config, gemini_daily_cap=1)
        provider = GeminiGroundingProvider(config, opener=unavailable)
        self.assertEqual(self.search(provider), [])
        self.assertEqual(self.budget.statuses['gemini']['status'], 'provider unavailable')
        self.assertEqual(self.search(provider), [])
        self.assertEqual(self.budget.statuses['gemini']['status'], 'daily query cap reached')
        self.assertEqual(len(self.requests), 1)
        self.assertNotIn('SECRET', repr(self.budget.statuses))

    def test_global_cost_cap_blocks_paid_search_before_transport(self):
        config_path = self.directory / 'zero-caps.json'
        config = json.loads((Path(__file__).resolve().parents[1] / 'ingest' / 'usage-prices.json').read_text())
        config['caps'] = {'daily_usd': '0', 'monthly_usd': '5', 'daily_run_reserve_usd': '0'}
        config_path.write_text(json.dumps(config))
        ledger = UsageLedger(config_path=config_path, ledger_dir=self.directory / 'zero-ledger')
        opener = self.http('gemini')
        provider = GeminiGroundingProvider(self.config, opener=opener, usage_ledger=ledger)
        self.assertEqual(self.search(provider), [])
        self.assertEqual(self.budget.statuses['gemini']['status'], 'cost_cap_reached')
        self.assertEqual(self.requests, [])

    def test_counter_failure_and_insecure_key_fail_closed(self):
        (self.directory / 'retrieval-counts.json').write_text('{broken')
        self.assertEqual(self.search(PerplexitySearchProvider(self.config, opener=self.http('perplexity'))), [])
        self.assertEqual(self.budget.statuses['perplexity']['status'], 'usage counter unavailable')
        self.key.chmod(0o644)
        self.assertEqual(self.search(GeminiGroundingProvider(self.config)), [])
        self.assertEqual(self.budget.statuses['gemini']['status'], 'search key must have mode 600')
        self.assertEqual(self.requests, [])

    def test_http_malformed_oversize_timeout_and_rate_limit(self):
        for body, expected in [(b'not json', 'invalid provider response'),
                               (b'[]', 'invalid provider response'),
                               (b'x' * (core.MAX_RESPONSE_BYTES + 1), 'provider response too large')]:
            with self.subTest(expected=expected):
                gdelt._negative_cache.clear()
                def opener(request, timeout): return Response(body, request.full_url)
                self.assertEqual(self.search(GdeltProvider(opener=opener)), [])
                self.assertEqual(self.budget.statuses['gdelt']['status'], expected)
        for error, expected in [(TimeoutError('secret'), 'provider timeout'),
                (HTTPError('https://api.example', 429, 'secret', {}, None), 'provider rate limited')]:
            gdelt._negative_cache.clear()
            def opener(request, timeout): raise error
            self.assertEqual(self.search(GdeltProvider(opener=opener)), [])
            self.assertEqual(self.budget.statuses['gdelt']['status'], expected)

    def test_redirect_hint_is_resolved_without_fetching_publisher(self):
        requests = []
        def raw(request, timeout):
            requests.append((request.get_method(), request.full_url))
            if request.full_url.endswith('robots.txt'):
                return Response(b'User-agent: *\nAllow: /', request.full_url)
            raise _Redirect('https://science.example/paper?utm_source=g')
        url = resolve_google_hint('https://vertexaisearch.cloud.google.com/grounding-api-redirect/test',
                                  RetrievalBudget(), raw_open=raw)
        self.assertEqual(url, 'https://science.example/paper')
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[-1][0], 'HEAD')
        self.assertTrue(all('cloud.google.com' in url for _, url in requests))

    def test_redirect_hint_private_destination_and_publisher_robots_redirect_blocked(self):
        def raw(request, timeout):
            if request.full_url.endswith('robots.txt'):
                return Response(b'User-agent: *\nAllow: /', request.full_url)
            raise _Redirect('https://127.0.0.1/private')
        self.assertIsNone(resolve_google_hint('https://vertexaisearch.cloud.google.com/redirect',
                                             RetrievalBudget(), raw_open=raw))
        core._transient_robots_cache.clear()
        def bad_robots(request, timeout): raise _Redirect('https://other.example/robots.txt')
        row = Candidate('https://science.example/paper', 'Title', 'Science', None, 'gemini', 1)
        self.assertIsNone(fetch_candidate(row, budget=RetrievalBudget(), raw_open=bad_robots))

    def test_fetch_runs_existing_extractor_with_memory_robots(self):
        requests = []
        def raw(request, timeout):
            requests.append(request.full_url)
            body = b'User-agent: *\nDisallow: /blocked' if request.full_url.endswith('robots.txt') else \
                   b'<html><article><h1>Battery study</h1><p>Researchers measured battery capacity.</p></article></html>'
            return Response(body, request.full_url, {'Content-Type': 'text/html'})
        row = Candidate('https://science.example/paper', 'Battery', 'Science', None, 'feeds', 1)
        with patch.object(core, '_save_robots_cache', side_effect=AssertionError('disk write')):
            text = fetch_candidate(row, budget=RetrievalBudget(), raw_open=raw)
            self.assertIn('Researchers measured battery capacity.', text)
            self.assertIsNone(fetch_candidate(replace(row, url='https://science.example/blocked'),
                                              budget=RetrievalBudget(), raw_open=raw))
        self.assertEqual(requests, ['https://science.example/robots.txt', 'https://science.example/paper'])

    def test_gemini_redirect_chunks_real_resolver_dedupe_and_cap(self):
        hint = 'https://vertexaisearch.cloud.google.com/grounding-api-redirect/'
        payload = {'candidates': [{'finishReason': 'STOP', 'groundingMetadata': {
            'groundingChunks': [{'web': {'uri': hint + str(i), 'title': 'Science Daily'}}
                                for i in range(8)]}}]}
        calls = []
        def raw(req, timeout):
            calls.append(req.full_url)
            self.assertLessEqual(timeout, 3)
            self.assertNotIn('robots.txt', req.full_url)
            raise _Redirect('https://science.example/paper?utm_source=' + str(len(calls)))
        def http(req, timeout):
            return Response(json.dumps(payload).encode(), req.full_url)
        provider = GeminiGroundingProvider(self.config, opener=http,
            resolver=lambda url, budget: resolve_google_hint(url, budget, raw_open=raw))
        rows = self.search(provider, count=2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].url, 'https://science.example/paper')
        self.assertEqual(rows[0].outlet, 'Science Daily')
        self.assertEqual(len(calls), 4)  # Bound failed/duplicate hints too.

    def test_google_head_get_fallback_loop_and_pinned_ssrf(self):
        url = 'https://vertexaisearch.cloud.google.com/grounding-api-redirect/test'
        for head_status in (200, 405, 501):
            calls = []
            def raw(req, timeout):
                calls.append(req.get_method())
                if req.get_method() == 'HEAD':
                    if head_status != 200:
                        raise HTTPError(req.full_url, head_status, 'unsupported', {}, None)
                    return Response(b'unused', req.full_url)
                raise _Redirect('https://science.example/paper')
            self.assertEqual(resolve_google_hint(url, RetrievalBudget(), raw_open=raw),
                             'https://science.example/paper')
            self.assertEqual(calls, ['HEAD', 'GET'])
        calls = []
        def loop(req, timeout):
            calls.append(1)
            raise _Redirect(url)
        self.assertIsNone(resolve_google_hint(url, RetrievalBudget(), raw_open=loop))
        self.assertEqual(len(calls), 4)
        original = core._safe_https_open
        def dns(host, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', port))]
        with patch.object(core, '_safe_https_open', side_effect=lambda req, hosts, timeout, **kw:
                          original(req, hosts, timeout, resolver=dns, **kw)), \
                patch.object(core, '_PinnedHTTPSConnection', side_effect=AssertionError('private connection')):
            with self.assertRaises(ValueError):
                resolve_google_hint(url, RetrievalBudget())

    def test_feed_evidence_survives_merge_and_uses_origin_policy(self):
        source = {'id': 'science-feed', 'name': 'Science', 'url': 'https://science.example/feed',
                  'enabled': True, 'robots_ok': True, 'article_access': 'feed-full'}
        disabled = {**source, 'id': 'old-feed', 'enabled': False}
        body = 'Researchers measured battery capacity. ' * 100
        feed = FeedProvider([{'source_id': source['id'], 'title': 'Battery research',
                              'url': 'https://science.example/paper', 'feed_body': body}],
                            registry=[disabled, source])
        rows = self.search(feed, freshness=None)
        rows = [replace(row, published='2026-11-18T10:00:00Z') for row in rows]
        gemini = replace(rows[0], provider='gemini', source_id=None, feed_body=None,
                         title='science.example', published=None)
        merged = merge_candidates([('gemini', [gemini]), ('feeds', rows)], max_results=1)
        self.assertEqual(merged[0].title, 'Battery research')
        self.assertEqual(merged[0].published, '2026-11-18T10:00:00Z')
        with patch.object(core, 'fetch_article_text', side_effect=AssertionError('unneeded fetch')):
            self.assertEqual(fetch_candidate(merged[0], budget=RetrievalBudget(), registry=[disabled, source]),
                             core._clean_text(body, 12000))
        diagnostics = []
        self.assertIsNone(fetch_candidate(rows[0], budget=RetrievalBudget(),
                                         registry=[{**source, 'enabled': False}], diagnostics=diagnostics))
        self.assertEqual(diagnostics[-1]['reason'], 'article_access_disabled')
        self.assertNotIn(body, repr(rows[0]))

    def test_article_wait_budget_and_pipeline_extraction(self):
        source = {'id': 'science-feed', 'url': 'https://science.example/feed', 'enabled': True,
                  'robots_ok': True, 'article_access': 'transient-read', 'minimum_interval_seconds': 60}
        row = Candidate('https://science.example/paper', 'Battery', 'Science', None, 'feeds', 1,
                        source_id=source['id'])
        def wait(host, interval, deadline):
            if interval == 60 and deadline - time.monotonic() < 60:
                raise TimeoutError('host interval')
        def raw(req, timeout):
            self.assertEqual(req.get_header('User-agent'), core.USER_AGENT)
            body = b'User-agent: *\nAllow: /' if req.full_url.endswith('robots.txt') else \
                   b'<article><p>Researchers measured battery capacity.</p></article>'
            return Response(body, req.full_url, {'Content-Type': 'text/html'})
        with patch.object(core, '_wait_for_host', side_effect=wait):
            reasons = []
            self.assertIsNone(fetch_candidate(row, budget=RetrievalBudget(timeout_seconds=20),
                registry=[source], raw_open=raw, diagnostics=reasons))
            self.assertEqual(reasons[-1]['reason'], 'host_rate_limit_wait_timeout')
            text = fetch_candidate(row, budget=RetrievalBudget(timeout_seconds=90), registry=[source], raw_open=raw)
            self.assertIn('Researchers measured battery capacity.', text)

    def test_same_host_robots_redirect_uses_pipeline_policy(self):
        row = Candidate('https://science.example/paper', 'Battery', 'Science', None, 'gemini', 1)
        calls = []
        def raw(req, timeout):
            calls.append(req.full_url)
            if req.full_url == 'https://science.example/robots.txt':
                raise _Redirect('https://science.example/policy/robots.txt')
            body = b'User-agent: *\nAllow: /' if '/policy/' in req.full_url else \
                   b'<article><p>Researchers measured battery capacity.</p></article>'
            return Response(body, req.full_url, {'Content-Type': 'text/html'})
        self.assertIn('Researchers measured battery capacity.',
                      fetch_candidate(row, budget=RetrievalBudget(), raw_open=raw))
        self.assertEqual(len(calls), 3)

    def test_gdelt_single_retry_negative_cache_expiry_and_deadline(self):
        clock = [100.0]
        calls = []
        def limited(req, timeout):
            calls.append(clock[0])
            raise HTTPError(req.full_url, 429, 'PRIVATE RESPONSE', {'Retry-After': '6'}, None)
        with patch.object(gdelt.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(gdelt.time, 'sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)):
            for _ in range(2):  # New instances share the negative cache.
                self.assertEqual(self.search(GdeltProvider(opener=limited)), [])
                self.assertEqual(self.budget.statuses['gdelt']['status'], 'provider rate limited')
            self.assertEqual(calls, [100, 106])
            self.assertNotIn('battery', repr(gdelt._negative_cache))
            self.assertNotIn('PRIVATE', repr(gdelt._negative_cache))
            clock[0] = 167
            self.search(GdeltProvider(opener=limited), budget=RetrievalBudget(timeout_seconds=2))
            self.assertEqual(calls, [100, 106, 167])  # No retry beyond deadline.
            gdelt._negative_cache.clear()
            empty_calls = []
            def empty(req, timeout):
                empty_calls.append(clock[0])
                return Response(b'{"articles": []}', req.full_url)
            self.search(GdeltProvider(opener=empty))
            self.search(GdeltProvider(opener=empty))
            self.assertEqual(empty_calls, [167])
            clock[0] += 61
            self.search(GdeltProvider(opener=empty))
            self.assertEqual(empty_calls, [167, 228])

    def test_gdelt_rate_limiter_shared_across_instances_and_retry_success(self):
        clock = [100.0]
        calls = []
        self.waiter.stop()
        real_wait = core._wait_for_host
        core._last_request_by_host.pop('api.gdeltproject.org', None)
        self.addCleanup(core._last_request_by_host.pop, 'api.gdeltproject.org', None)
        def http(req, timeout):
            calls.append(clock[0])
            if len(calls) == 1:
                raise HTTPError(req.full_url, 429, 'limited', {}, None)
            return Response((FIXTURES / 'gdelt.json').read_bytes(), req.full_url)
        with patch.object(gdelt.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(gdelt.time, 'sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)), \
                patch.object(core, '_wait_for_host', side_effect=real_wait):
            self.assertEqual(len(self.search(GdeltProvider(opener=http))), 2)
            self.assertEqual(len(self.search(GdeltProvider(opener=http))), 2)
        self.assertEqual(calls, [100, 105, 110])
        self.assertFalse(gdelt._negative_cache)

    def test_fetch_rechecks_redirect_robots_and_respects_known_policy(self):
        requests = []
        def raw(request, timeout):
            requests.append(request.full_url)
            if request.full_url.endswith('robots.txt'):
                return Response(b'User-agent: *\nDisallow: /blocked', request.full_url)
            raise _Redirect('https://science.example/blocked')
        row = Candidate('https://science.example/paper', 'Battery', 'Science', None, 'gemini', 1)
        self.assertIsNone(fetch_candidate(row, budget=RetrievalBudget(), raw_open=raw))
        self.assertEqual(len(requests), 2)  # blocked redirect destination never fetched
        registry = [{'id': 'science', 'url': row.url, 'enabled': False, 'robots_ok': True,
                     'article_access': 'transient-read', 'minimum_interval_seconds': 60}]
        self.assertIsNone(fetch_candidate(row, budget=RetrievalBudget(), registry=registry, raw_open=raw))
        self.assertEqual(len(requests), 2)

    def test_fetch_ssrf_dns_and_cross_host_redirect_never_connect(self):
        # Exercise the actual pinned opener with mocked DNS and connections.
        row = Candidate('https://private.example/page', 'Private', 'Private', None, 'gemini', 1)
        def resolver(host, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', port))]
        original = core._safe_https_open
        with patch.object(core, '_safe_https_open', side_effect=lambda req, hosts, timeout, **kw:
                          original(req, hosts, timeout, resolver=resolver, **kw)), \
                patch.object(core, '_PinnedHTTPSConnection', side_effect=AssertionError('unsafe connection')):
            self.assertIsNone(fetch_candidate(row, budget=RetrievalBudget()))
        requests = []
        def raw(request, timeout):
            requests.append(request.full_url)
            if request.full_url.endswith('robots.txt'):
                return Response(b'User-agent: *\nAllow: /', request.full_url)
            raise _Redirect('https://other.example/article')
        row = replace(row, url='https://science.example/page')
        self.assertIsNone(fetch_candidate(row, budget=RetrievalBudget(), raw_open=raw))
        self.assertEqual(len(requests), 2)


    def test_transient_feed_refresh_uses_mock_http_and_no_store(self):
        requests = []
        source = {'id': 'science-fixture', 'name': 'Science', 'url': 'https://science.example/feed',
                  'topics': ['science'], 'format': 'rss', 'enabled': True, 'robots_ok': True,
                  'minimum_interval_seconds': 17}
        def raw(request, timeout):
            requests.append(request.full_url)
            body = b'User-agent: *\nAllow: /' if request.full_url.endswith('robots.txt') else \
                   b'<rss><channel><item><title>Battery research</title><link>https://science.example/paper</link></item></channel></rss>'
            return Response(body, request.full_url)
        provider = FeedProvider(registry=[source], refresh=True, opener=raw)
        with patch.object(core, 'save_store', side_effect=AssertionError('store write')), \
                patch.object(core, '_save_robots_cache', side_effect=AssertionError('robots write')):
            rows = self.search(provider, freshness=None)
        self.assertEqual([r.url for r in rows], ['https://science.example/paper'])
        self.assertEqual(requests, ['https://science.example/robots.txt', 'https://science.example/feed'])
        self.assertEqual(self.wait.call_args.args[1], 17)
        self.assertEqual(provider.items, [])

    def test_failed_feed_refresh_reports_timeout_and_keeps_unseen_90_day_cache(self):
        now = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
        source = {'id': 'fictional-payments', 'name': 'Fictional Payments Feed',
                  'url': 'https://payments.example.test/feed', 'topics': ['payments', 'battery'],
                  'format': 'rss', 'enabled': True, 'robots_ok': True}
        seen = {'source_id': source['id'], 'title': 'Battery research already received',
                'url': 'https://payments.example.test/seen-story',
                'published': (now - timedelta(days=12)).isoformat()}
        cached = {'source_id': source['id'], 'title': 'Battery research explains payment settlement',
                  'url': 'https://payments.example.test/cached-unseen-story',
                  'published': (now - timedelta(days=70)).isoformat()}
        requests = []

        def offline(request, timeout):
            if request.full_url.endswith('/robots.txt'):
                return Response(b'User-agent: *\nAllow: /', request.full_url)
            requests.append(request.full_url)
            raise TimeoutError('FICTIONAL_REFRESH_TIMEOUT_SECRET')

        provider = FeedProvider([seen, cached], registry=[source], refresh=True,
                                opener=offline, max_feeds=1)
        provider.candidate_filter = lambda item, _source: (
            item.get('url') != seen['url'] and
            bool((published := core.parse_date(item.get('published'))) and
                 now - timedelta(days=90) <= published <= now))
        budget = RetrievalBudget(freshness=None)

        with patch.object(core, 'utc_now', return_value=now):
            rows = provider.search('battery research', max_results=4, freshness=None, budget=budget)

        self.assertEqual([row.url for row in rows], [cached['url']])
        self.assertEqual(requests, [source['url']])
        self.assertEqual(budget.statuses['feeds']['status'],
                         'provider timeout; cached feed items used')
        self.assertEqual(budget.statuses['feeds']['count'], 1)
        self.assertEqual(budget.queries, 1)
        self.assertNotIn('SECRET', repr(budget.statuses))

    def test_article_interval_paywall_and_redirect_loop(self):
        row = Candidate('https://science.example/paper', 'Battery', 'Science', None, 'feeds', 1)
        source = {'id': 'science-fixture', 'url': row.url, 'enabled': True, 'robots_ok': True,
                  'article_access': 'transient-read', 'minimum_interval_seconds': 17}
        def raw(request, timeout):
            body = b'User-agent: *\nAllow: /' if request.full_url.endswith('robots.txt') else \
                   b'<article><p>Subscribe to continue reading this article.</p></article>'
            return Response(body, request.full_url, {'Content-Type': 'text/html'})
        self.assertIsNone(fetch_candidate(row, budget=RetrievalBudget(), registry=[source], raw_open=raw))
        self.assertEqual(self.wait.call_args.args[1], 17)
        calls = []
        def redirect(request, timeout):
            calls.append(request.full_url)
            raise _Redirect(row.url)
        self.assertIsNone(fetch_candidate(row, budget=RetrievalBudget(), raw_open=redirect))
        self.assertEqual(len(calls), 4)

    def test_api_transport_forbids_redirects_and_bounds_timeout(self):
        def raw(request, hosts, timeout, **kwargs):
            self.assertEqual(hosts, {'api.gdeltproject.org'})
            self.assertEqual(kwargs, {'max_redirects': 0})
            self.assertLessEqual(timeout, 0.5)
            return Response(b'{"articles": []}', request.full_url)
        with patch.object(core, '_safe_https_open', side_effect=raw) as opened:
            rows = self.search(GdeltProvider(), budget=RetrievalBudget(timeout_seconds=.5))
            self.assertEqual(rows, [])
            self.assertEqual(opened.call_count, 1)


class MergeAndCliTest(unittest.TestCase):
    def setUp(self):
        gdelt._negative_cache.clear()
        self.addCleanup(gdelt._negative_cache.clear)

    def row(self, url, provider='stub', rank=1):
        return Candidate(url, 'Synthetic article', urlsplit(url).hostname, None, provider, rank)

    def test_agreement_canonical_dedupe_domain_tiers_and_caps(self):
        batches = [('gemini', [self.row('https://low.example/shared?utm_source=g'),
                                self.row('https://low.example/shared#duplicate'),
                                self.row('https://agency.gov/report', rank=2)]),
                   ('gdelt', [self.row('https://low.example/shared'),
                              self.row('https://news.example/new')])]
        rows = merge_candidates(batches, max_results=2)
        self.assertEqual([r.url for r in rows], ['https://low.example/shared', 'https://agency.gov/report'])
        self.assertEqual(rows[0].providers, ('gdelt', 'gemini'))
        self.assertEqual(len(rows), 2)
        self.assertEqual(merge_candidates(batches, max_results=0), [])
        custom = merge_candidates([('a', [self.row('https://x.example/'), self.row('https://y.example/')])],
                                  max_results=2, domain_tiers={'y.example': 5})
        self.assertEqual(custom[0].url, 'https://y.example/')

    def test_providers_fan_out_concurrently_and_count_once(self):
        barrier = threading.Barrier(2)
        class Stub:
            def __init__(self, name): self.name = name
            def search(self, query, *, max_results, freshness, budget):
                self.asserted = budget.claim()
                barrier.wait(timeout=1)
                return [Candidate('https://science.example/a', query, 'Science', None, self.name, 1)]
        budget = RetrievalBudget(max_queries=2)
        providers = [Stub('one'), Stub('two')]
        rows = retrieve('battery', providers, budget)
        self.assertEqual(budget.queries, 2)
        self.assertTrue(all(p.asserted for p in providers))
        self.assertEqual(rows[0].providers, ('one', 'two'))

    def test_cap_order_missing_key_fallback_and_provider_failure(self):
        calls = []
        class Stub:
            name = 'first'
            def search(self, query, **kwargs):
                calls.append(self.name)
                kwargs['budget'].claim()
                return [Candidate('https://science.example/a', query, 'Science', None, self.name, 1)]
        with tempfile.TemporaryDirectory() as directory:
            missing = GeminiGroundingProvider(RetrievalConfig(gemini_key_file=Path(directory) / 'absent'))
            a, b = Stub(), Stub()
            b.name = 'second'
            budget = RetrievalBudget(max_queries=1)
            rows = retrieve('battery', [missing, a, a, b], budget)
            self.assertEqual(calls, ['first'])
            self.assertEqual(len(rows), 1)
            self.assertEqual(budget.statuses['gemini']['status'], 'search key not configured')
            self.assertEqual(budget.statuses['second']['status'], 'query budget exhausted')
        class Broken:
            name = 'broken'
            def search(self, query, **kwargs): raise RuntimeError('secret upstream text')
        budget = RetrievalBudget()
        self.assertEqual(len(retrieve('battery', [Broken(), a], budget)), 1)
        self.assertEqual(budget.statuses['broken']['status'], 'provider failed')
        self.assertNotIn('secret', repr(budget.statuses))

    def test_result_limits_and_zero_query_cap(self):
        called = []
        class Stub:
            name = 'stub'
            def search(self, query, **kwargs):
                called.append(1)
                return [Candidate(f'https://science.example/{i}', 'Title', 'Science', None, 'stub', i + 1)
                        for i in range(40)]
        self.assertEqual(retrieve('battery', [Stub()], RetrievalBudget(max_queries=0)), [])
        self.assertEqual(called, [])
        rows = retrieve('battery', [Stub()], RetrievalBudget(per_provider_results=3, max_results=2))
        self.assertEqual(len(rows), 2)

    def test_slow_provider_does_not_hold_results_past_deadline(self):
        released = threading.Event()
        class Slow:
            name = 'slow'
            def search(self, query, **kwargs):
                released.wait(1)
                return []
        started = time.monotonic()
        try:
            budget = RetrievalBudget(timeout_seconds=.03)
            self.assertEqual(retrieve('battery', [Slow()], budget), [])
            self.assertLess(time.monotonic() - started, .5)
            self.assertEqual(budget.statuses['slow']['status'], 'provider timeout')
        finally:
            released.set()

    def test_config_switch_and_invalid_inputs(self):
        providers = build_providers(RetrievalConfig(provider_order=('perplexity', 'gdelt', 'feeds')))
        self.assertEqual([p.name for p in providers], ['perplexity', 'gdelt', 'feeds'])
        for kwargs in [{'timeout_seconds': float('nan')}, {'max_queries': -1}, {'max_workers': 0},
                       {'freshness': 'forever'}, {'max_results': 9999}]:
            with self.assertRaises(ValueError): RetrievalBudget(**kwargs)
        with self.assertRaises(ValueError): retrieve('', [], RetrievalBudget())
        with self.assertRaises(ValueError): build_providers(RetrievalConfig(provider_order=('unknown',)))

    def test_cli_gdelt_and_feeds_demo_with_mocked_http_and_fetch(self):
        from dev.ingest import retrieve_cli
        items = json.loads((FIXTURES / 'feeds.json').read_text())['items']
        with tempfile.TemporaryDirectory() as directory:
            config = RetrievalConfig(gemini_key_file=Path(directory) / 'missing', state_dir=Path(directory))
            def gdelt_http(request, timeout):
                return Response((FIXTURES / 'gdelt.json').read_bytes(), request.full_url)
            providers = [GeminiGroundingProvider(config), GdeltProvider(opener=gdelt_http), FeedProvider(items)]
            output = StringIO()
            with patch.object(retrieve_cli, 'build_providers', return_value=providers), \
                    patch.object(core, '_wait_for_host'), patch.object(core, 'load_registry', return_value=[]), \
                    patch.object(retrieve_cli, 'fetch_candidate', return_value='Fictional independently fetched article.') as fetch, \
                    patch('sys.stdout', output), \
                    patch.object(socket, 'create_connection', side_effect=AssertionError('real network forbidden')):
                result = retrieve_cli.main(['battery research', '--freshness', 'any', '--fetch', '1', '--state-dir', directory])
            self.assertEqual(result, 0)
            self.assertIn('gemini: search key not configured', output.getvalue())
            self.assertIn('gdelt: ok', output.getvalue())
            self.assertIn('feeds: ok', output.getvalue())
            self.assertIn('evidence rank=1 chars=40', output.getvalue())
            self.assertNotIn('Fictional independently', output.getvalue())
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(fetch.call_args.kwargs['budget'].timeout_seconds, 90)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_gdelt_offline_preserves_feed_fallback(self):
        def offline(request, timeout): raise URLError('offline')
        feeds = FeedProvider([{'title': 'Battery research', 'url': 'https://science.example/a'}])
        budget = RetrievalBudget(freshness=None)
        with patch.object(core, '_wait_for_host'):
            rows = retrieve('battery', [GdeltProvider(opener=offline), feeds], budget)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].provider, 'feeds')
        self.assertEqual(budget.statuses['gdelt']['status'], 'provider unavailable')


if __name__ == '__main__':
    unittest.main()
