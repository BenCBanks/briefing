"""Exercise real orchestration using fictional HTML and an injected mock LLM."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from contextlib import ExitStack
from copy import deepcopy
from functools import partial
import json
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from dev.ingest.corrections import CorrectionService
from dev.ingest.episodes.fixture_research import ExtractedFixtureResearch, prepare_research
from dev.ingest.episodes.generate import (EpisodeCancelled, EpisodeError, InsufficientEvidence,
                                          generate_episode, digest, transcript)
from dev.ingest.episodes.policy import WORDS_PER_MINUTE
from dev.ingest.jobs import EpisodeJobStore
from dev.ingest.episodes_audio import render_episode_audio
from dev.tests.episodes_audio_test import FakeProvider
from dev.ingest.retrieval.multiround import FixtureResearch, ResearchLimits

FIXTURE = Path(__file__).with_name('fixtures') / 'episode-e2e.json'
PROFESSIONAL_LIBRARY = Path(__file__).with_name('fixtures') / 'professional-topic-library.json'
PROFILE = {'minutes': 12, 'topics': [{'id': 'aster', 'label': 'Fictional Aster ferry'}]}
EPISODE_ID = 'f' * 32


class MockLLM:
    """Copies annotated fixture sentences; verifies exact fixture membership.

    This tests wiring, not an actual model's factual judgement or writing skill.
    """
    def __init__(self, fixture):
        self.fixture = fixture
        self.payloads = []
        self.force_short_claim = False
        self.missing_questions = [fixture['missing_question']]

    def planner(self, prompt):
        self.payloads.append({'planner_prompt': prompt})
        return deepcopy(self.fixture['plan'])

    def __call__(self, system, payload, schema, timeout):
        self.payloads.append(deepcopy(payload))
        if 'script' in payload:
            return {'on_topic': True, 'coherent': True, 'uncertainty_relevant': True,
                    'verdicts': [{'id': row['id'], 'supported': row['kind'] == 'heading' or any(
                        row['text'] == payload['evidence'][eid]['text']
                        for eid in row['evidence_ids']), 'relevant': True}
                        for row in payload['script']
                        if row['kind'] not in {'navigation', 'opening'}]}
        assignments = ([payload['chapter_assignment']] if 'chapter_assignment' in payload
                       else payload['research_plan'])
        chapter_sources = []
        for assignment in assignments:
            evidence = {eid: row for eid, row in payload['evidence'].items()
                        if eid in assignment['evidence_ids']}
            if not evidence:
                continue
            chapter_sources.append((assignment, evidence))
        chapters = []
        for index, (assignment, evidence) in enumerate(chapter_sources):
            sentences = [{'text': row['text'], 'kind': 'claim', 'evidence_ids': [eid]}
                         for eid, row in evidence.items()]
            chapter = {'title': 'Ferry trial', 'title_evidence_ids': [next(iter(evidence))],
                       'sentences': sentences}
            if payload.get('chapter_assignment'):
                first = bool(assignment.get('is_first_chapter'))
                last = bool(assignment.get('is_last_chapter'))
            else:
                first = index == 0
                last = index == len(chapter_sources) - 1
            if first:
                chapter['sentences'].insert(0, {'text': 'Let us start with the part that answers your question.',
                                                'kind': 'opening', 'evidence_ids': []})
            else:
                transition = ('That leaves another part of the process to explain.',
                              'A different detail helps connect those pieces.',
                              'There is also a question about what happens next.')[index % 3]
                chapter['sentences'].insert(0, {'text': transition,
                                                'kind': 'navigation', 'evidence_ids': []})
            if last and chapter['sentences'][-1]['kind'] == 'claim':
                chapter['sentences'][-1] = {**chapter['sentences'][-1],
                                            'kind': 'closing'}
            chapters.append(chapter)
        if self.force_short_claim:
            claim = next((row for chapter in chapters for row in chapter['sentences']
                          if row['kind'] == 'claim'), None)
            if claim:
                claim['text'] = 'Passengers reserve places.'
        return {'chapters': chapters, 'could_not_confirm': list(self.missing_questions)}


class EpisodeEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        # Hard tripwires: even an accidental default selection fails the test.
        for name in ('socket.create_connection', 'socket.socket.connect', 'socket.getaddrinfo',
                     'dev.ingest.episodes.generate.GeminiClient',
                     'dev.ingest.episodes.generate.build_providers',
                     'dev.ingest.episodes.generate.core.load_registry',
                     'dev.ingest.episodes.generate.read_key'):
            self.stack.enter_context(patch(name, side_effect=AssertionError('forbidden external dependency')))
        self.fixture = json.loads(FIXTURE.read_text())
        self.model = MockLLM(self.fixture)

    def dependencies(self, fixture=None, **extra):
        fixture = fixture or self.fixture
        documents = deepcopy(fixture['documents'])
        for document in documents.values():
            document['body'] = document['body'].encode()
        return dict(research=ExtractedFixtureResearch(fixture['searches'], documents),
                    planner=self.model.planner, client=self.model,
                    created_at=fixture['created_at'], clock=lambda: 100.0, **extra)

    def generate(self, fixture=None, *, diagnose=False, **extra):
        try:
            return generate_episode(self.fixture['topic'], 5, state_dir=self.root,
                                    episode_id=EPISODE_ID, **self.dependencies(fixture, **extra))
        except InsufficientEvidence as error:
            if diagnose:
                self.fail(f'{error}: {error.diagnostics}')
            raise

    def test_full_json_is_repeatable_with_citations_gaps_timings_and_no_raw_text(self):
        self.model.missing_questions.append('What will the fictional Aster ferry charge after the trial?')
        episode = self.generate(diagnose=True)
        self.assertEqual(json.dumps(episode, sort_keys=True), json.dumps(self.generate(), sort_keys=True))
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(episode['research']['usage']['rounds'], 3)
        self.assertEqual(episode['retrieval']['queries'], 9)
        self.assertEqual(episode['retrieval']['read_attempts'], 6)
        self.assertTrue(episode['verification']['script_checks']['passed'])
        self.assertEqual(episode['transcript'], transcript(episode['chapters']))
        self.assertEqual(episode['script_hash'], digest(episode['transcript']))
        missing_questions = {row['question'] for row in episode['could_not_confirm']}
        self.assertTrue(set(self.model.missing_questions).issubset(missing_questions))
        self.assertEqual(len(self.model.missing_questions), 2)
        for question in missing_questions:
            with self.subTest(unresolved_question=question):
                self.assertNotIn(question, episode['transcript'])
        self.assertTrue(any('background' in question for question in missing_questions))
        self.assertEqual(episode['audio']['status'], 'not_recorded')
        self.assertTrue(episode['shorter_than_requested'])
        final_chapter = episode['chapters'][-1]
        gap_rows = [row for chapter in episode['chapters'] for row in chapter['sentences']
                    if row['kind'] == 'editorial']
        self.assertEqual(len(gap_rows), 1, 'state the evidence gap once in one plain sentence')
        self.assertIn("couldn't find solid evidence", gap_rows[0]['text'])
        self.assertNotIn('What we couldn\'t confirm', episode['transcript'])
        self.assertIn('episode is shorter', final_chapter['spoken_text'])
        closing = final_chapter['sentences'][-1]
        self.assertEqual(closing['kind'], 'closing')
        self.assertTrue(closing['evidence_ids'] and closing['source_ids'] and closing['source_numbers'])
        self.assertEqual(sum(row['kind'] == 'closing' for chapter in episode['chapters']
                             for row in chapter['sentences']), 1)
        self.assertTrue(episode['transcript'].endswith(closing['text']))
        source_by_id = {s['id']: s for s in episode['sources']}
        self.assertEqual([source['number'] for source in episode['sources']],
                         list(range(1, len(episode['sources']) + 1)))
        identifiers, end = set(), 0
        for chapter in episode['chapters']:
            self.assertEqual(chapter['estimated_start_seconds'], end)
            end = chapter['estimated_end_seconds']
            self.assertGreater(end, chapter['estimated_start_seconds'])
            self.assertNotRegex(chapter['spoken_text'], r'\[\d+(?:,\s*\d+)*\]')
            for row in chapter['sentences']:
                self.assertNotIn(row['id'], identifiers)
                identifiers.add(row['id'])
                self.assertEqual(row['source_numbers'], [source_by_id[s]['number'] for s in row['source_ids']])
                self.assertTrue(all(1 <= number <= len(episode['sources']) for number in row['source_numbers']))
                self.assertNotRegex(row['text'], r'\[\d+(?:,\s*\d+)*\]')
                locators = {loc['id'] for sid in row['source_ids'] for loc in source_by_id[sid]['locators']}
                self.assertTrue(set(row['evidence_ids']) <= locators)
        self.assertEqual(len(identifiers), sum(len(chapter['sentences']) for chapter in episode['chapters']))
        story_chapters = episode['chapters']
        self.assertEqual(story_chapters[0]['sentences'][0]['kind'], 'opening')
        self.assertTrue(all(chapter['sentences'][0]['kind'] == 'navigation'
                            for chapter in story_chapters[1:]))
        self.assertAlmostEqual(end, episode['word_count'] * 60 / WORDS_PER_MINUTE, places=3)
        metadata = json.dumps({'research': episode['research'], 'sources': episode['sources']})
        for document in self.fixture['documents'].values():
            self.assertNotIn(document['body'], json.dumps(episode))
            for passage in document['passages']:
                self.assertNotIn(passage['text'], metadata)
        self.assertNotIn('Hidden', json.dumps(episode))
        self.assertTrue(all(row['reason'] == 'ok' for row in episode['research']['extraction']))
        writers = [p for p in self.model.payloads if 'chapter_assignment' in p and not p.get('expansion_pass')]
        self.assertTrue(all(set(p['evidence']) == set(p['chapter_assignment']['evidence_ids']) for p in writers))

    def test_episode_research_uses_shared_professional_topic_evidence(self):
        library = json.loads(PROFESSIONAL_LIBRARY.read_text(encoding='utf-8'))
        self.assertTrue(library['fixture_only'])
        self.assertEqual(len(library['topics']), 10)
        for topic in library['topics']:
            with self.subTest(topic=topic['id']):
                url = topic['source']['url']
                planner_rows = [{**row, 'query': f"{topic['label']} {row['query']}"}
                                for row in topic['questions']]
                planner = lambda _prompt, rows=planner_rows: {'questions': rows}
                searches = {row['query']: [[url]] for row in planner_rows}
                documents = {url: {'passages': [
                    {'text': row['text'], 'answers': [row['sub_question']]}
                    for row in topic['questions']]}}
                sources, evidence, chapters, metadata = prepare_research(
                    topic['label'], 3, 'news-brief', fixtures=FixtureResearch(searches, documents),
                    planner=planner,
                    limits=ResearchLimits(max_rounds=1, max_queries=3, max_reads=3,
                                          results_per_query=1, reads_per_question_round=1,
                                          min_sources=1, min_passages=1),
                    now='2026-11-19T12:00:00Z', clock=lambda: 0.0, cancelled=lambda: False)
                self.assertEqual(len(chapters), 3)
                self.assertTrue(all(chapter['evidence_ids'] for chapter in chapters))
                self.assertEqual(len(sources), 1, 'all three questions use the topic fixture source')
                self.assertEqual(set(row['text'] for row in evidence.values()),
                                 {row['text'] for row in topic['questions']})
                self.assertEqual(metadata['planner']['topic'], topic['label'])

    def test_thin_evidence_below_minimum_is_refused_but_adequate_evidence_passes(self):
        fixture = deepcopy(self.fixture)
        for query in fixture['searches']:
            fixture['searches'][query] = fixture['searches'][query][:1]
        with self.assertRaises(InsufficientEvidence) as refused:
            self.generate(fixture)
        self.assertLess(refused.exception.diagnostics['supported_words'],
                        refused.exception.diagnostics['minimum_supported_words'])
        self.assertFalse(any('chapter_assignment' in p for p in self.model.payloads))
        fixture = deepcopy(self.fixture)
        last = fixture['plan']['questions'][-1]
        fixture['searches'][last['query']] = fixture['searches'][last['query']][:1]
        episode = self.generate(fixture)
        self.assertEqual(len(episode['chapters']), 2)
        self.assertEqual(episode['sources_read'], 5)
        self.assertEqual(episode['source_count'], 4)
        self.assertIn({'question': last['sub_question']}, episode['could_not_confirm'])
        merged = [row for row in episode['research_plan'] if row.get('merged_into')]
        self.assertTrue(merged, 'unanswerable question is retained and its word budget merges into an answered chapter')
        self.assertEqual(sum(row['target_words'] for row in episode['research_plan']), 5 * WORDS_PER_MINUTE)

    def test_non_news_thin_and_contradictory_evidence_stay_explicit(self):
        def renamed_fixture(label):
            fixture = deepcopy(self.fixture)

            def rename(value):
                if isinstance(value, str):
                    return value.replace('Aster ferry', label).replace('Aster', label.split()[1])
                if isinstance(value, list):
                    return [rename(item) for item in value]
                if isinstance(value, dict):
                    return {rename(key): rename(item) for key, item in value.items()}
                return value

            return rename(fixture)

        ecology = renamed_fixture('Fictional marsh filtration')
        ecology['topic'] = 'How does fictional marsh filtration work?'
        for query in ecology['searches']:
            ecology['searches'][query] = ecology['searches'][query][:1]
        ecology_model = MockLLM(ecology)
        ecology_dependencies = self.dependencies(ecology)
        ecology_dependencies.update(planner=ecology_model.planner, client=ecology_model)
        with self.assertRaises(InsufficientEvidence) as refused:
            generate_episode(ecology['topic'], 5, state_dir=self.root / 'ecology',
                             episode_id='e' * 32, **ecology_dependencies)
        self.assertLess(refused.exception.diagnostics['supported_words'],
                        refused.exception.diagnostics['minimum_supported_words'])
        self.assertFalse(any('chapter_assignment' in payload for payload in ecology_model.payloads),
                         'thin non-news evidence stops before a writer can fill the gap')

        archive = renamed_fixture('Fictional map archive')
        archive['topic'] = 'How did the fictional map archive preserve records?'

        class ContradictoryModel(MockLLM):
            def __call__(self, system, payload, schema, timeout):
                result = super().__call__(system, payload, schema, timeout)
                assignment = payload.get('chapter_assignment', {})
                if (result.get('chapters') and
                        (assignment.get('index') == 1 or payload.get('repair'))
                        and not payload.get('expansion_pass')):
                    claim = next(row for row in result['chapters'][0]['sentences']
                                 if row['kind'] == 'claim')
                    claim['text'] = 'The archive preserved every map without any loss.'
                if 'script' in payload:
                    for row, verdict in zip(payload['script'], result.get('verdicts', [])):
                        if row['text'] == 'The archive preserved every map without any loss.':
                            verdict['supported'] = False
                return result

        archive_model = ContradictoryModel(archive)
        archive_dependencies = self.dependencies(archive)
        archive_dependencies.update(planner=archive_model.planner, client=archive_model)
        episode = generate_episode(archive['topic'], 5, state_dir=self.root / 'archive',
                                   episode_id='a' * 32, **archive_dependencies)
        self.assertTrue(episode['verification']['passed'])
        self.assertNotIn('preserved every map without any loss', episode['transcript'])
        self.assertTrue(any(item.get('unverified_claim') ==
                            'The archive preserved every map without any loss.'
                            for item in episode['could_not_confirm']))
        self.assertTrue(all(row['source_ids'] and row['source_numbers']
                            for chapter in episode['chapters']
                            for row in chapter['sentences']
                            if row['kind'] in {'claim', 'closing'}),
                        'retained non-news claims stay linked to cited sources')

    def test_blocked_extraction_and_fabricated_annotations_never_supply_evidence(self):
        for mode in ('paywall', 'annotation', 'empty'):
            fixture = deepcopy(self.fixture)
            for document in fixture['documents'].values():
                if mode == 'paywall':
                    document['body'] += '<aside>Subscribe to continue</aside>'
                elif mode == 'annotation':
                    for row in document['passages']:
                        row['text'] = 'Invented evidence absent from the document.'
                else:
                    document['body'] = ''
            with self.subTest(mode=mode):
                with self.assertRaises(InsufficientEvidence) as refused:
                    self.generate(fixture)
                self.assertEqual(refused.exception.diagnostics['sources_read'], 0)

    def test_repair_is_checked_again_and_cannot_erase_research_gaps(self):
        fixture = deepcopy(self.fixture)
        last = fixture['plan']['questions'][-1]
        fixture['searches'][last['query']] = []
        base = self.model
        def bad_first(system, payload, schema, timeout):
            result = base(system, payload, schema, timeout)
            if payload.get('chapter_assignment', {}).get('index') == 1:
                first_claim = next(row for row in result['chapters'][0]['sentences'] if row['kind'] == 'claim')
                first_claim['evidence_ids'] = ['invented']
            return result
        dependencies = self.dependencies(fixture)
        dependencies['client'] = bad_first
        episode = generate_episode(fixture['topic'], 5, state_dir=self.root, **dependencies)
        self.assertEqual(episode['verification']['attempts'], 2)
        self.assertIn({'question': last['sub_question']}, episode['could_not_confirm'])
        self.assertNotIn('invented', json.dumps(episode))
        self.assertTrue(episode['verification']['complete_script_checked'])

    def test_shared_pages_still_give_each_writer_only_its_annotated_passages(self):
        fixture = deepcopy(self.fixture)
        urls = list(fixture['documents'])
        shared = {}
        for index in range(2):
            passages = [p for url in urls[index::2] for p in fixture['documents'][url]['passages']]
            shared[urls[index]] = {'passages': passages, 'content_type': 'text/plain',
                                   'body': ' '.join(p['text'] for p in passages)}
        fixture['documents'] = shared
        fixture['searches'] = {q: [[urls[0]], [urls[1]]] for q in fixture['searches']}
        episode = self.generate(fixture)
        self.assertEqual(episode['source_count'], 2)
        self.assertEqual(episode['retrieval']['read_attempts'], 2)
        writers = [p for p in self.model.payloads if 'chapter_assignment' in p and not p.get('expansion_pass')]
        self.assertEqual([len(p['evidence']) for p in writers], [6, 6, 6])
        self.assertFalse(set(writers[0]['evidence']) & set(writers[1]['evidence']))

    def test_failed_semantic_verifier_never_returns_an_episode(self):
        base = self.model
        def rejected(system, payload, schema, timeout):
            result = base(system, payload, schema, timeout)
            if 'script' in payload:
                result['on_topic'] = False
            return result
        deps = self.dependencies()
        deps['client'] = rejected
        with self.assertRaisesRegex(InsufficientEvidence, '^script_verification_failed$'):
            generate_episode(self.fixture['topic'], 5, state_dir=self.root, **deps)

    def test_cancellation_during_extraction_discards_late_evidence(self):
        deps = self.dependencies()
        stopped = []
        extract = deps['research'].extractor
        def cancel_after_read(*args):
            result = extract(*args)
            stopped.append(True)
            return result
        deps['research'].extractor = cancel_after_read
        with self.assertRaises(EpisodeCancelled):
            generate_episode(self.fixture['topic'], 5, state_dir=self.root,
                             cancelled=lambda: bool(stopped), **deps)
        self.assertFalse(any('chapter_assignment' in p for p in self.model.payloads))

    def test_script_checks_block_short_sentence_even_with_valid_evidence(self):
        self.model.force_short_claim = True
        result = self.generate()
        self.assertNotIn('Passengers reserve places.', result['transcript'])
        self.assertTrue(result['verification']['script_checks']['passed'])
        repair = next(p['repair'] for p in self.model.payloads if 'repair' in p)
        self.assertIn('script_check:sentence_length', repair['issues'])

    def test_explicit_dependencies_cancellation_and_budget_fail_closed(self):
        deps = self.dependencies()
        deps.pop('client')
        with self.assertRaisesRegex(EpisodeError, '^fixture_dependencies_required$'):
            generate_episode(self.fixture['topic'], state_dir=self.root, **deps)
        with self.assertRaisesRegex(EpisodeError, '^mixed_research_dependencies$'):
            self.generate(providers=[])
        with self.assertRaises(EpisodeCancelled):
            self.generate(cancelled=lambda: True)
        with self.assertRaises(InsufficientEvidence):
            self.generate(research_limits=ResearchLimits(max_rounds=1))
        deps = self.dependencies()
        deps['clock'] = Mock(side_effect=range(1000))
        with self.assertRaisesRegex(EpisodeError, '^episode_deadline$'):
            generate_episode(self.fixture['topic'], 5, state_dir=self.root, timeout_seconds=2, **deps)

    def test_jobs_use_real_flag_and_publish_only_successful_verified_episode(self):
        spy = Mock(side_effect=AssertionError('real path must remain opt-in'))
        with patch.dict('os.environ', {'BRIEFING_EPISODES': 'fake'}):
            store = EpisodeJobStore(self.root, stage_delay=0, real_generator=spy)
        _, job = store.submit(PROFILE, {'topic': self.fixture['topic'], 'length': 5, 'style': 'explainer'}, start=False)
        store.run_pending(PROFILE, job['job_id'])
        spy.assert_not_called()
        with patch.dict('os.environ', {'BRIEFING_EPISODES': 'real'}):
            store = EpisodeJobStore(self.root, stage_delay=0,
                                    real_generator=partial(generate_episode, **self.dependencies()),
                                    key_available=lambda: True,
                                    audio_renderer=partial(render_episode_audio, provider=FakeProvider()))
        _, job = store.submit(PROFILE, {'topic': self.fixture['topic'], 'length': 5, 'style': 'explainer'}, start=False)
        store.run_pending(PROFILE, job['job_id'])
        completed = store.get(PROFILE, job['job_id'])
        self.assertEqual(completed['status'], 'done')
        self.assertEqual(completed['episode']['id'], job['job_id'])
        fixture = deepcopy(self.fixture)
        fixture['documents'] = {}
        store.real_generator = partial(generate_episode, **self.dependencies(fixture))
        _, job = store.submit(PROFILE, {'topic': self.fixture['topic'], 'length': 5, 'style': 'explainer',
                                        'fresh': True}, start=False)
        store.run_pending(PROFILE, job['job_id'])
        failed = store.get(PROFILE, job['job_id'])
        self.assertEqual(failed['failure_reason'], 'insufficient_evidence')
        self.assertIn('not enough reliable evidence', failed['message'].casefold())
        self.assertIn('shorter length', failed['message'].casefold())
        self.assertIsNone(failed.get('episode'))

    def test_generated_episode_correction_invalidates_text_approval_audio_and_timings(self):
        episode = self.generate()
        queue = Mock()
        queue.status.return_value = 'regenerating'
        service = CorrectionService(self.root, audio_queue=queue,
                                    episode_reader=lambda *_: {'job_id': EPISODE_ID, 'status': 'done', 'episode': episode})
        first_index = next(index for index, row in enumerate(episode['chapters'][0]['sentences'])
                           if row['kind'] == 'claim')
        first = episode['chapters'][0]['sentences'][first_index]
        new_text = 'Aster passengers can choose a crossing before reaching the quay using the booking form.'
        corrected = service.create(PROFILE, {
            'target_type': 'episode', 'target_id': EPISODE_ID, 'chapter_id': 'chapter-1', 'sentence_index': first_index,
            'previous_revision_id': episode['revision_id'], 'old_text': first['text'], 'new_text': new_text,
            'what_changed': 'Clarified the fictional booking process.',
            'evidence': [{'source_id': first['source_ids'][0], 'quote': first['text']}],
        })['episode']
        self.assertEqual(corrected['transcript'], transcript(corrected['chapters']))
        self.assertEqual(corrected['script_hash'], digest(corrected['transcript']))
        self.assertNotEqual(corrected['revision_id'], episode['revision_id'])
        self.assertEqual(corrected['chapters'][0]['sentences'][first_index]['id'], first['id'])
        self.assertNotIn('evidence_ids', corrected['chapters'][0]['sentences'][first_index])
        self.assertFalse(corrected['verification']['passed'])
        self.assertTrue(corrected['audio_stale'])
        self.assertNotIn('estimated_start_seconds', json.dumps(corrected['chapters']))
        self.assertEqual(queue.enqueue.call_count, 1)
        self.assertEqual(service.apply_job({'episode': episode, 'job_id': EPISODE_ID}, PROFILE)['episode']['transcript'],
                         corrected['transcript'])
        self.assertEqual(episode['chapters'][0]['sentences'][first_index]['text'], first['text'])

        # Seed a stale stored closing; this does not claim the generator approved it.
        stale = deepcopy(episode)
        last_chapter = stale['chapters'][-1]
        last_index = len(last_chapter['sentences']) - 1
        old_close = last_chapter['sentences'][last_index]
        replacement = old_close['text']
        bad_close = 'The fictional Aster ferry will definitely offer overnight crossings.'
        old_close['text'] = bad_close
        last_chapter['spoken_text'] = ' '.join(row['text'] for row in last_chapter['sentences'])
        stale['transcript'] = transcript(stale['chapters'])
        stale['script_hash'] = digest(stale['transcript'])
        stale['revision_id'] = 'fictional-stale-closing'
        closing_service = CorrectionService(self.root / 'closing-control', audio_queue=queue,
            episode_reader=lambda *_: {'job_id': EPISODE_ID, 'status': 'done', 'episode': stale})
        fixed = closing_service.create(PROFILE, {
            'target_type': 'episode', 'target_id': EPISODE_ID,
            'chapter_id': last_chapter['id'], 'sentence_index': last_index,
            'previous_revision_id': stale['revision_id'], 'old_text': bad_close,
            'new_text': replacement, 'what_changed': 'Remove unsupported overnight service guarantee.',
            'evidence': [{'source_id': old_close['source_ids'][0], 'quote': replacement}],
        })['episode']
        final_close = fixed['chapters'][-1]['sentences'][-1]
        self.assertEqual(final_close['kind'], 'closing')
        self.assertEqual(final_close['text'], replacement)
        self.assertEqual(final_close['source_ids'], old_close['source_ids'])
        self.assertTrue(final_close['source_numbers'])
        self.assertNotIn('evidence_ids', final_close)
        self.assertNotIn(bad_close, fixed['transcript'])
        self.assertNotIn(bad_close, fixed['chapters'][-1]['spoken_text'])
        self.assertTrue(fixed['transcript'].endswith(replacement))
        self.assertEqual(fixed['transcript'], transcript(fixed['chapters']))
        self.assertEqual(fixed['script_hash'], digest(fixed['transcript']))
        self.assertNotEqual(fixed['script_hash'], stale['script_hash'])
        self.assertEqual(fixed['verification'], {
            'passed': False, 'reason': 'material_correction_requires_recheck'})


if __name__ == '__main__':
    unittest.main()
