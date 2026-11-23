import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from contextlib import redirect_stdout
from datetime import datetime, timezone, timedelta
from io import StringIO
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dev import content_check as check

NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parent / 'fixtures'
PROFILE = {'topics': [{'label': 'payments cryptography Medicaid', 'scope': 'policy standards'}]}


def fixture(day=18):
    return json.loads((FIXTURES / f'content-2026-11-{day}.json').read_text())


def result(payload=None, history=None, profile=None, now=NOW):
    return {row['name']: row for row in check.check_content(payload or fixture(), history or [], profile or PROFILE, now=now)}


class ContentTests(unittest.TestCase):
    def test_reported_three_stories_repeat_on_nov_18(self):
        repeats = result(history=[fixture(17)])['repeats']
        self.assertFalse(repeats['passed'])
        self.assertEqual(len(repeats['reasons']), 3)
        for reason in repeats['reasons']:
            self.assertIn('same source URL', reason)
            self.assertIn('headline similarity 1.00', reason)
            self.assertIn('same entities', reason)

    def test_fresh_cited_relevant_three_stories_pass_without_history(self):
        self.assertTrue(all(row['passed'] for row in result().values()))

    def test_repeat_requires_same_source_or_similar_headline_not_only_shared_entity(self):
        for keep in ('url', 'headline', 'entities'):
            old = fixture(17)
            edition, _ = check.unpack(old)
            for section in edition['sections']:
                if keep != 'headline':
                    section['heading'] = 'unrelated archival subject'
                if keep != 'entities':
                    section['entities'] = []
            if keep != 'url':
                for i, source in enumerate(old['sources']['sources']):
                    source['url'] = f'https://other.example.test/{i}'
            with self.subTest(keep=keep):
                self.assertEqual(result(history=[old])['repeats']['passed'], keep == 'entities')

    def test_similarity_threshold_and_tracking_url_canonicalization(self):
        self.assertGreaterEqual(check.similarity('a b c d e', 'a b c d f'), .6)
        self.assertLess(check.similarity('orbit telescope galaxies', 'housing rent tenants'), .6)
        self.assertEqual(check.canonical_url('http://a.test/story/?utm_medium=r&edition=2#here'),
                         'https://a.test/story?edition=2')
        self.assertNotEqual(check.canonical_url('https://a.test/?id=1'), check.canonical_url('https://a.test/?id=2'))
        self.assertEqual(check.canonical_url(
            'https://cointelegraph.com/news/tokenized-deposits?utm_source=rss&utm_medium=feed'),
            'https://cointelegraph.com/news/tokenized-deposits')

    def test_generated_but_unseen_editions_do_not_trigger_repeat_failures(self):
        old = fixture(17)
        old_edition, _ = check.unpack(old)
        old_edition.pop('seen_by_reader')
        self.assertTrue(result(history=[old])['repeats']['passed'])
        old_edition['viewed_at'] = '2026-11-17T12:05:00Z'
        self.assertFalse(result(history=[old])['repeats']['passed'])

    def test_cited_sponsored_path_fails_quality_even_when_story_text_is_neutral(self):
        payload = fixture()
        edition, sources = check.unpack(payload)
        payload['sources']['sources'].append({'id': 'src-sponsored',
                                              'url': 'https://www.paymentsdive.com/spons/survey/?utm_source=email',
                                              'published_at': '2026-11-18T07:00:00Z'})
        edition['sections'][0]['source_ids'] = ['src-sponsored']
        rows = result(payload)
        self.assertFalse(rows['quality']['passed'])
        self.assertTrue(any('sponsored content path' in reason for reason in rows['quality']['reasons']))

    def test_only_five_prior_editions_sorted_skip_current_and_future(self):
        current = fixture()
        edition, _ = check.unpack(current)
        history = [current]
        for i in range(7):
            old = fixture(17)
            e, _ = check.unpack(old)
            e['id'] = str(i)
            e['generated_at'] = (NOW - timedelta(days=i+1)).isoformat()
            history.insert(0, old)
        future = copy.deepcopy(current)
        e, _ = check.unpack(future)
        e.update(id='future', generated_at=(NOW+timedelta(days=1)).isoformat())
        history.append(future)
        selected = check.prior_editions(history, edition)
        self.assertEqual([e['id'] for e, _ in selected], ['0','1','2','3','4'])

    def test_freshness_boundary_missing_future_and_stale(self):
        for hours, passed in ((48 * 42, True), (48 * 90, True), (-1, False), (0, True)):
            payload = fixture()
            payload['sources']['sources'][0]['published_at'] = (NOW-timedelta(hours=hours)).isoformat()
            self.assertEqual(result(payload)['freshness']['passed'], passed)
        payload = fixture()
        del payload['sources']['sources'][0]['published_at']
        self.assertFalse(result(payload)['freshness']['passed'])

    def test_daily_short_script_requires_every_rung_exhausted_and_search_recorded(self):
        payload = fixture()
        edition, _ = check.unpack(payload)
        script = [{'kind': 'claim', 'text': 'briefing ' * 500}]
        edition['daily_briefing'] = {'target_spoken_words': 750, 'spoken_words': 500,
                                     'briefing_script': script,
                                     'supply_ladder': [{'rung': rung, 'status': 'exhausted'}
                                                       for rung in ('feed', 'open_web', 'followups', 'background')]}
        edition['thin_day'] = True
        rows = result(payload)
        self.assertFalse(rows['daily length']['passed'])
        edition['daily_briefing']['search_summary'] = 'Searched feeds, open web, follow-ups and references.'
        self.assertTrue(result(payload)['daily length']['passed'])
        edition['daily_briefing']['supply_ladder'][1]['status'] = 'not_configured'
        self.assertFalse(result(payload)['daily length']['passed'])
        edition['daily_briefing']['supply_ladder'].pop()
        self.assertFalse(result(payload)['daily length']['passed'])
        edition['daily_briefing']['supply_ladder'] = [
            {'rung': rung, 'status': 'exhausted'}
            for rung in ('feed', 'open_web', 'followups', 'background')]
        edition['daily_briefing']['spoken_words'] = 449
        edition['daily_briefing']['briefing_script'] = [{'kind': 'claim', 'text': 'briefing ' * 449}]
        edition['daily_briefing']['search_summary'] = 'Searched feeds, open web, follow-ups and references.'
        self.assertTrue(result(payload)['daily length']['passed'], 'duration below target is advisory')

    def test_daily_full_target_passes_and_listicle_quality_fails(self):
        payload = fixture()
        edition, _ = check.unpack(payload)
        edition['daily_briefing'] = {'target_spoken_words': 750, 'spoken_words': 750,
                                     'briefing_script': [{'text': 'briefing ' * 750}],
                                     'supply_ladder': []}
        rows = result(payload)
        self.assertTrue(rows['daily length']['passed'])
        edition['daily_briefing']['maximum_spoken_words'] = 749
        self.assertFalse(result(payload)['daily length']['passed'])
        edition['daily_briefing'].pop('maximum_spoken_words')
        edition['sections'][0]['heading'] = '5 Things to Know About the Fictional Concora Credit Mastercard'
        self.assertFalse(result(payload)['quality']['passed'])

    def test_offline_gate_keeps_short_informative_script_and_rejects_repeated_read(self):
        body_sentence = 'Zelle and Solana announced a new payment link feature. [1]'

        def payload_for(spoken_words, takeaway):
            payload = fixture()
            edition, _sources = check.unpack(payload)
            edition['sections'][0]['read_takeaway'] = takeaway
            intros = [{'kind': 'story_intro', 'text': 'Published 18 Nov.'} for _ in range(3)]
            claim_words = spoken_words - sum(len(row['text'].split()) for row in intros)
            edition['daily_briefing'] = {
                'target_spoken_words': 750,
                'minimum_spoken_words': 450,
                'maximum_spoken_words': 825,
                'narrator_words_per_minute': 150,
                'spoken_words': spoken_words,
                'briefing_script': intros + [{'kind': 'claim', 'text': 'fictional ' * claim_words}],
            }
            return payload

        short = payload_for(432, 'Payment teams can use another payment link option. [1]')
        short_rows = result(short)
        self.assertTrue(short_rows['daily length']['passed'])

        repeated = payload_for(750, body_sentence)
        repeated_rows = result(repeated)
        self.assertTrue(repeated_rows['daily length']['passed'])
        self.assertFalse(repeated_rows['boilerplate']['passed'])
        self.assertTrue(any('Read: repeats a body sentence' in reason
                            for reason in repeated_rows['boilerplate']['reasons']))

        distinct = payload_for(750, 'Payment teams can use another payment link option. [1]')
        distinct_rows = result(distinct)
        self.assertTrue(distinct_rows['daily length']['passed'])
        self.assertTrue(distinct_rows['boilerplate']['passed'])
        self.assertFalse(distinct_rows['quality']['passed'],
                         'this short-body fixture is intentionally not substantive')

        abbreviation = payload_for(750,
            "OakNorth's new U.S. national banking charter enables direct business lending. [1]")
        abbreviation_edition, _ = check.unpack(abbreviation)
        abbreviation_edition['sections'][0]['body'] = (
            'OakNorth received a U.S. national banking charter. [1] '
            'The approval lets the fictional lender serve business customers directly. [1]')
        self.assertTrue(result(abbreviation)['boilerplate']['passed'],
                        'U.S. sentence fragments must not create a false duplicate')

    def test_daily_script_dates_every_story_and_allows_undated_primary_reference(self):
        payload = fixture()
        edition, sources = check.unpack(payload)
        edition['daily_briefing'] = {
            'target_spoken_words': 750, 'spoken_words': 750,
            'briefing_script': [{'kind': 'story_intro', 'text': f'Next. Published {day} Nov.'}
                                for day in (17, 16, 15)]}
        self.assertTrue(result(payload)['freshness']['passed'])
        edition['daily_briefing']['briefing_script'][1]['text'] = 'Next.'
        self.assertFalse(result(payload)['freshness']['passed'])
        source_id = edition['sections'][1]['source_ids'][0]
        sources[source_id]['source_class'] = 'reference'
        del sources[source_id]['published_at']
        edition['daily_briefing']['briefing_script'][1]['text'] = 'Next. Date not listed in the primary source.'
        self.assertTrue(result(payload)['freshness']['passed'])

    def test_topic_ids_do_not_fake_relevance_and_citations_resolve(self):
        payload = fixture()
        e, _ = check.unpack(payload)
        e['sections'][0].update(heading='orbit galaxy', body='telescope stars [7]', topic_ids=['payments'], entities=[])
        rows = result(payload)
        self.assertFalse(rows['relevance']['passed'])
        self.assertFalse(rows['citations']['passed'])
        e['sections'][0]['body'] = 'telescope stars'
        self.assertFalse(result(payload)['citations']['passed'])

    def test_topic_assignment_cannot_borrow_another_topics_vocabulary_or_source_title(self):
        payload = fixture()
        edition, sources = check.unpack(payload)
        edition['sections'] = [edition['sections'][0]]
        story = edition['sections'][0]
        story.update(topic_ids=['payments'], heading='City expands its tenant eviction clinic',
                     body='Housing staff offer renters more help with eviction notices. [1]', entities=[])
        sources[story['source_ids'][0]]['title'] = 'Payments team expands card transfer service'
        scoped_profile = {'topics': [
            {'id': 'payments', 'label': 'Payments', 'scope': 'card settlement transfer'},
            {'id': 'housing', 'label': 'Housing', 'scope': 'tenant rent eviction'},
        ]}

        relevance = result(payload, profile=scoped_profile)['relevance']
        self.assertFalse(relevance['passed'])
        self.assertIn('story 1 (City expands its tenant eviction clinic)', relevance['reasons'][0])

    def test_story_wording_can_establish_relevance_without_matching_source_title(self):
        payload = fixture()
        edition, sources = check.unpack(payload)
        edition['sections'] = [edition['sections'][0]]
        story = edition['sections'][0]
        story.update(topic_ids=['payments'], heading='Card transfers settle in seconds',
                     body='The payment network shortened card settlement for local merchants. [1]', entities=[])
        sources[story['source_ids'][0]]['title'] = 'Tenant rent notices change this month'
        scoped_profile = {'topics': [
            {'id': 'payments', 'label': 'Payments', 'scope': 'card settlement transfer'},
            {'id': 'housing', 'label': 'Housing', 'scope': 'tenant rent eviction'},
        ]}

        self.assertTrue(result(payload, profile=scoped_profile)['relevance']['passed'])

    def test_topic_include_phrases_contribute_to_that_topics_vocabulary(self):
        payload = fixture()
        edition, _ = check.unpack(payload)
        edition['sections'] = [edition['sections'][0]]
        story = edition['sections'][0]
        story.update(topic_ids=['payments'], heading='Instant account-to-account settlement expands',
                     body='A new instant account-to-account settlement path is now available. [1]', entities=[])
        include_profile = {'topics': [
            {'id': 'payments', 'label': 'Payments', 'include': ['instant account-to-account settlement']},
            {'id': 'housing', 'label': 'Housing', 'include': ['tenant rent eviction']},
        ]}

        self.assertTrue(result(payload, profile=include_profile)['relevance']['passed'])

    def test_selected_topic_mappings_use_narrow_bank_and_public_payer_synonyms(self):
        # Fictional selected topics and stories retain their IDs exactly.
        banking = {
            'id': 'ridge-banking', 'label': 'Ridge banking',
            'scope': 'Bank supervision, deposits, lending and financial stability',
            'entities': ['bank'],
        }
        health_policy = {
            'id': 'larkspur-health-policy',
            'label': 'Larkspur health insurance policy',
            'scope': 'Larkspur state health coverage, insurance and policy',
            'include': ['health insurance', 'marketplace enrollment'],
            'entities': ['health', 'policy'],
        }

        def fictional_story(topic_id, title, body, selected_topic, entities=()):
            payload = fixture()
            edition, sources = check.unpack(payload)
            story = copy.deepcopy(edition['sections'][0])
            story.update(heading=title, body=body + ' [1]', topic_ids=[topic_id], entities=list(entities))
            edition['sections'] = [story]
            sources[story['source_ids'][0]].update(
                title=title, url='https://fictional.example.test/article',
                published_at='2026-11-18T09:00:00Z')
            return payload, {'topics': [selected_topic]}

        bank_payload, bank_profile = fictional_story(
            'ridge-banking', 'Fictional Reserve closes an action involving Valeport Bank',
            'The fictional regulator ended a written agreement involving Valeport Bank.',
            banking)
        self.assertTrue(result(bank_payload, profile=bank_profile)['relevance']['passed'])

        bank_transaction, _ = fictional_story(
            'ridge-banking', 'Valeport Bank resolves a transaction-processing disruption',
            'Valeport Bank restored transaction processing after a short delay for customers.',
            banking)
        self.assertTrue(result(bank_transaction, profile=bank_profile)['relevance']['passed'])

        cms_payload, cms_profile = fictional_story(
            'larkspur-health-policy', 'CMS proposes healthcare fraud controls for state Medicaid programs',
            'CMS proposed anti-fraud controls for Larkspur state Medicaid agencies reviewing claims.',
            health_policy)
        self.assertTrue(result(cms_payload, profile=cms_profile)['relevance']['passed'])

        federal_state_program, _ = fictional_story(
            'larkspur-health-policy', 'CMS proposes federal safeguards for Medicaid and CHIP',
            'The federal rule would require state-administered Medicaid and CHIP programs to report suspected fraud.',
            health_policy)
        self.assertTrue(result(federal_state_program, profile=cms_profile)['relevance']['passed'])

        coverage_finance, _ = fictional_story(
            'larkspur-health-policy', 'Hospital margins shift ahead of coverage reforms',
            'Fictional hospital margins fell as uncompensated care costs rose ahead of coverage reforms.',
            health_policy)
        self.assertTrue(result(coverage_finance, profile=cms_profile)['relevance']['passed'])

        hospital_finance_only, _ = fictional_story(
            'larkspur-health-policy', 'Hospital margins shift after a supply-cost increase',
            'Fictional hospital margins fell after supply costs rose.', health_policy)
        self.assertFalse(result(hospital_finance_only, profile=cms_profile)['relevance']['passed'],
                         'hospital finance without insurance coverage context stays out')

        unrelated_bank, _ = fictional_story(
            'ridge-banking', 'River bank erosion redraws a fictional flood map',
            'The river bank erosion changed a local flood map.', banking, entities=['bank'])
        self.assertFalse(result(unrelated_bank, profile=bank_profile)['relevance']['passed'])

        unrelated_health, _ = fictional_story(
            'larkspur-health-policy', 'CMS hospital opens a healthcare training wing',
            'The fictional hospital opened a training wing for clinicians.', health_policy,
            entities=['health', 'policy'])
        self.assertFalse(result(unrelated_health, profile=cms_profile)['relevance']['passed'])

        unrelated_medicare, _ = fictional_story(
            'larkspur-health-policy', 'CMS proposes a Medicare program-integrity rule',
            'CMS proposed a fraud rule for the Medicare program, leaving details to federal administrators.',
            health_policy)
        self.assertFalse(result(unrelated_medicare, profile=cms_profile)['relevance']['passed'],
                         'a Medicare fraud label without the fictional state connection is insufficient')

        national_policy, _ = fictional_story(
            'larkspur-health-policy', 'CMS proposes a nationwide Medicare claims rule',
            'CMS proposed Medicare claim safeguards that apply in every state, including Westhaven state.',
            health_policy)
        self.assertTrue(result(national_policy, profile=cms_profile)['relevance']['passed'],
                        'national applicability is accepted only when the story states it')

        other_region_only, _ = fictional_story(
            'larkspur-health-policy', 'CMS limits a Medicaid proposal to Westhaven',
            'CMS proposed fraud controls that apply only in Westhaven state Medicaid agencies.',
            health_policy)
        self.assertFalse(result(other_region_only, profile=cms_profile)['relevance']['passed'],
                         'a payer name cannot override explicit other-region-only scope')

        other_region, _ = fictional_story(
            'larkspur-health-policy', 'CMS proposes Medicaid fraud controls for Westhaven',
            'CMS proposed fraud controls for Westhaven state Medicaid agencies.', health_policy)
        self.assertFalse(result(other_region, profile=cms_profile)['relevance']['passed'],
                         'a different region must not satisfy a location-specific selected topic')

        visitor_policy, _ = fictional_story(
            'larkspur-health-policy', 'Hospital visitor policy changes for evening access',
            'The hospital changed its visitor policy for evening access.', health_policy)
        self.assertFalse(result(visitor_policy, profile=cms_profile)['relevance']['passed'],
                         'the generic word policy alone is not health-insurance relevance')

    def test_ai_finance_machine_learning_synonym_is_scoped_and_keeps_three_story_gate(self):
        topic = {'id': 'fictional-ai-finance', 'label': 'AI in finance',
                 'scope': 'Artificial intelligence in financial services, lending and payments'}
        _identifier, vocabulary, places = check.topic_vocabularies({'topics': [topic]})[0]
        self.assertTrue(check._topic_relevant(
            vocabulary,
            'A machine-learning model is used by a fictional lender to review credit applications.',
            places))
        self.assertFalse(check._topic_relevant(
            vocabulary,
            'A machine-learning model is used by a fictional university to review admissions.',
            places), 'machine learning alone must not imply a financial topic')

        payload = fixture()
        edition, _ = check.unpack(payload)
        source_rows = payload['sources']['sources']
        source_rows.clear()
        cases = [
            ('ml-lender', topic['id'], 'Fictional lender reviews machine-learning controls',
             'A fictional lender uses a machine-learning model to review credit applications. [1] '
             'Staff document how flagged cases receive a manual review. [1]'),
            ('bank-model', 'fictional-banking', 'Fictional bank adds loan model oversight',
             'A fictional bank added a review step for its loan model. [1] '
             'Supervisors will check the process during the next control assessment. [1]'),
            ('payment-ai', 'fictional-payments', 'Fictional payment network tests AI fraud controls',
             'A fictional payment network is testing AI controls for transaction fraud. [1] '
             'The network said staff can review a flagged payment before settlement. [1]'),
        ]
        topics = [topic,
                  {'id': 'fictional-banking', 'label': 'Ridge banking',
                   'scope': 'Bank supervision, lending and loan controls'},
                  {'id': 'fictional-payments', 'label': 'Fictional payments',
                   'scope': 'Payment networks, transfers and settlement'}]
        sections = []
        for index, (slug, topic_id, title, body) in enumerate(cases, 1):
            source_id = f'lexical-gap-{slug}'
            source_rows.append({'id': source_id,
                                'url': f'https://fictional-{slug}.example.test/story',
                                'title': title, 'published_at': '2026-11-18T09:00:00Z'})
            sections.append({'cluster_id': f'cluster-{slug}', 'heading': title, 'body': body,
                             'topic_ids': [topic_id], 'source_ids': [source_id], 'entities': []})
        edition['sections'] = sections
        checks = {row['name']: row for row in check.check_content(
            payload, [], {'topics': topics}, now=NOW)}
        self.assertTrue(checks['relevance']['passed'], checks['relevance']['reasons'])
        self.assertTrue(checks['citations']['passed'], checks['citations']['reasons'])
        self.assertTrue(checks['quality']['passed'], checks['quality']['reasons'])
        self.assertTrue(checks['story count']['passed'], checks['story count']['reasons'])
        self.assertEqual(len({story['cluster_id'] for story in sections}), 3)

    def test_national_medicaid_policy_matches_regional_health_topic_but_other_state_only_does_not(self):
        topic = {
            'id': 'silverpine-health-insurance',
            'label': 'Silverpine health insurance',
            'scope': 'Silverpine state health coverage and insurance',
        }

        def fictional_policy(title, body):
            payload = fixture()
            edition, sources = check.unpack(payload)
            story = copy.deepcopy(edition['sections'][0])
            story.update(heading=title, body=body + ' [1]',
                         topic_ids=['silverpine-health-insurance'], entities=[])
            edition['sections'] = [story]
            source = sources[story['source_ids'][0]]
            source.update(title=title, url='https://fictional.example.test/policy',
                          published_at='2026-11-18T09:00:00Z')
            return payload

        national = fictional_policy(
            'Fictional agency updates Medicaid eligibility rules',
            'The agency proposed new eligibility and coverage requirements for Medicaid. '
            'The notice says the requirements apply in every state.')
        self.assertTrue(result(national, profile={'topics': [topic]})['relevance']['passed'],
                        'explicitly nationwide policy qualifies without naming Silverpine')

        other_state_only = fictional_policy(
            'Fictional agency revises Westhaven Medicaid coverage',
            'The agency proposed new Medicaid coverage and eligibility rules that apply only in Westhaven state.')
        self.assertFalse(result(other_state_only, profile={'topics': [topic]})['relevance']['passed'],
                         'coverage limited to another state does not qualify for Silverpine')

    def test_daily_concise_two_fact_story_passes_without_character_quota(self):
        payload = fixture()
        edition, sources = check.unpack(payload)
        story = copy.deepcopy(edition['sections'][0])
        story.update(heading='Valeport Bank closes its review',
                     body='Valeport Bank closed its review on May 12. [1] The order applied to its deposit service. [1]',
                     topic_ids=['ridge-banking'], entities=[])
        edition['sections'] = [story]
        source = sources[story['source_ids'][0]]
        source.update(title=story['heading'], url='https://fictional.example.test/valeport-review',
                      published_at='2026-11-18T09:00:00Z')
        edition['daily_briefing'] = {'spoken_words': 120, 'target_spoken_words': 675}
        profile = {'topics': [{'id': 'ridge-banking', 'label': 'Ridge banking',
                               'scope': 'Bank supervision, deposits and lending'}]}

        self.assertLess(len(story['body']), 400)
        checks = {row['name']: row for row in check.check_content(payload, [], profile, now=NOW)}
        self.assertTrue(checks['quality']['passed'], checks['quality']['reasons'])
        self.assertTrue(checks['relevance']['passed'])
        self.assertTrue(checks['citations']['passed'])

    def test_repeating_one_cited_sentence_does_not_count_as_two_story_facts(self):
        profile = {'topics': [{'id': 'ridge-banking', 'label': 'Ridge banking',
                               'scope': 'Bank supervision, deposits and lending'}]}

        def checks_for(body):
            payload = fixture()
            edition, sources = check.unpack(payload)
            story = copy.deepcopy(edition['sections'][0])
            story.update(heading='Valeport Bank closes its review', body=body,
                         topic_ids=['ridge-banking'], entities=[])
            edition['sections'] = [story]
            source = sources[story['source_ids'][0]]
            source.update(title=story['heading'], url='https://fictional.example.test/valeport-review',
                          published_at='2026-11-18T09:00:00Z')
            edition['daily_briefing'] = {'spoken_words': 80, 'target_spoken_words': 675}
            return {row['name']: row for row in check.check_content(payload, [], profile, now=NOW)}

        repeated = checks_for(
            'Valeport Bank closed its review on May 12. [1] '
            'Valeport Bank closed its review on May 12. [1]')
        self.assertFalse(repeated['quality']['passed'])
        self.assertTrue(any('1 distinct cited sentences' in reason
                            for reason in repeated['quality']['reasons']))
        self.assertTrue(repeated['citations']['passed'])

        concise_two_fact = checks_for(
            'Valeport Bank closed its review on May 12. [1] '
            'The order applied to its deposit service. [1]')
        self.assertTrue(concise_two_fact['quality']['passed'], concise_two_fact['quality']['reasons'])
        self.assertTrue(concise_two_fact['citations']['passed'])

    def test_daily_rejects_thin_bank_story_but_three_substantive_topics_still_pass(self):
        payload = fixture()
        edition, _sources = check.unpack(payload)
        source_rows = payload['sources']['sources']
        profile = {'topics': [
            {'id': 'ridge-banking', 'label': 'Ridge banking',
             'scope': 'Bank supervision, deposits, lending and financial stability'},
            {'id': 'larkspur-health-policy', 'label': 'Larkspur health insurance policy',
             'scope': 'Larkspur state health coverage, insurance and policy',
             'include': ['health insurance', 'marketplace enrollment']},
            {'id': 'fictional-payments', 'label': 'Fictional payments',
             'scope': 'Merchant settlement schedules and payment transfers'},
        ]}
        thin_body = ('The fictional regulator ended its written agreement with Valeport Bank. '
                     'The notice confirms the start and end dates for the supervisory action, '
                     'which lasted just over two years. [1]')
        strong_bodies = [
            ('Fictional Clearpath Network moved merchant transfers from next-day settlement to a '
             'rolling same-day window, with separate cutoffs for weekend batches. [1] The published '
             'schedule applies to participating banks and gives treasury teams a predictable point '
             'for reconciling incoming funds. It changes when balances become final, not when a '
             'customer first sees an authorization. [1] The notice separates merchant payout timing '
             'from the customer-facing approval, which helps teams forecast available cash. [1]'),
            ('CMS proposed new safeguards for Larkspur state Medicaid agencies reviewing provider '
             'claims, with a staged review timetable and updated reporting duties. [1] The proposal '
             'connects federal program-integrity rules to state administration, where agencies '
             'check eligibility records and payment claims. Providers would need to follow the '
             'published reporting process when the proposal takes effect. [1] For administrators, '
             'the key change is a shared review schedule and the record providers submit at each stage. [1]'),
            ('The fictional Reserve reported that Valeport Bank has completed a supervisory '
             'remediation plan and closed its review. [1] The regulator says the bank updated its '
             'deposit controls, added a quarterly review by independent staff, and documented how '
             'exceptions reach senior managers. [1] The closure provides a concrete signal about '
             'the completed oversight process without extending the notice beyond its stated scope. [1] '
             'The notice lists the closing date and confirms that routine controls remain subject '
             'to quarterly review. [1]'),
        ]
        story_rows = [
            ('thin-bank', 'ridge-banking', 'Valeport Bank closes a fictional supervisory agreement',
             thin_body, 'The agreement has ended.'),
            ('payments', 'fictional-payments', 'Clearpath changes merchant settlement timing',
             strong_bodies[0], 'Settlement timing changed; authorization did not.'),
            ('health', 'larkspur-health-policy', 'CMS proposes safeguards for state Medicaid reviews',
             strong_bodies[1], 'The proposal connects program integrity with state administration.'),
            ('banking', 'ridge-banking', 'Valeport Bank completes a fictional remediation plan',
             strong_bodies[2], 'The regulator closed its review after documented controls were updated.'),
        ]
        sections = []
        for index, (story_id, topic_id, title, body, takeaway) in enumerate(story_rows, 1):
            source_id = f'fictional-substance-{index}'
            source_rows.append({'id': source_id,
                                'url': f'https://fictional-{index}.example.test/story',
                                'title': title, 'published_at': '2026-11-18T09:00:00Z'})
            sections.append({'cluster_id': story_id, 'heading': title, 'body': body,
                             'topic_ids': [topic_id], 'source_ids': [source_id],
                             'entities': [], 'read_takeaway': takeaway})
        edition['sections'] = sections
        edition['daily_briefing'] = {
            'target_spoken_words': 675, 'minimum_spoken_words': 675,
            'spoken_words': 499, 'supply_ladder': [],
        }

        full_checks = {row['name']: row for row in check.check_content(
            payload, [], profile, now=NOW)}
        self.assertTrue(full_checks['relevance']['passed'])
        self.assertTrue(full_checks['daily length']['passed'])
        self.assertFalse(full_checks['quality']['passed'])
        self.assertTrue(any('insufficient cited substance' in reason
                            and 'Valeport Bank closes' in reason
                            for reason in full_checks['quality']['reasons']))
        self.assertEqual(len(full_checks['quality']['reasons']), 1,
                         'the other three stories meet the cited-substance rule')
        self.assertLess(len(thin_body), 400)
        self.assertEqual(thin_body.count('[1]'), 1)

        # Once the thin bank item is omitted, the other three substantive
        # stories still cover payments, state health policy and banking.
        edition['sections'] = edition['sections'][1:]
        remaining = {row['name']: row for row in check.check_content(
            payload, [], profile, now=NOW)}
        self.assertTrue(remaining['quality']['passed'], remaining['quality']['reasons'])
        self.assertTrue(remaining['relevance']['passed'])
        self.assertTrue(remaining['daily length']['passed'])
        concise_spoken = (
            'Three updates today. Clearpath moved participating merchant transfers into a same-day '
            'settlement window, with separate cutoffs for weekend batches. That changes when treasury '
            'teams can reconcile final funds, while customer authorization still happens earlier. '
            'CMS proposed fraud controls for Larkspur state Medicaid agencies, adding staged reviews '
            'and updated reporting duties. The proposal ties federal program integrity to state '
            'administration. Valeport Bank completed its remediation plan. The fictional regulator '
            'says controls are updated, independent quarterly checks are in place, and exceptions '
            'reach senior managers. That closes the published supervisory review.')
        edition['daily_briefing']['spoken_words'] = 100
        edition['daily_briefing']['briefing_script'] = (
            [{'kind': 'story_intro', 'text': 'Published 18 Nov.'} for _ in range(3)] +
            [{'kind': 'claim', 'text': concise_spoken}])
        concise = {row['name']: row for row in check.check_content(payload, [], profile, now=NOW)}
        self.assertTrue(concise['quality']['passed'])
        self.assertTrue(concise['relevance']['passed'])
        self.assertTrue(concise['daily length']['passed'])
        self.assertIn('duration review', concise['daily length']['review'])
        self.assertTrue(all(row['passed'] for row in concise.values()),
                        {name: row['reasons'] for name, row in concise.items() if not row['passed']})
        self.assertIn('duration review', '\n'.join(check.format_checks(list(concise.values()))))

        edition['sections'][1]['read_takeaway'] = edition['sections'][0]['read_takeaway']
        duplicate_takeaway = {row['name']: row for row in check.check_content(
            payload, [], profile, now=NOW)}['quality']
        self.assertFalse(duplicate_takeaway['passed'])
        self.assertTrue(any('takeaway duplicates story' in reason
                            for reason in duplicate_takeaway['reasons']))

    def test_four_relevant_stories_at_499_words_pass_below_five_minute_aim(self):
        payload = fixture()
        edition, _sources = check.unpack(payload)
        fourth = copy.deepcopy(edition['sections'][0])
        fourth.update(heading='Fictional card settlement schedule changes',
                      body='A payment network changed its settlement schedule for merchants. [1]',
                      topic_ids=['payments'], entities=[])
        edition['sections'].append(fourth)
        edition['daily_briefing'] = {
            'target_spoken_words': 675, 'minimum_spoken_words': 675,
            'spoken_words': 499,
            'supply_ladder': [],
        }
        length = result(payload)['daily length']
        self.assertTrue(length['passed'], length['reasons'])

        edition['daily_briefing']['spoken_words'] = 99
        edition['daily_briefing']['briefing_script'] = [{'kind': 'claim', 'text': 'Three updates today.'}]
        checks = {row['name']: row for row in check.check_content(payload, [], PROFILE, now=NOW)}
        self.assertTrue(checks['daily length']['passed'], checks['daily length']['reasons'])
        self.assertIn('duration review', checks['daily length']['review'])

        edition['sections'] = edition['sections'][:2]
        checks = {row['name']: row for row in check.check_content(payload, [], PROFILE, now=NOW)}
        self.assertTrue(checks['story count']['passed'],
                        'a useful shorter edition is valid and must not be padded to three stories')

    def test_limits_and_repeated_read_sentence_fail(self):
        payload = fixture()
        e, _ = check.unpack(payload)
        e['sections'][0]['body'] = 'Payments changed yesterday. [1]\nRead: Payments changed yesterday.\nLimits: preliminary'
        self.assertEqual(len(result(payload)['boilerplate']['reasons']), 2)
        e['sections'][0]['body'] = 'Payments changed yesterday. [1]\nRead: The wider policy context.'
        self.assertTrue(result(payload)['boilerplate']['passed'])
        e['sections'][0]['read_takeaway'] = 'Payments changed yesterday.'
        self.assertFalse(result(payload)['boilerplate']['passed'])

    def test_missing_history_profile_and_zero_stories_are_failures(self):
        payload = fixture()
        e, _ = check.unpack(payload)
        e['sections'].clear()
        rows = {row['name']: row for row in check.check_content(payload, None, {}, now=NOW)}
        for name in ('repeats', 'relevance', 'story count'):
            self.assertFalse(rows[name]['passed'])

    def test_offline_cli_never_falls_back_to_network(self):
        with patch.object(check, 'urlopen', side_effect=AssertionError('no network')), redirect_stdout(StringIO()):
            self.assertEqual(check.main([str(FIXTURES/'content-2026-11-18.json'), '--now', NOW.isoformat()]), 1)

    def test_cli_failure_and_success_exit_codes(self):
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(StringIO()):
            p = Path(temp)
            (p/'edition.json').write_text(json.dumps(fixture()))
            (p/'profile.json').write_text(json.dumps(PROFILE))
            (p/'history.json').write_text('[]')
            args = [str(p/'edition.json'), '--profile', str(p/'profile.json'), '--history', str(p/'history.json'), '--now', NOW.isoformat()]
            self.assertEqual(check.main(args), 0)
            (p/'history.json').write_text(json.dumps([fixture(17)]))
            self.assertEqual(check.main(args), 1)

