"""Fixture-only source preference, junk filtering, outlet diversity and reporting."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from types import SimpleNamespace

from dev.ingest.source_selection import (reserve_outlet_slot, select_daily_clusters,
                                         select_episode_reads)


NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
DATA = json.loads((Path(__file__).parent / 'fixtures' / 'source-selection.json').read_text())
AUDIT = json.loads((Path(__file__).parent / 'fixtures' / 'source-diversity-audit.json').read_text())
REGISTRY = {row['id']: row for row in DATA['registry']}
BODY = ('The fictional regulator published the rule on Wednesday. Banks and payment '
        'companies have until next spring to update settlement systems.')


def clusters():
    return [{'id': f'cluster-{index}', 'rows': [{'item': item, 'source': REGISTRY[item['source_id']]}]}
            for index, item in enumerate(DATA['items'])]


class SourceSelectionTest(unittest.TestCase):
    def test_fixture_source_diversity_audit_report(self):
        expected = AUDIT['daily_candidate_pool']
        selected, report = select_daily_clusters(clusters(), REGISTRY, DATA['topic'],
                                                  limit=8, now=NOW)
        kept = [row for row in report['items'] if row['action'] == 'kept']
        outlets = {}
        tiers = {}
        for row in kept:
            outlets[row['outlet']] = outlets.get(row['outlet'], 0) + 1
            tiers[row['tier']] = tiers.get(row['tier'], 0) + 1
        concentration = max(outlets.values()) / len(kept)
        self.assertEqual(report['considered'], expected['considered'])
        self.assertEqual(len(selected), expected['kept'])
        self.assertEqual(outlets, expected['outlet_counts'])
        self.assertEqual(tiers, expected['tier_counts'])
        self.assertEqual(round(concentration, 4), expected['max_outlet_share'])
        self.assertTrue(all(count <= report['max_per_outlet'] for count in outlets.values()))
        self.assertGreater(concentration, expected['scorecard_max_publisher_share'])

        duplicate = AUDIT['cross_outlet_duplicate']
        fixture = json.loads((Path(__file__).parent / 'fixtures' /
                              'canonical-story-clusters.json').read_text())
        registry = {row['id']: row for row in fixture['registry']}
        reads = []
        for index, item in enumerate(fixture['stories']):
            source = registry[item['source_id']]
            candidate = SimpleNamespace(url=item['url'], title=item['title'], outlet=source['name'],
                                        published=item['published'], source_id=item['source_id'], rank=index)
            reads.append((candidate, BODY))
        selected, report = select_episode_reads(reads, fixture['registry'], 'payments', now=NOW)
        dropped_clusters = [row for row in report['items']
                            if row['reason'] == duplicate['dropped_reason']]
        self.assertEqual(report['considered'], duplicate['considered'])
        self.assertEqual(len(selected), duplicate['kept'])
        self.assertEqual(len(dropped_clusters), duplicate['dropped'])
        self.assertEqual(selected[0][0].source_id, duplicate['winner_source_id'])

    def test_daily_edition_outlet_cap_is_shared_across_topic_pools(self):
        # The writer plans candidates round-robin across topics, but an outlet
        # gets one shared allowance for the whole edition.
        daily_candidates = {
            'payments': DATA['items'][3],
            'banking': DATA['items'][5],
            'insurance': DATA['items'][6],
        }
        counts = {}
        accepted = []
        for item in daily_candidates.values():
            accepted.append(reserve_outlet_slot(counts, item, REGISTRY[item['source_id']]))

        self.assertEqual(accepted, [True, True, False],
                         'the third topic cannot add another story from Payments Dive')
        self.assertEqual(counts, {'payments-dive': 2})

    def test_daily_filters_commerce_caps_outlet_and_prefers_registry_trade(self):
        selected, report = select_daily_clusters(clusters(), REGISTRY, DATA['topic'],
                                                  limit=8, now=NOW)
        titles = [cluster['rows'][0]['item']['title'] for cluster in selected]
        self.assertEqual(titles[:2], [DATA['items'][3]['title'], DATA['items'][4]['title']])
        self.assertNotIn(DATA['items'][0]['title'], titles)
        self.assertNotIn(DATA['items'][1]['title'], titles)
        self.assertNotIn(DATA['items'][2]['title'], titles)
        self.assertEqual(sum('Payments Dive' in row['outlet'] and row['action'] == 'kept'
                             for row in report['items']), 2)
        reasons = {row['title']: row['reason'] for row in report['items'] if row['action'] == 'dropped'}
        self.assertEqual(reasons[DATA['items'][0]['title']], 'denied_publisher')
        self.assertEqual(reasons[DATA['items'][1]['title']], 'listicle')
        self.assertEqual(reasons[DATA['items'][2]['title']], 'listicle')
        self.assertEqual(reasons[DATA['items'][6]['title']], 'outlet_cap_2')
        self.assertNotIn('url', json.dumps(report).lower())
        self.assertNotIn(BODY, json.dumps(report))

    def test_daily_preserves_caller_order_across_source_tiers(self):
        registry = [
            {'id': 'low', 'name': 'Low-quality site', 'tier': 'low-quality', 'topics': ['payments']},
            {'id': 'aggregator', 'name': 'News aggregator', 'tier': 'aggregator', 'topics': ['payments']},
            {'id': 'mainstream', 'name': 'Mainstream desk', 'tier': 'mainstream', 'topics': ['payments']},
            {'id': 'trade', 'name': 'Payments trade press', 'tier': 'trade', 'topics': ['payments']},
            {'id': 'regulator', 'name': 'Financial regulator', 'tier': 'regulator', 'topics': ['payments']},
        ]
        hosts = {'low': 'low.example.test', 'aggregator': 'aggregate.example.test',
                 'mainstream': 'reuters.com', 'trade': 'paymentsdive.com',
                 'regulator': 'federalreserve.gov'}
        items = [{'source_id': row['id'], 'url': f"https://{hosts[row['id']]}/news/{row['id']}",
                  'title': f"{row['name']} reports a payment policy change", 'published': '2026-11-18T10:00:00Z'}
                 for row in registry]
        candidates = [{'id': item['source_id'], 'rows': [{'item': item, 'source': registry[index]}]}
                     for index, item in enumerate(items)]
        selected, report = select_daily_clusters(candidates,
                                                  {row['id']: row for row in registry},
                                                  DATA['topic'], limit=5, now=NOW)
        self.assertEqual([row['id'] for row in selected],
                         ['low', 'aggregator', 'mainstream', 'trade', 'regulator'])
        self.assertEqual([row['tier'] for row in report['items'] if row['action'] == 'kept'],
                         ['low', 'aggregator', 'major', 'trade', 'primary'])

    def test_daily_keeps_relevant_writer_rank_ahead_of_higher_tier_source(self):
        registry = [
            {'id': 'trade', 'name': 'Payments Trade', 'tier': 'trade', 'topics': ['payments']},
            {'id': 'primary', 'name': 'Central Bank', 'tier': 'primary', 'topics': ['payments']},
        ]
        registry_by_id = {row['id']: row for row in registry}
        ranked = [
            {'id': 'writer-ranked-first', 'rows': [{'item': {
                'source_id': 'trade', 'url': 'https://payments.example.test/settlement-change',
                'title': 'Payment network changes settlement timing for merchants',
                'published': '2026-11-11T12:00:00Z'}, 'source': registry_by_id['trade']}]},
            {'id': 'writer-ranked-second', 'rows': [{'item': {
                'source_id': 'primary', 'url': 'https://centralbank.example.test/general-review',
                'title': 'Central bank reviews broad financial supervision program',
                'published': '2026-11-17T12:00:00Z'}, 'source': registry_by_id['primary']}]},
        ]

        selected, report = select_daily_clusters(ranked, registry_by_id, DATA['topic'],
                                                  limit=2, now=NOW)

        self.assertEqual([cluster['id'] for cluster in selected],
                         ['writer-ranked-first', 'writer-ranked-second'])
        self.assertEqual([row['tier'] for row in report['items'] if row['action'] == 'kept'],
                         ['trade', 'primary'])

    def test_publisher_punctuation_and_case_variants_share_outlet_cap(self):
        ranked = []
        names = ('NORTHSTAR, Media Desk', 'Northstar Media-Desk',
                 'Northstar media desk!', 'Lakeside Ledger')
        hosts = ('northstar.example.test', 'northstar.example.test',
                 'northstar.example.test', 'lakeside.example.test')
        titles = ('Northstar changes payment settlement timing',
                  'Northstar explains merchant reconciliation options',
                  'Northstar reviews bank transfer controls',
                  'Lakeside reports payment network resilience')
        ages = (7, 30, 89, 30)
        for index, (name, host, title, age) in enumerate(zip(names, hosts, titles, ages)):
            source = {'id': f'publisher-{index}', 'name': name, 'tier': 'trade',
                      'topics': ['payments']}
            item = {'source_id': source['id'], 'url': f'https://{host}/story-{index}',
                    'title': title, 'published': (NOW - timedelta(days=age)).isoformat()}
            ranked.append({'id': f'publisher-story-{index}',
                           'rows': [{'item': item, 'source': source}]})

        selected, report = select_daily_clusters(ranked, {}, DATA['topic'],
                                                  limit=3, now=NOW, max_per_outlet=2)

        self.assertEqual([row['id'] for row in selected],
                         ['publisher-story-0', 'publisher-story-1', 'publisher-story-3'])
        dropped = {row['title']: row['reason'] for row in report['items']
                   if row['action'] == 'dropped'}
        self.assertEqual(dropped[titles[2]], 'outlet_cap_2')

    def test_selected_topic_three_story_cap_keeps_independent_reporting_in_writer_order(self):
        registry = [
            {'id': 'alpha', 'name': 'Alpha Payments Desk', 'tier': 'trade', 'topics': ['payments']},
            {'id': 'beta', 'name': 'Beta Banking Review', 'tier': 'trade', 'topics': ['payments']},
            {'id': 'gamma', 'name': 'Gamma Finance Journal', 'tier': 'major', 'topics': ['payments']},
            {'id': 'junk', 'name': 'Search Tips Weekly', 'tier': 'blog', 'topics': ['payments']},
        ]
        by_id = {row['id']: row for row in registry}
        ranked_stories = [
            ('alpha', 7, 'Alpha changes payment settlement timing for merchants'),
            ('alpha', 30, 'Alpha explains new merchant reconciliation options'),
            ('alpha', 89, 'Alpha reviews payment network resilience planning'),
            ('beta', 30, 'Beta reports bank adoption of settlement controls'),
            ('gamma', 89, 'Gamma analyzes payment concentration risk'),
            ('junk', 7, 'Top 7 payment transfer tips for checking accounts'),
        ]
        candidates = []
        for index, (source_id, age, title) in enumerate(ranked_stories):
            item = {'source_id': source_id, 'url': f'https://{source_id}.example.test/story-{index}',
                    'title': title, 'published': (NOW - timedelta(days=age)).isoformat()}
            candidates.append({'id': f'story-{index}',
                               'rows': [{'item': item, 'source': by_id[source_id]}]})

        selected, report = select_daily_clusters(candidates, by_id, DATA['topic'],
                                                  limit=3, now=NOW, max_per_outlet=2)

        self.assertEqual([row['id'] for row in selected], ['story-0', 'story-1', 'story-3'])
        kept = [row for row in report['items'] if row['action'] == 'kept']
        self.assertEqual([row['outlet'] for row in kept],
                         ['Alpha Payments Desk', 'Alpha Payments Desk', 'Beta Banking Review'])
        dropped = {row['title']: row['reason'] for row in report['items']
                   if row['action'] == 'dropped'}
        self.assertEqual(dropped[ranked_stories[2][2]], 'outlet_cap_2')
        self.assertEqual(dropped[ranked_stories[5][2]], 'listicle')
        self.assertNotIn('url', json.dumps(report).lower())

    def test_unlabeled_independent_publishers_keep_writer_order_and_separate_caps(self):
        sources = {
            'northstar': {'id': 'northstar', 'tier': 'trade', 'topics': ['payments']},
            'lakeside': {'id': 'lakeside', 'tier': 'trade', 'topics': ['payments']},
            'harbor': {'id': 'harbor', 'tier': 'major', 'topics': ['payments']},
        }
        ranked_stories = [
            ('northstar', 'northstarwire.test', 'Northstar reviews payment settlement changes', 7),
            ('northstar', 'northstarwire.test', 'Northstar explains merchant reconciliation', 30),
            ('northstar', 'northstarwire.test', 'Northstar examines bank transfer operations', 89),
            ('lakeside', 'lakesideledger.test', 'Lakeside reports payment network controls', 30),
            ('harbor', 'harborbanknews.test', 'Harbor analyzes financial settlement risk', 89),
        ]
        candidates = []
        for index, (source_id, host, title, age) in enumerate(ranked_stories):
            item = {'source_id': source_id, 'url': f'https://{host}/story-{index}',
                    'title': title, 'published': (NOW - timedelta(days=age)).isoformat()}
            candidates.append({'id': f'unlabeled-{index}',
                               'rows': [{'item': item, 'source': sources[source_id]}]})

        selected, report = select_daily_clusters(candidates, {}, DATA['topic'],
                                                  limit=3, now=NOW, max_per_outlet=2)

        self.assertEqual([row['id'] for row in selected],
                         ['unlabeled-0', 'unlabeled-1', 'unlabeled-3'])
        self.assertEqual(len(selected), 3)
        dropped = {row['title']: row['reason'] for row in report['items']
                   if row['action'] == 'dropped'}
        self.assertEqual(dropped[ranked_stories[2][2]], 'outlet_cap_2')
        self.assertEqual(dropped[ranked_stories[4][2]], 'candidate_limit')

    def test_age_ranked_supply_keeps_older_items_after_empty_recent_day_and_rejects_bad_rows(self):
        registry = [
            {'id': 'northstar', 'name': 'Northstar Wire', 'tier': 'trade', 'topics': ['payments']},
            {'id': 'lakeside', 'name': 'Lakeside Ledger', 'tier': 'trade', 'topics': ['payments']},
            {'id': 'harbor', 'name': 'Harbor Business', 'tier': 'trade', 'topics': ['payments']},
            {'id': 'junk', 'name': 'Consumer Finance SEO', 'tier': 'blog', 'topics': ['payments']},
        ]
        registry_by_id = {row['id']: row for row in registry}
        days = (7, 30, 89)
        stories = [
            {'cluster_id': f'fictional-{source_id}', 'topic_ids': ['payments'],
             'rows': [{'item': {'source_id': source_id,
                                'url': f'https://{source_id}.example.test/payment-policy',
                                'title': title, 'published': (NOW - timedelta(days=age)).isoformat()},
                       'source': registry_by_id[source_id]}]}
            for source_id, title, age in zip(
                ('northstar', 'lakeside', 'harbor'),
                ('Northstar network changes payment settlement schedules',
                 'Lakeside regulator revises bank capital review',
                 'Harbor processor updates payment interchange reporting'), days)
        ]
        junk = {'cluster_id': 'fictional-junk', 'topic_ids': ['payments'],
                'rows': [{'item': {'source_id': 'junk', 'url': 'https://nerdwallet.com/article/7-payment-tips',
                                   'title': 'Top 7 payment tips for settlement transfers',
                                   'published': (NOW - timedelta(days=2)).isoformat()},
                          'source': registry_by_id['junk']}]}

        self.assertFalse(any(age in {0, 1} for age in days), 'fixture has an empty recent-day pool')
        selected, report = select_daily_clusters([junk, *stories], registry_by_id, DATA['topic'],
                                                  limit=3, now=NOW)

        selected_ages = [(NOW - datetime.fromisoformat(row['rows'][0]['item']['published'])).days
                         for row in selected]
        self.assertEqual(selected_ages, list(days),
                         'an empty today/yesterday pool must not stop selection of eligible 7–89 day items')
        self.assertEqual(len(selected), 3)
        self.assertTrue(any(row['title'] == junk['rows'][0]['item']['title'] and
                            row['reason'] in {'listicle', 'denied_publisher'} and
                            row['action'] == 'dropped' for row in report['items']))

        reads = []
        for cluster in stories:
            item = cluster['rows'][0]['item']
            candidate = SimpleNamespace(url=item['url'], title=item['title'], outlet='Fixture outlet',
                                        published=item['published'], source_id=item['source_id'], rank=0)
            reads.append((candidate, BODY))
        newest = next(candidate for candidate, _body in reads if candidate.source_id == 'northstar')
        duplicate = SimpleNamespace(url=newest.url, title=newest.title,
                                    outlet='Mirror outlet', published=newest.published,
                                    source_id='lakeside', rank=0)
        low_quality_item = junk['rows'][0]['item']
        low_quality = SimpleNamespace(url=low_quality_item['url'], title=low_quality_item['title'],
                                      outlet='Consumer Finance SEO', published=low_quality_item['published'],
                                      source_id='junk', rank=4)
        selected_reads, read_report = select_episode_reads(
            [*reads, (duplicate, BODY), (low_quality, BODY)], registry, 'payments', now=NOW)
        self.assertEqual([candidate.source_id for candidate, _body in selected_reads],
                         ['northstar', 'lakeside', 'harbor'])
        dropped_reasons = [row['reason'] for row in read_report['items'] if row['action'] == 'dropped']
        self.assertIn('duplicate_article_url', dropped_reasons)
        self.assertTrue(any(reason in {'listicle', 'denied_publisher'} for reason in dropped_reasons))

    def test_episode_read_selection_uses_same_quality_and_two_item_outlet_cap(self):
        reads = []
        for index, item in enumerate(DATA['items']):
            source = REGISTRY[item['source_id']]
            candidate = SimpleNamespace(url=item['url'], title=item['title'], outlet=source['name'],
                                        published=item['published'], source_id=item['source_id'], rank=index)
            reads.append((candidate, BODY))
        selected, report = select_episode_reads(reads, DATA['registry'], 'payments and banking', now=NOW)
        self.assertEqual([candidate.title for candidate, _body in selected],
                         [DATA['items'][3]['title'], DATA['items'][5]['title'], DATA['items'][4]['title']])
        reasons = {row['title']: row['reason'] for row in report['items'] if row['action'] == 'dropped'}
        self.assertEqual(reasons[DATA['items'][0]['title']], 'denied_publisher')
        self.assertEqual(reasons[DATA['items'][2]['title']], 'listicle')
        self.assertEqual(reasons[DATA['items'][6]['title']], 'outlet_cap_2')
        self.assertEqual(report['kept'], 3)
        self.assertEqual(report['dropped'], 4)

    def test_episode_reads_collapse_cross_outlet_canonical_story_cluster(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures' /
                              'canonical-story-clusters.json').read_text())
        reads = []
        registry = {row['id']: row for row in fixture['registry']}
        for index, item in enumerate(fixture['stories']):
            source = registry[item['source_id']]
            candidate = SimpleNamespace(url=item['url'], title=item['title'], outlet=source['name'],
                                        published=item['published'], source_id=item['source_id'], rank=index)
            reads.append((candidate, BODY))
        selected, report = select_episode_reads(reads, fixture['registry'], 'payments', now=NOW)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0][0].source_id, 'fictional-payments-dive')
        self.assertEqual(report['kept'], 1)
        self.assertEqual(report['dropped'], 1)
        kept = next(row for row in report['items'] if row['action'] == 'kept')
        dropped = next(row for row in report['items'] if row['reason'] == 'duplicate_story_cluster')
        self.assertTrue(kept['story_cluster_id'].startswith('event-'))
        self.assertEqual(dropped['reason'], 'duplicate_story_cluster')
        self.assertEqual(dropped['story_cluster_id'], kept['story_cluster_id'])
        self.assertNotIn('url', json.dumps(report).lower())
        self.assertNotIn(BODY, json.dumps(report))


if __name__ == '__main__':
    unittest.main()
