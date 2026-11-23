"""Offline tests for the five-minute briefing assembler and supply hooks."""

import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dev.ingest import daily_briefing as daily
from dev.ingest import topic_weighting

FIXTURE = ROOT / 'dev/tests/fixtures/daily-briefing.json'
PROFESSIONAL_LIBRARY = ROOT / 'dev/tests/fixtures/professional-topic-library.json'
NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)


class DailyBriefingTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(FIXTURE.read_text(encoding='utf-8'))

    def fresh_copy(self):
        return json.loads(json.dumps(self.data['edition']))

    def test_shared_professional_topic_evidence_flows_into_daily_scripts(self):
        library = json.loads(PROFESSIONAL_LIBRARY.read_text(encoding='utf-8'))
        self.assertTrue(library['fixture_only'])
        self.assertEqual(len(library['topics']), 10)
        for topic in library['topics']:
            with self.subTest(topic=topic['id']):
                source = topic['source']
                topic_profile = {'topics': [{key: topic[key] for key in ('id', 'label', 'scope', 'include')} ]}
                story_id = f"library-story-{topic['id']}"
                claims = [
                    {'section': 'what_happened' if index == 0 else 'things_to_consider',
                     'text': row['text'], 'verified': True,
                     'source_sentence_ids': [f'{story_id}-sentence-{index + 1}'],
                     'citation_numbers': [1]}
                    for index, row in enumerate(topic['questions'])
                ]
                edition = self.fresh_copy()
                edition['sections'] = [{
                    'cluster_id': story_id, 'heading': source['title'], 'topic_ids': [topic['id']],
                    'source_ids': [source['id']], 'claim_links': claims, 'scorecard_passed': True,
                    'relevance_score': .9, 'credibility_score': .95,
                    'published_at': source['published_at'],
                }]
                result = daily.assemble(
                    edition, topic_profile, target=3, today=NOW.date(),
                    source_dates={source['id']: {'published_at': source['published_at'], 'citation_number': 1}})
                self.assertEqual([row['cluster_id'] for row in result['sections']], [story_id])
                coverage = result['daily_briefing']['topic_allocation']['coverage']
                self.assertEqual(len(coverage), 1)
                self.assertEqual(coverage[0]['topic_id'], topic['id'])
                self.assertTrue(coverage[0]['has_item'])
                used_ids = {sentence_id for row in result['daily_briefing']['briefing_script']
                            for sentence_id in row.get('source_sentence_ids', [])}
                self.assertEqual(used_ids, {claim['source_sentence_ids'][0] for claim in claims})

    def test_default_length_and_flags_freshness_window(self):
        self.assertEqual(daily.target_minutes('5'), 5)
        self.assertEqual(daily.target_minutes('bad'), 5)
        self.assertEqual(daily.narrator_words_per_minute(self.data['profile'], 120), 120)
        self.assertEqual(daily.narrator_words_per_minute(self.data['profile'], 20), 150)
        self.assertEqual(daily.freshness_days('/missing/flags.json'), 90)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'flags.json'
            path.write_text(json.dumps({'freshness_days': 45}))
            self.assertEqual(daily.freshness_days(path), 45)
            path.write_text(json.dumps({'freshness_days': 0}))
            self.assertEqual(daily.freshness_days(path), 90)

    def test_duration_uses_selected_narrator_speed_and_daily_floor_is_three_minutes(self):
        edition = daily.assemble(self.fresh_copy(), self.data['profile'], target=1,
                                 narrator_wpm=120, today=NOW.date())
        metadata = edition['daily_briefing']
        self.assertEqual(metadata['target_minutes'], 3)
        self.assertEqual(metadata['target_spoken_words'], 360)
        self.assertEqual(metadata['minimum_spoken_words'], 360)
        self.assertEqual(metadata['narrator_words_per_minute'], 120)
        self.assertEqual(metadata['duration_seconds'], round(metadata['spoken_words'] * 60 / 120))

    def test_short_and_five_minute_scripts_report_duration_from_spoken_words(self):
        profile = {'topics': [{'id': 'fictional-topic', 'label': 'Fictional topic'}]}

        def assemble_claim(claim_text):
            section = {
                'cluster_id': 'fictional-duration-story',
                'heading': 'Fictional service update',
                'topic_ids': ['fictional-topic'],
                'scorecard_passed': True,
                'relevance_score': .9,
                'credibility_score': .9,
                'claim_links': [{
                    'section': 'what_happened',
                    'text': claim_text,
                    'verified': True,
                    'source_sentence_ids': ['fictional-duration-evidence'],
                    'citation_numbers': [1],
                }],
            }
            return daily.assemble({'kind': 'daily', 'sections': [section]}, profile, target=5,
                                  narrator_wpm=150, today=NOW.date())

        baseline = assemble_claim('signal.')
        framing_words = (baseline['daily_briefing']['spoken_words'] -
                         daily._spoken_words('signal.'))
        for expected_words, expected_minutes in ((450, 3.0), (750, 5.0)):
            with self.subTest(spoken_words=expected_words):
                claim = ' '.join(['signal'] * (expected_words - framing_words)) + '.'
                result = assemble_claim(claim)
                metadata = result['daily_briefing']
                actual_script_words = sum(daily._spoken_words(row['text'])
                                          for row in metadata['briefing_script'])

                self.assertEqual(actual_script_words, expected_words)
                self.assertEqual(metadata['spoken_words'], expected_words)
                self.assertEqual(result['spoken_word_count'], expected_words)
                expected_seconds = round(expected_words * 60 / metadata['narrator_words_per_minute'])
                self.assertEqual(metadata['duration_seconds'], expected_seconds)
                self.assertEqual(result['duration_seconds'], expected_seconds)
                self.assertEqual(metadata['duration_minutes'], expected_minutes)
                self.assertEqual(result['duration_minutes'], expected_minutes)
                if expected_words == 450:
                    self.assertNotEqual(metadata['duration_minutes'], metadata['target_minutes'])

    def test_four_story_shortfall_is_evidence_limited_and_fails_the_hard_floor(self):
        from dev.content_check import check_content

        topics = [{'id': f'fictional-topic-{index}', 'label': f'Fictional topic {index}'}
                  for index in range(1, 5)]
        profile = {'topics': topics}
        edition = {'kind': 'daily', 'sections': []}
        sources = []
        source_dates = {}
        expected_claim_words = 0
        expected_sentence_ids = set()
        for story_index, topic in enumerate(topics, 1):
            source_id = f'fictional-source-{story_index}'
            sentence_rows = []
            for fact_index in range(1, 5):
                sentence_id = f'fictional-story-{story_index}-fact-{fact_index}'
                sentence = (f'Fictional agency {story_index} reports verified development {fact_index}, '
                            f'which changes planning steps for organizations following '
                            f'topic {story_index} and affects their documented review process.')
                sentence_rows.append({'section': 'what_happened' if fact_index == 1 else 'things_to_consider',
                                      'text': sentence, 'verified': True,
                                      'source_sentence_ids': [sentence_id], 'citation_numbers': [1]})
                expected_claim_words += daily._spoken_words(sentence)
                expected_sentence_ids.add(sentence_id)
            sources.append({'id': source_id, 'url': f'https://fictional-{story_index}.example.test/item',
                            'published_at': '2026-11-18T08:00:00Z'})
            source_dates[source_id] = {'published_at': '2026-11-18T08:00:00Z', 'citation_number': 1}
            edition['sections'].append({
                'cluster_id': f'fictional-story-{story_index}',
                'heading': f'Fictional development {story_index}',
                'topic_ids': [topic['id']], 'source_ids': [source_id],
                'published_at': '2026-11-18T08:00:00Z',
                'scorecard_passed': True, 'relevance_score': .95, 'credibility_score': .95,
                'claim_links': sentence_rows,
            })

        result = daily.assemble(edition, profile, target=5, today=NOW.date(), source_dates=source_dates)
        metadata = result['daily_briefing']
        spoken_claims = [row for row in metadata['briefing_script'] if row['kind'] == 'claim']
        spoken_sentence_ids = {sentence_id for row in spoken_claims
                               for sentence_id in row['source_sentence_ids']}

        self.assertEqual(len(result['sections']), 4)
        self.assertEqual(spoken_sentence_ids, expected_sentence_ids)
        self.assertEqual(expected_claim_words, 352)
        self.assertEqual(sum(daily._spoken_words(row['text']) for row in spoken_claims), expected_claim_words)
        self.assertEqual(metadata['spoken_words'], 427)
        self.assertLess(metadata['spoken_words'], metadata['minimum_spoken_words'])
        self.assertEqual(metadata['nominal_target_spoken_words'], 750)
        self.assertEqual(metadata['target_spoken_words'], 675)
        checks = check_content({'edition': result, 'sources': sources}, [], profile, now=NOW)
        length_check = next(row for row in checks if row['name'] == 'daily length')
        self.assertTrue(length_check['passed'], length_check['reasons'])
        self.assertIn('duration review', length_check['review'])

    def test_opening_counts_selected_stories_and_thin_supply_is_not_padded(self):
        profile = {'topics': [{'id': 'fictional-topic', 'label': 'Fictional topic'}]}

        def story(index, *, with_claim=True):
            row = {
                'cluster_id': f'fictional-story-{index}',
                'heading': f'Fictional development {index}',
                'topic_ids': ['fictional-topic'],
                'scorecard_passed': True,
                'relevance_score': .9,
                'credibility_score': .9,
                'published_at': NOW.isoformat().replace('+00:00', 'Z'),
                'claim_links': [],
            }
            if with_claim:
                row['claim_links'] = [{
                    'section': 'what_happened',
                    'text': f'A fictional agency reports supported development {index}.',
                    'verified': True,
                    'source_sentence_ids': [f'fictional-sentence-{index}'],
                    'citation_numbers': [1],
                }]
            return row

        three = daily.assemble({'kind': 'daily', 'sections': [story(i) for i in range(1, 4)]},
                               profile, target=5, today=NOW.date())
        self.assertEqual(len(three['sections']), 3)
        self.assertEqual(three['daily_briefing']['briefing_script'][0]['text'],
                         'Good morning. Three stories today: Fictional topic.')

        retrieval_calls, followup_calls, background_calls = [], [], []

        def retrieval(*_args, **_kwargs):
            retrieval_calls.append(True)
            unsupported = story('invented', with_claim=False)
            return [story(1), unsupported]

        def followup(*_args, **_kwargs):
            followup_calls.append(True)
            return [story('unsupported-followup') | {
                'new_fact_supported': False,
                'previous_covered_at': '2026-11-17T12:00:00Z',
            }]

        recycled = {'segment_id': 'fictional-prior-reference', 'sentences': [
            {'text': 'A fictional prior reference explains this topic.',
             'source_sentence_ids': ['prior-sentence'], 'citation_numbers': [1]},
        ]}

        def background(*_args, **_kwargs):
            background_calls.append(True)
            return {
                'segment_id': recycled['segment_id'],
                'source_kind': 'reference',
                'scorecard_passed': True,
                'sentences': list(recycled['sentences']),
            }

        thin = daily.assemble({'kind': 'daily', 'sections': [story(1)]}, profile, target=5,
                              retrieval_fill=retrieval, followup_fill=followup,
                              background_fill=background, background_history=[recycled],
                              today=NOW.date())
        self.assertEqual(thin['daily_briefing']['briefing_script'][0]['text'],
                         'Good morning. Your briefing has one story.')
        self.assertEqual([row['cluster_id'] for row in thin['sections']], ['fictional-story-1'])
        self.assertEqual(thin['daily_briefing']['background_segments'], [])
        spoken_ids = {sentence_id for row in thin['daily_briefing']['briefing_script']
                      for sentence_id in row.get('source_sentence_ids', [])}
        self.assertEqual(spoken_ids, {'fictional-sentence-1'})
        self.assertTrue(retrieval_calls and followup_calls and background_calls)

    def test_daily_projection_keeps_takeaway_separate_and_preserves_claim_evidence(self):
        from dev.content_check import check_content

        edition = self.fresh_copy()
        edition['sections'] = []
        sources = []
        source_dates = {}
        for index, topic_id in enumerate(('payments', 'health-policy', 'construction'), 1):
            source_id = f'fictional-source-{index}'
            source = {'id': source_id, 'url': f'https://fictional.example.test/report-{index}',
                      'published_at': '2026-11-18T08:00:00Z', 'evidence_chars': 700}
            sources.append(source)
            source_dates[source_id] = {'published_at': source['published_at'], 'citation_number': 1}
            claims = [
                {'section': 'what_happened', 'text': ('A fictional public agency published a detailed timetable for a new program, '
                 'including the dates when participating organizations must begin using the updated process.'),
                 'verified': True, 'source_sentence_ids': [f'fictional-fact-{index}'], 'citation_numbers': [1]},
                {'section': 'why_it_matters', 'text': ('The published schedule gives participating organizations a clear planning window '
                 'before the new process takes effect across the fictional program.'),
                 'verified': True, 'source_sentence_ids': [f'fictional-impact-{index}'], 'citation_numbers': [1]},
                {'section': 'things_to_consider', 'text': ('The public notice explains which organizations are covered and identifies the '
                 'documents they should review before the transition date.'),
                 'verified': True, 'source_sentence_ids': [f'fictional-detail-{index}'], 'citation_numbers': [1]},
                {'section': 'things_to_consider', 'text': ('The notice also says local offices will publish implementation details separately, '
                 'so readers can check the later instructions for their own circumstances.'),
                 'verified': True, 'source_sentence_ids': [f'fictional-next-{index}'], 'citation_numbers': [1]},
            ]
            edition['sections'].append({
                'cluster_id': f'fictional-event-{index}', 'heading': f'Fictional program timetable {index}',
                'topic_ids': [topic_id], 'source_ids': [source_id], 'claim_links': claims,
                'read_takeaway': 'Organizations can use the notice to prepare before the transition. [1]',
                'scorecard_passed': True, 'relevance_score': .9, 'credibility_score': .95,
            })
        result = daily.assemble(
            edition, self.data['profile'], target=3, today=NOW.date(),
            source_dates=source_dates)
        section = result['sections'][0]

        self.assertEqual(section['read_takeaway'], edition['sections'][0]['read_takeaway'])
        self.assertIn('The published schedule gives participating organizations a clear planning window',
                      section['body'], 'all verified body claims remain visible')
        self.assertNotIn('Organizations can use the notice to prepare before the transition.', section['body'])
        self.assertEqual(len(section['claim_links']), 4, 'verified evidence stays attached to the story')
        spoken_claim_ids = {sentence_id for row in result['daily_briefing']['briefing_script']
                            if row.get('kind') == 'claim' for sentence_id in row.get('source_sentence_ids', [])}
        self.assertEqual(len(spoken_claim_ids), 12)

        from dev.acceptance_real_build import substantive_stories
        accepted, _assessments = substantive_stories(result, sources)
        self.assertEqual(len(accepted), 3, 'takeaway projection preserves the 3-story substantive minimum')
        checks = check_content({'edition': result, 'sources': sources}, [], self.data['profile'], now=NOW)
        boilerplate = next(row for row in checks if row['name'] == 'boilerplate')
        self.assertTrue(boilerplate['passed'], boilerplate)

    def test_behavior_allocation_is_bounded_reported_and_prefers_topics_after_floor_pass(self):
        topics = [{'id': topic_id} for topic_id in ('payments', 'health-policy', 'construction')]
        neutral = topic_weighting.allocate_words(topics, {}, 750)
        weighted = topic_weighting.allocate_words(topics, {
            'payments': 1.5, 'health-policy': 0.5, 'construction': 0.5,
            'unselected-topic': 99,
        }, 750)
        self.assertEqual(neutral, {'payments': 250, 'health-policy': 250, 'construction': 250})
        self.assertEqual(sum(weighted.values()), 750)
        self.assertEqual(max(weighted.values()), 375)
        self.assertGreater(weighted['payments'], neutral['payments'])
        self.assertGreater(weighted['health-policy'], 0, 'every topic keeps a non-zero allocation')
        report = topic_weighting.allocation_report(topics, {'payments': 1.5}, 750)
        self.assertEqual(report['minimum_items'], 1)
        self.assertEqual(report['maximum_share'], 0.5)
        self.assertIn('raised', report['topics'][0]['reason'])
        self.assertEqual(topic_weighting.allocate_words(topics[:1], {'payments': 1.5}, 750)['payments'], 750)

        candidates = [
            {'cluster_id': 'p1', 'topic_ids': ['payments'], 'relevance_score': .8, 'credibility_score': .8},
            {'cluster_id': 'h1', 'topic_ids': ['health-policy'], 'relevance_score': .8, 'credibility_score': .8},
            {'cluster_id': 'c1', 'topic_ids': ['construction'], 'relevance_score': .8, 'credibility_score': .8},
            {'cluster_id': 'p2', 'topic_ids': ['payments'], 'relevance_score': .8, 'credibility_score': .8},
        ]
        selected = daily._select_topic_sections(candidates, topics,
                                               topic_weighting.normalize_weights(topics, {'payments': 1.5}), 4)
        self.assertEqual([row['cluster_id'] for row in selected[:3]], ['p1', 'h1', 'c1'],
                         'the first item pass covers every topic before any second item')
        self.assertEqual(selected[3]['cluster_id'], 'p2', 'the extra slot follows the behavior weight')

    def test_daily_report_explains_behavior_share_and_topic_coverage(self):
        profile = json.loads(json.dumps(self.data['profile']))
        profile['behavior_weights'] = {'payments': 1.4, 'health-policy': 0.7, 'construction': 0.9}
        edition = daily.assemble(self.fresh_copy(), profile, target=1, today=NOW.date())
        allocation = edition['daily_briefing']['topic_allocation']
        self.assertEqual(allocation['source'], 'bounded behavior summary; raw events stay on device')
        self.assertEqual([row['topic_id'] for row in allocation['coverage']],
                         ['payments', 'health-policy', 'construction'])
        self.assertTrue(all(row['has_item'] for row in allocation['coverage']))
        self.assertIn('raised', allocation['topics'][0]['reason'])
        self.assertLessEqual(max(row['share'] for row in allocation['topics']), 0.5)
        self.assertLessEqual(max(row['actual_share'] for row in allocation['topics']), 0.5)

    def test_uneven_supply_accounts_for_each_topic_and_dates_older_item_plainly(self):
        profile = {'topics': [
            {'id': 'payments', 'label': 'Payments'},
            {'id': 'health-policy', 'label': 'Health policy'},
            {'id': 'construction', 'label': 'Construction'},
        ]}
        candidates = []
        source_dates = {}
        for index, (topic_id, age_days) in enumerate((
                ('payments', 1), ('payments', 4), ('payments', 8),
                ('health-policy', 42), ('construction', 9)), 1):
            story_id = f'fictional-coverage-{index}'
            source_id = f'fictional-coverage-source-{index}'
            published = NOW - timedelta(days=age_days)
            candidates.append({
                'cluster_id': story_id,
                'heading': f'Fictional {topic_id} development {index}',
                'topic_ids': [topic_id],
                'source_ids': [source_id],
                'scorecard_passed': True,
                'relevance_score': .9,
                'credibility_score': .9,
                'published_at': published.isoformat().replace('+00:00', 'Z'),
                'claim_links': [{
                    'section': 'what_happened',
                    'text': f'A fictional {topic_id} organization published development {index}.',
                    'verified': True,
                    'source_sentence_ids': [f'{story_id}-sentence'],
                    'citation_numbers': [index],
                }],
            })
            source_dates[source_id] = {
                'published_at': published.isoformat().replace('+00:00', 'Z'),
                'citation_number': index,
            }

        result = daily.assemble({'kind': 'daily', 'sections': candidates}, profile, target=1,
                                today=NOW.date(), source_dates=source_dates)

        coverage = {row['topic_id']: row for row in result['daily_briefing']['topic_allocation']['coverage']}
        self.assertEqual(set(coverage), {'payments', 'health-policy', 'construction'})
        self.assertTrue(all(row['has_item'] for row in coverage.values()))
        selected_older = next(row for row in result['sections'] if row['cluster_id'] == 'fictional-coverage-4')
        self.assertEqual(selected_older['published_at'], '2026-10-07T12:00:00Z')
        older_intro = next(row for row in result['daily_briefing']['briefing_script']
                           if row['kind'] == 'story_intro' and row['section_id'] == selected_older['cluster_id'])
        self.assertIn('Published 7 Oct [4].', older_intro['text'])
        self.assertNotRegex(older_intro['text'].casefold(), r'\b(breaking|no coverage|no stories today)\b')

    def test_daily_mix_chooses_primary_or_trade_per_topic_before_major_when_available(self):
        topics = self.data['profile']['topics']
        candidates = [
            {'cluster_id': 'payments-mainstream', 'topic_ids': ['payments'],
             'source_ids': ['payments-main'], 'relevance_score': 1.0, 'credibility_score': 1.0},
            {'cluster_id': 'payments-trade', 'topic_ids': ['payments'],
             'source_ids': ['payments-trade'], 'relevance_score': .4, 'credibility_score': .4},
            {'cluster_id': 'health-mainstream', 'topic_ids': ['health-policy'],
             'source_ids': ['health-main'], 'relevance_score': 1.0, 'credibility_score': 1.0},
            {'cluster_id': 'health-primary', 'topic_ids': ['health-policy'],
             'source_ids': ['health-primary'], 'relevance_score': .3, 'credibility_score': .3},
            {'cluster_id': 'construction-mainstream', 'topic_ids': ['construction'],
             'source_ids': ['construction-main'], 'relevance_score': .9, 'credibility_score': .9},
        ]
        tiers = {'payments-main': 'major', 'payments-trade': 'trade',
                 'health-main': 'mainstream', 'health-primary': 'regulator',
                 'construction-main': 'major'}
        edition = self.fresh_copy()
        edition['sections'] = candidates
        result = daily.assemble(edition, self.data['profile'], target=1,
                                source_tiers=tiers, today=NOW.date())
        self.assertEqual([row['cluster_id'] for row in result['sections'][:3]],
                         ['payments-trade', 'health-primary', 'construction-mainstream'])
        coverage = {row['topic_id']: row for row in result['daily_briefing']['topic_allocation']['coverage']}
        self.assertEqual(coverage['payments']['source_mix_status'], 'primary_or_trade_selected')
        self.assertEqual(coverage['health-policy']['source_mix_status'], 'primary_or_trade_selected')
        self.assertEqual(coverage['construction']['source_mix_status'], 'primary_or_trade_unavailable')
        self.assertEqual(coverage['payments']['available_source_tiers'], ['major', 'trade'])
        self.assertEqual(coverage['health-policy']['available_source_tiers'], ['major', 'primary'])

    def test_source_tier_map_uses_final_credibility_and_source_fallback(self):
        self.assertEqual(daily.source_tier_map([
            {'id': 'primary-source', 'credibility': {'tier': 'primary'}, 'tier': 'unknown'},
            {'id': 'trade-source', 'tier': 'trade'},
            {'credibility': {'tier': 'major'}},
            None,
        ]), {'primary-source': 'primary', 'trade-source': 'trade'})

    def test_freshness_ranks_but_does_not_gate_six_week_feature(self):
        older = daily.freshness_rank(1.0, 1.0, '2026-10-07T12:00:00Z', now=NOW, window_days=90)
        recent = daily.freshness_rank(0.2, 0.2, '2026-11-17T12:00:00Z', now=NOW, window_days=90)
        self.assertTrue(older['eligible'])
        self.assertEqual(older['age_days'], 42.0)
        self.assertGreater(older['score'], recent['score'], 'relevance and credibility outweigh recency')
        self.assertFalse(daily.freshness_rank(1, 1, None, now=NOW)['eligible'])
        self.assertTrue(daily.freshness_rank(1, 1, None, now=NOW, source_kind='reference')['eligible'])
        self.assertTrue(daily.freshness_rank(1, 1, None, now=NOW, background=True)['eligible'])

    def test_ui_fields_script_citations_duration_and_also_today(self):
        edition_fixture = self.fresh_copy()
        edition_fixture['sections'][0]['source_ids'] = ['fictional-source']
        edition = daily.assemble(
            edition_fixture, self.data['profile'], target=1,
            source_dates={'fictional-source': {'published_at': '2026-11-03T12:00:00Z', 'citation_number': 3}},
            today=NOW.date())
        self.assertEqual(len(edition['sections']), 3)
        lead = edition['sections'][0]
        self.assertTrue(lead['lede'].endswith('[1]'))
        self.assertTrue(lead['so_what'].endswith('[1]'))
        self.assertTrue(any(row['kind'] == 'claim' and row['citation_numbers'] == [1]
                            and row['source_sentence_ids'] == ['p1']
                            for row in edition['daily_briefing']['briefing_script']))
        self.assertEqual(edition['also_today']['label'], 'Also today · 2 more')
        self.assertGreater(edition['daily_briefing']['duration_seconds'], 0)
        self.assertEqual(edition['daily_briefing']['duration_seconds'],
                         round(edition['daily_briefing']['spoken_words'] * 60 /
                               edition['daily_briefing']['narrator_words_per_minute']))
        self.assertIn('Good morning.', edition['daily_briefing']['greeting'])
        story_intro = next(row for row in edition['daily_briefing']['briefing_script']
                           if row['kind'] == 'story_intro')
        self.assertIn('Published 3 Nov [3].', story_intro['text'])

    def test_selected_story_date_is_spoken_from_its_section_for_7_30_and_89_day_sources(self):
        source_dates = {}
        for index, age in enumerate((7, 30, 89), 1):
            published = NOW - timedelta(days=age)
            source_dates[f'fictional-date-{age}'] = {
                'published_at': published.isoformat().replace('+00:00', 'Z'),
                'citation_number': index,
            }

        for age, citation in zip((7, 30, 89), (1, 2, 3)):
            with self.subTest(age_days=age):
                published = NOW - timedelta(days=age)
                edition = self.fresh_copy()
                section = edition['sections'][0]
                section.update({
                    'source_ids': [f'fictional-date-{day}' for day in (7, 30, 89)],
                    'published_at': published.isoformat().replace('+00:00', 'Z'),
                    'scorecard_passed': True,
                    'relevance_score': .9,
                    'credibility_score': .9,
                })
                edition['sections'] = [section]

                result = daily.assemble(edition, self.data['profile'], target=1,
                                        today=NOW.date(), source_dates=source_dates)

                self.assertEqual(result['sections'][0]['published_at'], section['published_at'])
                intro = next(row for row in result['daily_briefing']['briefing_script']
                             if row['kind'] == 'story_intro')
                expected = f'Published {published.strftime("%-d %b")} [{citation}].'
                self.assertEqual(intro['text'], f'First up. {expected}')
                self.assertEqual(intro['citation_numbers'], [citation])
                self.assertNotRegex(intro['text'].casefold(), r'\b(breaking|just in|urgent|today)\b')

    def test_also_today_requires_two_quality_topic_matched_news_items(self):
        edition = self.fresh_copy()
        edition['quick_hits'] = [
            {'title': 'Fictional card network expands settlement service', 'topic_id': 'payments',
             'published_at': '2026-11-18T11:00:00Z', 'url': 'https://payments.example.test/1', 'sentences': ['The network changed its schedule.']},
            {'title': 'Fictional health agency updates coverage rules', 'topic_id': 'health-policy',
             'published_at': '2026-11-18T10:00:00Z', 'url': 'https://health.example.test/2', 'sentences': ['The change starts in June.']},
            {'title': '5 Things to Know About the Fictional Concora Credit Mastercard',
             'topic_id': 'payments', 'published_at': '2026-11-18T09:00:00Z', 'url': 'https://cards.example.test/list', 'sentences': []},
            {'title': 'Fictional outlet reviews a new payment card', 'topic_id': 'payments',
             'published_at': '2026-11-18T09:00:00Z', 'url': 'https://nerdwallet.com/review', 'sentences': ['Sponsored offer.']},
            {'title': 'Fictional payment trends', 'topic_id': 'payments',
             'published_at': '2026-11-18T09:00:00Z', 'url': 'https://payments.example.test/3', 'sentences': ['No concrete update.']},
            {'title': 'Fictional county expands housing permits', 'topic_id': 'sports',
             'published_at': '2026-11-18T09:00:00Z', 'url': 'https://local.example.test/4', 'sentences': ['A new rule passed.']},
        ]
        result = daily.assemble(edition, self.data['profile'], target=1, today=NOW.date())
        self.assertEqual([row['url'] for row in result['also_today']['items']],
                         ['https://payments.example.test/1', 'https://health.example.test/2'])
        result['quick_hits'] = result['quick_hits'][2:]
        result = daily.assemble(result, self.data['profile'], target=1, today=NOW.date())
        self.assertNotIn('also_today', result)

    def test_supply_ladder_uses_validated_open_web_followup_and_background_hooks(self):
        retrieval_calls, followup_calls, background_calls = [], [], []
        base = self.fresh_copy()
        def section(cluster, source_id, topic='payments', kicker=None):
            row = {'cluster_id': cluster, 'heading': f'Fictional {cluster} development',
                   'topic_ids': [topic], 'scorecard_passed': True,
                   'relevance_score': 1.0, 'credibility_score': 1.0,
                   'published_at': '2026-10-07T12:00:00Z',
                   'claim_links': [{'section': 'what_happened', 'text': 'A fictional agency published a new rule.',
                                    'verified': True, 'source_sentence_ids': [source_id],
                                    'citation_numbers': [1]}]}
            if kicker:
                row['kicker'] = kicker
            return row

        def retrieve(_profile, *, remaining_words, freshness_days):
            retrieval_calls.append((remaining_words, freshness_days))
            return [section('six-week-feature', 'web1', 'payments')]

        def followups(_profile, *, remaining_words):
            followup_calls.append(remaining_words)
            return [section('verified-followup', 'update1', 'health-policy') | {
                'new_fact_supported': True, 'published_at': '2026-11-18T11:00:00Z',
                'previous_covered_at': '2026-11-17T12:00:00Z'}]

        def background(topic_id, *, angle, remaining_words, exclude_ids):
            background_calls.append((topic_id, angle, remaining_words, exclude_ids))
            return {'segment_id': f'reference-{topic_id}', 'source_kind': 'reference',
                    'scorecard_passed': True, 'published_at': None,
                    'sentences': [{'text': 'A fictional reference guide explains how the program works and identifies the public office that maintains its rules.',
                                   'source_sentence_ids': [f'bg-{topic_id}'], 'citation_numbers': [2]}]}

        edition = daily.assemble(base, self.data['profile'], target=1,
                                 retrieval_fill=retrieve, followup_fill=followups,
                                 background_fill=background, today=NOW.date())
        ids = [row['cluster_id'] for row in edition['sections']]
        self.assertIn('six-week-feature', ids)
        update = next(row for row in edition['sections'] if row['cluster_id'] == 'verified-followup')
        self.assertEqual(update['kicker'], 'Update:')
        self.assertTrue(retrieval_calls and retrieval_calls[0][1] == 90)
        self.assertTrue(followup_calls)
        self.assertTrue(background_calls)
        self.assertIn('construction', [call[0] for call in background_calls],
                      'a topic without a fresh story gets its own background slot first')
        backgrounds = edition['daily_briefing']['background_segments']
        self.assertTrue(backgrounds)
        self.assertTrue(all(row['has_item'] for row in edition['daily_briefing']['topic_allocation']['coverage']))
        cited_background = next(row for row in edition['daily_briefing']['briefing_script']
                                if row['kind'] == 'background')
        self.assertEqual(cited_background['source_sentence_ids'], [f"bg-{cited_background['topic_id']}"])
        self.assertEqual(cited_background['citation_numbers'], [2])

    def test_history_blocks_repeated_explainers_and_thin_day_requires_all_rungs_exhausted(self):
        prior = [{'segment_id': 'prior-reference', 'sentences': [
            {'text': 'A fictional source explains a program.'}]}]
        edition = daily.assemble(self.fresh_copy(), self.data['profile'], target=5,
                                 retrieval_fill=lambda *_a, **_k: [],
                                 followup_fill=lambda *_a, **_k: [],
                                 background_fill=lambda topic, **_kw: {
                                     'segment_id': f'new-reference-{topic}', 'source_kind': 'reference',
                                     'scorecard_passed': True,
                                     'sentences': [{'text': 'A fictional source explains a program.',
                                                    'source_sentence_ids': ['bg1'], 'citation_numbers': [1]}]
                                 }, background_history=prior, today=NOW.date())
        self.assertTrue(edition['thin_day'])
        self.assertIn('Searched:', edition['daily_briefing']['search_summary'])
        self.assertEqual(edition['daily_briefing']['background_segments'], [])
        pending = daily.assemble(self.fresh_copy(), self.data['profile'], target=5)
        self.assertFalse(pending['thin_day'], 'unconfigured supply rungs are not falsely reported exhausted')
        self.assertTrue(any(row['status'] == 'not_configured'
                            for row in pending['daily_briefing']['supply_ladder']))
        premature = daily.assemble({**self.fresh_copy(), 'thin_day': True}, self.data['profile'], target=5)
        self.assertFalse(premature['thin_day'], 'the writer marker cannot bypass unexhausted ladder rungs')

    def test_curated_background_uses_only_rights_reviewed_reference_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = {'id': 'fictional-reference', 'title': 'Fictional agency guide',
                      'url': 'https://agency.example.test/guide', 'kind': 'Agency reference'}
            dive = {'id': 'fixture-dive', 'topic_id': 'payments', 'reviewed_at': '2026-11-16',
                    'sections': [{'id': 'fixture-mechanism', 'heading': 'How the process works',
                                  'source_ids': [source['id']],
                                  'body': 'A fictional agency describes a payment instruction. A separate step settles obligations between institutions.'}]}
            (root / 'sources.json').write_text(json.dumps({'sources': [source]}))
            (root / 'deep-dives.json').write_text(json.dumps({'deep_dives': [dive]}))
            current_sources = [{'registry_id': 'news-source'}]
            registry = [{'id': source['id'], 'url': source['url'], 'enabled': True,
                         'source_class': 'reference', 'reuse_terms': 'OBSERVED: attribute and link to agency.'}]
            provider = daily.curated_background_provider(
                current_sources, deep_dives_path=root / 'deep-dives.json',
                source_content_path=root / 'sources.json', registry=registry)
            segment = provider('payments', angle='how_it_works', remaining_words=200, exclude_ids=[])
            self.assertEqual(segment['source_kind'], 'reference')
            self.assertEqual(segment['reviewed_at'], '2026-11-16')
            self.assertEqual(segment['sentences'][0]['citation_numbers'], [2])
            self.assertEqual(current_sources[1]['registry_id'], source['id'])
            self.assertEqual(current_sources[1]['source_sentence_count'], 2)
            self.assertEqual(provider('payments', angle='how_it_works', remaining_words=200,
                                      exclude_ids=[segment['segment_id']]), None)

            disabled = daily.curated_background_provider(
                [], deep_dives_path=root / 'deep-dives.json', source_content_path=root / 'sources.json',
                registry=[{**registry[0], 'enabled': False}])
            self.assertIsNotNone(disabled('payments', angle='how_it_works', remaining_words=200,
                                          exclude_ids=[]))
            unverified = daily.curated_background_provider(
                [], deep_dives_path=root / 'deep-dives.json', source_content_path=root / 'sources.json',
                registry=[{**registry[0], 'reuse_terms': 'UNVERIFIED: terms unknown.'}])
            self.assertIsNone(unverified('payments', angle='how_it_works', remaining_words=200,
                                         exclude_ids=[]))

    def test_mocked_ladder_reaches_five_minutes_with_zero_or_one_feed_and_rotates(self):
        topic_ids = ['payments', 'health-policy', 'construction']

        def section(cluster, topic_id):
            return {'cluster_id': cluster, 'heading': f'Fictional {topic_id} update',
                    'topic_ids': [topic_id], 'scorecard_passed': True,
                    'relevance_score': .95, 'credibility_score': .9,
                    'published_at': '2026-11-17T10:00:00Z',
                    'claim_links': [{'section': 'what_happened', 'verified': True,
                                     'text': f'Fictional {topic_id} report {index} describes a verified policy development and its practical effects for people following this selected topic area.',
                                     'source_sentence_ids': [f'{cluster}-{index}'],
                                     'citation_numbers': [1]} for index in range(1, 5)]}

        def run(feed_count, day, background_history=()):
            base = self.fresh_copy()
            base['sections'] = base['sections'][:feed_count]

            def retrieval(_profile, *, remaining_words, freshness_days):
                self.assertEqual(freshness_days, 90)
                covered = {topic for row in base['sections'] for topic in row.get('topic_ids', [])}
                return [section(f'web-{topic}', topic) for topic in topic_ids if topic not in covered]

            def background(topic, *, angle, remaining_words, exclude_ids):
                lines = [f'Fictional {topic} reference angle {angle} describes a documented process step {index} and explains how it affects the selected topic and its readers.'
                         for index in range(1, 13)]
                return {'segment_id': f'{topic}-{angle}', 'source_kind': 'reference',
                        'scorecard_passed': True, 'reviewed_at': '2026-10-01',
                        'sentences': [{'text': line, 'source_sentence_ids': [f'{topic}-{angle}-{index}'],
                                       'citation_numbers': [2]}
                                      for index, line in enumerate(lines, 1)]}

            return daily.assemble(
                base, self.data['profile'], retrieval_fill=retrieval,
                followup_fill=lambda *_a, **_k: [], background_fill=background,
                background_history=background_history, today=day)

        one_feed = run(1, NOW.date())
        no_feed = run(0, NOW.date())
        for edition in (one_feed, no_feed):
            metadata = edition['daily_briefing']
            self.assertGreaterEqual(metadata['spoken_words'], metadata['target_spoken_words'])
            self.assertLessEqual(metadata['spoken_words'], metadata['maximum_spoken_words'])
        self.assertFalse(one_feed['thin_day'])
        self.assertFalse(no_feed['thin_day'])
        self.assertTrue(one_feed['daily_briefing']['background_segments'])
        saved_history = [{'editions': {'editions': [no_feed]}}]
        next_day = run(0, NOW.date() + timedelta(days=1), daily.background_history(saved_history))
        yesterday_ids = {row['segment_id'] for row in no_feed['daily_briefing']['background_segments']}
        next_ids = {row['segment_id'] for row in next_day['daily_briefing']['background_segments']}
        self.assertTrue(yesterday_ids.isdisjoint(next_ids))
        self.assertGreaterEqual(next_day['daily_briefing']['spoken_words'],
                                next_day['daily_briefing']['target_spoken_words'])

    def test_writer_supply_report_is_reflected_in_daily_ladder(self):
        edition = self.fresh_copy()
        edition['sections'][0]['supply_origin'] = 'search-first'
        edition['sections'][1]['supply_origin'] = 'followup'
        result = daily.assemble(edition, self.data['profile'], target=5,
                                open_web_report={'enabled': True, 'triggered': True,
                                                 'search_first': True, 'candidates_added': 3,
                                                 'planner_queries': 4, 'retrieval_rounds': 2,
                                                 'guarded_reads': 2, 'search_requests': 5},
                                followup_count=1, background_fill=lambda *_a, **_k: None,
                                today=NOW.date())
        ladder = {row['rung']: row for row in result['daily_briefing']['supply_ladder']}
        statuses = {rung: row['status'] for rung, row in ladder.items()}
        self.assertEqual(statuses['open_web'], 'available')
        self.assertEqual(ladder['open_web']['counts'], {'items_added': 1, 'search_requests': 5,
                         'candidates_added': 3, 'planner_queries': 4, 'retrieval_rounds': 2,
                         'guarded_reads': 2})
        self.assertEqual(statuses['followups'], 'available')

    def test_audio_hook_is_opt_in_and_passes_daily_reserve_identity(self):
        edition = {}
        calls = []
        def renderer(*args, **kwargs):
            calls.append((args, kwargs))
            return {'state': 'recorded'}
        daily.maybe_render_audio(edition, 'fictional-profile', renderer, enabled=False)
        self.assertEqual(calls, [])
        daily.maybe_render_audio(edition, 'fictional-profile', renderer, enabled=True)
        self.assertEqual(calls[0][1], {'caller': 'daily'})
        self.assertEqual(edition['audio']['state'], 'recorded')

    def test_preview_trim_keeps_daily_script_citations_and_hides_private_hook_fields(self):
        from dev.preview_server import trim_edition_payload
        edition = daily.assemble(self.fresh_copy(), self.data['profile'], target=1, today=NOW.date())
        edition['profile_id'] = 'f' * 24
        edition['daily_briefing']['private_debug'] = 'not for the client'
        payload = trim_edition_payload({'generated': True, 'edition': edition, 'sources': [], 'history': []})
        exposed = payload['edition']
        self.assertTrue(exposed['thin_day'] is False or exposed['thin_day'] is True)
        self.assertEqual(exposed['profile_id'], 'f' * 24,
                         'the client needs the active profile identity to mark the edition seen')
        self.assertIn('briefing_script', exposed['daily_briefing'])
        self.assertNotIn('private_debug', exposed['daily_briefing'])
        self.assertTrue(any(row.get('citation_numbers') for row in exposed['daily_briefing']['briefing_script']))


if __name__ == '__main__':
    unittest.main()