class IntegrationTests(unittest.TestCase):
    def test_acceptance_exports_actual_produced_edition_and_history(self):
        from dev import acceptance_real_build as acceptance
        from dev.ingest.paths import profile_identifier
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(StringIO()):
            state = Path(temp)
            directory = state / 'generated/profiles' / profile_identifier(PROFILE)
            directory.mkdir(parents=True)
            (directory/'editions-history.json').write_text(json.dumps([fixture(17)]))
            report = state/'content.json'
            edition, sources = check.unpack(fixture())
            job = {'status': 'complete', 'edition_ready': {'edition': edition, 'sources': list(sources.values())}}
            acceptance._inspect(state, PROFILE, job, 0, content_report=report)
            data = json.loads(report.read_text())
            self.assertEqual(data['payload'], job['edition_ready'])
            rows = check.check_content(data['payload'], data['history'], data['profile'], now=NOW)
            self.assertFalse(next(row for row in rows if row['name'] == 'repeats')['passed'])
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)

    def test_morning_report_fails_on_repeated_content_even_with_green_smoke(self):
        from dev import morning_check
        edition, sources = check.unpack(fixture())
        payload = {'generated': True, 'edition': edition, 'sources': list(sources.values()), 'history': [fixture(17)]}
        def fetch(url):
            if url.endswith('/api/edition?profile=active'):
                return payload
            if url.endswith('/api/profile'):
                return PROFILE
            return {'ok': True}
        lines, status = morning_check.summarize('http://example.test', lambda _: [('smoke', True, 'ok')], fetch,
                                               today=NOW.date(), health_reader=lambda: {})
        self.assertEqual(status, 1)
        self.assertTrue(any('Content: FAIL repeats:' in line for line in lines))
        self.assertIn('content checks', lines[2])


if __name__ == '__main__':
    unittest.main()
