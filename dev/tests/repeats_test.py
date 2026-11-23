"""Recent-story suppression uses saved fictional editions only; no provider calls."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from dev.ingest import daily_briefing
from dev.ingest.repeats import (clean_tracking_url, filter_candidates, load_history,
                                match_story, published_stories)

ROOT = Path(__file__).resolve().parent
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


class RepeatSuppressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = json.loads((ROOT / 'fixtures/repeat-suppression.json').read_text())
        cls.history = fixture['history']
        cls.candidates = fixture['candidates']

    def test_named_recent_edition_stories_are_suppressed_and_fresh_story_passes(self):
        candidates = {key: [self.candidates[key]] for key in
                      ('zelle_sol', 'post_quantum', 'cms_delay', 'fresh')}
        filtered, counts, followups = filter_candidates(candidates, self.history, NOW)
        self.assertEqual([row['id'] for row in filtered['fresh']], ['new-federal-payment-rule'])
        self.assertEqual(sum(map(len, filtered.values())), 1)
        self.assertEqual(counts, {'repeat_suppressed': 3, 'stale_suppressed': 0})
        self.assertEqual(followups, set())

    def test_repeat_suppression_uses_only_editions_the_reader_saw(self):
        hidden = json.loads(json.dumps(self.history))
        edition = hidden[0]['editions']['editions'][0]
        edition.pop('seen_by_reader')
        self.assertEqual(published_stories(hidden), [])
        filtered, counts, _ = filter_candidates(
            {'payments': [self.candidates['zelle_sol']]}, hidden, NOW)
        self.assertEqual([row['id'] for row in filtered['payments']], ['current-zelle-solana'])
        self.assertEqual(counts['repeat_suppressed'], 0)

        edition['reader_seen_at'] = '2026-11-17T12:02:00Z'
        filtered, counts, _ = filter_candidates(
            {'payments': [self.candidates['zelle_sol']]}, hidden, NOW)
        self.assertEqual(filtered['payments'], [])
        self.assertEqual(counts['repeat_suppressed'], 1)

    def test_tracking_cleanup_keeps_article_identity_without_analytics(self):
        self.assertEqual(clean_tracking_url(
            'https://cointelegraph.com/story?utm_source=feed&id=7&fbclid=abc'),
            'https://cointelegraph.com/story?id=7')

    def test_table_driven_repeat_window_outlets_and_updates(self):
        fixture = json.loads((ROOT / 'fixtures/repeat-suppression.json').read_text())
        for case in fixture['evaluation_cases']:
            with self.subTest(case=case['id']):
                history = json.loads(json.dumps(self.history))
                if case['history'] == 'boundary':
                    edition = history[0]['editions']['editions'][0]
                    edition['generated_at'] = '2026-11-04T12:00:00Z'
                    edition['sections'] = [next(row for row in edition['sections']
                                                 if row['cluster_id'] == 'prior-cms')]
                    history[0]['sources']['sources'] = [next(row for row in history[0]['sources']['sources']
                                                               if row['id'] == 'src-cms')]
                    history[0]['sources']['sources'][0]['published_at'] = '2026-11-04T11:00:00Z'
                candidate = fixture['candidates'][case['candidate']]
                match = match_story(candidate, published_stories(history))
                if case.get('match_reason'):
                    self.assertEqual(match['reason'], case['match_reason'])
                filtered, counts, followups = filter_candidates({'fixture-topic': [candidate]}, history, NOW)
                kept = [row['id'] for row in filtered['fixture-topic']]
                if case['outcome'] == 'suppressed':
                    self.assertEqual(kept, [])
                    self.assertEqual(counts['repeat_suppressed'], 1)
                    self.assertEqual(followups, set())
                elif case['outcome'] == 'update':
                    self.assertEqual(kept, [candidate['id']])
                    self.assertEqual(followups, {candidate['id']})
                    section = {'cluster_id': candidate['id'], 'heading': candidate['rows'][0]['item']['title'],
                               'published_at': '2026-11-18T11:00:00Z', 'previous_covered_at': '2026-11-17T12:00:00Z',
                               'new_fact_supported': True, 'scorecard_passed': True,
                               'claim_links': [{'text': 'A fictional agency published a dated revision.',
                                                'verified': True, 'source_sentence_ids': ['update-1'],
                                                'citation_numbers': [1]}]}
                    labeled = daily_briefing._fill_sections([section], origin='followup', existing_clusters=set())
                    self.assertEqual(labeled[0]['kicker'], 'Update:')
                else:
                    self.assertEqual(kept, [candidate['id']])
                    self.assertEqual(followups, set())
                    self.assertEqual(counts['repeat_suppressed'], 0)

    def test_story_from_more_than_fourteen_days_ago_is_new_even_inside_history_limit(self):
        fixture = json.loads((ROOT / 'fixtures/repeat-suppression.json').read_text())
        old_bundle = json.loads(json.dumps(self.history[0]))
        old_bundle['editions']['editions'][0]['generated_at'] = '2026-11-03T11:59:59Z'
        history = [old_bundle]
        for index in range(13):
            day = f'2026-11-{17 - index:02d}T12:00:00Z'
            history.append({'editions': {'editions': [{'generated_at': day, 'sections': []}]},
                            'sources': {'sources': []}})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.json'
            path.write_text(json.dumps(history))
            loaded = load_history(path)
        self.assertEqual(len(loaded), 14)
        self.assertEqual(len(published_stories(loaded, NOW)), 0)
        filtered, counts, followups = filter_candidates(
            {'cms': [fixture['candidates']['cms_delay']]}, loaded, NOW)
        self.assertEqual([row['id'] for row in filtered['cms']], ['current-cms'])
        self.assertEqual(counts, {'repeat_suppressed': 0, 'stale_suppressed': 0})
        self.assertEqual(followups, set())

    def test_dated_followup_is_allowed_once_older_news_passes_and_unknown_news_is_removed(self):
        second_followup = {
            'id': 'cms-follow-up-second', 'rows': [{'item': {
                'title': 'CMS delays Medicaid requirements until December 20, 2026',
                'url': 'https://health.example.test/cms-second-update',
                'entities': ['CMS', 'Medicaid work requirements'],
                'published_at': '2026-11-18T11:30:00Z'}}]}
        stale = {'id': 'old-fresh-looking-headline', 'rows': [{'item': {
            'title': 'Treasury announces a new payment settlement window',
            'url': 'https://treasury.example.test/old',
            'published_at': '2026-11-16T10:00:00Z'}}]}
        undated = {'id': 'undated-lead', 'rows': [{'item': {
            'title': 'A new settlement measure takes effect',
            'url': 'https://settlement.example.test/undated'}}]}
        candidates = {'cms': [self.candidates['cms_followup'], second_followup],
                      'fresh': [self.candidates['fresh']], 'stale': [stale], 'unknown': [undated]}
        filtered, counts, followups = filter_candidates(candidates, self.history, NOW)
        self.assertEqual([row['id'] for row in filtered['cms']], ['cms-follow-up'])
        self.assertEqual(followups, {'cms-follow-up'})
        self.assertEqual([row['id'] for row in filtered['stale']], ['old-fresh-looking-headline'])
        self.assertEqual(counts, {'repeat_suppressed': 1, 'stale_suppressed': 1})

    def test_six_week_feature_is_eligible_and_reference_can_have_unknown_date(self):
        six_week = {'id': 'six-week-feature', 'rows': [{'item': {
            'title': 'A detailed payments market feature explains settlement options',
            'url': 'https://trade.example.test/six-week',
            'published_at': '2026-10-07T12:00:00Z'}}]}
        reference = {'id': 'evergreen-reference', 'rows': [{'item': {
            'title': 'A payment reference explains clearing and settlement',
            'url': 'https://reference.example.test/settlement'},
            'source': {'source_class': 'reference'}}]}
        filtered, counts, _followups = filter_candidates({'payments': [six_week, reference]}, [], NOW)
        self.assertEqual([row['id'] for row in filtered['payments']],
                         ['six-week-feature', 'evergreen-reference'])
        self.assertEqual(counts, {'repeat_suppressed': 0, 'stale_suppressed': 0})

    def test_history_load_is_limited_and_extracts_headline_source_and_cluster(self):
        fixture_path = ROOT / 'fixtures/repeat-suppression.json'
        history_rows = json.loads(fixture_path.read_text())['history']
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'editions-history.json'
            path.write_text(json.dumps(history_rows))
            history = load_history(path)
        self.assertEqual(len(history), 1)
        stories = published_stories(history)
        self.assertEqual(len(stories), 3)
        self.assertIn('https://payments.example.test/zelle-solana', stories[0]['urls'])
        self.assertEqual(stories[0]['cluster_id'], 'prior-zelle-solana')
        self.assertEqual(match_story(self.candidates['zelle_sol'], stories)['reason'], 'canonical_url')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'many-editions.json'
            path.write_text(json.dumps([{'index': index} for index in range(20)]))
            self.assertEqual([row['index'] for row in load_history(path)], list(range(20)))
            path.write_text(json.dumps([{'index': index} for index in range(140)]))
            self.assertEqual([row['index'] for row in load_history(path)], list(range(120)))


if __name__ == '__main__':
    unittest.main()
