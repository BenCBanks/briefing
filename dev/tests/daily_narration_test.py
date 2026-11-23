"""Fixture/replay checks for opt-in, evidence-checked daily narration."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dev.ingest.daily_audio import daily_episode_from_edition
from dev.ingest.daily_narration import (ENRICH_PROMPT, ENRICH_SCHEMA, NARRATION_MODEL_INPUT_TOKEN_LIMIT,
                                        SCRIPT_MODEL, SCRIPT_PROMPT, SCRIPT_SCHEMA, VERIFY_MODEL, VERIFY_PROMPT,
                                        VERIFY_SCHEMA,
                                        _apply_narration, _deterministically_supported,
                                        _contains_read_takeaway, _has_high_read_takeaway_overlap,
                                        _review_writer_draft,
                                        _evidence_for_story,
                                        _select_story_evidence,
                                        _spoken_text_key,
                                        _story_word_range, _target_story_words,
                                        lint_spoken_text, lint_story_set,
                                        narration_cost_estimate, write_spoken_script)

FIXTURE = Path(__file__).parent / 'fixtures/daily-narration-replay.json'
LIVE_EDITION_TEXT = Path(__file__).parent / 'fixtures/daily-narration-live-edition-text.json'
LIVE_REPLAY = Path(__file__).parent / 'fixtures/daily-narration-live-replay.json'
CONTEXT_EXAMPLES = Path(__file__).parent / 'fixtures/daily-narration-context-examples.json'
CORRECTED_REVISION_FIXTURE = Path(__file__).parent / 'fixtures/daily-narration-corrected-revision.json'
CONCISE_THREE_STORY_FIXTURE = Path(__file__).parent / 'fixtures/daily-narration-concise-three-story.json'
FOUR_STORY_SPOKEN_CONTRACT = {
    'fixture_only': True,
    'stories': [
        {'id': 'transit', 'heading': 'Fictional transit agency revises weekend schedules',
         'spoken': [
             {'text': 'Fictional transit agency revises weekend schedules.'},
             {'text': 'That matters because riders can check a published timetable before choosing a connection.'},
             {'text': 'More broadly, clear departure windows help passengers compare routes without guessing about transfer time.',
              'general_context': True},
             {'text': 'The agency lists when the change takes effect.'},
             {'text': 'That gives local operators a concrete point to review as they update signs and trip planners.'},
             {'text': 'Staff can compare missed transfers after the schedule begins.'},
         ]},
        {'id': 'payments', 'heading': 'Fictional payment network changes settlement timing',
         'spoken': [
             {'text': 'Fictional payment network changes settlement timing.'},
             {'text': 'That matters because merchants can reconcile payments against a known daily schedule.'},
             {'text': 'More broadly, predictable settlement windows help businesses manage cash without changing customer-facing rules.',
              'general_context': True},
             {'text': 'The operator says the updated process applies to small business accounts.'},
             {'text': 'That gives payment teams a clear checkpoint when they review reconciliation problems.'},
             {'text': 'Merchants can compare the new timing with their existing bank statements.'},
         ]},
        {'id': 'coverage', 'heading': 'Fictional coverage office extends appeal window',
         'spoken': [
             {'text': 'Fictional coverage office extends appeal window.'},
             {'text': 'That matters because families have more time to send documents and request a review.'},
             {'text': 'As a general rule, a clear deadline gives people a fair chance to understand the next step.',
              'general_context': True},
             {'text': 'The office says the extension covers pending applications.'},
             {'text': 'That gives advocates a specific date to share with people waiting for an appeal.'},
             {'text': 'Staff can track whether the added time helps resolve incomplete cases.'},
         ]},
        {'id': 'construction', 'heading': 'Fictional county shortens permit review period',
         'spoken': [
             {'text': 'Fictional county shortens permit review period.'},
             {'text': 'That matters because builders can plan applications around a published review timeline.'},
             {'text': 'More broadly, predictable reviews help applicants coordinate design work and financing.',
              'general_context': True},
             {'text': 'The county says the shorter period applies to standard residential permits.'},
             {'text': 'That gives contractors a concrete measure to compare with recent applications.'},
             {'text': 'Officials can see whether the revised process reduces avoidable delays.'},
         ]},
    ],
    'daily_target_words': 675,
}


def _word_count(text):
    import re
    return len(re.findall(r"\b[\w’'-]+\b", str(text or '')))


class DailyNarrationTests(unittest.TestCase):
    def test_story_evidence_uses_source_identity_not_reused_local_citation_number(self):
        sources = [
            {'id': 'bridge-source', 'publisher': 'Fictional Bridge Desk', 'title': 'Bridge notice'},
            {'id': 'clinic-source', 'publisher': 'Fictional Clinic Desk', 'title': 'Clinic notice'},
        ]
        evidence = {
            'bridge-sentence': {'text': 'The fictional bridge adds a weekend inspection.',
                                'source_number': 1, 'source_id': 'bridge-source'},
            'clinic-sentence': {'text': 'The fictional clinic extends evening appointments.',
                                'source_number': 1, 'source_id': 'clinic-source'},
        }
        bridge = {'source_ids': ['bridge-source'], 'claim_links': [{
            'source_sentence_ids': ['bridge-sentence'],
        }]}
        rows = _evidence_for_story(bridge, evidence, sources, [])
        self.assertEqual([row['id'] for row in rows], ['bridge-sentence'])
        self.assertEqual(rows[0]['source_id'], 'bridge-source')

    def test_background_evidence_stays_with_its_exact_topic(self):
        section = {'topic_ids': ['ai-chips'], 'source_ids': [], 'claim_links': []}
        backgrounds = [
            {'topic_id': 'ai-chips', 'sentences': [{'text': 'Fictional chip makers compare memory bandwidth.',
                                                    'source_sentence_ids': ['chips-bg'],
                                                    'citation_numbers': [1]}]},
            {'topic_id': 'payments', 'sentences': [{'text': 'A fictional bank explains password resets.',
                                                   'source_sentence_ids': ['password-bg'],
                                                   'citation_numbers': [2]}]},
        ]

        rows = _evidence_for_story(section, {}, [], backgrounds)

        self.assertEqual([row['id'] for row in rows], ['chips-bg'])
        self.assertNotIn('password-bg', {row['id'] for row in rows})

    def test_context_prompt_requires_topic_relevance(self):
        self.assertIn("directly explain this story's topic", SCRIPT_PROMPT)
        self.assertIn('password explainer in a story about', VERIFY_PROMPT)

    def test_depth_revision_requires_distinct_unused_evidence_and_rejects_second_thin_draft(self):
        stories, verdicts, evidence = {}, {}, {}
        context_openers = ('More broadly', 'At a high level', 'Think of it this way')
        for index in range(3):
            section_id = f'fictional-story-{index}'
            used = f'{section_id}-used'
            reported = [
                'The fictional office published a documented change for local teams.',
                'That gives staff one clear process to review before the next operations cycle.',
                'The notice also identifies a documented handoff for the review team.',
            ]
            stories[section_id] = [
                *[{'text': text, 'evidence_ids': [used], 'general_context': False}
                  for text in reported],
                {'text': (f'{context_openers[index]}, a published process helps teams compare results and '
                          'identify where ordinary handoffs need attention.'),
                 'evidence_ids': [], 'general_context': True},
            ]
            for sentence_index in range(4):
                verdicts[(section_id, sentence_index)] = True
            evidence[section_id] = [
                {'id': used, 'text': ' '.join(reported), 'type': 'reported',
                 'source_id': f'{section_id}-source', 'outlet': 'Fictional Desk',
                 'title': 'Fictional process notice', 'citation_numbers': [1]},
                *[{'id': f'{section_id}-unused-{extra}',
                   'text': ('Distinct fictional evidence explains the mechanism, affected group, '
                            'documented timing, and next operational step in useful detail.'),
                   'type': 'reported', 'source_id': f'{section_id}-source',
                   'outlet': 'Fictional Desk', 'title': 'Fictional process notice',
                   'citation_numbers': [1]} for extra in range(3)],
            ]
        _accepted, first_findings, first_fatal = _review_writer_draft(
            stories, verdicts, evidence, 215, allow_soft_style=False)
        self.assertIsNone(first_fatal)
        self.assertEqual(sum(row['code'] == 'narration_underdeveloped_with_unused_evidence'
                             for row in first_findings), 3)
        _accepted, final_findings, final_fatal = _review_writer_draft(
            stories, verdicts, evidence, 215, allow_soft_style=True)
        self.assertEqual(final_fatal, 'narration_underdeveloped_with_unused_evidence')
        aggregate = next(row for row in final_findings
                         if row.get('minimum_daily_words'))
        self.assertEqual(aggregate['minimum_daily_words'], 550)

    def test_enrichment_discards_unknown_ids_and_keeps_bounded_verified_evidence(self):
        evidence = [{'id': 'required'}, *[{'id': f'optional-{index}'} for index in range(25)]]
        stories = [{'section_id': 'fictional-story', 'heading': 'Fictional payment notice',
                    'topics': ['payments'], 'required_evidence_ids': ['required'],
                    'evidence': evidence}]

        def selector(_system, user, schema, _key, **_kwargs):
            self.assertEqual(schema, ENRICH_SCHEMA)
            self.assertEqual(json.loads(user)['stories'][0]['section_id'], 'fictional-story')
            return {'stories': [{'section_id': 'fictional-story', 'evidence_ids': [
                'invented-id', 'optional-0', 'required', 'optional-0',
                *[f'optional-{index}' for index in range(1, 25)],
            ]}]}

        selected = _select_story_evidence(stories, selector)['fictional-story']
        self.assertEqual(selected[0], 'required')
        self.assertEqual(selected[1:], [f'optional-{index}' for index in range(18)])
        self.assertNotIn('invented-id', selected)
        self.assertEqual(len(selected), 19)

    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
        self.assertTrue(self.fixture['fixture_only'])
        self.edition = deepcopy(self.fixture['edition'])
        self.profile = deepcopy(self.fixture['profile'])
        self.sources = deepcopy(self.fixture['sources'])
        self.evidence = deepcopy(self.fixture['runtime_evidence'])
        self.calls = []
    def narrate(self, *args, **kwargs):
        return write_spoken_script(*args, **kwargs)

    def replay(self, *, writer=None, verifier=None, error=None):
        writer = deepcopy(writer if writer is not None else self.fixture['writer_response'])
        verifier = deepcopy(verifier if verifier is not None else self.fixture['verifier_response'])

        def call_json(system, user, schema, api_key, **kwargs):
            self.calls.append({'system': system, 'user': user, 'schema': schema,
                               'api_key': api_key, **kwargs})
            if error:
                raise error
            if schema == ENRICH_SCHEMA:
                payload = json.loads(user)
                return {'stories': [{'section_id': story['section_id'],
                                     'evidence_ids': [row['id'] for row in story['evidence'][:18]]}
                                    for story in payload['stories']]}
            return writer if kwargs.get('model') == SCRIPT_MODEL else verifier
        return call_json


    def test_too_short_verified_narration_falls_back_after_retry(self):
        before = deepcopy(self.edition)
        before_chapters = daily_episode_from_edition(self.edition)['chapters']
        result = self.narrate(self.edition, self.profile, self.evidence, self.sources,
                                     call_json=self.replay())

        self.assertEqual(result['status'], 'fallback', result)
        self.assertEqual(result['reason'], 'narration_style_or_evidence_check')
        self.assertEqual(self.edition, before, 'too-short verified drafts leave the original edition intact')
        self.assertEqual([row['model'] for row in self.calls],
                         [VERIFY_MODEL, SCRIPT_MODEL, VERIFY_MODEL, SCRIPT_MODEL, VERIFY_MODEL])
        self.assertEqual(len(self.calls), 5,
                         'one enrichment and up to two writer/verifier pairs')
        self.assertEqual(self.calls[0]['max_output_tokens'], 1800)
        self.assertEqual(self.calls[1]['max_output_tokens'], 6500)
        self.assertEqual(self.calls[2]['max_output_tokens'], 2500)
        writer_payload = json.loads(self.calls[1]['user'])
        self.assertTrue(all(not row['id'].endswith('-bg-1') and not row['id'].endswith('-bg-2')
                            for story in writer_payload['stories'] for row in story['evidence']))
        after_chapters = daily_episode_from_edition(self.edition)['chapters']
        self.assertEqual([row['id'] for row in after_chapters], [row['id'] for row in before_chapters])
        self.assertEqual(len(after_chapters), len(before_chapters), 'fallback preserves story and background chapters')


    def test_input_token_ceiling_rejects_each_narration_stage_before_transport(self):
        self.assertEqual(NARRATION_MODEL_INPUT_TOKEN_LIMIT, 1_048_576)
        for stage in ('enrichment', 'writer', 'verifier'):
            with self.subTest(stage=stage):
                edition = deepcopy(self.edition)
                before = deepcopy(edition)
                calls = []

                def count_tokens(system_prompt, _user_text, model):
                    if stage == 'enrichment' and system_prompt.startswith(ENRICH_PROMPT.split('{max_extra}', 1)[0]):
                        return 1_048_577
                    if stage == 'writer' and model == SCRIPT_MODEL:
                        return 1_048_577
                    if stage == 'verifier' and system_prompt.startswith(VERIFY_PROMPT):
                        return 1_048_577
                    return 1

                def call_json(*args, **kwargs):
                    calls.append({'schema': args[2], **kwargs})
                    return self.replay()(*args, **kwargs)

                result = self.narrate(
                    edition, self.profile, self.evidence, self.sources,
                    call_json=call_json, input_token_counter=count_tokens)

                self.assertEqual(result['status'], 'fallback', result)
                self.assertEqual(result['reason'], 'narration_input_token_limit')
                self.assertEqual(edition, before)
                self.assertEqual(len(calls), {'enrichment': 0, 'writer': 1, 'verifier': 2}[stage])

    def test_caller_owned_smaller_input_limit_rejects_before_transport(self):
        edition = deepcopy(self.edition)
        before = deepcopy(edition)
        calls = []

        def count_tokens(_system_prompt, _user_text, _model):
            return 65

        def call_json(*args, **kwargs):
            calls.append({'schema': args[2], **kwargs})
            return self.replay()(*args, **kwargs)

        result = self.narrate(
            edition, self.profile, self.evidence, self.sources,
            call_json=call_json, input_token_counter=count_tokens,
            input_token_limit=64)

        self.assertEqual(result['status'], 'fallback', result)
        self.assertEqual(result['reason'], 'narration_input_token_limit')
        self.assertEqual(len(calls), 0,
                         'a caller-owned tighter ceiling refuses enrichment before transport')
        self.assertEqual(edition, before)
        self.assertEqual(NARRATION_MODEL_INPUT_TOKEN_LIMIT, 1_048_576,
                         'the provider maximum remains the unchanged default')

    def test_corrected_daily_revision_keeps_screen_and_spoken_claims_current(self):
        fixture = json.loads(CORRECTED_REVISION_FIXTURE.read_text(encoding='utf-8'))
        self.assertTrue(fixture['fixture_only'])
        edition = deepcopy(fixture['edition'])
        profile = deepcopy(fixture['profile'])
        old_claim = fixture['superseded_revision']['claim']
        current_claim = fixture['current_claim']
        section = edition['sections'][0]
        self.assertIn(current_claim, section['body'])
        self.assertEqual(section['claim_links'][0]['text'], current_claim)
        self.assertEqual(section['read_takeaway'], fixture['read_takeaway'])

        self.assertTrue(_apply_narration(edition, profile, fixture['stories'],
                                         fixture['evidence_by_story']))

        visible_claims = ' '.join([section['body'], *(row['text'] for row in section['claim_links'])])
        spoken_script = ' '.join(row['text'] for row in edition['daily_briefing']['briefing_script']
                                 if row.get('text'))
        spoken_audio = section['audio_script']['spoken_text']
        episode = daily_episode_from_edition(edition)
        self.assertIn('Friday, May 15', visible_claims)
        self.assertIn('Friday, May 15', spoken_script)
        self.assertNotIn(old_claim, visible_claims)
        self.assertNotIn('ends Thursday, May 14', spoken_script)
        self.assertEqual(section['read_takeaway'], fixture['read_takeaway'])
        self.assertNotIn(fixture['read_takeaway'], spoken_script)
        self.assertEqual(section['claim_links'][0]['citation_numbers'], [1])
        self.assertEqual(section['claim_links'][0]['source_sentence_ids'], ['river-correction'])
        story_row = next(row for row in edition['daily_briefing']['briefing_script']
                         if row.get('kind') == 'story_intro')
        self.assertEqual(story_row['citation_numbers'], [1])
        self.assertIn('river-correction', story_row['source_sentence_ids'])
        self.assertNotIn('river-obsolete', story_row['source_sentence_ids'])
        cited_sources = [section['source_ids'][number - 1]
                         for number in story_row['citation_numbers']]
        self.assertEqual(cited_sources, ['fictional-metro-source'])
        self.assertIn('Friday, May 15', spoken_audio)
        self.assertEqual(episode['revision_id'], edition['revision_id'])
        self.assertEqual(episode['chapters'][0]['spoken_text'], spoken_audio)
        self.assertNotIn(old_claim, episode['chapters'][0]['spoken_text'])
        self.assertIn('remains through Friday, May 15', episode['chapters'][0]['spoken_text'])
        repeated_episode = daily_episode_from_edition(edition)
        self.assertFalse(any(old_claim in chapter['spoken_text']
                             for chapter in repeated_episode['chapters']),
                         'a repeated export continues to omit the superseded claim')
        expected_hash = hashlib.sha256(spoken_script.encode('utf-8')).hexdigest()
        self.assertEqual(edition['script_hash'], expected_hash)
        self.assertEqual(edition['audio']['script_hash'], expected_hash)
        self.assertEqual(edition['audio']['revision_id'], edition['revision_id'])
        self.assertEqual(edition['revision_id'], 'daily-' + expected_hash[:16])

    def test_older_materially_corrected_source_dates_survive_generated_daily_export(self):
        fixture = json.loads(CONCISE_THREE_STORY_FIXTURE.read_text(encoding='utf-8'))
        self.assertTrue(fixture['fixture_only'])
        edition = deepcopy(fixture['edition'])
        sources = deepcopy(fixture['sources'])
        runtime_evidence = deepcopy(fixture['runtime_evidence'])
        writer_response = deepcopy(fixture['writer_response'])

        # The six-week-old notice is fictional and unseen; a material correction
        # changes its scope without changing the original or correction dates.
        source = next(row for row in sources if row['id'] == 'source-permits')
        source.update({'published_at': '2026-10-02', 'corrected_at': '2026-11-20',
                       'unseen_by_reader': True})
        edition['sources'] = deepcopy(sources)
        runtime_evidence['permits-1']['text'] = (
            'Fictional County first published the permit notice October 2, 2026. '
            'The county announced a shorter review target November 18. '
            'A material correction on November 20 clarified that the fourteen-business-day '
            'target applies only to standard home permits, down from twenty-one days, '
            'starting December 1. Commercial permits remain at thirty business days.')
        permit_story = next(row for row in writer_response['stories']
                            if row['section_id'] == 'home-permit-review')
        permit_story['sentences'] = [
            {'text': 'A material correction clarified the target: standard home permits move from twenty-one to fourteen business days starting December first.',
             'evidence_ids': ['permits-1'], 'general_context': False},
            {'text': 'Commercial permits remain at thirty business days.',
             'evidence_ids': ['permits-1'], 'general_context': False},
        ]
        edition['sections'][-1]['body'] = (
            'Fictional County clarified the standard home permit target. [1]')
        edition['sections'][-1]['claim_links'][0]['text'] = (
            'Fictional County clarified the standard home permit target.')

        def call_json(_system, user, schema, _api_key, **kwargs):
            payload = json.loads(user)
            if schema == ENRICH_SCHEMA:
                return {'stories': [{'section_id': row['section_id'],
                    'evidence_ids': [item['id'] for item in row['evidence']]}
                    for row in payload['stories']]}
            if schema == SCRIPT_SCHEMA:
                return deepcopy(writer_response)
            return {'verdicts': [{'section_id': row['section_id'],
                'sentence_index': row['sentence_index'], 'supported': True}
                for row in payload['sentences']]}

        result = self.narrate(edition, fixture['profile'], runtime_evidence, sources,
                              call_json=call_json)
        self.assertEqual(result['status'], 'written', result)
        spoken = ' '.join(row['text'] for row in edition['daily_briefing']['briefing_script'])
        permit_row = next(row for row in edition['daily_briefing']['briefing_script']
                          if row.get('section_id') == 'home-permit-review')
        permit_spoken = permit_row['text']
        self.assertIn('material correction clarified the target', permit_spoken)
        self.assertNotIn('October second', permit_spoken,
                         'the original publication date need not be repeated in speech')
        self.assertNotIn('November twentieth', permit_spoken,
                         'the correction date remains inspectable in source metadata')
        self.assertIn('standard home permits', spoken)
        self.assertIn('Commercial permits remain at thirty business days.', spoken)
        self.assertNotIn("That's today's briefing.", spoken)
        self.assertEqual(edition['daily_briefing']['briefing_script'][-1]['kind'], 'story_intro')
        self.assertEqual(permit_row['source_sentence_ids'], ['permits-1'])
        self.assertEqual(permit_row['citation_numbers'], [1])
        self.assertEqual(edition['sections'][-1]['claim_links'][0]['citation_numbers'], [1])
        exported = daily_episode_from_edition(edition)
        exported_spoken = ' '.join(row['spoken_text'] for row in exported['chapters'])
        self.assertEqual(exported_spoken, spoken)
        exported_source = next(row for row in exported['sources']
                               if row['id'] == 'source-permits')
        self.assertEqual(exported_source['published_at'], '2026-10-02')
        self.assertEqual(exported_source['corrected_at'], '2026-11-20')
        self.assertEqual(edition['script_hash'], hashlib.sha256(spoken.encode()).hexdigest())
        self.assertEqual(exported['revision_id'], edition['revision_id'])

    def test_four_story_script_retries_duplicate_takeaways_and_keeps_concise_output(self):
        scripts = [
            ('harbor', 'Harbor Safety Planning', 'The harbor office moved its portable pump beside the raised service road after spring water reached the old storage shed.',
             'That matters because crews can reach the equipment without crossing the lowest section of the access path during another storm.',
             'More broadly, clear access routes help teams inspect equipment and coordinate repairs when weather interrupts normal service for nearby residents.',
             'Staff will post inspection times near the entrance, giving residents a simple way to know when the pump is checked.',
             "The inspection will compare the pump's position with the marked route and document any access problems for the monthly review."),
            ('library', 'Library Evening Access', 'The library added evening shifts after visitor records showed residents often arrived shortly before closing during the workweek on weekdays.',
             'That gives working residents more time to return books and collect holds without rushing through a short visit after work.',
             'As a general rule, predictable opening hours help families plan visits around jobs, school schedules, and public transportation during weekdays.',
             'The library will compare attendance with returned holds before deciding whether the later schedule should continue next season for residents.',
             'Staff will share the attendance summary at the monthly board meeting so residents can review how the hours are working.'),
            ('school', 'School Crossing Routes', 'The school marked a second walking route after families reported the main crossing became crowded near dismissal on most Fridays.',
             'That gives younger students another supervised path between the bus stop and neighborhood entrance during afternoon pickup at the school.',
             'At a high level, clear route markings help families and staff coordinate movement where vehicles and pedestrians meet each afternoon.',
             'Teachers will record crossing delays during pickup and share the summary with families at the school council meeting before summer.',
             'The school will use notes to decide whether the extra route should remain open during the following term for students.'),
            ('market', 'Market Repair Funding', 'The market replaced two damaged stall roofs after inspectors found leaks near the produce area during autumn rain this season.',
             'That keeps the covered walkway usable for sellers and visitors while crews finish the remaining work on the older stalls.',
             'To put that in perspective, phased repairs let public spaces remain usable while crews address one area at a time.',
             'The market will post a weekly work map so vendors can see which stalls remain open during each repair stage.',
             'Managers will review visitor flow and seller feedback before scheduling the final roof repairs for spring work.'),
        ]
        fictional_evidence = {
            'harbor': 'Harbor staff moved a portable pump beside the raised service road after spring water reached the shed. Inspection notes say the hose runs above the waterline, leaving the low access path clear so crews can reach equipment during another storm.',
            'library': 'Library records show many visitors arrived shortly before closing, so staff added evening shifts and will compare attendance with returned holds before deciding whether to keep the later schedule.',
            'school': 'Families reported crowds at the main crossing near Friday dismissal on most Fridays, prompting staff to mark a second supervised route from the bus stop. Teachers will record crossing delays on both routes and share the results with families at the next school council meeting.',
            'market': 'Inspectors found autumn leaks near the produce area; crews replaced two damaged stall roofs and will post a weekly map showing open stalls as they finish remaining repairs.',
        }
        sections, profile_topics, sources, evidence, claim_links = [], [], [], {}, {}
        response_stories = []
        for story_id, topic, *sentences in scripts:
            expected_lengths = [20, 20, 20, 20, 17] if story_id == 'market' else [20] * 5
            self.assertEqual([_word_count(text) for text in sentences], expected_lengths, sentences)
            spoken_sentences = [sentences[2], sentences[0], sentences[1], sentences[3], sentences[4]]
            if story_id == 'market':
                spoken_sentences[-1] = (
                    'Managers will review visitor flow and seller feedback before scheduling roof repairs for spring work.')
            evidence_id = f'{story_id}-fictional-report'
            source_id = f'{story_id}-fictional-source'
            source_number = len(sources) + 1
            evidence[evidence_id] = {'text': fictional_evidence[story_id],
                                     'source_number': source_number}
            sources.append({'id': source_id, 'publisher': f'Fictional {topic} Desk',
                            'title': f'Synthetic {topic} report'})
            profile_topics.append({'id': story_id, 'label': topic})
            claim_links[story_id] = [{'text': sentences[0], 'section': 'what_happened',
                                      'source_sentence_ids': [evidence_id],
                                      'citation_numbers': [1], 'verified': True}]
            sections.append({'cluster_id': story_id, 'heading': f'Fictional {topic} update',
                             'topic_ids': [story_id], 'source_ids': [row['id'] for row in sources],
                             'claim_links': claim_links[story_id], 'body': sentences[0],
                             'read_takeaway': sentences[0]})
            response_stories.append({'section_id': story_id, 'sentences': [
                {'text': text, 'evidence_ids': [evidence_id] if index else [],
                 'general_context': index == 0}
                for index, text in enumerate(spoken_sentences)
            ]})

        edition = {'local_date': '2026-11-19', 'sections': sections,
                   'daily_briefing': {'target_minutes': 5, 'target_spoken_words': 675,
                                      'maximum_spoken_words': 675, 'narrator_words_per_minute': 150,
                                      'greeting': 'Good morning.', 'background_segments': [],
                                      'briefing_script': [
                                          {'kind': 'greeting', 'text': 'Good morning.'},
                                          *[{'kind': 'story_intro', 'section_id': row['cluster_id'], 'text': 'Old intro.'}
                                            for row in sections],
                                          {'kind': 'closing', 'text': "That's today's briefing."}
                                      ]}}
        original_edition = deepcopy(edition)
        profile = {'topics': profile_topics}
        repeated = {'stories': deepcopy(response_stories)}
        corrected = {'stories': deepcopy(response_stories)}
        read_takeaway = sections[0]['read_takeaway']
        repeated['stories'][0]['sentences'][3]['text'] += ' [1]'
        repeated['stories'][0]['sentences'][1]['text'] = 'The report states: ' + read_takeaway.lower()
        self.assertTrue(_contains_read_takeaway(
            repeated['stories'][0]['sentences'][1]['text'], {_spoken_text_key(read_takeaway)}))
        self.assertTrue(lint_spoken_text(repeated['stories'][0]['sentences'][3]['text'])['forbidden_marker'])
        correction_details = {
            'harbor': 'The hose stays above the water line, so it reaches the storage shed without blocking the road during inspection work.',
            'library': 'The board will compare evening attendance with returned holds before deciding whether staff should keep the later schedule next season.',
            'school': 'Teachers will record crossing delays on both routes and share the results with families at the next school council meeting.',
            'market': 'Managers will post a weekly map showing open stalls while crews finish roof repairs and review the remaining inspection notes.',
        }
        for story in corrected['stories']:
            story['sentences'][1]['text'] = correction_details[story['section_id']]
        writer_responses = [repeated, corrected]
        writer_prompts = []

        def call_json(_system, user, schema, _key, **kwargs):
            if schema == ENRICH_SCHEMA:
                payload = json.loads(user)
                return {'stories': [{'section_id': row['section_id'],
                                     'evidence_ids': [item['id'] for item in row['evidence']]}
                                    for row in payload['stories']]}
            if schema == SCRIPT_SCHEMA:
                writer_prompts.append(user)
                return deepcopy(writer_responses[min(len(writer_prompts) - 1, 1)])
            payload = json.loads(user)
            return {'verdicts': [{'section_id': row['section_id'],
                                  'sentence_index': row['sentence_index'], 'supported': True}
                                 for row in payload['sentences']]}

        result = self.narrate(edition, profile, evidence, sources, call_json=call_json)
        self.assertEqual(result['status'], 'written', result)
        self.assertTrue(result['retry_used'], result)
        self.assertEqual(result['retry_reason'], 'narration_repeated_read_takeaway', result)
        self.assertIn('"code":"narration_repeated_read_takeaway"', writer_prompts[1])
        self.assertIn(read_takeaway, writer_prompts[0])
        self.assertIn('listener-sized question', SCRIPT_PROMPT)
        self.assertIn('short plain-language bridge', SCRIPT_PROMPT)
        self.assertIn('Style-only example', SCRIPT_PROMPT)
        self.assertIn('Connect the development to the reader\'s selected domain only when the supplied evidence supports that link.',
                      SCRIPT_PROMPT)
        self.assertIn('Specific programs, affected groups and jurisdictional effects require source support.',
                      SCRIPT_PROMPT)
        self.assertIn('explain the documented action or outcome and its practical consequence', SCRIPT_PROMPT)
        self.assertTrue(corrected['stories'][0]['sentences'][0]['general_context'],
                        'the fixture includes a context-led spoken opening')
        self.assertEqual(result['story_word_counts'], {'harbor': 100, 'library': 100,
                                                       'school': 100, 'market': 95})
        # This proves retry behavior only: the concise 424-word draft is not padded.
        self.assertEqual(edition['daily_briefing']['spoken_words'], 395)
        self.assertLess(edition['daily_briefing']['spoken_words'],
                        edition['daily_briefing']['target_spoken_words'])
        final_spoken = ' '.join(row.get('text', '') for row in edition['daily_briefing']['briefing_script'])
        self.assertNotIn(read_takeaway, final_spoken)
        self.assertNotRegex(final_spoken, r'\[\s*1\s*\]|https?://|www\.')
        self.assertNotIn('This episode is shorter than requested', final_spoken)
        introductions = [row['text'] for row in edition['daily_briefing']['briefing_script']
                         if row.get('kind') == 'story_intro']
        self.assertTrue(all(introductions))
        self.assertFalse(any(text.startswith(('First,', 'Turning to', 'Finally.')) for text in introductions))
        self.assertEqual(edition['daily_briefing']['briefing_script'][0]['kind'], 'story_intro')
        self.assertNotIn("That's today's briefing.", final_spoken)

        rich_evidence = deepcopy(evidence)
        source_details = {
            'harbor': (
                'Field notes record the pump location, route clearance, waterline conditions, hose placement, inspection timing, equipment access, and responsibility for the next maintenance review. '
                'Staff will compare access after heavy rain, record whether service vehicles can reach the shed, and flag any blockage before routine operations resume. '
                'The maintenance log tracks who checks the equipment, what condition they find, and which steps remain before the route map is updated. '
                'Resident notices describe the posted inspection schedule, the entrance used to reach the equipment, and how crews will keep the road available. '
                'The office plans to compare new observations with earlier checks and share the result with operators responsible for future repairs. '
                'Workers will confirm that the pump is secure, the hose remains usable, and the raised road stays clear for the inspection team. '
                'The follow-up report will identify remaining access concerns and note any adjustment needed before the next scheduled equipment check.'),
            'library': (
                'Visitor records distinguish evening returns, hold pickups, short visits, and arrivals close to the usual closing time on regular workdays. '
                'Staff will compare those counts across evening shifts and note whether residents use the added time for returns, pickups, or other services. '
                'The schedule review records staffing hours, open service points, visitor totals, and the time needed to complete closing routines. '
                'Library managers will compare each summary with the existing schedule before choosing whether later shifts should continue next season. '
                'The public board summary will explain which services remain available in the evening and where visitors can collect holds. '
                'Staff will also track whether attendance changes on different workdays and whether the new hours help regular visitors plan ahead. '
                'The follow-up record identifies the measures the board will use when it reviews the schedule and available staffing.'),
            'school': (
                'Pickup notes describe crowding near the main crossing, the location of the second supervised route, and the staff assigned to each path. '
                'Teachers will record delays on both routes and compare whether students can reach the neighborhood entrance without gathering at one crossing. '
                'The school council summary will list the observations families shared, the route conditions staff checked, and supervision available during pickup. '
                'Staff will review the route map with families and record whether the added path remains clear as students leave the bus stop. '
                'The next staffing review will compare crossing delays, route use, and reports of crowding near the main entrance. '
                'School notes will identify any time of day when students need additional direction or when the marked path needs adjustment. '
                'The council will use those observations before deciding whether the second route remains open for the next term.'),
            'market': (
                'Inspection notes list the affected stalls, roof seams checked by crews, access routes kept open, vendor notices, visitor flow, and remaining repair work. '
                'The work map identifies open stalls and shows how customers can reach sellers while crews repair each section. '
                'Managers will compare seller feedback with visitor movement and record whether signs direct people around active repair areas. '
                'The repair schedule notes which crews are assigned, what work remains, and when vendors receive the next public update. '
                'Follow-up records will track roof conditions after repairs and identify any stall that needs another inspection. '
                'The market will keep the route map current as work moves between sections and access points change. '
                'Managers will use those observations when they schedule the final work and review how visitors moved through the space.'),
        }
        for story_id, note in source_details.items():
            rich_evidence[f'{story_id}-fictional-report']['text'] += ' ' + note

        expanded = deepcopy(corrected)
        additional_context = {
            'harbor': 'The next inspection will compare route clearance with recent field notes before crews schedule further equipment checks.',
            'library': 'The public summary will list evening services and show visitors where to collect holds during later hours.',
            'school': 'The council will review reported crowding before deciding whether to keep the second route open next school term.',
            'market': 'Managers will use seller feedback and visitor movement to schedule final roof work around open market routes.',
        }
        for story in expanded['stories']:
            story_id = story['section_id']
            story['sentences'].append({'text': additional_context[story_id],
                                       'evidence_ids': [f'{story_id}-fictional-report'],
                                       'general_context': False})

        writer_responses[:] = [deepcopy(repeated), expanded]
        writer_prompts.clear()
        story_body_words = sum(_word_count(' '.join(row['text'] for row in story['sentences']))
                               for story in corrected['stories'])
        opening_and_transition_words = edition['daily_briefing']['spoken_words'] - story_body_words
        first_draft_words = story_body_words + opening_and_transition_words
        self.assertEqual(first_draft_words, 395)
        short_edition = deepcopy(original_edition)
        rich_result = self.narrate(short_edition, profile, rich_evidence, sources,
                                          call_json=call_json)
        self.assertEqual(rich_result['status'], 'written', rich_result)
        self.assertEqual(rich_result['retry_reason'], 'narration_repeated_read_takeaway', rich_result)
        self.assertEqual(len(writer_prompts), 2, 'the writer gets one bounded revision attempt')
        revision_feedback = json.loads(writer_prompts[1])['revision_feedback']
        repeated_read_feedback = [row for row in revision_feedback
                                  if row.get('code') == 'narration_repeated_read_takeaway']
        self.assertEqual({row['section_id'] for row in repeated_read_feedback},
                         {row['cluster_id'] for row in sections},
                         'all four body-matching Read takeaways get corrected in the production retry')
        self.assertIn('Never pad, repeat facts, or', SCRIPT_PROMPT)
        self.assertIn('Five minutes is a flexible aim, not a length gate', SCRIPT_PROMPT)
        attempted_sentences = [row['text'] for story in expanded['stories'] for row in story['sentences']]
        self.assertEqual(len(attempted_sentences), len(set(attempted_sentences)),
                         'the short revision uses distinct details rather than repeated claims')
        for story in sections:
            self.assertNotIn(story['read_takeaway'], ' '.join(attempted_sentences))
        attempted_words = sum(_word_count(' '.join(row['text'] for row in story['sentences']))
                              for story in expanded['stories']) + opening_and_transition_words
        self.assertEqual(attempted_words, 464)
        self.assertEqual(rich_result['spoken_words'], 464)
        self.assertEqual(short_edition['daily_briefing']['spoken_words'],
                         _word_count(' '.join(row['text'] for row in
                                              short_edition['daily_briefing']['briefing_script'])))
        self.assertLess(rich_result['spoken_words'], rich_result['target_daily_words'])

    def test_concise_three_story_daily_is_one_dated_spoken_narrative(self):
        fixture = json.loads(CONCISE_THREE_STORY_FIXTURE.read_text(encoding='utf-8'))
        self.assertTrue(fixture['fixture_only'])
        edition = deepcopy(fixture['edition'])
        original_sections = deepcopy(edition['sections'])
        writer_response = deepcopy(fixture['writer_response'])
        calls = []

        def call_json(_system, user, schema, _api_key, **kwargs):
            calls.append({'schema': schema, **kwargs})
            if schema == ENRICH_SCHEMA:
                payload = json.loads(user)
                return {'stories': [
                    {'section_id': story['section_id'],
                     'evidence_ids': [row['id'] for row in story['evidence']]}
                    for story in payload['stories']
                ]}
            if schema == SCRIPT_SCHEMA:
                return deepcopy(writer_response)
            payload = json.loads(user)
            return {'verdicts': [
                {'section_id': row['section_id'], 'sentence_index': row['sentence_index'],
                 'supported': True}
                for row in payload['sentences']
            ]}

        result = self.narrate(
            edition, fixture['profile'], fixture['runtime_evidence'], fixture['sources'],
            call_json=call_json)

        self.assertEqual(result['status'], 'written', result)
        self.assertEqual(result['stories'], 3)
        self.assertEqual(len(edition['sections']), 3)
        for current, original in zip(edition['sections'], original_sections):
            for field in ('body', 'read_takeaway', 'claim_links'):
                self.assertEqual(current[field], original[field],
                                 f'spoken generation leaves cited display field {field} intact')

        script_rows = edition['daily_briefing']['briefing_script']
        intros = [row for row in script_rows if row.get('kind') == 'story_intro']
        self.assertEqual(len(intros), 3)
        self.assertTrue(all(row['text'] for row in intros))
        self.assertFalse(any(row.get('kind') == 'heading' for row in script_rows))
        self.assertEqual(script_rows[-1]['kind'], 'story_intro')
        self.assertNotIn("That's today's briefing.", ' '.join(row['text'] for row in script_rows))

        spoken = ' '.join(row['text'] for row in script_rows)
        self.assertNotIn('Here is your briefing for', spoken)
        permit_fact = ('For standard home permits, the review target falls from twenty-one to fourteen '
                       'business days on December first.')
        permit_chapter = next(row for row in intros if row['section_id'] == 'home-permit-review')
        self.assertTrue(permit_chapter['text'].startswith(permit_fact), permit_chapter['text'])
        self.assertNotIn("Now, let's turn to Building permits.", permit_chapter['text'])
        self.assertIn('Wednesday, November eighteenth', permit_chapter['text'])
        payments_scope = 'If an account uses an international correspondent bank, that account is outside the pilot.'
        self.assertIn(payments_scope, spoken)
        self.assertNotIn('reconcile incoming cash', spoken)
        self.assertNotRegex(spoken, r'\[\s*1\s*\]|https?://|www\.')
        self.assertNotRegex(spoken, r'(?i)too short|not enough stories|target duration')
        self.assertEqual(edition['daily_briefing']['spoken_words'], _word_count(spoken))
        self.assertLess(_word_count(spoken), edition['daily_briefing']['target_spoken_words'],
                        'the short, informative script is not padded to the five-minute aim')
        self.assertIn("Don't read a headline aloud", SCRIPT_PROMPT)
        self.assertIn('publication-date-and-organization formula', SCRIPT_PROMPT)
        self.assertIn('one distinct, supported reason it matters', SCRIPT_PROMPT)
        self.assertEqual([story['sentences'][0]['text'] for story in writer_response['stories']], [
            'Riders have a new weekend timetable to plan around.',
            'Small-business payouts are moving to a later window.',
            'For standard home permits, the review target falls from twenty-one to fourteen business days on December first.',
        ], 'the fictional script opens with the development instead of reading headlines aloud')
        self.assertEqual(edition['daily_briefing']['duration_seconds'],
                         round(_word_count(spoken) * 60 / 150))

        for section, story in zip(edition['sections'], writer_response['stories']):
            takeaway = section['read_takeaway'].removesuffix(' [1]')
            story_intro = next(row for row in intros if row['section_id'] == section['cluster_id'])
            self.assertTrue(section['body'].endswith('[1]'))
            self.assertTrue(section['read_takeaway'].endswith('[1]'))
            self.assertEqual(section['claim_links'][0]['citation_numbers'], [1])
            self.assertEqual(story_intro['citation_numbers'], [1])
            self.assertNotIn(takeaway, story_intro['text'])
            self.assertFalse(_contains_read_takeaway(
                story_intro['text'], {_spoken_text_key(takeaway)}),
                f"{story['section_id']} does not read its on-screen takeaway verbatim")
        expected_call_limits = [1800, *(limit for _ in range(1 + int(result['retry_used']))
                                        for limit in (6500, 2500))]
        self.assertEqual([row['max_output_tokens'] for row in calls], expected_call_limits)

        exported = daily_episode_from_edition(edition)
        self.assertEqual(len(exported['chapters']), 3)
        exported_spoken = ' '.join(chapter['spoken_text'] for chapter in exported['chapters'])
        self.assertEqual(exported_spoken, spoken,
                         'the exported three-story chapters preserve the narrated transcript')
        self.assertEqual(hashlib.sha256(exported_spoken.encode('utf-8')).hexdigest(),
                         edition['script_hash'])
        self.assertEqual(exported['revision_id'], edition['revision_id'])
        self.assertTrue(exported['chapters'][1]['spoken_text'])
        self.assertIn(payments_scope, exported['chapters'][1]['spoken_text'])
        self.assertIn(permit_fact, exported['chapters'][2]['spoken_text'])
        self.assertIn('Wednesday, November eighteenth', exported['chapters'][2]['spoken_text'])
        self.assertIn('Commercial permits keep their current thirty-business-day target.',
                        exported['chapters'][2]['spoken_text'])
        first_facts = [story['sentences'][0]['text'] for story in writer_response['stories']]
        self.assertEqual(len(first_facts), len(set(first_facts)),
                         'each exported story keeps a distinct direct answer')
        for chapter, fact in zip(exported['chapters'], first_facts):
            self.assertIn(fact, chapter['spoken_text'])

    def test_numeric_range_keeps_both_endpoints_and_unit_in_daily_export(self):
        fixture = json.loads(CONCISE_THREE_STORY_FIXTURE.read_text(encoding='utf-8'))
        edition = deepcopy(fixture['edition'])
        sentence = ('Fictional County set a review window of 14-21 business days for standard home permits; '
                    'at least 14 business days are required.')
        evidence = fixture['runtime_evidence']['permits-1']
        evidence['text'] = sentence + ' The new window begins December 1.'
        section = edition['sections'][-1]
        section['body'] = sentence + ' [1]'
        section['claim_links'][0]['text'] = sentence
        story = fixture['writer_response']['stories'][-1]
        story['sentences'][0]['text'] = 'Builders have a stated review window for standard home permits.'
        story['sentences'][1]['text'] = sentence

        def call_json(_system, user, schema, _api_key, **kwargs):
            payload = json.loads(user)
            if schema == ENRICH_SCHEMA:
                return {'stories': [{'section_id': row['section_id'],
                    'evidence_ids': [item['id'] for item in row['evidence']]}
                    for row in payload['stories']]}
            if schema == SCRIPT_SCHEMA:
                return deepcopy(fixture['writer_response'])
            return {'verdicts': [{'section_id': row['section_id'],
                'sentence_index': row['sentence_index'], 'supported': True}
                for row in payload['sentences']]}

        result = self.narrate(edition, fixture['profile'], fixture['runtime_evidence'],
                              fixture['sources'], call_json=call_json)
        self.assertEqual(result['status'], 'written', result)
        spoken = ' '.join(row['text'] for row in edition['daily_briefing']['briefing_script'])
        self.assertIn('fourteen to twenty-one business days', spoken)
        exported = daily_episode_from_edition(edition)
        self.assertEqual(' '.join(row['spoken_text'] for row in exported['chapters']), spoken)
        permit_chapter = next(row for row in exported['chapters']
                              if row['id'] == 'home-permit-review')
        self.assertIn('fourteen to twenty-one business days', permit_chapter['spoken_text'])
        self.assertIn('at least fourteen business days are required.', permit_chapter['spoken_text'])
        self.assertEqual(edition['script_hash'], hashlib.sha256(spoken.encode()).hexdigest())
        self.assertEqual(exported['revision_id'], edition['revision_id'])

    def test_complete_three_story_script_survives_global_verifier_indices(self):
        fixture = json.loads(CONCISE_THREE_STORY_FIXTURE.read_text(encoding='utf-8'))
        edition = deepcopy(fixture['edition'])
        calls = []

        def call_json(_system, user, schema, _api_key, **kwargs):
            calls.append({'schema': schema, **kwargs})
            if schema == ENRICH_SCHEMA:
                payload = json.loads(user)
                return {'stories': [
                    {'section_id': story['section_id'],
                     'evidence_ids': [row['id'] for row in story['evidence']]}
                    for story in payload['stories']
                ]}
            if schema == SCRIPT_SCHEMA:
                return deepcopy(fixture['writer_response'])
            payload = json.loads(user)
            # This reproduces a complete verifier that numbers the flattened
            # script globally instead of resetting at each section.
            return {'verdicts': [
                {'section_id': row['section_id'], 'sentence_index': index,
                 'supported': True}
                for index, row in enumerate(payload['sentences'])
            ]}

        result = self.narrate(edition, fixture['profile'], fixture['runtime_evidence'],
                              fixture['sources'], call_json=call_json)
        self.assertEqual(result['status'], 'written', result)
        self.assertEqual(result['stories'], 3)
        self.assertIn('zero-based within each section', VERIFY_PROMPT)
        self.assertTrue(any(row['schema'] == VERIFY_SCHEMA for row in calls))

    def test_one_based_global_verifier_indices_are_normalized_only_when_complete(self):
        fixture = json.loads(CONCISE_THREE_STORY_FIXTURE.read_text(encoding='utf-8'))

        def run_variant(verdict_rows):
            edition = deepcopy(fixture['edition'])

            def call_json(_system, user, schema, _api_key, **_kwargs):
                if schema == ENRICH_SCHEMA:
                    payload = json.loads(user)
                    return {'stories': [
                        {'section_id': story['section_id'],
                         'evidence_ids': [row['id'] for row in story['evidence']]}
                        for story in payload['stories']
                    ]}
                if schema == SCRIPT_SCHEMA:
                    return deepcopy(fixture['writer_response'])
                return {'verdicts': deepcopy(verdict_rows)}

            return self.narrate(edition, fixture['profile'], fixture['runtime_evidence'],
                                fixture['sources'], call_json=call_json), edition

        expected = []
        offset = 0
        for story in fixture['writer_response']['stories']:
            for index, _sentence in enumerate(story['sentences'], 1):
                expected.append({'section_id': story['section_id'],
                                 'sentence_index': offset + index, 'supported': True})
            offset += len(story['sentences'])
        complete, _edition = run_variant(expected)
        self.assertEqual(complete['status'], 'written', complete)
        self.assertEqual(complete['stories'], 3)

        duplicate = [deepcopy(expected[0]) for _ in expected]
        duplicate_result, duplicate_edition = run_variant(duplicate)
        self.assertEqual(duplicate_result['status'], 'fallback', duplicate_result)
        self.assertEqual(duplicate_result['reason'], 'narration_style_or_evidence_check')
        self.assertEqual(duplicate_edition, fixture['edition'])

        partial = [row for row in expected
                   if row['section_id'] != fixture['writer_response']['stories'][-1]['section_id']]
        partial_result, partial_edition = run_variant(partial)
        self.assertEqual(partial_result['status'], 'fallback', partial_result)
        self.assertEqual(partial_result['reason'], 'narration_style_or_evidence_check')
        self.assertEqual(partial_edition, fixture['edition'])

    def test_three_story_export_with_audible_or_consumed_background_closes_once(self):
        fixture = json.loads(CONCISE_THREE_STORY_FIXTURE.read_text(encoding='utf-8'))
        background_text = 'A route map helps riders compare the added weekend departures.'
        for consumed in (False, True):
            with self.subTest(consumed=consumed):
                edition = deepcopy(fixture['edition'])
                writer_response = deepcopy(fixture['writer_response'])
                if consumed:
                    writer_response['stories'][0]['sentences'][0]['evidence_ids'].append('transit-bg-1')
                edition['daily_briefing']['briefing_script'] = [
                    *edition['daily_briefing']['briefing_script'][:-1],
                    {'kind': 'background_intro', 'segment_id': 'background-transit',
                     'text': 'Worth knowing about transit planning.', 'source_sentence_ids': [],
                     'citation_numbers': []},
                    {'kind': 'background', 'segment_id': 'background-transit',
                     'text': background_text, 'source_sentence_ids': ['transit-bg-1'],
                     'citation_numbers': [2]},
                    edition['daily_briefing']['briefing_script'][-1],
                ]
                edition['daily_briefing']['background_segments'] = [{
                    'segment_id': 'background-transit', 'topic_id': 'city-transit',
                    'sentences': [{'text': background_text,
                                   'source_sentence_ids': ['transit-bg-1'],
                                   'citation_numbers': [2]}],
                }]

                def call_json(_system, user, schema, _api_key, **_kwargs):
                    if schema == ENRICH_SCHEMA:
                        payload = json.loads(user)
                        return {'stories': [
                            {'section_id': story['section_id'],
                             'evidence_ids': [row['id'] for row in story['evidence']]}
                            for story in payload['stories']
                        ]}
                    if schema == SCRIPT_SCHEMA:
                        return deepcopy(writer_response)
                    payload = json.loads(user)
                    return {'verdicts': [
                        {'section_id': row['section_id'], 'sentence_index': row['sentence_index'],
                         'supported': True}
                        for row in payload['sentences']
                    ]}

                result = self.narrate(edition, fixture['profile'], fixture['runtime_evidence'],
                                      fixture['sources'], call_json=call_json)
                self.assertEqual(result['status'], 'written', result)
                transcript_text = ' '.join(
                    row['text'] for row in edition['daily_briefing']['briefing_script'])
                exported = daily_episode_from_edition(edition)
                exported_text = ' '.join(chapter['spoken_text'] for chapter in exported['chapters'])
                self.assertNotIn("That's today's briefing.", transcript_text)
                self.assertNotIn("That's today's briefing.", exported_text)
                self.assertEqual(exported_text, transcript_text)
                expected_hash = hashlib.sha256(transcript_text.encode('utf-8')).hexdigest()
                self.assertEqual(edition['script_hash'], expected_hash)
                self.assertEqual(edition['audio']['script_hash'], expected_hash)
                self.assertEqual(exported['revision_id'], edition['revision_id'])


    def test_daily_keeps_useful_supported_content_without_padding(self):
        fixture_stories = {
            'harbor': [
                ('The harbor office moved its portable pump beside the raised service road after spring water reached the old storage shed.', False),
                ('That matters because crews can reach the equipment without crossing the lowest section of the access path during another storm.', False),
                ('More broadly, clear access routes help teams inspect equipment and coordinate repairs when weather interrupts normal service for nearby residents.', True),
                ('Staff will post inspection times near the entrance, giving residents a simple way to know when the pump is checked.', False),
                ('The inspection compares the pump position with the marked route and records access problems for the regular maintenance review.', False),
                ('Crews can test the hose from the service road and document whether equipment remains reachable after heavy rain.', False),
                ('As a general rule, maintenance teams need clear access before they can check equipment and plan repairs safely.', True),
                ('The office will use those notes when it schedules repairs and updates the route map for nearby crews.', False),
            ],
            'library': [
                ('The library added evening shifts after visitor records showed residents often arrived shortly before closing during the workweek.', False),
                ('That gives working residents more time to return books and collect holds without rushing through a short visit after work.', False),
                ('At a high level, predictable opening hours help families plan visits around jobs, school schedules, and public transportation.', True),
                ('The library will compare attendance with returned holds before deciding whether the later schedule should continue next season.', False),
                ('Staff will share the attendance summary at the monthly board meeting so residents can review how the hours are working.', False),
                ('The comparison should show whether evening visits include more returns, pickups, or other regular library services.', False),
                ('To put that in perspective, public schedules work best when people can plan around them with confidence.', True),
                ('The board can use the results to decide whether later staffing fits both visitor needs and available resources.', False),
            ],
            'school': [
                ('The school marked a second walking route after families reported the main crossing became crowded near dismissal on Fridays.', False),
                ('That gives younger students another supervised path between the bus stop and neighborhood entrance during afternoon pickup.', False),
                ('This kind of route change can help families and staff coordinate movement where vehicles and pedestrians meet each day.', True),
                ('Teachers will record crossing delays during pickup and share the summary with families at the school council meeting.', False),
                ('Staff will review whether students use the second route and whether the added supervision keeps both paths orderly.', False),
                ('The notes can help the school adjust staffing if families report continued crowding near the main entrance.', False),
                ('Generally, clear route markings make it easier for visitors to understand where they should walk.', True),
                ('The school will consider the attendance notes before deciding whether to keep the extra path open next term.', False),
            ],
            'market': [
                ('The market replaced two damaged stall roofs after inspectors found leaks near the produce area during autumn rain.', False),
                ('That keeps the covered walkway usable for sellers and visitors while crews finish work on the older stalls.', False),
                ('Think of it this way, phased repairs let public spaces remain usable while crews address one area at a time.', True),
                ('The market will post a weekly work map so vendors can see which stalls remain open during each repair stage.', False),
                ('Managers will review visitor flow and seller feedback before scheduling the final roof repairs for spring.', False),
                ('The inspection notes identify which remaining stalls need attention and help crews plan work around market hours.', False),
                ('A closure of this kind can help public spaces stay useful while workers address damaged sections in sequence.', True),
                ('The posted route lets shoppers reach open stalls without walking through repair areas or disrupting crews working overhead.', False),
            ],
        }
        stories = {}
        evidence_by_story = {}
        verdicts = {}
        for section_id, rows in fixture_stories.items():
            evidence_id = f'{section_id}-fictional-inspection'
            stories[section_id] = [
                {'text': text, 'evidence_ids': [] if general else [evidence_id],
                 'general_context': general}
                for text, general in rows
            ]
            evidence_by_story[section_id] = [{'id': evidence_id, 'type': 'report',
                'outlet': f'Fictional {section_id} office',
                'title': f'Synthetic {section_id} inspection report',
                'text': (' '.join(text for text, general in rows if not general) +
                         f' Fictional {section_id} records also document staff observations, '
                         'practical access constraints, follow-up responsibilities, and how the service '
                         'remains usable while the work proceeds. The report identifies remaining tasks, '
                         'the information shared with affected people, and the review planned after crews '
                         'complete the work. It records why timing matters, how staff coordinate updates, '
                         'which details people can check, and when managers will revisit the plan.') }]
            verdicts.update({(section_id, index): True for index in range(len(rows))})

        original = deepcopy(stories)
        accepted, findings, fatal_reason = _review_writer_draft(
            stories, verdicts, evidence_by_story, _target_story_words(4),
            headings={section_id: f'Fictional {section_id} update' for section_id in stories},
            read_takeaways={'harbor': 'Crews moved the pump above the service road.'},
            allow_soft_style=True)
        spoken_words = sum(_word_count(' '.join(row['text'] for row in rows))
                           for rows in accepted.values())

        self.assertIsNone(fatal_reason, findings)
        self.assertLess(spoken_words + 30, 675)
        self.assertLess(_word_count(' '.join(row['text'] for row in accepted['market'])), 151)
        self.assertEqual(accepted, original, 'supported context is retained without filler')
        self.assertFalse(any(row.get('code') == 'narration_repeated_read_takeaway'
                             for row in findings))

    def test_deterministic_fact_check_rejects_unsupported_numbers(self):
        evidence = [{'id': 'fed-1', 'text': self.evidence['fed-1']['text'],
                     'outlet': 'Federal Reserve Board', 'title': 'Fictional notice'}]
        hallucination = {'text': 'SNB Bancshares moved 5 billion dollars into a reserve account.',
                         'evidence_ids': ['fed-1'], 'general_context': False}
        supported = {'text': 'The Federal Reserve ended an enforcement action involving SNB Bancshares.',
                     'evidence_ids': ['fed-1'], 'general_context': False}
        self.assertFalse(_deterministically_supported(hallucination, evidence))
        self.assertTrue(_deterministically_supported(supported, evidence))

    def test_takeaway_overlap_is_advisory_for_supported_date_change(self):
        takeaway = 'The fictional central bank lowered mortgage rates in June.'
        changed_claim = {
            'text': ('The fictional central bank lowered mortgage rates in July after reviewing '
                     'borrowing conditions and updating its variable-loan guidance.'),
            'evidence_ids': ['rates-1'], 'general_context': False,
        }
        general_context = {
            'text': 'For context, lower rates cut costs because loan payments adjust.',
            'evidence_ids': [], 'general_context': True,
        }
        keys = {_spoken_text_key(takeaway)}
        evidence = [{'id': 'rates-1', 'text':
                     ('The fictional central bank lowered mortgage rates in July after reviewing '
                      'borrowing conditions and updating its variable-loan guidance.'),
                     'outlet': 'Fictional Monetary Desk', 'title': 'Synthetic rate review'}]

        self.assertTrue(_has_high_read_takeaway_overlap(changed_claim['text'], keys))
        self.assertFalse(_contains_read_takeaway(changed_claim['text'], keys),
                         'the changed publication month makes this a distinct claim')
        self.assertTrue(_deterministically_supported(changed_claim, evidence))

        _accepted, first_findings, _first_failure = _review_writer_draft(
            {'rates': [changed_claim, general_context]},
            {('rates', 0): True, ('rates', 1): True}, {'rates': evidence},
            target_words=120, read_takeaways={'rates': takeaway})
        self.assertTrue(any(row.get('code') == 'narration_read_takeaway_overlap'
                            for row in first_findings), first_findings)

        accepted, retry_findings, fatal_reason = _review_writer_draft(
            {'rates': [changed_claim, general_context]},
            {('rates', 0): True, ('rates', 1): True}, {'rates': evidence},
            target_words=120, read_takeaways={'rates': takeaway}, allow_soft_style=True)
        self.assertEqual(accepted['rates'], [changed_claim, general_context], retry_findings)
        self.assertIsNone(fatal_reason, retry_findings)
        self.assertTrue(any(row.get('code') == 'narration_read_takeaway_overlap'
                            for row in retry_findings), retry_findings)

    def test_exact_and_embedded_read_takeaway_copies_remain_hard_failures(self):
        takeaway = 'Crews moved the pump above the service road.'
        evidence = [{'id': 'pump-1', 'text':
                     'Crews moved the pump above the service road. The raised position leaves the lower path clear.',
                     'outlet': 'Fictional Harbor Desk', 'title': 'Synthetic pump report'}]
        for spoken in (takeaway, 'The report says crews moved the pump above the service road.'):
            with self.subTest(spoken=spoken):
                accepted, findings, fatal_reason = _review_writer_draft(
                    {'harbor': [{'text': spoken, 'evidence_ids': ['pump-1'],
                                 'general_context': False}]},
                    {('harbor', 0): True}, {'harbor': evidence}, target_words=120,
                    read_takeaways={'harbor': takeaway}, allow_soft_style=True)
                self.assertEqual(fatal_reason, 'narration_repeated_read_takeaway', findings)
                self.assertEqual(accepted['harbor'][0]['text'], spoken)
                self.assertTrue(any(row.get('code') == 'narration_repeated_read_takeaway'
                                    for row in findings), findings)

    def test_supported_natural_flow_without_lint_phrase_is_advisory_on_retry(self):
        evidence = [{'id': 'transit-1', 'text':
                     ('The fictional transit office published its revised weekend timetable beside a route map. '
                      'Riders can compare the departure times before choosing a connection.'),
                     'outlet': 'Fictional Transit Desk', 'title': 'Synthetic weekend timetable'}]
        sentences = [
            {'text': 'The fictional transit office published its revised weekend timetable beside a route map.',
             'evidence_ids': ['transit-1'], 'general_context': False},
            {'text': 'Riders can compare the departure times before choosing a connection.',
             'evidence_ids': ['transit-1'], 'general_context': False},
        ]

        self.assertFalse(lint_spoken_text(' '.join(row['text'] for row in sentences))['host_flow'])
        self.assertTrue(all(_deterministically_supported(row, evidence) for row in sentences))
        accepted, findings, fatal_reason = _review_writer_draft(
            {'transit': sentences}, {('transit', 0): True, ('transit', 1): True},
            {'transit': evidence}, target_words=120, allow_soft_style=True)
        self.assertEqual(accepted['transit'], sentences, findings)
        self.assertIsNone(fatal_reason, findings)
        self.assertTrue(any(row.get('code') == 'narration_style_or_evidence_check'
                            for row in findings), findings)

    def test_semantic_verifier_owns_polarity_and_keeps_entities_separate(self):
        evidence = [{'id': 'transit-1',
                     'text': "The fictional transit authority didn't suspend weekend service during bridge repair. "
                             'The fictional school board approved a route plan, so staff could prepare.',
                     'outlet': 'Fictional Transit Desk', 'title': 'Synthetic service note'}]
        reversed_claim = {'text': 'The fictional transit authority did suspend weekend service during repair.',
                          'evidence_ids': ['transit-1'], 'general_context': False}
        school_claim = {'text': 'The fictional school board approved its route plan, so staff can prepare.',
                        'evidence_ids': ['transit-1'], 'general_context': False}

        self.assertTrue(_deterministically_supported(reversed_claim, evidence),
                        'deterministic specifics do not pretend to resolve sentence meaning')
        self.assertTrue(_deterministically_supported(school_claim, evidence),
                        'a different entity action must not inherit transit polarity')
        accepted, findings, fatal_reason = _review_writer_draft(
            {'transit': [reversed_claim]}, {('transit', 0): False},
            {'transit': evidence}, target_words=120, allow_soft_style=True)
        self.assertEqual(accepted['transit'], [], findings)
        self.assertEqual(fatal_reason, 'narration_style_or_evidence_check')
        self.assertTrue(any(row.get('code') == 'unsupported_sentence' and
                            row.get('verifier_supported') is False
                            for row in findings), findings)

        accepted, findings, fatal_reason = _review_writer_draft(
            {'transit': [school_claim]}, {('transit', 0): True},
            {'transit': evidence}, target_words=120, allow_soft_style=True)
        self.assertEqual(accepted['transit'], [school_claim], findings)
        self.assertIsNone(fatal_reason, findings)

    def test_general_context_below_fifteen_percent_is_revision_guidance(self):
        evidence = [{'id': 'board-1',
                     'text': 'The fictional board approved its annual route plan, so staff could prepare.',
                     'outlet': 'Fictional Board Desk', 'title': 'Synthetic route plan'}]
        sentence = {'text': 'The fictional board approved its annual route plan, so staff can prepare.',
                    'evidence_ids': ['board-1'], 'general_context': False}
        lint = lint_spoken_text(sentence['text'], require_context=True)
        self.assertTrue(lint['ok'], lint)
        self.assertTrue(lint['general_context_too_low'], lint)
        accepted, findings, fatal_reason = _review_writer_draft(
            {'board': [sentence]}, {('board', 0): True}, {'board': evidence},
            target_words=120, allow_soft_style=False)
        self.assertEqual(accepted['board'], [sentence], findings)
        self.assertEqual(fatal_reason, 'narration_style_or_evidence_check')
        self.assertTrue(any(row.get('code') == 'narration_context_below_guidance'
                            for row in findings), findings)
        accepted, findings, fatal_reason = _review_writer_draft(
            {'board': [sentence]}, {('board', 0): True}, {'board': evidence},
            target_words=120, allow_soft_style=True)
        self.assertEqual(accepted['board'], [sentence], findings)
        self.assertIsNone(fatal_reason, findings)

    def test_general_knowledge_context_examples_are_framed_and_limited_to_forty_percent(self):
        fixture = json.loads(CONTEXT_EXAMPLES.read_text(encoding='utf-8'))
        self.assertTrue(fixture['fixture_only'])
        story_rows = []
        for story in fixture['stories']:
            general = [{'text': text, 'evidence_ids': [], 'general_context': True}
                       for text in story['general_context']]
            for row in general:
                self.assertTrue(_deterministically_supported(row, []), row['text'])
            all_rows = [{'text': story['evidence'], 'evidence_ids': ['fixture-claim'],
                         'general_context': False}, *general]
            context_words = sum(_word_count(row['text']) for row in general)
            lint = lint_spoken_text(' '.join(row['text'] for row in all_rows),
                                    general_context_words=context_words, require_context=True)
            self.assertTrue(lint['ok'], lint)
            self.assertGreaterEqual(lint['general_context_ratio'], 0.15)
            self.assertLessEqual(lint['general_context_ratio'], 0.40)
            story_rows.append(all_rows)
        self.assertTrue(lint_story_set(story_rows)['ok'])

        too_much_context = lint_spoken_text(
            'The rule changed, so states have more time. A written agreement is a binding deal.',
            general_context_words=12, require_context=True)
        self.assertTrue(too_much_context['general_context_too_high'])
        self.assertFalse(too_much_context['ok'])
        card_copy = lint_spoken_text('The rule changed. The agency set a deadline.')
        self.assertFalse(card_copy['host_flow'])

    def test_writer_transitions_do_not_inject_topic_labels_or_spell_acronyms(self):
        sections = [
            {'cluster_id': 'ai-1', 'topic_ids': ['ai'], 'heading': 'First AI update'},
            {'cluster_id': 'nc', 'topic_ids': ['nc'], 'heading': 'Health update'},
            {'cluster_id': 'ai-2', 'topic_ids': ['ai'], 'heading': 'Second AI update'},
        ]
        story_body = ('The update describes the decision and gives readers immediate context. '
                      'That matters because organizations rely on clear rules to plan operations '
                      'and manage changes. Leaders can use the information to understand timing, '
                      'prepare teams, and check how policy affects daily work across their organization. '
                      'That kind of preparation connects a public announcement with the work that follows '
                      'and future operational planning.')
        script = [{'kind': 'greeting', 'text': 'Morning', 'source_sentence_ids': []}]
        script.append({'kind': 'heading', 'text': 'Unlinked spoken story heading.'})
        script.extend({'kind': 'story_intro', 'section_id': row['cluster_id'], 'text': 'Old intro',
                       'source_sentence_ids': []} for row in sections)
        script.append({'kind': 'closing', 'text': 'That is the briefing.', 'source_sentence_ids': []})
        edition = {'local_date': '2026-11-20', 'sections': sections,
                   'daily_briefing': {'briefing_script': script, 'narrator_words_per_minute': 150,
                                      'maximum_spoken_words': 1000, 'background_segments': []}}
        profile = {'topics': [{'id': 'ai', 'label': 'ai in finance'},
                              {'id': 'nc', 'label': 'nc health insurance'}]}
        stories = {row['cluster_id']: [{'text': story_body, 'evidence_ids': ['e-' + row['cluster_id']],
                                        'general_context': False}] for row in sections}
        evidence = {row['cluster_id']: [{'id': 'e-' + row['cluster_id'], 'citation_numbers': [1],
                                         'type': 'reported'}] for row in sections}

        self.assertTrue(_apply_narration(edition, profile, stories, evidence))
        intros = [row['text'].split(' ', 4)[:4]
                  for row in edition['daily_briefing']['briefing_script']
                  if row.get('kind') == 'story_intro']
        texts = [row.get('text', '') for row in edition['daily_briefing']['briefing_script']
                 if row.get('kind') == 'story_intro']
        self.assertTrue(all(texts))
        self.assertFalse(any(text.startswith(('First,', 'Another point.', 'Turning to', 'Finally.'))
                             for text in texts))
        self.assertNotIn('A I in finance', ' '.join(texts))
        self.assertEqual(len(intros), 3)
        self.assertFalse(any(row.get('kind') == 'heading'
                             for row in edition['daily_briefing']['briefing_script']))

    def test_story_transitions_follow_unique_topic_order_after_multiple_stories(self):
        topics = [('a', 'Alpha'), ('b', 'Beta'), ('a', 'Alpha'),
                  ('c', 'Gamma'), ('d', 'Delta')]
        sections = []
        stories = {}
        evidence = {}
        script = [{'kind': 'greeting', 'text': 'Morning'}]
        for index, (topic_id, label) in enumerate(topics, 1):
            section_id = f'section-{index}'
            sections.append({'cluster_id': section_id, 'topic_ids': [topic_id],
                             'heading': f'{label} update {index}'})
            evidence_id = f'evidence-{index}'
            stories[section_id] = [{'text': f'Supported detail {index} gives listeners useful context.',
                                    'evidence_ids': [evidence_id], 'general_context': False}]
            evidence[section_id] = [{'id': evidence_id, 'citation_numbers': [1]}]
            script.append({'kind': 'story_intro', 'section_id': section_id, 'text': 'Old intro'})
        script.append({'kind': 'closing', 'text': "That's today's briefing."})
        edition = {'local_date': '2026-11-21', 'sections': sections,
                   'daily_briefing': {'briefing_script': script,
                                      'narrator_words_per_minute': 150,
                                      'maximum_spoken_words': 1000,
                                      'background_segments': []}}
        profile = {'topics': [{'id': 'a', 'label': 'Alpha'},
                              {'id': 'b', 'label': 'Beta'},
                              {'id': 'c', 'label': 'Gamma'},
                              {'id': 'd', 'label': 'Delta'}]}

        self.assertTrue(_apply_narration(edition, profile, stories, evidence))
        intros = [row['text'] for row in edition['daily_briefing']['briefing_script']
                  if row.get('kind') == 'story_intro']
        self.assertTrue(all(intros))
        self.assertFalse(any(text.startswith(('First,', 'Another point.', 'Turning to', 'Finally.'))
                             for text in intros))
        episode = daily_episode_from_edition(edition)
        self.assertEqual([chapter['title'] for chapter in episode['chapters']],
                         ['Alpha update 1', 'Alpha update 3', 'Beta update 2',
                          'Gamma update 4', 'Delta update 5'])
        transcript = ' '.join(row['text'] for row in edition['daily_briefing']['briefing_script'])
        rendered_audio = ' '.join(chapter['spoken_text'] for chapter in episode['chapters'])
        self.assertEqual(rendered_audio, transcript)
        self.assertNotIn("That's today's briefing.", rendered_audio)

    def test_model_error_keeps_written_text_and_audio_chapters_unchanged(self):
        before = deepcopy(self.edition)
        result = self.narrate(self.edition, self.profile, self.evidence, self.sources,
                                     call_json=self.replay(error=TimeoutError('fixture timeout')))
        self.assertEqual(result['status'], 'fallback')
        self.assertEqual(self.edition, before)

    def test_rejected_draft_is_retried_with_feedback_and_saved_privately(self):
        from dev.ingest.daily_narration import STATE_DIR

        writer_prompts = []
        def call_json(_system, user, schema, _key, **kwargs):
            if schema == ENRICH_SCHEMA:
                payload = json.loads(user)
                return {'stories': [{'section_id': row['section_id'],
                                     'evidence_ids': [item['id'] for item in row['evidence']]}
                                    for row in payload['stories']]}
            if schema == SCRIPT_SCHEMA:
                writer_prompts.append(user)
                if len(writer_prompts) == 1:
                    return {'stories': []}
                def rows(lines, evidence_id):
                    return [{'text': text, 'evidence_ids': [evidence_id], 'general_context': False}
                            for text in lines]
                return {'stories': [
                    {'section_id': 'story-fed-snb', 'sentences': [*rows([
                        'The order has ended, and that closes the specific enforcement action described in the notice.',
                        'A bank regulator can use a formal agreement to require corrective work under its supervision.',
                        'For banking readers, the completed order is the key outcome recorded in this update.',
                        "The written agreement gives the bank a defined process to address the regulator's stated concerns on record."
                    ], 'fed-1'), {'text': 'More broadly, a written agreement creates binding supervised corrective duties for a bank.',
                                  'evidence_ids': [], 'general_context': True}]},
                    {'section_id': 'story-cms', 'sentences': [*rows([
                        'The federal schedule has been delayed, so the implementation timeline now moves later for states.',
                        'State agencies can handle practical renewal details once federal guidance sets the overall policy.',
                        'People following coverage policy can watch how local instructions reflect the revised timetable.',
                        'The update affects when rules begin, while the underlying requirements remain in place.',
                        'That gives state offices more time to prepare their renewal operations.'
                    ], 'cms-1'), {'text': 'At a high level, states manage local eligibility and enrollment work for residents.',
                                  'evidence_ids': [], 'general_context': True}]},
                ]}
            payload = json.loads(user)
            return {'verdicts': [{'section_id': row['section_id'],
                                  'sentence_index': row['sentence_index'], 'supported': True}
                                 for row in payload['sentences']]}

        result = self.narrate(self.edition, self.profile, self.evidence, self.sources,
                                     call_json=call_json)
        self.assertEqual(result['status'], 'written', result)
        self.assertTrue(result['retry_used'])
        self.assertEqual(result['retry_reason'], 'narration_writer_shape')
        self.assertEqual(len(writer_prompts), 2)
        feedback = json.loads(writer_prompts[1])['revision_feedback']
        self.assertEqual(feedback, [{'code': 'narration_writer_shape'}])
        self.assertEqual(len(result['debug_files']), 1)
        debug_path = Path(STATE_DIR) / result['debug_files'][0]
        self.assertEqual(debug_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(debug_path.parent.stat().st_mode & 0o777, 0o700)
        debug = json.loads(debug_path.read_text(encoding='utf-8'))
        self.assertEqual(debug['reason'], 'narration_writer_shape')
        self.assertEqual(debug['draft'], {'stories': []})
        self.assertEqual(debug['verifier_findings'], [{'code': 'narration_writer_shape'}])

    def test_fallback_reason_preserves_safe_narration_value_error_codes(self):
        from dev.ingest.daily_narration import _safe_failure_reason

        self.assertEqual(_safe_failure_reason(ValueError('narration_style_or_evidence_check')),
                         'narration_style_or_evidence_check')
        self.assertEqual(_safe_failure_reason(ValueError('narration_enrichment_shape')),
                         'narration_enrichment_shape')
        self.assertEqual(_safe_failure_reason(ValueError('story contains a number format that is not supported for speech')),
                         'story contains a number format that is not supported for speech')
        self.assertEqual(_safe_failure_reason(ValueError('provider secret detail')), 'ValueError')
        result = self.narrate(self.edition, self.profile, self.evidence, self.sources,
                                     call_json=self.replay(error=ValueError('narration_enrichment_shape')))
        self.assertEqual(result['status'], 'fallback')
        self.assertEqual(result['reason'], 'narration_enrichment_shape')

    def test_final_rejection_reports_the_safe_value_error_and_keeps_both_debug_drafts(self):
        from dev.ingest.daily_narration import STATE_DIR

        script_calls = 0
        def call_json(_system, user, schema, _key, **_kwargs):
            nonlocal script_calls
            if schema == ENRICH_SCHEMA:
                payload = json.loads(user)
                return {'stories': [{'section_id': row['section_id'],
                                     'evidence_ids': [item['id'] for item in row['evidence']]}
                                    for row in payload['stories']]}
            if schema == SCRIPT_SCHEMA:
                script_calls += 1
                return {'stories': []}
            raise AssertionError('malformed draft must be retried before verification')

        result = self.narrate(self.edition, self.profile, self.evidence, self.sources,
                                     call_json=call_json)
        self.assertEqual(result['status'], 'fallback')
        self.assertEqual(result['reason'], 'narration_writer_shape')
        self.assertEqual(script_calls, 2)
        self.assertEqual(len(result['debug_files']), 2)
        for relative in result['debug_files']:
            payload = json.loads((Path(STATE_DIR) / relative).read_text(encoding='utf-8'))
            self.assertEqual(payload['reason'], 'narration_writer_shape')
            self.assertIn('draft', payload)

    def test_unsupported_specifics_still_fall_back_with_verifier_diagnostics(self):
        from dev.ingest.daily_narration import STATE_DIR

        def call_json(_system, user, schema, _key, **_kwargs):
            if schema == ENRICH_SCHEMA:
                payload = json.loads(user)
                return {'stories': [{'section_id': row['section_id'],
                                     'evidence_ids': [item['id'] for item in row['evidence']]}
                                    for row in payload['stories']]}
            if schema == SCRIPT_SCHEMA:
                return {'stories': [
                    {'section_id': 'story-fed-snb', 'sentences': [
                        {'text': 'SNB Bancshares moved 5 billion dollars.',
                         'evidence_ids': ['fed-1'], 'general_context': False}]},
                    {'section_id': 'story-cms', 'sentences': [
                        {'text': 'CMS approved 7 new state plans.',
                         'evidence_ids': ['cms-1'], 'general_context': False}]},
                ]}
            payload = json.loads(user)
            return {'verdicts': [{'section_id': row['section_id'],
                                  'sentence_index': row['sentence_index'], 'supported': True}
                                 for row in payload['sentences']]}

        result = self.narrate(self.edition, self.profile, self.evidence, self.sources,
                                     call_json=call_json)
        self.assertEqual(result['status'], 'fallback')
        self.assertEqual(result['reason'], 'narration_style_or_evidence_check')
        self.assertEqual(len(result['debug_files']), 2)
        final = json.loads((Path(STATE_DIR) / result['debug_files'][-1]).read_text(encoding='utf-8'))
        self.assertEqual(final['verifier_results'][0]['supported'], True)
        self.assertTrue(any(row['code'] == 'unsupported_sentence'
                            for row in final['verifier_findings']))
        self.assertIn('5 billion', final['draft']['stories'][0]['sentences'][0]['text'])
        exported_text = ' '.join(row['spoken_text']
                                 for row in daily_episode_from_edition(self.edition)['chapters'])
        self.assertNotIn(final['draft']['stories'][0]['sentences'][0]['text'], exported_text,
                         'the rejected debug claim never appears in final exported chapters')

    def test_explicit_verifier_rejection_refuses_daily_script_without_mutation(self):
        from dev.ingest.daily_narration import STATE_DIR

        writer = deepcopy(self.fixture['writer_response'])
        rejected_claim = 'The Federal Reserve continued its enforcement action against SNB Bancshares.'
        writer['stories'][0]['sentences'][0]['text'] = rejected_claim
        verifier = deepcopy(self.fixture['verifier_response'])
        rejected_verdict = next(row for row in verifier['verdicts']
                                if row['section_id'] == 'story-fed-snb' and row['sentence_index'] == 0)
        rejected_verdict['supported'] = False
        before = deepcopy(self.edition)

        result = self.narrate(self.edition, self.profile, self.evidence, self.sources,
                              call_json=self.replay(writer=writer, verifier=verifier))

        self.assertEqual(result['status'], 'fallback', result)
        self.assertEqual(result['reason'], 'narration_style_or_evidence_check')
        self.assertEqual(self.edition, before,
                         'an explicitly rejected claim does not mutate or pad the daily edition')
        final = json.loads((Path(STATE_DIR) / result['debug_files'][-1]).read_text(encoding='utf-8'))
        self.assertTrue(any(row.get('supported') is False for row in final['verifier_results']))
        self.assertTrue(any(row.get('code') == 'unsupported_sentence' and
                            row.get('verifier_supported') is False
                            for row in final['verifier_findings']))

    def test_model_output_has_a_deterministic_spoken_style_lint(self):
        response = self.fixture['writer_response']
        for story in response['stories']:
            script = ' '.join(row['text'] for row in story['sentences'])
            lint = lint_spoken_text(script)
            self.assertTrue(lint['ok'], lint)
            self.assertLess(lint['average_sentence_words'], 20)
            self.assertFalse(lint['forbidden_marker'])
        self.assertFalse(lint_spoken_text('Here is a citation [1] and click this link.')['ok'])
        self.assertFalse(lint_spoken_text('The notice does not say. We should not guess.')['ok'])
        self.assertFalse(lint_spoken_text('First point is here. First point is here.')['ok'])
        self.assertEqual(lint_spoken_text('Across the region, banks are adjusting. Across the region, rates are changing.')['repeated_openers'],
                         ['across the region'])
        self.assertTrue(lint_spoken_text('In bank supervision, an agreement is binding.')['field_framing'])
        self.assertEqual(_target_story_words(5), 134)
        self.assertEqual(_target_story_words(5) * 5 + 30, 700)
        self.assertEqual(_target_story_words(4), 168)
        self.assertEqual(_target_story_words(4) * 4 + 30, 702)
        self.assertEqual(_target_story_words(3), 223)
        self.assertEqual(_target_story_words(3) * 3 + 30, 699)
        self.assertEqual(_story_word_range(_target_story_words(5), 180), (124, 149))
        self.assertEqual(_story_word_range(_target_story_words(5), 80), (124, 149),
                         'general context can help a thin story reach the daily target')
        duplicate = [{'text': "For context, that is the mechanism."}]
        self.assertFalse(lint_story_set([duplicate, duplicate])['ok'])
        repeated_context = [[{'text': 'For context, supervisors monitor progress.'}],
                            [{'text': 'For context, agencies review results.'}]]
        self.assertEqual(lint_story_set(repeated_context)['repeated_context_lead_ins'],
                         {'for-context': 2})
        repeated_practice = [[{'text': 'In practice, systems need clean records.'}],
                             [{'text': 'In practice, states process renewals.'}]]
        self.assertEqual(lint_story_set(repeated_practice)['repeated_context_lead_ins'],
                         {'in-practice': 2})
        self.assertFalse(lint_story_set([[{'text': 'The plan is a modest positive step.'}]])['ok'])
        self.assertFalse(lint_story_set([[{'text': "The practical thing to follow is any separate notice."}]])['ok'])
        estimate = narration_cost_estimate(self.edition, {
            'story-fed-snb': [{'text': 'evidence ' * 100}],
            'story-cms': [{'text': 'evidence ' * 100}],
        })
        self.assertGreater(float(estimate['estimated_usd']), 0)
        self.assertEqual(estimate['max_output_tokens'], 19_800)

    def test_four_story_fixture_rejects_spoken_citations_headings_and_headline_repeats(self):
        self.assertTrue(FOUR_STORY_SPOKEN_CONTRACT['fixture_only'])
        stories = {row['id']: row['spoken']
                   for row in FOUR_STORY_SPOKEN_CONTRACT['stories']}
        headings = {row['id']: row['heading'] for row in FOUR_STORY_SPOKEN_CONTRACT['stories']}
        lint = lint_story_set(stories, headings)
        self.assertFalse(lint['ok'])
        self.assertEqual(lint['repeated_headlines'], list(headings.values()))
        self.assertTrue(lint_spoken_text('The report cites source one for the timetable.')['forbidden_marker'])
        self.assertAlmostEqual(_target_story_words(4) * 4 + 30, 700, delta=3)

    def test_live_edition_text_replay_builds_two_context_rich_scripts_without_changing_story_count(self):
        edition_fixture = json.loads(LIVE_EDITION_TEXT.read_text(encoding='utf-8'))
        replay = json.loads(LIVE_REPLAY.read_text(encoding='utf-8'))
        self.assertTrue(edition_fixture['fixture_only'])
        self.assertTrue(replay['fixture_only'])
        for story in replay['writer_response']['stories']:
            for sentence in story['sentences']:
                if sentence['general_context']:
                    self.assertTrue(_deterministically_supported(sentence, []), sentence['text'])
        self.assertFalse(replay['writer_response']['stories'][0]['sentences'][0]['general_context'],
                         'the replay also covers a supported development-led opening')
        edition = deepcopy(self.edition)
        runtime_evidence = {}
        sources = []
        topics = []
        for story in edition_fixture['stories']:
            section = next(row for row in edition['sections'] if row['cluster_id'] == story['section_id'])
            section.update(heading=story['heading'], topic_ids=[story['topic_id']],
                           source_ids=story['source_ids'],
                           claim_links=[], body=story['heading'])
            topics.append({'id': story['topic_id'], 'label': story['topic_id'].replace('-', ' ')})
            sources.extend({'id': source_id,
                            'publisher': ('Federal Reserve Board' if source_id == 'source-fed' else 'CMS'),
                            'title': story['heading']} for source_id in story['source_ids'])
            for index, text in enumerate(story['claim_text'], 1):
                sentence_id = f"{'fed' if story['topic_id'] == 'banking' else 'cms'}-{index}"
                runtime_evidence[sentence_id] = {'text': text, 'source_number': 1}
                section['claim_links'].append({'text': text, 'section': 'what_happened',
                    'source_sentence_ids': [sentence_id], 'citation_numbers': [1], 'verified': True})
                section['body'] += f' {text} [1]'
        edition['local_date'] = edition_fixture['local_date']
        edition['daily_briefing']['background_segments'] = []
        for topic in topics:
            topic['label'] = ('Payments and banking' if topic['id'] == 'banking' else
                              'Health coverage policy')
        profile = {'topics': topics}

        def call_json(_system, _user, _schema, _api_key, **kwargs):
            if _schema == ENRICH_SCHEMA:
                payload = json.loads(_user)
                return {'stories': [{'section_id': story['section_id'],
                                     'evidence_ids': [row['id'] for row in story['evidence'][:18]]}
                                    for story in payload['stories']]}
            return (deepcopy(replay['writer_response']) if kwargs['model'] == SCRIPT_MODEL else
                    deepcopy(replay['verifier_response']))

        result = self.narrate(edition, profile, runtime_evidence, sources, call_json=call_json)
        self.assertEqual(result['status'], 'written', result)
        stories = [row for row in edition['daily_briefing']['briefing_script']
                   if row.get('kind') == 'story_intro']
        self.assertEqual(len(stories), 2)
        story_texts = [row['text'] for row in stories]
        self.assertTrue(all(90 <= _word_count(text) <= 150 for text in story_texts),
                        [(_word_count(text), text) for text in story_texts])
        for story, row in zip(story_texts, replay['writer_response']['stories']):
            context_words = sum(_word_count(sentence['text']) for sentence in row['sentences']
                                if sentence['general_context'])
            lint = lint_spoken_text(story, general_context_words=context_words, require_context=True)
            self.assertTrue(lint['ok'], lint)
            self.assertLessEqual(lint['general_context_ratio'], 0.40)
        self.assertIn('S-N-B Bancshares', story_texts[0])
        self.assertIn('Eufaula, Oklahoma', story_texts[0])
        self.assertIn('two years', story_texts[0])
        self.assertIn('binding supervisory', story_texts[0])
        self.assertIn('C-M-S', story_texts[1])
        self.assertIn('The Centers for Medicare and Medicaid Services, or C-M-S, has given states six more months before work requirements begin, moving the deadline to June thirtieth.',
                      story_texts[1])
        self.assertIn('ten point one million', story_texts[1])
        self.assertNotRegex(' '.join(story_texts),
                            r'(?i)(?:notice|source) (?:does not|doesn.t) say|we should not guess|details remain open')
        self.assertEqual(edition['daily_briefing']['greeting'], '')
        spoken = ' '.join(row['text'] for row in edition['daily_briefing']['briefing_script'])
        self.assertEqual(edition['daily_briefing']['spoken_words'], _word_count(spoken))
        self.assertLess(edition['daily_briefing']['spoken_words'],
                        edition['daily_briefing']['target_spoken_words'],
                        'the two-story daily stays concise instead of padding to the five-minute aim')
        self.assertTrue(all(row.get('source_sentence_ids') and row.get('citation_numbers') for row in stories))
        for story in stories:
            self.assertTrue(set(story['source_sentence_ids']).issubset(runtime_evidence))
            self.assertEqual(len(story['source_sentence_ids']), len(set(story['source_sentence_ids'])))
        self.assertEqual(len(edition['sections']), len(edition_fixture['stories']))


if __name__ == '__main__':
    unittest.main()
