"""Fictional supply, guarded reads and providers; no live network or state."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from dev.ingest import core, credibility, retrieval_fill as fill
from dev.ingest.retrieval import Candidate, FeedProvider
from dev.ingest.retrieval.provider import BaseProvider
from dev.ingest.write_edition import cluster_items, _source_maps, generate_edition, _quick_hit_items

NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
DATE = '2026-11-18T10:00:00Z'
TOPICS = [{'id': 'payments', 'label': 'Payments', 'scope': 'Payment settlement rules', 'include': [], 'exclude': []}]
BODY = ('The fictional office announced a settlement timetable for member banks after completing its scheduled review of payment processing. '
        'The timetable describes how participating institutions will report completed transfers and reconcile outstanding entries at the end of each business day. '
        'The office published the reporting instructions alongside the timetable and said the review covered the existing settlement procedures used by participating banks. '
        'The notice gives member institutions the documentation needed to prepare their reports and describes the procedure for correcting incomplete submissions.')


class FixtureProvider(BaseProvider):
    def __init__(self, name, rows, queries):
        self.name, self.rows, self.queries = name, rows, queries
    def _search(self, query, count, freshness, budget):
        self.queries.append((self.name, query))
        if self.name == 'gemini':
            budget.grounding_requests += 1
        return self.rows[:count]


class FillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.history = json.loads((Path(__file__).parent / 'fixtures/repeat-suppression.json').read_text())['history']
        hosts = ['payments.example.test', 'standards.example.test', 'health.example.test', 'wire.example.test',
                 'trade.example.test', 'blog.example.test']
        self.registry = [{'id': host.split('.')[0], 'name': 'Fictional ' + host.split('.')[0],
                          'url': 'https://' + host + '/feed', 'allowed_domains': [host],
                          'enabled': True, 'robots_ok': True, 'article_access': 'full-text',
                          'reuse_terms': 'OBSERVED: fictional test permission', 'topics': ['payments'],
                          'minimum_interval_seconds': 0} for host in hosts]
        tiers = {host: {'tier': 'blog' if host.startswith('blog.') else 'primary' if host.startswith('payments.')
                       else 'trade' if host.startswith('trade.') else 'major', 'reviewed_at': '2026-11-18'} for host in hosts}
        (self.root / 'docs').mkdir()
        (self.root / 'docs/source-rights.md').write_text('\n'.join('`' + row['id'] + '`' for row in self.registry))
        self.queries, self.reads, self.rows, self.metadata = [], [], [], {}
        self.body_by_url = {}
        self.patch(patch.dict(os.environ, {'BRIEFING_RETRIEVAL_FILL': 'on'}))
        self.patch(patch.object(credibility, '_DEFAULT_TIERS', tiers))
        self.patch(patch.object(core, 'ROOT', self.root))
        self.patch(patch.object(core, 'KILL_SWITCH_PATH', self.root / 'kill'))
        self.patch(patch.object(fill, 'process_ledger', return_value=SimpleNamespace(state_dir=self.root / 'ledger')))
        self.patch(patch.object(fill, 'build_providers', side_effect=lambda *a, **kw: [
            FixtureProvider(name, self.rows, self.queries) for name in ('feeds', 'gdelt', 'gemini')]))
        self.patch(patch.object(fill, 'fetch_candidate', side_effect=self.fetch))
        self.patch(patch('socket.create_connection', side_effect=AssertionError('external connection forbidden')))
        self.patch(patch('socket.getaddrinfo', side_effect=AssertionError('external DNS forbidden')))

    def patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def hint(self, title='Settlement timetable released', path='new', host='payments', date=DATE):
        return Candidate(f'https://{host}.example.test/{path}', title, 'Fictional desk', date, 'feeds', 1,
                         source_id=host)

    def fetch(self, hint, **kwargs):
        self.reads.append(hint.url)
        kwargs['metadata'].update(self.metadata.get(hint.url, {}))
        return self.body_by_url.get(hint.url, BODY)

    def run_fill(self, pool=None, topics=None, history=None):
        return fill.fill_candidates(pool or {}, topics or TOPICS, [], self.registry,
                                    self.history if history is None else history, NOW)

    def test_off_is_zero_work_and_three_unique_events_skip_fill(self):
        with patch.dict(os.environ, {'BRIEFING_RETRIEVAL_FILL': 'off'}):
            self.assertFalse(self.run_fill()[2]['enabled'])
        self.rows = [self.hint(title, str(i)) for i, title in enumerate([
            'Settlement timetable released', 'Bank charter approved', 'Consumer complaint portal opens'])]
        clusters = cluster_items([{'url': row.url, 'title': row.title, 'source_id': row.source_id,
                                   'published': row.published} for row in self.rows], _source_maps(self.registry))
        report = self.run_fill({'payments': clusters})[2]
        self.assertFalse(report['triggered'])
        self.assertEqual(self.queries, [])
        self.assertEqual(self.reads, [])

    def test_daily_clusters_same_story_across_outlets_with_order_stable_cluster_id(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures' /
                              'canonical-story-clusters.json').read_text())
        registry = {row['id']: row for row in fixture['registry']}
        rows = fixture['stories']
        forward = cluster_items(rows, _source_maps(fixture['registry']))
        reverse = cluster_items(list(reversed(rows)), _source_maps(fixture['registry']))
        self.assertEqual(len(forward), 1)
        self.assertEqual(len(forward[0]['rows']), 2)
        self.assertEqual(forward[0]['id'], reverse[0]['id'])
        self.assertTrue(forward[0]['canonical_story_key'])
        self.assertEqual({row['source']['id'] for row in forward[0]['rows']}, set(registry))

    def test_daily_canonical_url_removes_tracking_and_avoids_duplicate_cluster_rows(self):
        source = {'id': 'payments', 'name': 'Fictional Payments', 'enabled': True}
        registry = [source]
        items = [{'source_id': 'payments', 'url': 'https://payments.example.test/a?utm_source=one',
                  'title': 'One fictional payment update'},
                 {'source_id': 'payments', 'url': 'https://payments.example.test/a?utm_source=two',
                  'title': 'A differently titled mirror'}]
        clusters = cluster_items(items, _source_maps(registry))
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0]['rows']), 1)

    def test_named_repeats_rejected_and_fresh_sources_added(self):
        self.rows = [self.hint(row['title'], row['url'].split('/')[-1], row['url'].split('//')[1].split('.')[0])
                     for row in self.history[0]['sources']['sources']]
        self.rows += [self.hint(), self.hint('Bank charter approved', 'charter', 'wire'),
                      self.hint('Consumer complaint portal opens', 'portal', 'trade')]
        pool, bodies, report = self.run_fill()
        self.assertEqual(report['rejected']['already_published'], 3)
        self.assertEqual(report['candidates_added'], 3)
        self.assertEqual(len(pool['payments']), 3)
        self.assertEqual(len(bodies), 3)
        self.assertTrue(all('reporting analysis features and data releases' in query and
                            TOPICS[0]['scope'] in query for _, query in self.queries))
        self.assertFalse(any('zelle' in url or 'pci-pq' in url or 'cms-medicaid' in url for url in self.reads))
        self.assertNotIn('http', json.dumps(report))
        self.assertNotIn('settlement', json.dumps(report).lower())
        self.assertFalse((self.root / 'ledger').exists())

    def test_recent_repeats_and_junk_do_not_consume_older_story_read_slot(self):
        repeats = [self.hint(row['title'], row['url'].split('/')[-1],
                             row['url'].split('//')[1].split('.')[0])
                   for row in self.history[0]['sources']['sources']]
        junk = [self.hint(f'Top 7 payment tips for settlement transfers {index}', f'fictional-listicle-{index}')
                for index in range(9)]
        older = self.hint('Fictional settlement policy changes after agency review', 'older-70-days',
                          host='trade', date=(NOW - timedelta(days=70)).isoformat())
        items = [{'source_id': hint.source_id, 'title': hint.title, 'url': hint.url,
                  'published': hint.published, 'snippet': 'Payment settlement reporting update.'}
                 for hint in [*repeats, *junk, older]]

        def build_fixture_providers(config, *, items, registry):
            return [FeedProvider(items, registry=registry, result_cap=config.result_cap),
                    FixtureProvider('gdelt', [], self.queries),
                    FixtureProvider('gemini', [], self.queries)]

        with patch.object(fill, 'build_providers', side_effect=build_fixture_providers):
            pool, bodies, report = fill.fill_candidates(
                {}, TOPICS, items, self.registry, self.history, NOW, enabled=True)

        self.assertEqual(report['rejected']['already_published'], len(repeats))
        self.assertEqual(report['rejected']['listicle'], len(junk))
        self.assertEqual(report['guarded_reads'], 1)
        self.assertEqual(self.reads, [older.url])
        self.assertEqual(report['candidates_added'], 1)
        self.assertIn(older.url, bodies)
        self.assertEqual(core.parse_date(pool['payments'][0]['rows'][0]['item']['published']),
                         core.parse_date(older.published))

    def test_four_unreadable_recent_candidates_exhaust_reads_before_older_candidate(self):
        recent = [self.hint(f'Fictional settlement notice {index}', f'unreadable-{index}',
                            host='wire', date=(NOW - timedelta(days=index)).isoformat())
                  for index in range(4)]
        older = self.hint('Fictional settlement procedure explained', 'older-70-days', host='trade',
                          date=(NOW - timedelta(days=70)).isoformat())
        self.rows = [*recent, older]
        for row in recent:
            self.body_by_url[row.url] = ''

        pool, bodies, report = self.run_fill()

        self.assertEqual(report['guarded_reads'], 4)
        self.assertEqual(self.reads, [row.url for row in recent])
        self.assertNotIn(older.url, self.reads)
        self.assertEqual(report['rejected']['no_evidence'], 4)
        self.assertEqual(report['candidates_added'], 0)
        self.assertEqual(bodies, {})
        self.assertEqual(pool, {})

    def test_search_first_plans_rounds_even_when_feed_pool_already_has_three_stories(self):
        feed_rows = [self.hint(title, f'feed-{index}') for index, title in enumerate((
            'Settlement timetable released', 'Bank charter approved',
            'Consumer complaint portal opens'))]
        feed_pool = cluster_items([{'url': row.url, 'title': row.title, 'published': row.published,
                                    'source_id': row.source_id} for row in feed_rows],
                                  _source_maps(self.registry))
        self.rows = [self.hint('Payment network adopts new settlement timetable', 'search-one', 'payments'),
                     self.hint('Regulator approves bank payment charter', 'search-two', 'wire')]
        merged, bodies, report = fill.fill_candidates(
            {'payments': feed_pool}, TOPICS, [], self.registry, self.history, NOW,
            enabled=True, search_first=True)
        self.assertTrue(report['search_first'])
        self.assertEqual(report['planner_queries'], 3)
        self.assertEqual(report['retrieval_rounds'], 2)
        self.assertEqual(report['topics_searched'], 1)
        self.assertEqual(report['search_requests'], 6)
        self.assertEqual(report['grounding_requests'], 3)
        self.assertEqual(report['guarded_reads'], 2)
        self.assertEqual(report['candidates_added'], 2)
        self.assertEqual(len(merged['payments']), 5)
        self.assertEqual(len(bodies), 2)
        safe = fill.safe_report(report)
        self.assertEqual(safe['planner_queries'], 3)
        self.assertEqual(safe['retrieval_rounds'], 2)
        self.assertEqual(safe['guarded_reads'], 2)
        self.assertNotIn('http', json.dumps(safe))

    def test_search_first_rejects_articles_older_than_ninety_days(self):
        old_date = (NOW - timedelta(days=91)).isoformat()
        self.rows = [self.hint('Payment network adopts new settlement timetable',
                               'outside-search-window', date=old_date)]
        merged, bodies, report = fill.fill_candidates(
            {'payments': []}, TOPICS, [], self.registry, self.history, NOW,
            enabled=True, search_first=True)
        self.assertEqual(merged, {'payments': []})
        self.assertEqual(bodies, {})
        self.assertEqual(report['rejected']['outside_window'], 1)
        self.assertEqual(self.reads, [])

    def test_topic_floor_search_targets_only_empty_topics_with_two_queries_each(self):
        topics = [
            {'id': 'payments', 'label': 'Payments', 'scope': 'Payment settlement rules', 'include': []},
            {'id': 'health-policy', 'label': 'Health insurance policy',
             'scope': 'Health insurance coverage policy', 'include': []},
            {'id': 'ai-finance', 'label': 'AI in finance',
             'scope': 'Artificial intelligence in financial services', 'include': []},
        ]
        for row in self.registry:
            if row['id'] == 'health':
                row['topics'] = ['health-policy']
            elif row['id'] == 'wire':
                row['topics'] = ['ai-finance']
        self.rows = [
            self.hint('State regulator updates health insurance coverage rules', 'health-policy', 'health'),
            self.hint('Bank adopts artificial intelligence review for loan decisions', 'ai-finance', 'wire'),
        ]
        payment = self.hint('Regulator updates payment settlement rules', 'feed-payment', 'payments')
        payment_clusters = cluster_items([{'url': payment.url, 'title': payment.title,
                                           'published': payment.published, 'source_id': payment.source_id}],
                                         _source_maps(self.registry))
        topics_to_search = topics[1:]
        pool, bodies, report = fill.fill_candidates(
            {'payments': payment_clusters, 'health-policy': [], 'ai-finance': []},
            topics_to_search, [], self.registry, self.history, NOW,
            passed_candidates={'payments': payment_clusters, 'health-policy': [], 'ai-finance': []},
            enabled=True, topic_floor_search=True)

        queried = [provider for provider, _query in self.queries]
        self.assertEqual(len(queried), 4)
        self.assertNotIn('feeds', queried, 'the topic floor uses open-web providers only')
        self.assertLessEqual(report['search_requests'], 2 * len(topics_to_search))
        self.assertEqual(report['planner_queries'], 2 * len(topics_to_search))
        self.assertTrue(report['topic_floor'])
        self.assertEqual(report['candidates_added'], 2)
        self.assertEqual(len(pool['payments']), 1)
        self.assertEqual(len(pool['health-policy']), 1)
        self.assertEqual(len(pool['ai-finance']), 1)
        self.assertEqual(len(bodies), 2)

    def test_quality_prefers_relevance_and_credibility_without_a_recency_gate(self):
        self.rows = [self.hint('Seven insights', host='blog'), self.hint('Top 7 payment tips', 'tips'),
                     self.hint('Earlier timetable', 'old', date='2026-11-16T11:00:00Z'),
                     self.hint('Strong settlement feature', 'six-week',
                               date=(NOW - timedelta(days=42)).isoformat()),
                     self.hint('Tomorrow timetable', 'future', date='2026-11-19T10:00:00Z'),
                     self.hint('Unknown timetable', 'unknown', date=None),
                     self.hint('Fresh timetable', 'fresh', date='2026-11-16T12:00:00Z')]
        pool, _, report = self.run_fill()
        self.assertEqual(report['candidates_added'], 3)
        for reason in ('low_tier', 'listicle', 'future', 'unknown_date'):
            self.assertEqual(report['rejected'][reason], 1)
        self.assertEqual({row['rows'][0]['item']['published'] for row in pool['payments']},
                         {'2026-11-16T11:00:00Z', '2026-11-16T12:00:00Z',
                          (NOW - timedelta(days=42)).isoformat()})
        self.assertEqual(pool['payments'][0]['rows'][0]['item']['title'], 'Strong settlement feature')

    def test_article_metadata_required_for_search_and_redirect_repeat_checked(self):
        self.rows = [Candidate('https://wire.example.test/search', 'wire.example.test', 'wire', None, 'gemini', 1),
                     Candidate('https://trade.example.test/search', 'trade.example.test', 'trade', None, 'gemini', 2)]
        self.metadata[self.rows[0].url] = {'title': 'Bank charter approved', 'published': DATE}
        self.metadata[self.rows[1].url] = {'title': 'Zelle and Solana expand payment links', 'published': DATE,
                                         'final_url': 'https://payments.example.test/zelle-solana'}
        pool, _, report = self.run_fill()
        self.assertEqual(report['candidates_added'], 1)
        self.assertEqual(report['rejected']['already_published'], 1)
        self.assertEqual(pool['payments'][0]['rows'][0]['item']['title'], 'Bank charter approved')

    def test_undated_external_hint_reads_publisher_date_but_cached_feed_is_prefiltered(self):
        cached = {'source_id': 'payments', 'title': 'Fictional settlement update',
                  'url': 'https://payments.example.test/undated-cached', 'published': None,
                  'snippet': 'Payment settlement reporting update.'}
        external = Candidate('https://wire.example.test/undated-hint',
                             'Payment settlement process update', 'Fictional wire', None, 'gdelt', 1,
                             source_id='wire')
        self.metadata[external.url] = {'title': 'Payment settlement reporting update', 'published': DATE}

        def build_fixture_providers(config, *, items, registry):
            return [FeedProvider(items, registry=registry, result_cap=config.result_cap),
                    FixtureProvider('gdelt', [external], self.queries),
                    FixtureProvider('gemini', [], self.queries)]

        with patch.object(fill, 'build_providers', side_effect=build_fixture_providers):
            pool, _, report = fill.fill_candidates(
                {}, TOPICS, [cached], self.registry, self.history, NOW, enabled=True)

        self.assertEqual(self.reads, [external.url])
        self.assertEqual(report['rejected']['unknown_date'], 1)
        self.assertEqual(report['guarded_reads'], 1)
        self.assertEqual(report['candidates_added'], 1)
        self.assertEqual(pool['payments'][0]['rows'][0]['item']['published'], DATE)

    def test_four_article_attempts_and_six_grounding_requests(self):
        self.rows = [self.hint(f'Fictional notice {i}', str(i), date=None) for i in range(10)]
        topics = [{**TOPICS[0], 'id': str(i)} for i in range(9)]
        _, _, report = self.run_fill(topics=topics)
        self.assertEqual(report['grounding_requests'], 3)
        self.assertEqual(report['search_requests'], 6)
        self.assertEqual(sum(name == 'gemini' for name, _ in self.queries), 3)
        self.assertEqual(sum(name != 'feeds' for name, _ in self.queries), 6)
        self.assertLessEqual(len(self.reads), 4 * len(topics))
        self.assertEqual(report['rejected']['unknown_date'], 4 * len(topics))

    def test_same_story_shared_across_topics_is_not_three_stories(self):
        hint = self.hint()
        item = {'url': hint.url, 'title': hint.title, 'published': DATE, 'source_id': 'payments'}
        cluster = cluster_items([item], _source_maps(self.registry))[0]
        _, _, report = self.run_fill({'a': [cluster], 'b': [cluster], 'c': [cluster]})
        self.assertTrue(report['triggered'])

    def test_registry_restrictions_and_fourteen_edition_repeat_window(self):
        self.rows = [self.hint()]
        self.registry[0]['paywalled'] = True
        self.assertEqual(self.run_fill()[2]['rejected']['source_policy'], 1)
        self.assertEqual(self.reads, [])
        self.registry[0].pop('paywalled')
        self.rows = [self.hint('Zelle and Solana expand payment links', 'zelle-solana')]
        history = [{}] * 5 + self.history
        self.assertEqual(self.run_fill(history=history)[2]['candidates_added'], 0)

    def test_quick_hits_share_quality_gate_and_dates(self):
        items = [{'url': row.url, 'title': row.title, 'source_id': row.source_id, 'published': row.published,
                  'snippet': 'The fictional office published the timetable.'} for row in
                 [self.hint(), self.hint('Top 5 payment tips', 'tips'), self.hint(host='blog'),
                  self.hint('Undated note', 'unknown', date=None)]]
        pool = {'payments': cluster_items(items, _source_maps(self.registry))}
        hits = _quick_hit_items(pool, set(), set(), _source_maps(self.registry)['__source_ids__'], now=NOW)
        self.assertEqual([hit['url'] for hit in hits], [self.hint().url])

    def test_fixture_ab_one_normal_story_becomes_three_through_existing_writer_and_scorecard(self):
        from dev.ingest.score_edition import score_edition
        self.rows = [self.hint('Bank charter approved', 'charter', 'wire'),
                     self.hint('Consumer complaint portal opens', 'portal', 'trade')]
        self.body_by_url[self.rows[0].url] = (
            'The fictional regulator approved a bank charter following its review of the application and supporting payment records. '
            'The approval notice describes the settlement services the institution may provide and identifies the reporting obligations that apply to those services. '
            'The regulator published its decision alongside the application materials and said the bank must maintain records of completed payment transfers. '
            'The notice also explains the reporting timetable and the procedure for submitting corrections to the regulator after a settlement report has been filed. '
            'The institution must keep the transfer records available for review under the timetable set out in the approval notice.')
        self.body_by_url[self.rows[1].url] = (
            'The fictional consumer office opened a complaint portal for customers reporting payment processing errors at participating banks. '
            'The portal allows customers to submit descriptions of missing transfers and attach the payment reference supplied by their bank. '
            'The consumer office published instructions describing how complaints will be referred to the relevant institution for investigation and a written response. '
            'The notice explains the information required to submit a complaint and identifies the procedure for providing additional documents after submission. '
            'Customers can use the case number to add records after the initial complaint has been received.')
        normal_body = (BODY + ' The office will publish a monthly status summary, while individual banks remain '
                       'responsible for correcting their own transfer records.')
        normal = {'url': self.hint().url, 'title': self.hint().title, 'published': DATE,
                  'source_id': 'payments', 'feed_body': normal_body, 'topics': ['payments'],
                  'snippet': normal_body[:200]}
        calls, scores = [], []
        def writer(system, user, *args, **kwargs):
            calls.append(json.loads(user))
            evidence = json.loads(user)['source_sentences_only']
            return {'relevant': True, 'heading': evidence[0]['title'],
                    'claims': [{'section': 'what_happened', 'text': row['sentence'], 'evidence_ids': [row['id']]}
                               for row in evidence[:5]], 'quote': None}
        class FixtureClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW
        def score(section, sources, evidence):
            with patch('datetime.datetime', FixtureClock):
                report = score_edition({'sections': [section]}, sources, self.registry, evidence,
                                       rights_path=self.root / 'docs/source-rights.md')
            scores.append(report)
            return {'passed': not report['critical_failed']}
        def generate(search_first_enabled=False, scorer=score):
            return generate_edition({'topics': TOPICS}, [normal], self.registry, NOW,
                call_json=writer, article_fetcher=lambda *a: self.fail('retrieved body fetched twice'),
                story_scorer=scorer, recent_history=self.history,
                search_first_enabled=search_first_enabled)
        def free_feed_and_gdelt_only(config, *, items, registry):
            return [FeedProvider(items, registry=registry, result_cap=config.result_cap),
                    FixtureProvider('gdelt', self.rows, self.queries)]
        with patch.object(core, 'load_registry', return_value=self.registry):
            with patch.object(fill, 'build_providers', side_effect=free_feed_and_gdelt_only):
                with patch.dict(os.environ, {'BRIEFING_RETRIEVAL_FILL': 'off'}):
                    baseline, _, _ = generate()
                self.assertEqual(len(baseline['editions'][0]['sections']), 1)
                editions, sources, detail = generate()
                self.assertEqual(detail['retrieval_fill']['candidates_added'], 2)
                self.assertEqual(detail['retrieval_fill']['grounding_requests'], 0)
                self.assertEqual(detail['retrieval_fill']['first_round_passed'], 1)
                self.assertEqual(detail['retrieval_fill']['second_round_passed'], 2)
                self.assertTrue(any(row['action'] == 'kept'
                                    for row in detail['source_selection']['payments']['items']))
                self.assertIn('also_today_source_selection', detail)
                self.assertEqual(len(calls), 4)
                self.assertEqual(len(scores), 4)
                self.assertTrue(all(not report['critical_failed'] for report in scores))
                self.assertEqual(len(editions['editions'][0]['sections']), 3)
                self.assertFalse(editions['editions'][0]['thin_day'])
                search_edition, search_sources, search_detail = generate(
                    search_first_enabled=True, scorer=lambda *_: {'passed': True})
                self.assertTrue(search_detail['retrieval_fill']['search_first'])
                self.assertEqual(search_detail['retrieval_fill']['planner_queries'], 3)
                self.assertEqual(search_detail['retrieval_fill']['grounding_requests'], 0,
                                 'the fictional three-story supply path does not call paid Gemini grounding')
                self.assertEqual(len(search_edition['editions'][0]['sections']), 3)
                sections = search_edition['editions'][0]['sections']
                self.assertTrue(all(len(section['claim_links']) >= 4 for section in sections))
                self.assertGreaterEqual(sum(len(section['claim_links']) for section in sections), 12,
                                        'each story keeps multiple distinct, source-linked facts')
                from dev.ingest.write_edition import _daily_spoken_words
                self.assertGreaterEqual(_daily_spoken_words(sections), 260,
                                        'the spoken source claims approach the 264-word editorial sample')
                self.assertTrue(any(section.get('supply_origin') == 'search-first' for section in sections))
                self.assertEqual({row['registry_id'] for row in search_sources['sources']},
                                 {'payments', 'wire', 'trade'})

    def test_real_run_failure_shape_recovers_only_after_writer_round(self):
        from dev.ingest.score_edition import score_edition
        from dev.ingest.daily import _daily_run_report
        import time
        fixture = json.loads((Path(__file__).parent / 'fixtures/supply-second-pass.json').read_text())
        self.registry.append({**self.registry[0], 'id': 'nerdwallet',
                              'url': 'https://nerdwallet.com/feed',
                              'allowed_domains': ['nerdwallet.com']})
        with (self.root / 'docs/source-rights.md').open('a') as rights:
            rights.write('\n`nerdwallet`')
        normal = [{**row, 'published': DATE, 'topics': ['payments'],
                   'snippet': row['title'], 'feed_body': BODY} for row in fixture['first_round']]
        self.rows = [self.hint(row['title'], row['path'], row['host']) for row in fixture['fill']]
        for row, hint in zip(fixture['fill'], self.rows):
            self.body_by_url[hint.url] = row['body']
        # Providers may return failed URLs again; none should be fetched or written twice.
        self.rows = [Candidate(row['url'], row['title'], 'Fictional desk', DATE, 'feeds', 1,
                               source_id=row['source_id']) for row in normal[:3]] + self.rows
        # Reviewed status cannot rescue the real junk domain.
        credibility._DEFAULT_TIERS['nerdwallet.com'] = {'tier': 'trade', 'reviewed_at': '2026-11-18'}
        events, calls = [], []
        original_fetch = self.fetch
        def fetch(hint, **kwargs):
            events.append('fill')
            return original_fetch(hint, **kwargs)
        self.patch(patch.object(fill, 'fetch_candidate', side_effect=fetch))
        class FixtureClock(datetime):
            @classmethod
            def now(cls, tz=None): return NOW
        for failure in ('semantic_irrelevance', 'scorecard_dropped', 'insufficient_full_text_evidence'):
            with self.subTest(failure=failure):
                events.clear(); calls.clear()
                feed = deepcopy(normal)
                if failure == 'insufficient_full_text_evidence':
                    for row in feed: row.pop('feed_body', None)
                def writer(system, user, *args, **kwargs):
                    evidence = json.loads(user)['source_sentences_only']
                    title = evidence[0]['title']
                    calls.append(title); events.append('write')
                    self.assertNotIn('Chase', title)
                    bad = title in {row['title'] for row in normal}
                    if bad and failure == 'semantic_irrelevance':
                        return {'relevant': False, 'heading': title, 'claims': [], 'quote': None}
                    return {'relevant': True, 'heading': title,
                            'claims': [{'section': 'what_happened', 'text': row['sentence'], 'evidence_ids': [row['id']]}
                                       for row in evidence[:3]], 'quote': None}
                def score(section, sources, evidence):
                    if section['heading'] in {row['title'] for row in normal}:
                        return {'passed': False, 'failed_critical': ['fixture_rejection']}
                    with patch('datetime.datetime', FixtureClock):
                        report = score_edition({'sections': [section]}, sources, self.registry, evidence,
                                               rights_path=self.root / 'docs/source-rights.md')
                    self.assertFalse(report['critical_failed'], report)
                    return {'passed': True}
                with patch.object(core, 'load_registry', return_value=self.registry):
                    editions, sources, detail = generate_edition(
                        {'topics': [{**TOPICS[0], 'label': 'Payments and banking'}]}, feed, self.registry, NOW,
                        call_json=writer, article_fetcher=lambda *args: None, story_scorer=score,
                        deadline=time.monotonic() + 5, recent_history=[])
                report = detail['retrieval_fill']
                self.assertTrue(report['triggered'])
                self.assertEqual(report['first_round_passed'], 0)
                self.assertEqual(report['first_round_readable'], 0 if failure == 'insufficient_full_text_evidence' else 3)
                self.assertEqual(report['candidates_added'], 3)
                self.assertEqual(report['second_round_passed'], 3)
                self.assertEqual(report['rejected']['denied_publisher'], 1)
                self.assertEqual(report['rejected'][failure], 3)
                self.assertEqual(report['rejected']['duplicate'], 3)
                self.assertFalse({row['url'] for row in normal} & set(self.reads))
                if failure != 'insufficient_full_text_evidence':
                    self.assertEqual(events[:3], ['write'] * 3)
                self.assertEqual(len(editions['editions'][0]['sections']), 3)
                self.assertNotIn('nerdwallet', json.dumps(sources))
                daily = _daily_run_report({'status': 'complete', 'run_status': {'retrieval_fill': report}})
                self.assertEqual(daily['retrieval_fill'], fill.safe_report(report))

    def test_deadline_and_default_off_do_not_start_fill_after_failure(self):
        import time
        with patch.dict(os.environ, {'BRIEFING_RETRIEVAL_FILL': 'off'}):
            result = generate_edition({'topics': TOPICS}, [], self.registry, NOW,
                                      call_json=lambda *a: None, max_candidate_attempts=0)
        self.assertFalse(result[2]['retrieval_fill']['triggered'])
        callback = self.patch(patch('dev.ingest.retrieval_fill.build_providers'))
        result = generate_edition({'topics': TOPICS}, [], self.registry, NOW,
                                  call_json=lambda *a: None, deadline=time.monotonic() - 1)
        callback.assert_not_called()
        self.assertFalse(result[2]['retrieval_fill']['triggered'])

    def test_missing_topic_uses_open_web_fallback_even_when_feed_fill_flag_is_off(self):
        from dev.ingest.retrieval_fill import empty_report
        observed = []

        def fallback(pool, topics, *_args, **kwargs):
            observed.append((topics, kwargs.get('enabled'), kwargs.get('topic_floor_search')))
            report = empty_report(True)
            report['triggered'] = True
            return pool, {}, report

        with patch.dict(os.environ, {'BRIEFING_RETRIEVAL_FILL': 'off'}), \
             patch('dev.ingest.retrieval_fill.fill_candidates', side_effect=fallback):
            _editions, _sources, detail = generate_edition(
                {'topics': TOPICS}, [], self.registry, NOW,
                call_json=lambda *_args, **_kwargs: None, max_candidate_attempts=3,
                daily_coverage_fallback_enabled=True)

        self.assertEqual(len(observed), 1)
        self.assertEqual([topic['id'] for topic in observed[0][0]], ['payments'])
        self.assertEqual(observed[0][1:], (True, True))
        self.assertTrue(detail['retrieval_fill']['triggered'])

    def test_topic_floor_search_exception_does_not_fail_daily(self):
        with patch('dev.ingest.retrieval_fill.fill_candidates',
                   side_effect=RuntimeError('fixture search failure')):
            with self.assertLogs('dev.ingest.write_edition', level='WARNING') as captured:
                editions, _sources, detail = generate_edition(
                    {'topics': TOPICS}, [], self.registry, NOW,
                    call_json=lambda *_args, **_kwargs: self.fail('empty feed reached writer'),
                    max_candidate_attempts=3, daily_coverage_fallback_enabled=True)

        self.assertEqual(len(editions['editions']), 1)
        self.assertEqual(editions['editions'][0]['sections'], [])
        self.assertTrue(detail['retrieval_fill']['topic_floor'])
        self.assertFalse(detail['retrieval_fill']['triggered'])
        self.assertEqual(len(captured.output), 1)
        self.assertIn('continuing without the search fallback', captured.output[0])

    def test_story_quality_gate_all_topics_and_broad_fallback(self):
        junk = {'url': 'https://nerdwallet.com/travel/news', 'source_id': 'payments',
                'title': 'Chase Expands Credit Card Transfer Partners', 'published': DATE,
                'feed_body': BODY, 'topics': ['payments']}
        for label in ('Payments and banking', 'Travel', 'Any chosen topic'):
            with patch.dict(os.environ, {'BRIEFING_RETRIEVAL_FILL': 'off'}):
                editions, _, detail = generate_edition({'topics': [{**TOPICS[0], 'label': label}]},
                    [junk], self.registry, NOW, call_json=lambda *a: self.fail('junk reached writer'),
                    article_fetcher=lambda *a: self.fail('junk fetched'))
            self.assertEqual(editions['editions'][0]['sections'], [])
            self.assertEqual(detail['retrieval_fill']['rejected']['denied_publisher'], 1)

    def test_config_file_enables_fill_without_process_environment_changes(self):
        config = self.root / 'flags.json'
        config.write_text('{"retrieval_fill": "on"}')
        self.rows = [self.hint()]
        with patch.dict(os.environ, {'BRIEFING_FLAGS_FILE': str(config)}):
            os.environ.pop('BRIEFING_RETRIEVAL_FILL', None)
            _, _, report = self.run_fill()
            self.assertTrue(report['triggered'])
            self.assertEqual(report['candidates_added'], 1)
            self.assertNotIn('BRIEFING_RETRIEVAL_FILL', os.environ)

    def test_quality_commerce_and_regulator_exceptions(self):
        topic = {'label': 'Payments and banking'}
        for url, title, reason in [
            ('https://nerdwallet.com/travel/news', 'Chase Expands Credit Card Transfer Partners', 'denied_publisher'),
            ('https://www.bankrate.com/credit-cards/reviews/chase/', 'Chase card', 'affiliate_promo'),
            ('https://wire.example.test/deals/card', 'Card offer', 'affiliate_promo'),
            ('https://wire.example.test/news', 'Chase Expands Credit Card Transfer Partners', 'consumer_card_offer'),
            ('https://wire.example.test/news', 'Bank offers welcome bonus', 'affiliate_promo'),
            ('https://wire.example.test/news', 'Regulator caps credit card interchange fees', None),
            ('https://wire.example.test/news', 'Court rules on credit card network antitrust case', None),
            ('https://wire.example.test/credit-cards/news/fees', 'Regulator caps credit card interchange fees', None),
        ]:
            with self.subTest(title=title, url=url):
                self.assertEqual(credibility.news_quality_reason({'url': url, 'title': title},
                                 topic=topic, now=NOW), reason)


class MetadataAndReportTests(unittest.TestCase):
    def test_grounding_obeys_shared_job_cap_before_ledger_or_transport(self):
        from unittest.mock import Mock
        from dev.ingest.retrieval import RetrievalBudget
        from dev.ingest.retrieval.gemini import GeminiGroundingProvider
        from dev.ingest.retrieval.provider import ProviderUnavailable
        ledger = Mock()
        provider = GeminiGroundingProvider(usage_ledger=ledger)
        before = Mock(side_effect=RuntimeError('gemini_call_limit'))
        with patch('dev.ingest.retrieval.gemini.read_key', return_value='fictional'), \
             patch('dev.ingest.retrieval.gemini.request_json') as transport, \
             patch.object(provider.counter, 'claim') as claim:
            with self.assertRaisesRegex(ProviderUnavailable, 'gemini call cap reached'):
                provider._search('fixture', 3, 'week', RetrievalBudget(before_gemini_call=before))
        before.assert_called_once()
        claim.assert_not_called()
        transport.assert_not_called()
        self.assertEqual(ledger.mock_calls, [])

    def test_publication_only_metadata(self):
        from dev.ingest.extract.metadata import article_metadata
        self.assertEqual(article_metadata(b'<meta property="article:modified_time" content="2026-11-18">'), {})
        doc = b'<script type="application/ld+json">{"@graph":[{"@type":"NewsArticle","headline":"Fictional notice","datePublished":"2026-11-18T10:00:00Z","dateModified":"2027-01-01"}]}</script>'
        self.assertEqual(article_metadata(doc), {'title': 'Fictional notice', 'published': DATE})

    def test_guarded_read_extracts_publisher_metadata_without_persistence(self):
        from io import BytesIO
        from dev.ingest.retrieval.safety import fetch_candidate
        from dev.ingest.retrieval import RetrievalBudget
        class Response(BytesIO):
            headers = {'Content-Type': 'text/html'}
            def geturl(self): return 'https://fixture.example.test/news'
        html = ('<meta property="article:published_time" content="' + DATE + '">'
                '<meta property="og:title" content="Fictional notice"><article><p>' + BODY + '</p></article>').encode()
        hint = Candidate('https://fixture.example.test/news', 'hint', 'fixture', None, 'gemini', 1)
        metadata = {}
        with patch.object(core, '_robots_policy', return_value=(True, 0)), \
             patch.object(core, '_wait_for_host'), \
             patch('socket.create_connection', side_effect=AssertionError('offline')):
            body = fetch_candidate(hint, budget=RetrievalBudget(max_queries=0), metadata=metadata,
                                   raw_open=lambda *args: Response(html))
        self.assertIn('settlement timetable', body)
        self.assertEqual(metadata['published'], DATE)
        self.assertEqual(metadata['title'], 'Fictional notice')
        self.assertEqual(metadata['final_url'], hint.url)

    def test_run_report_carries_only_fixed_counts(self):
        from dev.ingest.daily import _daily_run_report
        value = {**fill.empty_report(True), 'candidates_added': 2,
                 'rejected': {'unknown_date': 3, 'https://secret.example.test': 5}}
        report = _daily_run_report({'status': 'complete', 'run_status': {'retrieval_fill': value}})
        import runpy
        script = runpy.run_path(str(Path(__file__).parents[1] / 'ingest/daily.py'))
        self.assertEqual(script['_daily_run_report']({'status': 'complete'})['retrieval_fill'], fill.empty_report())
        self.assertEqual(report['retrieval_fill']['candidates_added'], 2)
        self.assertEqual(report['retrieval_fill']['rejected'], {'unknown_date': 3})
        self.assertNotIn('secret', json.dumps(report))


if __name__ == '__main__':
    unittest.main()
