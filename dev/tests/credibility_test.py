"""Fictional publishers, bodies and clock; no network or live state."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dev.ingest import credibility as c
from dev.ingest.retrieval import Candidate, merge_candidates

NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
TIERS = {host: {'tier': tier, 'origin': host, 'reviewed_at': '2026-11-18', 'reason': 'Fictional editorial fixture.'}
         for host, tier in [('agency.example', 'primary'), ('paper.example', 'major'),
                            ('trade.example', 'trade'), ('blog.example', 'blog'),
                            ('wire.example', 'major'), ('vendor.example', 'vendor')]}
BODY = 'The fictional Alder council opened a library. Residents can visit it on weekdays.'


def row(host='agency.example', *, title='Alder library opens', body=BODY, **kwargs):
    return {'item': {'url': f'https://{host}/reports/library', 'title': title,
                     'published': '2026-11-18T10:00:00Z', **kwargs},
            'source': {}, 'body': body, 'evidence_depth': 'full-text'}


def assess(value, **kwargs):
    return c.assess_source(value['item'], value['source'], body=value['body'],
                           evidence_depth=value['evidence_depth'], now=NOW, tiers=TIERS, **kwargs)


def event_rows():
    fixtures = json.loads((Path(__file__).parent / 'fixtures' / 'credibility' /
                           'event-clusters.json').read_text(encoding='utf-8'))
    return [{'id': value['id'], 'item': {key: value[key] for key in
                ('url', 'title', 'published', 'syndicated_from', 'entities') if key in value},
             'source': {'name': value['outlet'], 'publisher_group': value['publisher_group']},
             'body': value['body'], 'evidence_depth': value['evidence_depth'], 'rank': value['rank']}
            for value in fixtures]


class CredibilityTest(unittest.TestCase):
    def test_real_shaped_daily_junk_candidates_keep_three_substantive_stories(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures' / 'daily-junk-repeat.json').read_text())
        kept, dropped = [], {}
        for candidate in fixture['candidates']:
            reason = c.news_quality_reason(
                {'url': candidate['url'], 'title': candidate['title'],
                 'published': candidate['published_at']}, candidate['source'], now=NOW,
                topic={'id': candidate['topic']}, require_reviewed_tier=False)
            if reason:
                dropped[candidate['id']] = reason
            else:
                kept.append(candidate['id'])
        self.assertEqual(set(kept), {'jpmorgan-outage', 'cms-fraud-rule', 'federal-reserve-settlement'})
        self.assertEqual(dropped['hkma-swift-tokenized-deposits'], 'crypto_only_outlet')
        self.assertEqual(dropped['embedded-payments-survey-sponsored'], 'sponsored_url_path')
        self.assertEqual(dropped['sponsored-content-path'], 'sponsored_url_path')
        self.assertEqual(dropped['paid-partnership-title'], 'affiliate_promo')
        self.assertEqual(dropped['press-release-path'], 'press_release_url')
        self.assertEqual(dropped['press-release-title'], 'press_release_title')
        self.assertGreaterEqual(len(kept), 3)

    def test_tracking_parameters_are_removed_from_reader_citation_links(self):
        from dev.ingest import repeats, write_edition
        self.assertEqual(repeats.clean_tracking_url(
            'https://cointelegraph.com/news/tokenized-deposits?utm_source=feed&id=4&utm_medium=rss'),
            'https://cointelegraph.com/news/tokenized-deposits?id=4')
        source = write_edition._source_entry({'registry_id': 'fiction', 'title': 'Story',
                                              'url': 'https://example.test/story?utm_source=feed',
                                              'publisher': 'Fictional Desk'}, 1)
        self.assertEqual(source['url'], 'https://example.test/story')

    def test_event_clustering_dedupes_wires_keeps_followups_and_picks_lead(self):
        rows = event_rows()
        with patch.dict(c.WIRE_NAMES, {'fiction wire': 'fiction-wire.example'}, clear=True):
            events = c.cluster_events(rows, now=NOW, tiers=TIERS)
            results = c.assess_bundle(rows, now=NOW, tiers=TIERS, event_dedupe=True)
        by_members = {tuple(event['member_indices']): event for event in events}
        main = by_members[(0, 1, 2, 3)]
        self.assertEqual(main['lead_index'], 3)
        self.assertEqual(main['lead_outlet'], 'Riverton Planning Office')
        self.assertEqual(main['independent_origins'], 3,
                         'two wire copies count once; local and official reporting add origins')
        self.assertTrue(main['corroborated'])
        self.assertIn((4,), by_members, 'same town alone does not join a separate event')
        self.assertIn((5,), by_members, 'a follow-up outside the date window is a separate event')
        self.assertEqual(results[0]['event']['event_id'], main['event_id'])
        self.assertEqual(results[0]['independence']['independent_origins'], 3)
        self.assertTrue(results[0]['independence']['corroborated'])
        self.assertNotIn(BODY, json.dumps(results))

    def test_event_matching_requires_entities_for_fuzzy_titles_and_time_bounds(self):
        one = row(title='Riverton approves Cedar Homes plan')
        two = row('paper.example', title='Cedar Homes project starts in Riverton',
                  published='2026-11-18T12:00:00Z')
        one['item']['entities'] = ['Riverton', 'Cedar Homes']
        two['item']['entities'] = ['Riverton', 'Cedar Homes']
        self.assertEqual(len(c.cluster_events([one, two], now=NOW, tiers=TIERS)), 1)
        two['item'].pop('entities')
        one['item']['title'] = 'riverton approves cedar homes plan'
        two['item']['title'] = 'cedar homes project starts in riverton'
        self.assertEqual(len(c.cluster_events([one, two], now=NOW, tiers=TIERS)), 2)
        one['item']['title'] = 'Riverton approves Cedar Homes plan'
        two['item']['title'] = 'Cedar Homes project starts in Riverton'
        two['item']['entities'] = ['Riverton', 'Cedar Homes']
        two['item']['published'] = '2026-12-20T12:00:00Z'
        self.assertEqual(len(c.cluster_events([one, two], now=NOW, tiers=TIERS)), 2)

    def test_event_ranking_is_opt_in_and_unread_candidates_do_not_corroborate(self):
        first, second = row(), row('paper.example', title='Alder library adds weekday reading hours')
        first['item']['entities'] = second['item']['entities'] = ['Alder Library']
        second['body'] = 'Fictional library staff reported weekend hours will change after a board review.'
        second['item']['published'] = '2026-11-18T11:00:00Z'
        before = c.assess_bundle([first, second], now=NOW, tiers=TIERS)
        after = c.assess_bundle([first, second], now=NOW, tiers=TIERS, event_dedupe=True)
        self.assertNotIn('event', before[0])
        self.assertTrue(after[0]['event']['corroborated'])
        self.assertEqual(after[0]['ranking_score'], min(100, before[0]['ranking_score'] + 20))
        second['evidence_depth'] = 'headline'
        unread = c.assess_bundle([first, second], now=NOW, tiers=TIERS, event_dedupe=True)
        self.assertFalse(unread[0]['event']['corroborated'])
        self.assertEqual(unread[0]['event']['independent_origins'], 1)
    def test_seed_is_dated_explainable_and_covers_reference_hosts(self):
        from urllib.parse import urlsplit
        tiers = c.load_tiers()
        self.assertTrue(290 <= len(tiers) <= 350)
        self.assertEqual({r['tier'] for r in tiers.values()}, set(c.AUTHORITY))
        for host, entry in tiers.items():
            self.assertEqual(entry['rights_status'], 'unverified')
            self.assertTrue(entry['basis'] and entry['reason'] and c._date(entry['reviewed_at']))
            self.assertEqual(c._host(entry['evidence_url']), host)
        for source in json.loads((c.TIERS_PATH.parent / 'sources.json').read_text())['sources']:
            self.assertIsNotNone(c._entry(source['url'], tiers)[1])

    def test_tiers_longest_host_boundary_and_no_suffix_trust(self):
        self.assertEqual(assess(row('www.agency.example'))['tier'], 'primary')
        for host in ['agency.example.evil.test', 'notagency.example', 'unknown.gov', 'student.edu']:
            self.assertEqual(assess(row(host))['tier'], 'unknown')
        tiers = {**TIERS, 'blog.agency.example': TIERS['blog.example']}
        self.assertEqual(c.assess_source(row('blog.agency.example')['item'], tiers=tiers)['tier'], 'blog')

    def test_primary_requires_document_evidence_and_does_not_grant_rights(self):
        primary = assess(row())
        self.assertEqual(primary['originality']['kind'], 'primary')
        self.assertEqual(primary['rights']['status'], 'unverified')
        self.assertEqual(primary['promotion'], 'probation')
        self.assertEqual(assess(row('paper.example'))['originality']['kind'], 'secondary')
        self.assertEqual(assess(row(url='https://agency.example/'))['originality']['kind'], 'unknown')

    def test_vendor_tier_is_reviewable_but_has_low_authority(self):
        value = row('vendor.example', title='Vendor describes a fictional accelerator')
        assessment = assess(value)
        self.assertEqual(assessment['tier'], 'vendor')
        self.assertEqual(assessment['authority']['points'], c.AUTHORITY['vendor'])
        with patch.object(c, '_DEFAULT_TIERS', {**c._DEFAULT_TIERS, 'vendor.example': TIERS['vendor.example']}):
            self.assertIsNone(c.news_quality_reason(value['item'], value['source'], now=NOW))

    def test_freshness_missing_invalid_future_old_and_fetch_does_not_refresh(self):
        for date, expected in [(None, 'unknown'), ('invalid', 'unknown'), (17, 'unknown'),
                               ('2026-12-01', 'future'), ('2026-11-01', 'recent'),
                               ('2026-01-01', 'stale')]:
            with self.subTest(date=date):
                result = assess(row(published=date, updated='2026-11-18', fetched_at='2026-11-18'))
                self.assertEqual(result['freshness']['status'], expected)
                self.assertEqual(result['authority']['points'], 40)

    def test_rights_and_model_proposals_remain_separate_from_quality(self):
        value = row()
        for terms in ['', 'UNKNOWN: pending', 'UNVERIFIED: pending', 'INFERRED: government']:
            value['source'] = {'reuse_terms': terms, 'rights_verified': True}
            self.assertEqual(assess(value)['promotion'], 'probation')
        value['source'] = {'reuse_terms': 'OBSERVED: fictional permission', 'rights_verified': True}
        self.assertEqual(assess(value)['promotion'], 'reviewed')
        value['source']['model_proposed'] = True
        self.assertEqual(assess(value)['promotion'], 'probation')

    def test_wire_mirrors_owners_and_exact_copies_do_not_corroborate(self):
        first, second = row('paper.example'), row('trade.example', body=BODY + ' Wire context.')
        first['item']['byline'] = 'By Fiction Wire'
        second['item']['syndicated_from'] = 'wire.example'
        with patch.dict(c.WIRE_NAMES, {'fiction wire': 'wire.example'}, clear=True):
            results = c.assess_bundle([first, second], now=NOW, tiers=TIERS)
        self.assertEqual([r['independence']['origin'] for r in results], ['wire.example'] * 2)
        self.assertFalse(results[0]['independence']['corroborated'])
        first, second = row('paper.example'), row('trade.example')
        self.assertFalse(c.assess_bundle([first, second], tiers=TIERS)[0]['independence']['corroborated'])
        second['body'] += ' Distinct details.'
        first['source']['publisher_group'] = second['source']['publisher_group'] = 'Fictional Group'
        self.assertFalse(c.assess_bundle([first, second], tiers=TIERS)[0]['independence']['corroborated'])

    def test_wire_credit_in_body_but_not_a_quoted_mention(self):
        with patch.dict(c.WIRE_NAMES, {'fiction wire': 'wire.example'}, clear=True):
            value = row(body='ALDER (Fiction Wire) — A library opened.')
            self.assertEqual(assess(value)['independence']['origin'], 'wire.example')
            self.assertEqual(assess(value)['originality']['kind'], 'secondary')
            value['body'] = 'The director told Fiction Wire that a library opened.'
            self.assertEqual(assess(value)['independence']['origin'], 'agency.example')

    def test_two_origins_only_for_read_same_story_not_whole_search_bundle(self):
        first, second = row(), row('paper.example', body='Alder opened its new library. It has reading rooms.')
        results = c.assess_bundle([first, second], now=NOW, tiers=TIERS)
        self.assertEqual(results[0]['independence']['independent_origins'], 2)
        self.assertTrue(results[0]['independence']['corroborated'])
        self.assertNotIn(BODY, json.dumps(results))
        second['item']['title'] = 'A different event'
        self.assertFalse(c.assess_bundle([first, second], tiers=TIERS)[0]['independence']['corroborated'])
        second['item']['title'] = first['item']['title']
        second['evidence_depth'] = 'headline'
        self.assertFalse(c.assess_bundle([first, second], tiers=TIERS)[0]['independence']['corroborated'])

    def test_risks_do_not_gain_authority_and_redirect_loses_old_tier(self):
        for risk in ['hijacked', 'domain_changed', 'content_farm', 'seo_penalty']:
            value = row(); value['source'][risk] = True
            self.assertEqual(assess(value)['authority']['points'], 0)
            self.assertEqual(assess(value)['promotion'], 'probation')
        result = assess(row(final_url='https://impostor.example/reports/library'))
        self.assertEqual(result['tier'], 'unknown')

    def test_ranking_signal_is_opt_in_and_providers_are_not_origins(self):
        low = Candidate('https://blog.example/post', 'Fictional report', 'Blog', None, 'one', 1)
        high = Candidate('https://agency.example/reports/library', 'Fictional report', 'Agency', '2026-11-18', 'one', 2)
        batches = [('one', [low, high])]
        with patch.object(c, 'load_tiers', return_value=TIERS):
            before = merge_candidates(batches, max_results=2, credibility_enabled=False, now=NOW)
            after = merge_candidates(batches, max_results=2, credibility_enabled=True, now=NOW)
            repeated = merge_candidates([('one', [high]), ('two', [high])], max_results=2, now=NOW)
        self.assertEqual(before[0].url, low.url)
        self.assertEqual(after[0].url, high.url)
        self.assertFalse(repeated[0].credibility['independence']['corroborated'])

    def test_evidence_packs_keep_metadata_out_of_writer_prompt(self):
        from dev.ingest import write_edition as writer
        from dev.ingest.episodes.generate import evidence_pack
        first = row()
        first['source'].update(id='fiction', name='Fictional Council', article_access='full-text')
        cluster = {'id': 'fictional-cluster', 'rows': [first]}
        with patch.object(c, 'load_tiers', return_value=TIERS):
            citations, evidence = writer.extract_source_sentences(cluster, lambda *_: BODY, now=NOW)
            sources, episode_evidence = evidence_pack([(Candidate(first['item']['url'], 'Library', 'Fictional Council',
                       '2026-11-18', 'stub', 1), BODY)], [], NOW.isoformat())
        self.assertIn('credibility', citations[0])
        self.assertIn('credibility', writer._source_entry(citations[0], 1))
        self.assertIn('credibility', sources[0])
        self.assertNotIn('credibility', json.dumps(episode_evidence))
        prompt = writer._story_prompt({'label': 'Library'}, citations, evidence)
        clean = deepcopy(citations); clean[0].pop('credibility')
        self.assertEqual(prompt, writer._story_prompt({'label': 'Library'}, clean, evidence))
        self.assertNotIn(BODY, json.dumps(sources))

    def test_discovery_does_not_promote_inferred_or_model_asserted_permissions(self):
        from dev.ingest import discover
        candidate = {'url': 'https://fiction.example/feed', 'authority': True,
                     'original_reporting': True, 'robots_ok': True, 'topics': ['fiction'],
                     'stable_days': 7, 'reuse_terms': 'INFERRED: probably open'}
        items = [{'published': '2026-11-18', 'snippet': 'Fictional summary. ' * 20}]
        score, detail = discover.score_candidate(candidate, items, True, now=NOW)
        self.assertLess(score, 70)
        self.assertFalse(detail['rights_known'])
        candidate.update(reuse_terms='OBSERVED: claimed permission', model_proposed=True)
        score, detail = discover.score_candidate(candidate, items, True, now=NOW)
        self.assertLess(score, 70)
        self.assertFalse(detail['rights_known'])

    def test_existing_discovery_validation_handles_stale_and_hijacked_feeds(self):
        from dev.ingest import discover
        candidate = {'publisher': 'Fictional Agency', 'homepage': 'https://fictional.example/',
                     'feed_url': 'https://fictional.example/feed', 'authority': True,
                     'original_reporting': True, 'topics': ['fiction'], 'stable_days': 7,
                     'reuse_terms': 'UNKNOWN: awaiting permission'}
        items = [{'title': f'Fictional report {i}', 'url': f'https://fictional.example/report/{i}',
                  'published': '2026-01-01', 'snippet': 'Fictional summary. ' * 20} for i in range(5)]
        with patch.object(discover.core, '_robots_policy', return_value=(True, None)), \
                patch.object(discover.core, 'parse_source', return_value=items), \
                patch.object(discover, '_sample_article', return_value=True):
            fetch = lambda url, _: (200, url, 'application/rss+xml', b'fictional fixture')
            stale = discover.evaluate_candidate(candidate, fetcher=fetch, now=NOW)
            self.assertEqual(stale['reason'], 'newest_item_older_than_30_days')
            infrequent = discover.evaluate_candidate({**candidate, 'infrequent': True}, fetcher=fetch, now=NOW)
            self.assertEqual(infrequent['state'], 'probation')
            self.assertFalse(infrequent['source']['discovery_details']['fresh'])
            hijack = discover.evaluate_candidate(candidate, now=NOW,
                fetcher=lambda *_: (200, 'https://impostor.example/feed', 'application/rss+xml', b'fixture'))
            self.assertEqual(hijack['reason'], 'feed_fetch_or_redirect_invalid')

    def test_runtime_discoveries_leave_seed_and_other_reader_unchanged(self):
        from dev.ingest import discover
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); seed = root / 'seed.json'
            seed.write_text('{"version":1,"sources":[]}')
            before = seed.read_bytes()
            result = {'state': 'probation', 'source': {'id': 'fiction', 'name': 'Fictional',
                      'tier': 'probation', 'allowed_domains': {'fiction.example'}, 'reuse_terms': 'UNKNOWN'}}
            with patch.object(discover, 'evaluate_candidate', side_effect=lambda *_args, **_kw: deepcopy(result)):
                discover.run_candidates([{}], seed, state_dir=root/'reader-a')
                discover.run_candidates([{}], seed, state_dir=root/'reader-b', dry_run=True)
            self.assertEqual(seed.read_bytes(), before)
            self.assertTrue((root/'reader-a/discovered-sources.json').is_file())
            self.assertFalse((root/'reader-b').exists())
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['reader-a', 'seed.json'])
            with self.assertRaises(ValueError):
                discover.run_candidates([], seed, state_dir=c.TIERS_PATH.parent)
