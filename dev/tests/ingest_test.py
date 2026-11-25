import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
import os
import sys
import tempfile
from threading import Event
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingest import core
from ingest.feed_api import feed_response


NOW = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
SOURCE = {
    'id': 'city-transit', 'name': 'Transit Authority', 'url': 'https://news.example.test/feed.xml',
    'format': 'rss', 'topics': ['transit'], 'enabled': False, 'robots_ok': True,
    'allow_article_fetch': True, 'minimum_interval_seconds': 1
}


def rss(title='Service changes', link='https://news.example.test/story/1', description='Weekend bus service changes.'):
    return f'''<?xml version="1.0"?><rss><channel><item><title>{title}</title><link>{link}</link>
    <description>{description}</description><pubDate>Mon, 16 Nov 2026 10:00:00 GMT</pubDate></item></channel></rss>'''.encode()


class FakeResponse:
    def __init__(self, body, url=None, headers=None):
        self.body = body
        self.url = url or SOURCE['url']
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def geturl(self):
        return self.url

    def read(self, size=-1):
        return self.body[:size]


class IngestTests(unittest.TestCase):
    def test_early_article_body_is_reused_only_in_memory_without_a_second_request(self):
        calls = []
        def fetch(url, source):
            calls.append(url)
            return 'Fictional body.'
        fetch.diagnostics = []
        url = 'https://news.example.test/article'
        cache = core.TransientArticlePrefetch([({'url': url}, SOURCE)], fetch)
        try:
            self.assertTrue(cache.has_prefetch(url))
            self.assertEqual(cache(url, SOURCE), 'Fictional body.')
            self.assertEqual(cache(url, SOURCE), 'Fictional body.')
            self.assertEqual(calls, [url])
        finally:
            cache.close()
        self.assertFalse(cache.has_prefetch(url))

    def test_ready_host_uses_worker_before_a_stronger_delayed_host(self):
        from threading import Event
        started = Event()
        calls = []
        def extract(candidate):
            calls.append(candidate['host'])
            started.set()
        queue = core.ArticleExtractionQueue([
            {'host': 'slow.test', 'topic_match': 5}, {'host': 'ready.test', 'topic_match': 1}],
            extract, max_workers=1)
        try:
            with patch.object(core, 'host_wait_seconds', side_effect=lambda host, _interval: 60 if host == 'slow.test' else 0):
                queue.poll()
            self.assertTrue(started.wait(1))
            self.assertEqual(calls, ['ready.test'])
        finally:
            queue.close()

    def test_extraction_queue_keeps_slow_host_from_blocking_another_host(self):
        from threading import Event
        release = Event()
        ready = Event()
        started = []
        def extract(candidate):
            started.append(candidate['id'])
            if candidate['host'] == 'slow.test':
                release.wait(2)
            else:
                ready.set()
            return candidate['id']
        queue = core.ArticleExtractionQueue([
            {'id': 'slow2', 'host': 'slow.test'}, {'id': 'slow1', 'host': 'slow.test', 'topic_match': 4},
            {'id': 'fast', 'host': 'fast.test'}], extract, max_workers=2)
        try:
            queue.poll()
            self.assertTrue(ready.wait(1))
            self.assertEqual(started, ['slow1', 'fast'])
            self.assertTrue(queue.pending)
        finally:
            queue.close()
            release.set()

    def test_transient_feed_reads_do_not_reuse_validators_without_a_cached_body(self):
        source = {**SOURCE, 'article_access': 'feed-full'}
        validators = {source['id']: {'etag': 'old-tag', 'last_modified': 'old-date'}}
        with patch.object(core, 'fetch_source', return_value=([], {})) as fetch:
            core._fetch_host_group([source], validators, self.store, None, NOW,
                                   time.monotonic() + 10, include_transient=True)
            self.assertNotIn('etag', fetch.call_args.args[0])
            self.assertNotIn('last_modified', fetch.call_args.args[0])
            core._fetch_host_group([source], validators, self.store, None, NOW,
                                   time.monotonic() + 10)
            self.assertEqual(fetch.call_args.args[0]['etag'], 'old-tag')
        self.assertEqual(validators[source['id']]['etag'], 'old-tag')

    def test_candidate_priority_uses_capped_same_run_article_wait(self):
        def cluster(source=None, item=None):
            return {'rows': [{'source': {**SOURCE, 'article_access': 'transient-read',
                                         'minimum_interval_seconds': 60, **(source or {})},
                              'item': {'url': 'https://news.example.test/story', **(item or {})}}]}
        with patch.object(core, '_last_request_by_host', {'news.example.test': 100}), \
             patch.object(core.time, 'monotonic', return_value=110):
            self.assertEqual(core.article_candidate_priority(cluster(), 111), 1)
            self.assertEqual(core.article_candidate_priority(cluster(), 180), 0)
            self.assertEqual(core.article_candidate_priority(cluster({'paywalled': 'unknown'}), 180), 0)
            self.assertEqual(core.article_candidate_priority(cluster({'article_access': 'feed-full'},
                                                                      {'feed_body': 'Readable evidence.'}), 140), 0)
            self.assertEqual(core.article_candidate_priority(cluster({'paywalled': True}), 180), 2)
            self.assertEqual(core._last_request_by_host, {'news.example.test': 100})

    def test_candidate_shortlist_diversifies_hosts_without_reordering_each_publisher(self):
        clusters = [{'id': key, 'rows': [{'source': SOURCE, 'item': {'url': f'https://{host}/{key}'}}]}
                    for key, host in [('a1', 'a.test'), ('a2', 'a.test'), ('b1', 'b.test'),
                                      ('a3', 'a.test'), ('c1', 'c.test'), ('b2', 'b.test')]]
        self.assertEqual([row['id'] for row in core.diverse_article_candidates(clusters, 5)],
                         ['a1', 'b1', 'c1', 'a2', 'b2'])

    def test_host_wait_estimate_is_nonblocking_and_respects_source_interval(self):
        with patch.object(core, '_last_request_by_host', {'slow.example.test': 104.0}), \
             patch.object(core.time, 'monotonic', return_value=100.0):
            self.assertEqual(core.host_wait_seconds('slow.example.test', 10), 14.0)
            self.assertEqual(core.host_wait_seconds('new.example.test', 10), 0.0)

    def setUp(self):
        core._last_request_by_host.clear()
        core._robots_cache.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.store = Path(self.temp.name) / 'items.json'

    def tearDown(self):
        self.temp.cleanup()

    def test_registry_has_faro_curated_sources_and_enables_only_robots_verified_unrestricted_entries(self):
        sources = core.load_registry(Path(__file__).resolve().parents[2] / 'content/sources-registry.json')
        self.assertGreaterEqual(len(sources), 230)
        self.assertEqual(sum(source.get('source_class') == 'explainer' for source in sources), 7)
        tier_a = [source for source in sources if source.get('tier') == 'A']
        self.assertGreaterEqual(len(tier_a), 80)
        self.assertGreaterEqual(sum(source['enabled'] for source in tier_a), 77)
        self.assertTrue(all(not any(tag.startswith(('city:', 'state:')) for tag in source.get('topics', []))
                            for source in sources))
        enabled = [source for source in sources if source['enabled']]
        self.assertTrue(enabled)
        self.assertTrue(all(source['robots_ok'] is True and not source.get('ai_bots_disallowed') for source in enabled))
        self.assertTrue(all(not source['enabled'] for source in sources if source.get('ai_bots_disallowed')))
        self.assertTrue(all(not source['enabled'] for source in sources if source['robots_ok'] != True))
        self.assertFalse(any('espn' in source['id'] for source in enabled))
        self.assertTrue(all(source.get('content_depth') in {'headline', 'snippet', 'full-text-safe'}
                            for source in sources if source.get('source_class') != 'explainer'))
        self.assertTrue(all(source.get('article_access') in {'full-text', 'transient-read', 'feed-full', 'headline'}
                            for source in sources))
        self.assertTrue(all(source.get('allow_article_fetch') == (source.get('article_access') in {'full-text', 'transient-read'})
                            for source in sources))
        self.assertTrue(next(source for source in sources if source['id'] == 'fedreg-rss-cms')['enabled'])
        self.assertEqual(next(source for source in sources if source['id'] == 'fedreg-rss-cms')['source_class'], 'official')
        self.assertEqual(next(source for source in sources if source['id'] == 'fedreg-rss-cms')['article_access'], 'full-text')
        self.assertTrue(next(source for source in sources if source['id'] == 'cms-newsroom')['enabled'])
        self.assertEqual(next(source for source in sources if source['id'] == 'cms-newsroom')['source_tier'], 'primary')
        registry_doc = json.loads((Path(__file__).resolve().parents[2] / 'content/sources-registry.json').read_text())
        watched = {row['id'] for row in registry_doc.get('watched_pages', [])}
        self.assertEqual(len(watched), 6)
        self.assertIn('fdic-national-rates', watched)
        self.assertEqual(len(watched), 6)
        for skipped in ('thepointsguy-deals', 'frankfurter', 'wikipedia-feed', 'newscientist'):
            self.assertFalse(next(source for source in sources if source['id'] == skipped)['enabled'])
        probation = [source for source in sources if source.get('tier') == 'probation']
        self.assertEqual({source['id'] for source in probation}, {'bpc-health'})

    def test_profile_local_source_id_resolves_through_external_path(self):
        example = Path(__file__).resolve().parents[2] / 'content/local-sources.example.json'
        ids = ['fictional-asterwick-feed', 'fictional-velmora-feed', 'fictional-dunlune-feed']
        default_ids = {source['id'] for source in core.load_registry()}
        self.assertTrue(default_ids.isdisjoint(ids))
        with patch.dict(os.environ, {'BRIEFING_LOCAL_SOURCES': str(example)}):
            selected = core.load_registry(local_source_ids=ids)
        self.assertTrue(set(ids) <= {source['id'] for source in selected})
        self.assertEqual(len(selected), len(default_ids) + len(ids))
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            (data_dir / 'local-sources.json').write_text(example.read_text())
            with patch.dict(os.environ, {'BRIEFING_LOCAL_SOURCES': '', 'BRIEFING_DATA_DIR': str(data_dir)}):
                selected = core.load_registry(local_source_ids=[ids[0]])
            self.assertIn(ids[0], {source['id'] for source in selected})
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / 'missing-local-sources.json'
            with patch.dict(os.environ, {'BRIEFING_LOCAL_SOURCES': str(missing)}):
                self.assertEqual(core.load_registry(local_source_ids=[ids[0]]), core.load_registry())
            with patch.dict(os.environ, {'BRIEFING_LOCAL_SOURCES': '', 'BRIEFING_DATA_DIR': '',
                                         'HOME': temp}):
                self.assertEqual(core.load_registry(local_source_ids=[ids[0]]), core.load_registry())
        with self.assertRaisesRegex(ValueError, 'Unknown optional local source'):
            core.load_registry(local_source_ids=['not-a-local-source'], local_sources_path=example)

    def test_missing_external_local_sources_file_is_harmless(self):
        missing = Path(self.temp.name) / 'missing-local-sources.json'
        default = core.load_registry()
        selected = core.load_registry(local_source_ids=['fictional-asterwick-feed'], local_sources_path=missing)
        self.assertEqual(selected, default)

    def test_registry_rejects_non_https_sources(self):
        path = Path(self.temp.name) / 'registry.json'
        path.write_text(json.dumps({'version': 1, 'sources': [{**SOURCE, 'url': 'http://news.example.test/feed'}]}))
        with self.assertRaises(ValueError):
            core.load_registry(path)
        for url in ('https://user:pass@news.example.test/feed', 'https://news.example.test:8443/feed'):
            path.write_text(json.dumps({'version': 1, 'sources': [{**SOURCE, 'url': url}]}))
            with self.assertRaises(ValueError):
                core.load_registry(path)

    def test_fetch_boundary_rejects_unapproved_host_credentials_ports_and_private_dns(self):
        public = lambda host, port, type: [(core.socket.AF_INET, type, 6, '', ('8.8.8.8', port))]
        private = lambda host, port, type: [(core.socket.AF_INET, type, 6, '', ('10.0.0.5', port))]
        for url in ('http://feed.example.test/rss', 'https://user@feed.example.test/rss',
                    'https://feed.example.test:8443/rss', 'https://other.example.test/rss'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                core._public_addresses(url, {'feed.example.test'}, resolver=public)
        with self.assertRaisesRegex(ValueError, 'non-public'):
            core._public_addresses('https://feed.example.test/rss', {'feed.example.test'}, resolver=private)
        mixed = lambda host, port, type: [
            (core.socket.AF_INET, type, 6, '', ('8.8.8.8', port)),
            (core.socket.AF_INET, type, 6, '', ('169.254.169.254', port)),
        ]
        with self.assertRaisesRegex(ValueError, 'non-public'):
            core._public_addresses('https://feed.example.test/rss', {'feed.example.test'}, resolver=mixed)
        for url in ('file:///etc/passwd', 'gopher://127.0.0.1/', 'https://[::1]/',
                    'https://[fd00::1]/', 'https://169.254.169.254/latest/meta-data/'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                core._public_addresses(url, {'feed.example.test'}, resolver=mixed)

    def test_redirect_is_revalidated_before_any_connection_to_new_host(self):
        class Reply:
            status = 302
            reason = 'Found'
            headers = {'Location': 'https://127.0.0.1/private'}
            def read(self, *_args): return b''
        calls = []
        class Connection:
            def __init__(self, host, address, timeout): calls.append((host, address))
            def request(self, *_args, **_kwargs): pass
            def getresponse(self): return Reply()
            def close(self): pass
        resolver = lambda host, port, type: [(core.socket.AF_INET, type, 6, '', ('8.8.8.8', port))]
        with self.assertRaises(ValueError):
            core._safe_https_open(Request('https://feed.example.test/rss'), {'feed.example.test'}, 1,
                                  resolver=resolver, connection_factory=Connection)
        self.assertEqual(calls, [('feed.example.test', '8.8.8.8')])

    def test_redirect_to_metadata_or_non_http_scheme_is_blocked_before_reconnect(self):
        for destination in ('http://169.254.169.254/latest/meta-data/',
                            'https://169.254.169.254/latest/meta-data/', 'file:///etc/passwd'):
            with self.subTest(destination=destination):
                class Reply:
                    status = 302
                    reason = 'Found'
                    headers = {'Location': destination}
                    def read(self, *_args):
                        raise AssertionError('redirect bodies must be discarded by closing the socket')
                calls = []

                class Connection:
                    def __init__(self, host, address, timeout):
                        calls.append((host, address))
                    def request(self, *_args, **_kwargs): pass
                    def getresponse(self): return Reply()
                    def close(self): pass

                resolver = lambda host, port, type: [(core.socket.AF_INET, type, 6, '', ('8.8.8.8', port))]
                with self.assertRaises(ValueError):
                    core._safe_https_open(Request('https://feed.example.test/rss'),
                                          {'feed.example.test'}, 1, resolver=resolver,
                                          connection_factory=Connection)
                self.assertEqual(calls, [('feed.example.test', '8.8.8.8')])

    def test_dns_rebinding_between_redirect_hops_never_connects_to_private_answer(self):
        resolver_calls = []
        connections = []

        def resolver(host, port, type):
            resolver_calls.append(host)
            address = '8.8.8.8' if len(resolver_calls) == 1 else '127.0.0.1'
            return [(core.socket.AF_INET, type, 6, '', (address, port))]

        class Reply:
            status = 302
            reason = 'Found'
            headers = {'Location': '/next'}
            def read(self, *_args): raise AssertionError('redirect body must not be read')
        class Connection:
            def __init__(self, host, address, _timeout): connections.append((host, address))
            def request(self, *_args, **_kwargs): pass
            def getresponse(self): return Reply()
            def close(self): pass

        with self.assertRaisesRegex(ValueError, 'non-public'):
            core._safe_https_open(Request('https://feed.example.test/start'),
                                  {'feed.example.test'}, 1, resolver=resolver,
                                  connection_factory=Connection)
        self.assertEqual(resolver_calls, ['feed.example.test', 'feed.example.test'])
        self.assertEqual(connections, [('feed.example.test', '8.8.8.8')])

    def test_slow_response_is_closed_at_absolute_request_deadline(self):
        closed = Event()

        class SlowReply:
            status = 200
            reason = 'OK'
            headers = {}
            def read(self, _amount):
                if not closed.wait(1):
                    raise AssertionError('slow response socket was not closed')
                raise OSError('connection closed at deadline')
            def close(self): pass

        class SlowConnection:
            def __init__(self, _host, _address, _timeout): pass
            def request(self, *_args, **_kwargs): pass
            def getresponse(self): return SlowReply()
            def close(self): closed.set()

        resolver = lambda host, port, type: [(core.socket.AF_INET, type, 6, '', ('8.8.8.8', port))]
        started = time.monotonic()
        response = core._safe_https_open(Request('https://feed.example.test/feed'),
                                         {'feed.example.test'}, 0.05, resolver=resolver,
                                         connection_factory=SlowConnection)
        with self.assertRaisesRegex(OSError, 'deadline'):
            response.read(100)
        response.close()
        self.assertLess(time.monotonic() - started, 0.5)

    def test_rss_and_atom_parsing(self):
        items = core.parse_source(rss(), SOURCE, NOW)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['title'], 'Service changes')
        self.assertEqual(items[0]['fetched_at'], '2026-11-16T12:00:00Z')
        self.assertEqual(items[0]['published'], '2026-11-16T10:00:00Z')
        self.assertIsNone(items[0]['updated'])
        self.assertEqual(items[0]['source_id'], SOURCE['id'])
        self.assertEqual(items[0]['original_url'], items[0]['final_url'])
        self.assertEqual(items[0]['rights_policy_version'], core.RIGHTS_POLICY_VERSION)
        self.assertEqual(len(items[0]['content_hash']), 64)
        self.assertEqual(set(items[0]), {'source_id', 'title', 'url', 'original_url', 'final_url', 'published',
                                         'updated', 'snippet', 'fetched_at', 'first_seen',
                                         'rights_policy_version', 'content_hash', 'topics'})
        self.assertLessEqual(len(items[0]['snippet']), 280)
        atom = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Rail notice</title>
        <link href="https://news.example.test/rail"/><summary>Station service changes</summary>
        <updated>2026-11-16T09:00:00Z</updated></entry></feed>'''
        atom_source = {**SOURCE, 'format': 'atom'}
        parsed = core.parse_source(atom, atom_source, NOW)
        self.assertEqual(parsed[0]['url'], 'https://news.example.test/rail')
        self.assertEqual(parsed[0]['snippet'], 'Station service changes')

    def test_publisher_feed_html_titles_and_malformed_anchor_links_are_sanitized(self):
        cms = {**SOURCE, 'id': 'cms-newsroom', 'name': 'CMS',
               'url': 'https://www.cms.gov/newsroom/rss-feeds'}
        payload = b'''<rss><channel><item>
          <title>&lt;strong&gt;Coverage rule update&lt;/strong&gt; for fictional readers</title>
          <link>&amp;lt;a href=&amp;quot;/newsroom/updates/coverage&amp;quot;&amp;gt;Read more&amp;lt;/a&amp;gt;</link>
          <description>Fictional notice.</description>
          <pubDate>Mon, 16 Nov 2026 10:00:00 GMT</pubDate></item></channel></rss>'''
        item = core.parse_source(payload, cms, NOW)[0]
        self.assertEqual(item['title'], 'Coverage rule update for fictional readers')
        self.assertEqual(item['url'], 'https://www.cms.gov/newsroom/updates/coverage')
        fierce = {**cms, 'id': 'fierce-healthcare', 'url': 'https://www.fiercehealthcare.com/rss/xml'}
        fierce_item = core.parse_source(rss('&lt;b&gt;Payer policy&lt;/b&gt; update',
            link='&amp;lt;a href=&amp;quot;/news/fictional-rule&amp;quot;&amp;gt;Read more&amp;lt;/a&amp;gt;'),
            fierce, NOW)[0]
        self.assertEqual(fierce_item['title'], 'Payer policy update')
        self.assertEqual(fierce_item['url'], 'https://www.fiercehealthcare.com/news/fictional-rule')

    def test_stat_plus_markers_and_broad_feed_keyword_filters_run_before_storage(self):
        stat = {**SOURCE, 'id': 'statnews', 'item_exclude_markers': ['STAT+']}
        payload = b'''<rss><channel>
          <item><title>STAT+ exclusive policy report</title><link>https://news.example.test/plus</link></item>
          <item><title>Public health policy report</title><link>https://news.example.test/open</link></item>
        </channel></rss>'''
        self.assertEqual([row['title'] for row in core.parse_source(payload, stat, NOW)],
                         ['Public health policy report'])
        firehose = {**SOURCE, 'item_keyword_filter': ['semiconductor', 'chip design']}
        broad = b'''<rss><channel>
          <item><title>New accelerator chip design</title><link>https://news.example.test/chip</link></item>
          <item><title>General model research</title><link>https://news.example.test/model</link></item>
        </channel></rss>'''
        self.assertEqual([row['title'] for row in core.parse_source(broad, firehose, NOW)],
                         ['New accelerator chip design'])

    def test_feed_full_content_is_transient_and_snippets_are_sanitized(self):
        paragraphs = ' '.join(f'<p>Verified feed detail number {index} with housing facts &amp; policy context.</p>'
                              for index in range(1, 30))
        payload = f'''<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><item>
          <title>Housing update</title><link>https://news.example.test/housing</link>
          <description>&lt;p&gt;Short summary &amp;amp; details&lt;/p&gt;</description>
          <content:encoded><![CDATA[{paragraphs}]]></content:encoded>
          <pubDate>Mon, 16 Nov 2026 10:00:00 GMT</pubDate></item></channel></rss>'''.encode()
        source = {**SOURCE, 'article_access': 'feed-full'}
        item = core.parse_source(payload, source, NOW)[0]
        self.assertIn('feed_body', item)
        self.assertEqual(item['feed_body_method'], 'rss_content_encoded')
        self.assertGreater(len(item['feed_body']), 1200)
        self.assertLessEqual(len(item['snippet']), 280)
        self.assertNotIn('<p>', item['feed_body'])
        self.assertNotIn('&amp;', item['feed_body'])
        saved = core.merge_items([], [item], NOW)
        self.assertNotIn('feed_body', saved[0])
        self.assertNotIn('feed_body_method', saved[0])

    def test_rss_body_and_method_are_returned_only_in_transient_ingest_payload(self):
        body = (' '.join(f'<p>Fictional source detail {index} describes local service data and review results.</p>'
                         for index in range(1, 15)))
        payload = f'''<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><item>
          <title>Fictional source report</title><link>https://news.example.test/report</link>
          <description>Short fictional summary.</description>
          <content:encoded><![CDATA[{body}]]></content:encoded></item></channel></rss>'''.encode()
        source = {**SOURCE, 'enabled': True, 'article_access': 'feed-full'}
        def opener(request, timeout):
            if request.full_url.endswith('/robots.txt'):
                return FakeResponse(b'User-agent: *\nAllow: /', url=request.full_url)
            return FakeResponse(payload, url=request.full_url)
        result = core.ingest_enabled([source], self.store, opener=opener, now=NOW,
                                     budget_seconds=2, include_transient=True)
        identity = ('city-transit', 'https://news.example.test/report')
        self.assertIn(identity, result['transient_feed_bodies'])
        self.assertEqual(result['transient_feed_methods'][identity], 'rss_content_encoded')
        self.assertNotIn('feed_body', json.loads(self.store.read_text())['items'][0])
        self.assertNotIn('transient_feed_bodies', result['source_results'])

    def test_clean_text_removes_markup_and_decodes_entities(self):
        self.assertEqual(core._clean_text('<p>Zero trust <span class="field">design</span> &amp; safety</p>'),
                         'Zero trust design & safety')

    def test_registry_housing_keyword_filter_drops_unrelated_tv_firehose_items(self):
        source = {**SOURCE, 'item_keyword_filter': ['housing', 'mortgage', 'zoning', 'permit']}
        body = b'''<rss><channel>
          <item><title>Storm warning expands east</title><link>https://news.example.test/weather</link><description>Heavy rain expected.</description><pubDate>Mon, 16 Nov 2026 10:00:00 GMT</pubDate></item>
          <item><title>City advances housing rezoning plan</title><link>https://news.example.test/housing</link><description>Planning commissioners reviewed a housing development proposal.</description><pubDate>Mon, 16 Nov 2026 10:00:00 GMT</pubDate></item>
        </channel></rss>'''
        parsed = core.parse_source(body, source, NOW)
        self.assertEqual([item['title'] for item in parsed], ['City advances housing rezoning plan'])

    def test_html_article_parsing(self):
        source = {**SOURCE, 'format': 'html'}
        body = b'''<html><head><title>Transit updates</title><meta name="description" content="Latest notices"></head>
        <body><article><h2>Weekend routes</h2><p>Two routes change this weekend.</p>
        <a href="/notices/weekend">Read notice</a></article></body></html>'''
        parsed = core.parse_source(body, source, NOW)
        self.assertEqual(parsed[0]['title'], 'Weekend routes')
        self.assertEqual(parsed[0]['url'], 'https://news.example.test/notices/weekend')

    def test_json_feed_parsing(self):
        source = {**SOURCE, 'format': 'json'}
        body = json.dumps({'version': 'https://jsonfeed.org/version/1', 'items': [{
            'id': 'one', 'url': 'https://news.example.test/story/json', 'title': 'JSON feed story',
            'summary': 'A brief feed summary.', 'date_published': '2026-11-16T08:00:00Z', 'tags': ['transit']
        }]}).encode()
        parsed = core.parse_source(body, source, NOW)
        self.assertEqual(parsed[0]['title'], 'JSON feed story')
        self.assertEqual(parsed[0]['topics'], ['transit'])

    def test_json_api_adapters_parse_editorial_records_and_ignore_rate_tables(self):
        federal = {**SOURCE, 'format': 'json', 'json_adapter': 'federal_register'}
        body = json.dumps({'results': [{'title': 'Rule notice', 'html_url': 'https://news.example.test/rule',
                                        'abstract': 'A fictional rule summary.', 'publication_date': '2026-11-16'}]}).encode()
        parsed = core.parse_source(body, federal, NOW)
        self.assertEqual(parsed[0]['title'], 'Rule notice')
        self.assertEqual(parsed[0]['snippet'], 'A fictional rule summary.')
        openfda = {**SOURCE, 'format': 'json', 'json_adapter': 'openfda'}
        record = {'results': [{'product_description': 'Example tablets', 'reason_for_recall': 'Label mix-up', 'report_date': '20261116'}]}
        fda_items = core.parse_source(json.dumps(record).encode(), openfda, NOW)
        self.assertIn('Example tablets', fda_items[0]['title'])
        rates = {**SOURCE, 'format': 'json', 'json_adapter': 'rate_table'}
        self.assertEqual(core.parse_source(b'{"rates":{"USD":1}}', rates, NOW), [])

    def test_snippet_cap_and_store_whitelist(self):
        item = core.parse_source(rss(description='x' * 700), SOURCE, NOW)[0]
        item['article_body'] = 'Transient article body must not persist.'
        merged = core.merge_items([], [item], NOW)
        self.assertLessEqual(len(merged[0]['snippet']), 280)
        self.assertEqual(set(merged[0]), {'source_id', 'title', 'url', 'original_url', 'final_url', 'published',
                                         'updated', 'snippet', 'fetched_at', 'first_seen',
                                         'rights_policy_version', 'content_hash', 'topics'})

    def test_probation_sources_keep_headlines_but_drop_feed_summaries(self):
        probation = {**SOURCE, 'tier': 'probation'}
        item = core.parse_source(rss(description='A detailed fictional feed summary.'), probation, NOW)[0]
        self.assertEqual(item['title'], 'Service changes')
        self.assertEqual(item['snippet'], '')

    def test_publication_update_fetch_and_first_seen_dates_stay_distinct(self):
        body = b'<rss><channel><item><title>Unknown date story</title><link>https://news.example.test/unknown</link><description>Fictional.</description></item></channel></rss>'
        unknown = core.parse_source(body, SOURCE, NOW)[0]
        self.assertIsNone(unknown['published'])
        self.assertIsNone(unknown['updated'])
        self.assertEqual(unknown['fetched_at'], '2026-11-16T12:00:00Z')
        self.assertEqual(unknown['first_seen'], '2026-11-16T12:00:00Z')
        updated_body = b'<rss><channel><item><title>Updated story</title><link>https://news.example.test/updated</link><description>Fictional.</description><pubDate>Mon, 16 Nov 2026 10:00:00 GMT</pubDate><updated>2026-11-16T11:30:00Z</updated></item></channel></rss>'
        item = core.parse_source(updated_body, SOURCE, NOW)[0]
        self.assertEqual(item['published'], '2026-11-16T10:00:00Z')
        self.assertEqual(item['updated'], '2026-11-16T11:30:00Z')

    def test_robots_disallow_prevents_feed_fetch(self):
        calls = []
        def opener(request, timeout):
            calls.append(request.full_url)
            return FakeResponse(b'User-agent: *\nDisallow: /feed.xml\n', url='https://news.example.test/robots.txt')
        with self.assertRaises(PermissionError):
            core.fetch_source({**SOURCE, 'enabled': True}, opener=opener, now=NOW,
                              robots_cache_path=Path(self.temp.name) / 'robots.json')
        self.assertEqual(calls, ['https://news.example.test/robots.txt'])

    def test_robots_cache_keeps_rules_and_rechecks_after_seven_days(self):
        calls = []
        policy = [b'User-agent: *\nDisallow: /private\n']
        def opener(request, timeout):
            calls.append(request.full_url)
            return FakeResponse(policy[0], url=request.full_url)
        cache = Path(self.temp.name) / 'robots.json'
        self.assertTrue(core._robots_policy('https://news.example.test/feed', opener, cache, NOW)[0])
        self.assertFalse(core._robots_policy('https://news.example.test/private/page', opener, cache, NOW)[0])
        self.assertEqual(len(calls), 1)
        core._robots_cache.clear()
        self.assertFalse(core._robots_policy('https://news.example.test/private/page', opener, cache, NOW + timedelta(days=6))[0])
        policy[0] = b'User-agent: *\nAllow: /\n'
        self.assertTrue(core._robots_policy('https://news.example.test/private/page', opener, cache, NOW + timedelta(days=8))[0])
        self.assertEqual(len(calls), 2)
        saved = json.loads(cache.read_text())['news.example.test']
        self.assertEqual(saved['checked_at'], '2026-11-24T12:00:00Z')

    def test_robots_ai_crawler_disallow_is_treated_as_blocked(self):
        rules = b'User-agent: GPTBot\nDisallow: /\nUser-agent: *\nAllow: /\n'
        opener = lambda request, timeout: FakeResponse(rules, 'https://news.example.test/robots.txt')
        allowed, _delay = core._robots_policy('https://news.example.test/story/1', opener,
            cache_path=self.store.with_name('ai-robots.json'), now=NOW,
            approved_hosts={'news.example.test'})
        self.assertFalse(allowed)

    def test_robots_fetch_forbidden_by_server_stays_fail_closed_in_cache(self):
        calls = []
        cache = Path(self.temp.name) / 'robots.json'
        def forbidden(request, timeout):
            calls.append(request.full_url)
            raise core.HTTPError(request.full_url, 403, 'forbidden', {}, None)
        self.assertFalse(core._robots_policy(SOURCE['url'], forbidden, cache, NOW)[0])
        core._robots_cache.clear()
        self.assertFalse(core._robots_policy(SOURCE['url'], forbidden, cache, NOW + timedelta(days=1))[0])
        self.assertEqual(len(calls), 1)

    def test_transient_article_extraction_and_paywall_skip(self):
        source = {**SOURCE, 'allowed_domains': ['news.example.test'], 'article_access': 'transient-read'}
        article = b'<main><h1>Service notice</h1><p>Routes change next week.</p></main>'
        def opener(request, timeout):
            if request.full_url.endswith('/robots.txt'):
                return FakeResponse(b'User-agent: *\nAllow: /', url=request.full_url)
            return FakeResponse(article, url=request.full_url, headers={'Content-Type': 'text/html'})
        self.assertEqual(core.fetch_article_text('https://news.example.test/story/1', source, opener,
                                                 Path(self.temp.name) / 'robots.json'),
                         'Service notice\nRoutes change next week.')
        paywall = b'<article><h1>Subscribe to continue</h1><p>Paywall</p></article>'
        def paywall_opener(request, timeout):
            if request.full_url.endswith('/robots.txt'):
                return FakeResponse(b'User-agent: *\nAllow: /', url=request.full_url)
            return FakeResponse(paywall, url=request.full_url, headers={'Content-Type': 'text/html'})
        core._robots_cache.clear()
        self.assertIsNone(core.fetch_article_text('https://news.example.test/story/2', source, paywall_opener,
                                                  Path(self.temp.name) / 'robots.json'))

    def test_article_body_containers_exclude_navigation_hidden_text_and_footer(self):
        for container in ('class="post-body"', 'class="dal-main-content"', 'itemprop="articleBody"'):
            parser = core._ArticleTextParser()
            parser.feed(f'''<div><p>Unrelated teaser.</p><div {container}>
                <nav><p>Menu links.</p></nav><h1>Fictional notice</h1>
                <p>The office <strong>announced</strong> new service.</p>
                <div><p>Work starts next week.</p></div>
                <p hidden>Hidden detail.</p><p style="display: none">Hidden offer.</p>
                <p>Public <span aria-hidden="true">secret</span> information.</p>
                <script>False data.</script><footer><p>Subscribe to updates.</p></footer>
                </div><p>Unrelated footer.</p></div>''')
            self.assertEqual(parser.parts, ['Fictional notice', 'The office announced new service.',
                                            'Work starts next week.', 'Public information.'])

    def test_paywall_marker_in_article_body_container_still_rejects_evidence(self):
        source = {**SOURCE, 'allowed_domains': ['news.example.test'], 'article_access': 'transient-read'}
        def opener(request, timeout):
            body = (b'User-agent: *\nAllow: /' if request.full_url.endswith('/robots.txt') else
                    b'<div class="post-body"><p>Subscribe to continue</p></div>')
            return FakeResponse(body, url=request.full_url)
        with patch.object(core, '_wait_for_host'):
            self.assertIsNone(core.fetch_article_text('https://news.example.test/locked', source, opener,
                                                      Path(self.temp.name) / 'robots.json'))

    def test_unknown_paywall_allows_transient_read_until_page_check_fails(self):
        source = {**SOURCE, 'allowed_domains': ['news.example.test'], 'article_access': 'transient-read',
                  'paywalled': 'unknown'}
        outcomes = []
        article = b'<main><h1>Notice</h1><p>Full article text available to readers.</p></main>'
        def opener(request, timeout):
            if request.full_url.endswith('/robots.txt'):
                return FakeResponse(b'User-agent: *\nAllow: /', url=request.full_url)
            return FakeResponse(article, url=request.full_url, headers={'Content-Type': 'text/html'})
        text = core.fetch_article_text('https://news.example.test/story/3', source, opener,
                                       Path(self.temp.name) / 'robots2.json', diagnostics=outcomes)
        self.assertIn('Full article text', text)
        self.assertEqual(outcomes[-1]['reason'], 'ok')

    def test_fetch_uses_polite_headers_and_parses_with_stubbed_transport(self):
        seen = []
        def opener(request, timeout):
            if request.full_url.endswith('/robots.txt'):
                return FakeResponse(b'User-agent: *\nAllow: /', url=request.full_url)
            seen.append((request, timeout))
            return FakeResponse(rss(), headers={'ETag': 'fictional-tag', 'Last-Modified': 'Mon, 16 Nov 2026 10:00:00 GMT'})
        items, metadata = core.fetch_source({**SOURCE, 'enabled': True}, opener=opener, now=NOW,
                                            robots_cache_path=Path(self.temp.name) / 'robots.json')
        self.assertEqual(len(items), 1)
        self.assertEqual(metadata['etag'], 'fictional-tag')
        request, timeout = seen[0]
        self.assertEqual(request.get_header('User-agent'), core.USER_AGENT)
        self.assertEqual(timeout, core.TIMEOUT_SECONDS)

    def test_cold_robots_check_does_not_consume_sixty_second_feed_slot(self):
        source = {**SOURCE, 'enabled': True, 'minimum_interval_seconds': 60}
        calls = []
        def opener(request, timeout):
            calls.append(request.full_url)
            if request.full_url.endswith('/robots.txt'):
                return FakeResponse(b'User-agent: *\nAllow: /', url=request.full_url)
            return FakeResponse(rss(link='https://news.example.test/story/cold'), url=request.full_url)
        start = time.monotonic()
        items, _metadata = core.fetch_source(source, opener=opener, now=NOW,
            robots_cache_path=Path(self.temp.name) / 'cold-robots.json', deadline=start + 60)
        self.assertEqual(len(items), 1)
        self.assertEqual(calls, ['https://news.example.test/robots.txt', SOURCE['url']])

    def test_host_group_obeys_interval_between_two_feeds_on_same_host(self):
        host = 'news.example.test'
        sources = [
            {**SOURCE, 'id': f'feed-{index}', 'url': f'https://{host}/feed-{index}.xml',
             'enabled': True, 'minimum_interval_seconds': 2}
            for index in (1, 2)
        ]
        clock = [100.0]
        feed_starts = []
        def opener(request, timeout):
            if request.full_url.endswith('/robots.txt'):
                return FakeResponse(b'User-agent: *\nAllow: /', url=request.full_url)
            feed_starts.append(clock[0])
            return FakeResponse(rss(title=request.full_url.rsplit('/', 1)[-1],
                                    link=request.full_url.replace('/feed-', '/story/')),
                                url=request.full_url)
        with patch.object(core.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(core.time, 'sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)):
            result = core._fetch_host_group(sources, {}, Path(self.temp.name) / 'shared-robots.json',
                                            opener, NOW, deadline=110.0)
        self.assertEqual([row[3] for row in result], [None, None])
        self.assertEqual(feed_starts, [100.0, 102.0])

    def test_nine_same_host_feeds_fit_budget_with_capped_gap_and_robot_delay(self):
        host = 'federal.example.test'
        sources = [
            {**SOURCE, 'id': f'feed-{index}', 'url': f'https://{host}/feed-{index}.xml',
             'allowed_domains': [host], 'enabled': True, 'minimum_interval_seconds': 60}
            for index in range(9)
        ]
        clock = [100.0]
        feed_starts = []
        def opener(request, timeout):
            if request.full_url.endswith('/robots.txt'):
                return FakeResponse(b'User-agent: *\nAllow: /\nCrawl-delay: 5', url=request.full_url)
            feed_starts.append(clock[0])
            return FakeResponse(rss(title=request.full_url.rsplit('/', 1)[-1],
                                    link=request.full_url.replace('/feed-', '/story/')),
                                url=request.full_url)
        with patch.object(core.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(core.time, 'sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)):
            results = core._fetch_host_group(sources, {}, Path(self.temp.name) / 'nine-robots.json',
                                             opener, NOW, deadline=160.0)
        self.assertEqual(len(results), 9)
        self.assertTrue(all(result[3] is None and len(result[1]) == 1 for result in results))
        self.assertEqual(feed_starts, [100.0 + index * 5 for index in range(9)])
        self.assertTrue(all(later - earlier >= 3 for earlier, later in zip(feed_starts, feed_starts[1:])))

    def test_ingest_cold_robots_fetches_twenty_distinct_hosts(self):
        sources = []
        for index in range(20):
            host = f'feed-{index}.example.test'
            sources.append({**SOURCE, 'id': f'feed-{index}', 'name': f'Feed {index}',
                            'url': f'https://{host}/feed.xml', 'allowed_domains': [host],
                            'enabled': True, 'robots_ok': True, 'minimum_interval_seconds': 60})
        calls = []
        def opener(request, timeout):
            calls.append(request.full_url)
            if request.full_url.endswith('/robots.txt'):
                return FakeResponse(b'User-agent: *\nAllow: /', url=request.full_url)
            host = request.full_url.split('/')[2]
            link = f'https://{host}/story/1'
            body = rss(title=f'Fictional update from {host}', link=link)
            return FakeResponse(body, url=request.full_url)
        result = core.ingest_enabled(sources, self.store, opener=opener, now=NOW,
                                     budget_seconds=60, max_workers=8)
        self.assertEqual(result['enabled_sources'], 20)
        self.assertGreaterEqual(result['fetched_sources'], 19)
        self.assertEqual(result['timed_out_sources'], 0)
        self.assertEqual(sum(url.endswith('/robots.txt') for url in calls), 20)

    def test_disabled_sources_never_call_fetcher_and_dry_run_counts(self):
        def forbidden(*_args, **_kwargs):
            self.fail('disabled source attempted a network fetch')
        result = core.ingest_enabled([{**SOURCE, 'enabled': False}], self.store, opener=forbidden, now=NOW)
        self.assertEqual(result, {'enabled_sources': 0, 'fetched_sources': 0, 'not_modified': 0,
                                  'errors': 0, 'timed_out_sources': 0, 'host_groups': 0,
                                  'parsed_items': 0, 'stored_items': 0, 'source_results': {},
                                  'source_error_types': {}, 'source_stats': {},
                                  'elapsed_seconds': result['elapsed_seconds']})
        self.assertEqual(json.loads(self.store.read_text())['items'], [])

    def test_unexpected_runtime_error_isolated_to_one_feed_host(self):
        source = {**SOURCE, 'id': 'runtime-feed', 'enabled': True, 'robots_ok': True}
        with patch.object(core, 'fetch_source', side_effect=RuntimeError('private upstream details')):
            result = core.ingest_enabled([source], self.store, now=NOW, budget_seconds=2)
        self.assertEqual(result['errors'], 1)
        self.assertEqual(result['source_results']['runtime-feed'], 'error')
        self.assertEqual(result['source_error_types']['runtime-feed'], 'RuntimeError')
        self.assertNotIn('private upstream details', json.dumps(result))

    def test_fetch_worker_ceiling_fits_preview_tasks_max(self):
        self.assertLessEqual(core.FETCH_MAX_WORKERS, 8)

    def test_thread_start_failure_cancels_queue_and_joins_started_workers(self):
        class Executor:
            def __init__(self):
                self.shutdown_args = None
            def submit(self, *_args):
                raise RuntimeError("can't start new thread")
            def shutdown(self, **kwargs):
                self.shutdown_args = kwargs
        executor = Executor()
        source = {**SOURCE, 'id': 'thread-cap-feed', 'enabled': True, 'robots_ok': True}
        with patch.object(core, 'ThreadPoolExecutor', return_value=executor):
            with self.assertRaisesRegex(RuntimeError, 'feed_worker_capacity'):
                core.ingest_enabled([source], self.store, now=NOW, budget_seconds=2)
        self.assertEqual(executor.shutdown_args, {'wait': True, 'cancel_futures': True})

    def test_distinct_hosts_fetch_concurrently_with_host_safe_store_and_counts(self):
        sources = [{**SOURCE, 'id': f'publisher-{index}', 'name': f'Publisher {index}',
                    'url': f'https://publisher-{index}.example.test/feed.xml', 'enabled': True}
                   for index in range(3)]
        active = 0
        maximum_active = 0
        guard = __import__('threading').Lock()
        def opener(request, timeout):
            nonlocal active, maximum_active
            if request.full_url.endswith('/robots.txt'):
                return FakeResponse(b'User-agent: *\nAllow: /', url=request.full_url)
            with guard:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.12)
            with guard:
                active -= 1
            return FakeResponse(rss(title=request.full_url, link=f"{request.full_url.rsplit('/', 1)[0]}/story"),
                                url=request.full_url)
        result = core.ingest_enabled(sources, self.store, opener=opener, now=NOW, budget_seconds=2)
        self.assertGreaterEqual(maximum_active, 2)
        self.assertEqual(result['host_groups'], 3)
        self.assertEqual(result['fetched_sources'], 3)
        self.assertEqual(result['parsed_items'], 3)
        self.assertEqual(set(result['source_results'].values()), {'ok'})
        self.assertEqual(set(result['source_stats']), {row['id'] for row in sources})
        self.assertTrue(all(row['new_item_count'] == 1 for row in result['source_stats'].values()))

    def test_feed_budget_timeout_is_counted_without_fetch(self):
        source = {**SOURCE, 'enabled': True}
        def opener(request, timeout):
            if request.full_url.endswith('/robots.txt'):
                return FakeResponse(b'User-agent: *\nAllow: /', url=request.full_url)
            time.sleep(0.12)
            return FakeResponse(rss(), url=request.full_url)
        result = core.ingest_enabled([source], self.store, opener=opener, now=NOW, budget_seconds=0.00001)
        self.assertEqual(result['fetched_sources'], 0)
        self.assertEqual(result['timed_out_sources'], 1)
        self.assertEqual(result['source_results'], {SOURCE['id']: 'timeout'})

    def test_enabled_source_is_fetched_once_and_results_are_deduplicated(self):
        calls = []
        def opener(request, timeout):
            if request.full_url.endswith('/robots.txt'):
                return FakeResponse(b'User-agent: *\nAllow: /', url=request.full_url)
            calls.append(request)
            return FakeResponse(rss(link='https://news.example.test/story/1#section'), headers={'ETag': 'tag'})
        result = core.ingest_enabled([{**SOURCE, 'enabled': True}], self.store, opener=opener, now=NOW)
        self.assertEqual((len(calls), result['fetched_sources'], result['stored_items']), (1, 1, 1))
        saved = json.loads(self.store.read_text())
        self.assertEqual(core.load_source_validators(self.store)['city-transit']['etag'], 'tag')
        self.assertEqual(saved['items'][0]['url'], 'https://news.example.test/story/1')

    def test_validator_cache_is_sent_on_next_invocation(self):
        core.save_store([], self.store, NOW, sources={'city-transit': {'etag': 'cached-tag'}})
        calls = []
        def opener(request, timeout):
            if request.full_url.endswith('/robots.txt'):
                return FakeResponse(b'User-agent: *\nAllow: /', url=request.full_url)
            calls.append(request)
            return FakeResponse(rss())
        core.ingest_enabled([{**SOURCE, 'enabled': True}], self.store, opener=opener, now=NOW)
        self.assertEqual(calls[0].get_header('If-none-match'), 'cached-tag')

    def test_dedupe_canonicalizes_links_and_90_day_retention_keeps_the_fallback_window(self):
        fresh = core.parse_source(rss(link='https://news.example.test/story/1#fragment'), SOURCE, NOW)[0]
        duplicate = {**fresh, 'title': 'Updated title', 'url': 'https://news.example.test/story/1'}
        fallback = {**fresh, 'url': 'https://news.example.test/story/fallback',
                    'original_url': 'https://news.example.test/story/fallback',
                    'final_url': 'https://news.example.test/story/fallback',
                    'published': core.iso_date(NOW - timedelta(days=45)),
                    'fetched_at': core.iso_date(NOW - timedelta(days=45))}
        expired = {**fresh, 'url': 'https://news.example.test/story/expired',
                   'original_url': 'https://news.example.test/story/expired',
                   'final_url': 'https://news.example.test/story/expired',
                   'fetched_at': core.iso_date(NOW - timedelta(days=91))}
        merged = core.merge_items([fresh, fallback, expired], [duplicate], NOW)
        self.assertEqual({row['url'] for row in merged}, {
            'https://news.example.test/story/1', 'https://news.example.test/story/fallback'})
        self.assertIn(next(row for row in merged if row['url'].endswith('/story/1'))['title'],
                      {'Updated title', 'Service changes'})

    def test_total_feed_outage_keeps_the_cached_90_day_fallback_items(self):
        cached = core.parse_source(rss(), SOURCE, NOW - timedelta(days=45))[0]
        cached.update(published=core.iso_date(NOW - timedelta(days=45)),
                      fetched_at=core.iso_date(NOW - timedelta(days=45)))
        core.save_store([cached], self.store, NOW)

        def opener(request, timeout):
            if request.full_url.endswith('/robots.txt'):
                return FakeResponse(b'User-agent: *\nAllow: /', url=request.full_url)
            from urllib.error import URLError
            raise URLError('fixture network outage')

        result = core.ingest_enabled([{**SOURCE, 'enabled': True}], self.store,
                                     opener=opener, now=NOW)
        self.assertEqual(result['source_results'], {'city-transit': 'network_error'})
        self.assertEqual(result['parsed_items'], 0)
        self.assertEqual(result['stored_items'], 1)
        self.assertEqual(core.load_store(self.store)[0]['url'], cached['url'])

    def test_same_title_from_different_outlets_is_kept_for_corroboration(self):
        first = core.parse_source(rss(), SOURCE, NOW)[0]
        second = {**first, 'source_id': 'other-publisher', 'url': 'https://other.example.test/story/1'}
        self.assertEqual(len(core.merge_items([], [first, second], NOW)), 2)

    def test_ranked_feed_uses_profile_and_returns_source_link_date(self):
        items = core.parse_source(rss(), SOURCE, NOW)
        core.save_store(items, self.store, NOW)
        profile = {'topics': [{'id': 'transit', 'label': 'Transit', 'scope': 'bus routes',
                               'include': ['weekend service'], 'exclude': ['sports scores'],
                               'mustFollow': False}], 'muted': []}
        registry = [{**SOURCE, 'enabled': False, 'allowed_domains': ['news.example.test']}]
        rows = core.feed_items('transit', profile, self.store, NOW, registry)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['source_name'], 'Transit Authority')
        self.assertEqual(rows[0]['link'], 'https://news.example.test/story/1')
        self.assertEqual(rows[0]['fetched_at'], '2026-11-16T12:00:00Z')
        profile['muted'] = [{'label': 'Transit'}]
        self.assertEqual(core.feed_items('transit', profile, self.store, NOW, registry), [])

    def test_insurance_profile_can_rank_health_registry_items(self):
        items = [
            {'title': 'Pinevale Medicaid health insurance changes', 'snippet': 'State insurance coverage update.',
             'topics': ['health'], 'source_id': 'health-feed'},
            {'title': 'Hospital technology investment', 'snippet': 'A health system announced a new device.',
             'topics': ['health'], 'source_id': 'health-feed'},
        ]
        profile = {'topics': [{'id': 'insurance', 'label': 'Pinevale health insurance',
                               'scope': 'Pinevale health insurance Medicaid policy',
                               'include': ['health insurance', 'Medicaid', 'insurance'], 'exclude': [],
                               'mustFollow': False}], 'muted': []}
        from ingest.write_edition import _matches_topic_scope
        ranked = core.rank_items(items, profile, 'insurance')
        relevant = [row for row in ranked if _matches_topic_scope(profile['topics'][0], row['title'] + ' ' + row['snippet'])]
        self.assertEqual([row['title'] for row in relevant], ['Pinevale Medicaid health insurance changes'])

    def test_any_profile_exclude_term_removes_a_matching_item(self):
        item = core.parse_source(rss(description='Weekend sports scores and service changes.'), SOURCE, NOW)
        profile = {'topics': [{'id': 'transit', 'label': 'Transit', 'scope': 'bus routes', 'include': ['service'],
                               'exclude': ['sports', 'stocks'], 'mustFollow': False}]}
        self.assertEqual(core.rank_items(item, profile, 'transit'), [])

    def test_multiword_exclusion_requires_the_complete_phrase(self):
        items = [
            {'title': 'Crypto oversight update', 'snippet': 'New oversight discussion.', 'topics': ['payments']},
            {'title': 'Crypto hype draws complaints', 'snippet': 'Officials discussed misleading promotion.', 'topics': ['payments']},
            {'title': 'Payments network update', 'snippet': 'Service policy changed.', 'topics': ['payments']},
        ]
        profile = {'topics': [{'id': 'payments', 'label': 'Payments', 'scope': 'payments policy',
                              'include': [], 'exclude': ['crypto hype'], 'mustFollow': True}],
                   'muted': [{'label': 'crypto hype'}]}
        self.assertEqual({item['title'] for item in core.rank_items(items, profile, 'payments')},
                         {'Crypto oversight update', 'Payments network update'})

    def test_muted_keyword_filters_matching_items_without_muting_followed_topic(self):
        items = [
            {'title': 'Crypto payment promotion', 'snippet': 'A fictional promotion.', 'topics': ['payments']},
            {'title': 'Payment network update', 'snippet': 'A fictional policy update.', 'topics': ['payments']},
        ]
        profile = {'topics': [{'id': 'payments', 'label': 'Payments', 'scope': 'payment network',
                              'include': [], 'exclude': [], 'mustFollow': True}],
                   'muted': [{'label': 'Crypto'}]}
        self.assertEqual([item['title'] for item in core.rank_items(items, profile, 'payments')],
                         ['Payment network update'])

    def test_feed_route_validation_and_ranked_response(self):
        result = [{'source_name': 'Transit Authority', 'link': 'https://news.example.test/story',
                   'fetched_at': '2026-11-16T12:00:00Z'}]
        status, body = feed_response({'topic': ['transit']}, lambda topic, profile: result)
        self.assertEqual(status, 200)
        self.assertEqual(body['items'][0]['source_name'], 'Transit Authority')
        self.assertEqual(feed_response({'topic': ['bad topic']})[0], 400)
        self.assertEqual(feed_response({'topic': ['transit'], 'profile': ['not-json']})[0], 400)


if __name__ == '__main__':
    unittest.main()
