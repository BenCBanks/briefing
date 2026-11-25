"""Offline end-to-end daily briefing coverage with fixture supply and audio."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import re
import tempfile
from threading import Thread
from urllib.request import urlopen
import unittest
import wave
from unittest.mock import patch

from dev import content_check, preview_server
from dev.ingest import daily, daily_audio, daily_briefing, episodes_audio, generation_api, repeats
from dev.ingest.score_edition import score_edition
from dev.ingest.paths import profile_identifier
from dev.ingest.tts.base import TtsResult
from dev.ingest.tts.gemini import DEFAULT_MODEL
from dev.tools.morning_report import build_report

NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
TODAY = NOW.date()
PROFILE = {
    'words': 'Payments and banking, housing and construction, health insurance policy',
    'topics': [
        {'id': 'payments', 'label': 'Payments and banking', 'scope': 'payments and banking settlement',
         'include': ['payments', 'banking', 'settlement']},
        {'id': 'housing', 'label': 'Housing and construction', 'scope': 'housing and construction',
         'include': ['housing', 'construction', 'permits']},
        {'id': 'health-policy', 'label': 'Health insurance policy', 'scope': 'health insurance coverage',
         'include': ['health insurance', 'coverage', 'Medicaid']},
    ],
}
ANGLES = ('data_point', 'how_it_works', 'local_angle', 'what_is_next', 'who_is_affected')
LABELS = {'payments': 'payments and banking', 'housing': 'housing and construction',
          'health-policy': 'health insurance coverage', 'ai-finance': 'artificial intelligence in banking',
          'energy': 'electricity and grid infrastructure', 'transport': 'freight and transportation'}
REALISTIC_FIXTURES = Path(__file__).parent / 'fixtures/realistic-daily-days.json'


class FakeLedger:
    def __init__(self, remaining):
        self.remaining = Decimal(str(remaining))

    def daily_audio_budget_remaining(self):
        return self.remaining

    @staticmethod
    def estimate_tts_cost(characters, duration_seconds):
        return Decimal('0.01')


class FixtureTtsProvider:
    """Return short synthetic WAV chapters without contacting a provider."""
    provider_name = 'fixture'
    model = DEFAULT_MODEL

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def synthesize(self, text, voice_name):
        self.calls.append((text, voice_name))
        if self.fail:
            raise RuntimeError('fictional TTS failure')
        output = BytesIO()
        with wave.open(output, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(1000)
            wav_file.writeframes(b'\0\0' * 1000)
        return TtsResult(output.getvalue(), 'audio/wav', 1.0, len(text),
                         Decimal('0.001'), DEFAULT_MODEL, voice_name)


def _story(story_id, topic_id, *, origin='feed', update=False, published='2026-11-10T12:00:00Z'):
    label = LABELS[topic_id]
    lead = story_id.split('-', 1)[0]
    entity = {
        'cedar': 'Cedar Bank', 'piedmont': 'Piedmont Network', 'triad': 'Triad Payments',
        'pinevale': 'Pinevale Permit Office', 'builder': 'Pinevale Builders Council',
        'cms': 'CMS', 'coverage': 'Health Coverage Council', 'regional': 'Regional Bank Council',
        'grid': 'Piedmont Grid', 'freight': 'Blue Ridge Freight',
    }.get(lead, lead.title())
    heading = {
        ('payments', 'cedar'): 'Cedar Bank moves its same-day transfer cutoff to 6 p.m.',
        ('payments', 'piedmont'): 'Piedmont Network reports an overnight instant-payments outage',
        ('payments', 'triad'): 'Triad Payments adds a transaction recall feature for businesses',
        ('housing', 'pinevale'): 'Pinevale Permit Office speeds review of small-home plans',
        ('housing', 'builder'): 'Pinevale Builders Council finds permit waits vary by county',
        ('health-policy', 'cms'): 'CMS sets a December date for the next Medicaid review phase',
        ('health-policy', 'coverage'): 'Health Coverage Council reports rural enrollment delays',
        ('ai-finance', 'regional'): 'Regional Bank Council publishes an AI risk review',
        ('energy', 'grid'): 'Piedmont Grid changes its interconnection study schedule',
        ('transport', 'freight'): 'Blue Ridge Freight opens planning for a new corridor',
    }.get((topic_id, lead), f'{entity} reports a new development in {label}')
    links = []
    freight_details = [
        'Blue Ridge Freight opened planning for a proposed freight corridor.',
        'Carriers can comment before officials choose a route.',
        'The corridor remains a proposal, and the final route has not been chosen.',
        'The planning review will compare possible connections for freight operators.',
        'Carriers can submit feedback before the agency selects a route.',
        'The notice describes planning work, not a completed corridor.',
        'The agency will consider comments before publishing its route decision.',
    ]
    for index in range(1, 8):
        if lead == 'freight':
            text = freight_details[index - 1]
        else:
            text = (f"The {label} report {story_id} describes verified detail {index}, and explains what the "
                    "documented change means for people following this topic today.")
        if update and index == 1:
            text += ' A November 17, 2026 review establishes a 12-day implementation schedule.'
        links.append({'section': 'what_happened' if index == 1 else
                      'why_it_matters' if index == 2 else 'things_to_consider',
                      'text': text, 'verified': True,
                      'source_sentence_ids': [f'{story_id}-sentence-{index}'],
                      'citation_numbers': [1]})
    body = (f"{heading}. What happened: {links[0]['text']} Why it matters: {links[1]['text']} "
            f"Things to consider: {' '.join(link['text'] + ' [1]' for link in links[2:])} "
            "Limits: Fictional fixture source; no additional context is asserted.")
    row = {'id': f'section-{story_id}', 'cluster_id': f'cluster-{story_id}',
           'heading': heading, 'body': body, 'topic_ids': [topic_id],
           'source_ids': [f'source-{story_id}'], 'claim_links': links,
           'section_blocks': {'what_happened': links[0]['text'],
                              'why_it_matters': links[1]['text'],
                              'things_to_consider': ' '.join(link['text'] for link in links[2:])},
           'source_status': 'single-source',
           'limits': 'Fictional fixture source; no additional context is asserted.',
           'scorecard_passed': True, 'relevance_score': .95, 'credibility_score': .9,
           'published_at': published, 'supply_origin': origin}
    if update:
        row.update(new_fact_supported=True, previous_covered_at='2026-11-01T00:00:00Z')
    return row


def _source(story):
    return {'id': story['source_ids'][0], 'registry_id': 'fictional-daily-news',
            'reuse_terms': 'Fictional test license permits attributed short excerpts.',
            'title': story['heading'],
            'url': f"https://news.example.test/{story['cluster_id']}",
            'publisher': f"Fictional {story['cluster_id']} News Desk", 'tier': 'major',
            'source_class': 'news', 'evidence_depth': 'full-text',
            'published_at': story['published_at']}


def _quick_hits():
    return [
        {'title': 'Payments agency opens fictional settlement test',
         'url': 'https://news.example.test/quick-payments', 'topic_id': 'payments',
         'published_at': '2026-11-17T12:00:00Z', 'tier': 'high'},
        {'title': 'Housing office releases fictional permit data',
         'url': 'https://news.example.test/quick-housing', 'topic_id': 'housing',
         'published_at': '2026-11-16T12:00:00Z', 'tier': 'high'},
    ]


class DailyBriefingEndToEndTests(unittest.TestCase):
    def _fixture_scenario(self, name):
        data = json.loads(REALISTIC_FIXTURES.read_text(encoding='utf-8'))
        self.assertIs(data['fictional'], True)
        return next(row for row in data['scenarios'] if row['id'] == name)

    def _fixture_profile(self, scenario):
        topics = [row for row in PROFILE['topics'] if row['id'] in scenario['topics']]
        for topic_id, label in LABELS.items():
            if topic_id in scenario['topics'] and not any(row['id'] == topic_id for row in topics):
                topics.append({'id': topic_id, 'label': label, 'scope': label,
                               'include': label.split()})
        return {'words': ', '.join(row['label'] for row in topics), 'topics': topics}

    def _assert_truthful_duration(self, edition):
        daily = edition['daily_briefing']
        words = sum(daily_briefing._spoken_words(row.get('text', ''))
                    for row in daily['briefing_script'])
        self.assertEqual(daily['spoken_words'], words)
        seconds = round(words * 60 / daily['narrator_words_per_minute'])
        self.assertEqual(daily['duration_seconds'], seconds)
        self.assertEqual(edition['duration_seconds'], seconds)

    def _case(self, name):
        if name in {row['id'] for row in json.loads(REALISTIC_FIXTURES.read_text())['scenarios']}:
            scenario = self._fixture_scenario(name)
            def rows(key, origin='feed', is_update=False):
                return [_story(row['id'], row['topic'], origin=origin, update=is_update,
                               published='2026-11-17T10:00:00Z' if is_update else '2026-11-10T12:00:00Z')
                        for row in scenario[key]]
            return rows('feed'), rows('open_web', 'open-web'), rows('followups', 'followup', True)
        feed = []
        retrieval = []
        update = []
        if name == 'rich':
            feed = [_story(story_id, topic_id) for story_id, topic_id in (
                ('cedar-rich', 'payments'), ('piedmont-rich', 'payments'),
                ('pinevale-rich', 'housing'), ('builder-rich', 'housing'),
                ('cms-rich', 'health-policy'), ('coverage-rich', 'health-policy'))]
        elif name == 'one-item':
            feed = [_story('feed-one', 'payments')]
            retrieval = [_story(f'web-{i}', topic) for i, topic in enumerate(('housing', 'health-policy'))]
            update = [_story('update-one', 'payments', origin='followup', update=True,
                             published='2026-11-17T10:00:00Z')]
        elif name == 'zero-item':
            retrieval = [_story(f'web-{i}', topic) for i, topic in enumerate(
                ('payments', 'housing', 'health-policy'))]
        elif name == 'repeat-heavy':
            feed = [_story(f'repeat-{i}', 'payments') for i in range(3)] + [_story('survivor', 'housing')]
            retrieval = [_story('web-after-repeats', 'health-policy')]
            update = [_story('repeat-0', 'payments', origin='followup', update=True,
                             published='2026-11-17T10:00:00Z')]
        elif name == 'search-failure':
            feed = [_story('feed-search-failure-payments', 'payments'),
                    _story('feed-search-failure-housing', 'housing')]
            update = [_story('update-after-search-failure-health', 'health-policy', origin='followup', update=True,
                             published='2026-11-17T10:00:00Z')]
        elif name == 'cap-reserve-short':
            retrieval = [_story('web-cap-payments', 'payments'), _story('web-cap-housing', 'housing'),
                         _story('web-cap-health', 'health-policy')]
        return feed, retrieval, update

    def _history_for(self, feed, *, repeat_heavy=False):
        repeated = feed[:3] if repeat_heavy else []
        edition = {'id': 'prior-fixture-edition', 'kind': 'daily',
                   'generated_at': '2026-11-10T12:00:00Z',
                   'seen_by_reader': True,
                   'sections': [dict(row) for row in repeated],
                   'daily_briefing': {'background_segments': []}}
        sources = [_source(row) for row in repeated]
        history = [{'editions': {'editions': [edition]}, 'sources': {'sources': sources}}] if repeated else []
        return history, repeats.published_stories(history)

    def _background(self, topic_id, *, angle, remaining_words, exclude_ids):
        segment_id = f'fixture-explainer-{topic_id}-{angle}'
        if segment_id in exclude_ids:
            return None
        sentences = [
            {'text': (f"A reviewed reference explains {LABELS[topic_id]} through the {angle.replace('_', ' ')} angle, "
                      f"including established context, the people affected, and how this system works in practice."),
             'source_sentence_ids': [f'{segment_id}-{index}'], 'citation_numbers': [1]}
            for index in range(1, 13)
        ]
        return {'segment_id': segment_id, 'source_kind': 'reference', 'scorecard_passed': True,
                'reviewed_at': '2026-11-01', 'sentences': sentences}

    def _run_case(self, name, *, verify_delivery=False, background_provider=None,
                  tts_failure=False):
        scenario = None
        profile = PROFILE
        if name in {row['id'] for row in json.loads(REALISTIC_FIXTURES.read_text())['scenarios']}:
            scenario = self._fixture_scenario(name)
            profile = self._fixture_profile(scenario)
        feed, retrieval_rows, updates = self._case(name)
        history, prior = self._history_for(feed, repeat_heavy=name == 'repeat-heavy')
        repeated_ids = {row['cluster_id'] for row in feed[:3]} if name == 'repeat-heavy' else set()
        background_history = []
        if name == 'repeat-heavy':
            topic = sorted(row['id'] for row in PROFILE['topics'])[0]
            previous_day = date.fromordinal(TODAY.toordinal() - 1)
            angle = ANGLES[(previous_day.toordinal()) % len(ANGLES)]
            background_history.append({'segment_id': f'fixture-explainer-{topic}-{angle}',
                                       'sentences': [{'text': 'Previously used fictional explainer.'}]})

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            edition_path = root / 'edition.json'
            voice_file = root / 'voice.json'
            voice_file.write_text(json.dumps({'voice_name': 'Kore'}), encoding='utf-8')
            rights_path = root / 'source-rights.md'
            rights_path.write_text('`fictional-daily-news` permits attributed short excerpts.\n',
                                   encoding='utf-8')
            report_path = root / 'state' / 'status.json'
            audio_calls, tts_calls = [], []
            fixture_tts = FixtureTtsProvider(fail=tts_failure)
            cap = Decimal('0') if name == 'cap-reserve-short' else Decimal('1')
            sources = []
            candidate_sections = []
            repeat_suppressed = 0
            for row in feed:
                candidate = {'id': row['cluster_id'], 'rows': [{'item': {
                    'title': row['heading'], 'url': _source(row)['url'],
                    'published': row['published_at']}}]}
                if repeats.match_story(candidate, prior):
                    repeat_suppressed += 1
                    continue
                candidate_sections.append(row)
            if repeated_ids:
                self.assertEqual(repeat_suppressed, len(repeated_ids))

            for row in [*candidate_sections, *retrieval_rows, *updates]:
                sources.append(_source(row))
            sources.extend([
                {'id': 'quick-one', 'title': 'Fictional payments reference',
                 'url': 'https://agency.example.test/payments', 'publisher': 'Fictional agency',
                 'source_class': 'reference', 'published_at': '2026-11-01T00:00:00Z'},
            ])
            source_dates = {row['id']: {**row, 'citation_number': index}
                            for index, row in enumerate(sources, 1)}

            def start(profile):
                self.assertEqual(profile, expected_profile)
                edition = {'id': f'daily-fixture-{name}', 'revision_id': f'rev-{name}',
                           'kind': 'daily', 'local_date': TODAY.isoformat(),
                           'generated_at': NOW.isoformat(), 'sections': candidate_sections,
                           'quick_hits': _quick_hits()}
                retrieval_calls = []

                def retrieval_fill(_profile, *, remaining_words, freshness_days):
                    retrieval_calls.append(remaining_words)
                    self.assertEqual(freshness_days, 90)
                    if name == 'search-failure':
                        raise OSError('fictional provider failure')
                    return retrieval_rows

                def followup_fill(_profile, *, remaining_words):
                    return updates

                score_rows = [*candidate_sections, *retrieval_rows, *updates]
                score_evidence = {
                    source_sentence_id: {'text': link['text'], 'source_number': 1}
                    for story in score_rows for link in story['claim_links']
                    for source_sentence_id in link['source_sentence_ids']
                }
                actual_scorecard = score_edition(
                    {'id': f'daily-fixture-{name}', 'revision_id': f'rev-{name}',
                     'sections': score_rows}, sources,
                    [{'id': 'fictional-daily-news',
                      'reuse_terms': 'Fictional test license permits attributed short excerpts.'}],
                    score_evidence, rights_path=rights_path)
                self.assertFalse(actual_scorecard['critical_failed'],
                                 f'{name} scorecard: {actual_scorecard}')
                checks_by_number = {row['number']: row for row in actual_scorecard['checks']}
                self.assertEqual(checks_by_number[6]['status'], 'PASS')
                self.assertEqual(checks_by_number[11]['status'], 'PASS')

                assembled = daily_briefing.assemble(
                    edition, profile, retrieval_fill=retrieval_fill,
                    followup_fill=followup_fill, followups_checked=True,
                    background_fill=background_provider or self._background,
                    background_history=background_history,
                    source_dates=source_dates, today=TODAY)
                if name == 'rich':
                    self.assertFalse(retrieval_calls, 'feed-only rich day already meets target')
                story_rows = [row for row in assembled['sections'] if row.get('cluster_id')]
                if name in {'zero-item', 'search-failure', 'cap-reserve-short'}:
                    self.assertGreaterEqual(len(story_rows), 3, f'{name} must use three eligible story clusters')
                    self.assertEqual(len({row['cluster_id'] for row in story_rows}), len(story_rows))
                    self.assertTrue(all(any(link.get('verified') is True and
                                            link.get('source_sentence_ids') and
                                            link.get('citation_numbers')
                                            for link in row.get('claim_links', []))
                                        for row in story_rows),
                                    f'{name} stories must remain substantive and cited')
                payload = {'edition': assembled, 'sources': sources}
                checks = content_check.check_content(payload, history, profile, now=NOW)
                hard_failures = generation_api._hard_content_failures(
                    generation_api._content_failure_codes(checks))
                if hard_failures:
                    dropped = generation_api._repair_daily_content_items(
                        assembled, checks, profile,
                        source_dates=source_dates,
                        source_tiers=daily_briefing.source_tier_map(sources),
                        recent_history=history,
                        detail={'retrieval_fill': {
                            'enabled': True, 'triggered': bool(retrieval_calls),
                            'provider_statuses': {'unavailable': 1} if name == 'search-failure' else {}},
                            'followup_count': len(updates)})
                    self.assertTrue(dropped, f'{name} named content failures should be repairable')
                    payload = {'edition': assembled, 'sources': sources}
                    checks = content_check.check_content(payload, history, profile, now=NOW)
                self.assertTrue(all(row['passed'] for row in checks),
                                f"{name} content check failed: {checks}")
                self._assert_truthful_duration(assembled)
                self.assertLessEqual(assembled['daily_briefing']['spoken_words'],
                                     assembled['daily_briefing']['maximum_spoken_words'])
                self.assertFalse(assembled['thin_day'])
                edition_path.write_text(json.dumps(payload), encoding='utf-8')
                job = {'status': 'complete', 'job_id': f'fixture-{name}', 'stories_written': len(candidate_sections),
                       'edition_ready': {'edition': assembled, 'sources': sources},
                       'daily_narration': {'status': 'off', 'reason': 'flag_disabled'},
                       'scorecard': actual_scorecard,
                       'run_status': {'repeat_suppressed': repeat_suppressed,
                                      'retrieval_fill': {'enabled': True, 'triggered': bool(retrieval_calls)}}}
                return 202, {'job_id': job['job_id']}, job

            def get_job(job_id):
                self.assertEqual(job_id, f'fixture-{name}')
                return 200, job_result[0]

            def audio_renderer(edition, *, profile):
                audio_calls.append(edition['id'])
                if tts_failure:
                    edition['audio'] = {
                        'audio_status': 'ready', 'audio_url': '/api/daily/audio',
                        'duration_seconds': 299.0, 'chapter_offsets': [
                            {'chapter_id': 'prior-story', 'start_seconds': 0.0,
                             'duration_seconds': 299.0}],
                        'voice_name': 'Kore', 'revision_id': 'prior-revision',
                        'script_hash': 'fictional-prior-script-hash',
                    }

                def render_fixture(episode, *args, **kwargs):
                    return _fixture_episode_renderer(episode, tts_calls, *args, **kwargs)

                return daily_audio.render_daily_audio(
                    edition, profile=profile, voice_file=voice_file, state_dir=root,
                    ledger=FakeLedger(cap),
                    provider=fixture_tts,
                    renderer=render_fixture)

            def persist_audio(_profile, edition, audio):
                edition['audio'] = audio
                saved = json.loads(edition_path.read_text(encoding='utf-8'))
                saved['edition']['audio'] = audio
                edition_path.write_text(json.dumps(saved), encoding='utf-8')
                return audio

            job_result = []
            start_errors = []

            def start_and_capture(profile):
                try:
                    status, response, complete = start(profile)
                    job_result.append(complete)
                    return status, response
                except Exception as error:
                    start_errors.append(error)
                    raise

            run_report = {}
            expected_profile = profile
            def save_report(value):
                run_report.update(daily.write_run_report(value, report_path))
            with patch.dict('os.environ', {'BRIEFING_TTS': 'enabled', 'BRIEFING_TESTING': '1',
                                           'BRIEFING_STATE_DIR': str(root / 'state'),
                                           'BRIEFING_FLAGS_FILE': str(root / 'flags.json')}):
                code, result = daily.run_daily(
                    profile_loader=lambda: expected_profile, start=start_and_capture, get_job=get_job,
                    wait_seconds=2, poll_seconds=0,
                    report_writer=save_report,
                    daily_audio_renderer=audio_renderer,
                    daily_audio_persister=persist_audio)
            if start_errors:
                raise start_errors[0]
            self.assertEqual(code, 0, result)
            self.assertEqual(len(audio_calls), 1)
            persisted_report = json.loads(report_path.read_text(encoding='utf-8'))['daily_run']
            self.assertEqual(persisted_report, run_report)
            self.assertEqual(persisted_report['status'], 'complete')
            self.assertEqual(persisted_report['daily_briefing']['target_minutes'], 5)
            self.assertEqual(persisted_report['daily_audio']['audio_status'],
                             result['daily_audio']['audio_status'])
            self.assertEqual(persisted_report['daily_audio']['duration_seconds'],
                             result['daily_audio']['duration_seconds'])
            stored = json.loads(edition_path.read_text(encoding='utf-8'))
            published = stored['edition']
            self._assert_truthful_duration(published)
            self.assertLessEqual(published['daily_briefing']['spoken_words'],
                                 published['daily_briefing']['maximum_spoken_words'])
            self.assertEqual(published['daily_briefing']['target_minutes'], 5)
            self.assertEqual(published['duration_seconds'],
                             round(published['daily_briefing']['spoken_words'] * 60 /
                                   published['daily_briefing']['narrator_words_per_minute']))
            self.assertTrue(published['daily_briefing']['greeting'].startswith('Good morning'))
            self.assertTrue(published['daily_briefing']['briefing_script'][0]['text'].startswith('Good morning'))
            spoken_copy = ' '.join(row['text'] for row in published['daily_briefing']['briefing_script'])
            self.assertNotRegex(spoken_copy, r'(?i)\b(?:breaking|just in|live|urgent|urgently)\b')
            for story in (row for row in published['sections'] if row.get('cluster_id')):
                intro = next(row for row in published['daily_briefing']['briefing_script']
                             if row['kind'] == 'story_intro' and row.get('section_id') == story['cluster_id'])
                self.assertRegex(intro['text'], r'Published \d{1,2} [A-Z][a-z]{2}')
            self.assertTrue(any(row.get('lede') and row.get('so_what') for row in published['sections']))
            self.assertTrue(any(row.get('more') for row in published['sections']))
            if len(profile['topics']) > 1:
                self.assertGreaterEqual(len(published.get('also_today', {}).get('items', [])), 2)
            self.assertEqual(run_report['daily_briefing']['spoken_words'],
                             published['daily_briefing']['spoken_words'])
            if scenario:
                story_rows = [row for row in published['sections'] if row.get('cluster_id')]
                if name == 'rich':
                    selected_topics = {topic['id'] for topic in profile['topics']}
                    seen_topics = set()
                    for story in story_rows:
                        topic_id = story['topic_ids'][0]
                        if topic_id in seen_topics:
                            self.assertEqual(seen_topics, selected_topics,
                                             'the first item pass must cover every selected topic')
                        seen_topics.add(topic_id)
                    self.assertEqual(seen_topics, selected_topics)
                self.assertGreaterEqual(len(story_rows), 3)
                cluster_ids = [row['cluster_id'] for row in story_rows]
                self.assertEqual(len(cluster_ids), len(set(cluster_ids)), 'no duplicate story clusters in one edition')
                for index, first in enumerate(story_rows):
                    first_urls = {_source(first)['url']}
                    for second in story_rows[index + 1:]:
                        self.assertLess(content_check.similarity(first['heading'], second['heading']), .6,
                                        'different clusters must not repeat a headline')
                        self.assertTrue(first_urls.isdisjoint({_source(second)['url']}),
                                        'different clusters must not reuse a source article')
                checks = content_check.check_content(
                    {'edition': published, 'sources': sources}, history, profile, now=NOW)
                repeat_check = next(row for row in checks if row['name'] == 'repeats')
                self.assertTrue(repeat_check['passed'], repeat_check)
                actual_rungs = set()
                if any(row.get('supply_origin') == 'feed' for row in story_rows):
                    actual_rungs.add('feed')
                if any(row.get('supply_origin') == 'open-web' for row in story_rows):
                    actual_rungs.add('open_web')
                if any(row.get('kicker') == 'Update:' for row in story_rows):
                    actual_rungs.add('followups')
                if published['daily_briefing']['background_segments']:
                    actual_rungs.add('background')
                expected_rungs = set(scenario['rungs_used'])
                if background_provider is not None and published['daily_briefing']['background_segments']:
                    expected_rungs.add('background')
                self.assertEqual(actual_rungs, expected_rungs)
                script_text = ' '.join(row['text'] for row in published['daily_briefing']['briefing_script'])
                self.assertIn('Today, we are following', script_text)
                self.assertIn('Now to', script_text)
                self.assertTrue(script_text.rstrip().endswith("That's today's briefing."))
                for topic in profile['topics']:
                    self.assertIn(topic['label'], next(row['text'] for row in
                                                       published['daily_briefing']['briefing_script']
                                                       if row['kind'] == 'topic_intro'))
                transitions = {row.get('topic_id') for row in published['daily_briefing']['briefing_script']
                               if row['kind'] == 'topic_transition'}
                self.assertEqual(transitions, {topic['id'] for topic in profile['topics']})
                transition_rows = [row for row in published['daily_briefing']['briefing_script']
                                   if row['kind'] == 'topic_transition']
                self.assertEqual(len(transition_rows), len(profile['topics']))
                self.assertTrue(all(row['text'].endswith('.') and row['text'].count('.') == 1
                                    for row in transition_rows))
                self.assertEqual(published['daily_briefing']['target_minutes'], 5)
                self.assertEqual(published['daily_briefing']['nominal_target_spoken_words'],
                                 published['daily_briefing']['target_minutes'] *
                                 published['daily_briefing']['narrator_words_per_minute'])
                self.assertTrue(all(section.get('audio_script', {}).get('spoken_text')
                                    for section in story_rows))
                if name == 'repeat-heavy':
                    self.assertEqual(repeat_suppressed, len(scenario['prior']))
            if name == 'search-failure':
                statuses = {row['rung']: row['status'] for row in published['daily_briefing']['supply_ladder']}
                self.assertEqual(statuses['open_web'], 'unavailable')
                self.assertIn('open web search (unavailable)', published['daily_briefing']['search_summary'])
            if name == 'repeat-heavy':
                self.assertEqual(run_report['repeat_suppressed'], 3)
                background_ids = {row['segment_id'] for row in published['daily_briefing']['background_segments']}
                self.assertTrue(background_ids.isdisjoint({row['segment_id'] for row in background_history}))
                topic_order = sorted(row['id'] for row in profile['topics'])
                self.assertTrue(all(row['angle'] == ANGLES[(TODAY.toordinal() +
                                                            topic_order.index(row['topic_id'])) % len(ANGLES)]
                                    for row in published['daily_briefing']['background_segments']))
            if name == 'cap-reserve-short':
                self.assertEqual(result['daily_audio']['reason'], 'daily_budget')
                self.assertFalse(tts_calls, 'the renderer must not run without protected daily reserve')
            elif tts_failure:
                audio = result['daily_audio']
                self.assertEqual(audio['audio_status'], 'pending')
                self.assertEqual(audio['reason'], 'synthesis_failed')
                self.assertIsNone(audio['audio_url'])
                self.assertIsNone(audio['duration_seconds'])
                self.assertEqual(audio['chapter_offsets'], [])
                for key in ('episode', 'revision_id', 'script_hash', 'voice_name'):
                    self.assertNotIn(key, audio)
                self.assertGreater(len(fixture_tts.calls), 0)
                self.assertEqual(job_result[0]['status'], 'complete')
                self.assertFalse(job_result[0]['scorecard']['critical_failed'])
                self.assertGreater(len(published['sections']), 0)
                self.assertTrue(published['daily_briefing']['briefing_script'])
                self.assertEqual(published['audio'], audio)
                self.assertNotEqual(published['audio'].get('revision_id'), 'prior-revision')
                self.assertNotEqual(published['audio'].get('audio_url'), '/api/daily/audio')
            else:
                # The daily API advertises a successfully rendered edition as
                # ready; only its underlying chapter manifest says recorded.
                self.assertEqual(result['daily_audio']['audio_status'], 'ready')
                self.assertEqual(len(tts_calls), 1)
                self.assertEqual(len(tts_calls[0]['chapters']),
                                 len([row for row in published['sections'] if row.get('cluster_id')]) +
                                 len(published['daily_briefing']['background_segments']))
                rendered_episode = result['daily_audio']['episode']
                self.assertEqual(rendered_episode['id'], published['id'])
                self.assertEqual(rendered_episode['revision_id'], published['revision_id'])
                expected_hash, _ = episodes_audio._fingerprints(
                    rendered_episode, episodes_audio._chapter_scripts(rendered_episode),
                    result['daily_audio']['voice_name'], 'fixture', DEFAULT_MODEL)
                self.assertEqual(rendered_episode['audio']['revision_id'], published['revision_id'])
                self.assertEqual(rendered_episode['audio']['script_hash'], expected_hash)
                transcript = ' '.join(re.sub(r'\s+([,.!?;:])', r'\1',
                                             re.sub(r'\s*\[\d+\]', '', row['text']))
                                      for row in published['daily_briefing']['briefing_script'])
                self.assertEqual(' '.join(row['spoken_text'] for row in tts_calls[0]['chapters']), transcript)
            self.assertEqual(json.loads(edition_path.read_text())['edition']['audio']['audio_status'],
                             result['daily_audio']['audio_status'])
            if verify_delivery:
                self._verify_delivery_and_morning_report(root, profile, published, sources,
                                                         result, run_report, repeat_suppressed)
            return published

    def _verify_delivery_and_morning_report(self, root, profile, edition, sources,
                                            result, run_report, repeat_suppressed):
        state = root / 'report-state'
        profile_id = profile_identifier(profile)
        profile_dir = state / 'generated' / 'profiles' / profile_id
        profile_dir.mkdir(parents=True)
        (state / 'episode-jobs').mkdir(parents=True)
        (state / 'profile.json').write_text(json.dumps(profile), encoding='utf-8')
        report_status = {
            'ok': True, 'last_run_at': NOW.isoformat(),
            'daily_run': {**run_report, 'status': 'complete', 'local_date': TODAY.isoformat(),
                          'repeat_suppressed': repeat_suppressed,
                          'daily_audio': result['daily_audio']},
        }
        (state / 'status.json').write_text(json.dumps(report_status), encoding='utf-8')
        bundle = {'editions': {'editions': [edition]}, 'sources': {'sources': sources}}
        (profile_dir / 'current.json').write_text(json.dumps(bundle), encoding='utf-8')

        api_payload = {'generated': True, 'edition': edition, 'sources': sources, 'history': []}
        server = ThreadingHTTPServer(('127.0.0.1', 0), preview_server.Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(preview_server, 'profile_response',
                              return_value=(200, {'profile': profile})), \
                    patch.object(preview_server, 'edition_response', return_value=(200, api_payload)), \
                    patch.object(preview_server, 'status_response', return_value=(200, report_status)), \
                    patch.object(preview_server.CORRECTIONS, 'apply_edition',
                                 side_effect=lambda payload, _profile: payload):
                base = f'http://127.0.0.1:{server.server_address[1]}'
                with urlopen(base + '/api/edition?profile=active', timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    live_payload = json.loads(response.read())
                with urlopen(base + '/api/status', timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    live_status = json.loads(response.read())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(live_payload['edition']['id'], edition['id'])
        self.assertEqual(live_payload['edition']['audio']['audio_status'], 'ready')
        self.assertNotIn('history', live_payload, 'Today API stays lean')
        self.assertEqual(live_status['daily_run']['status'], 'complete')
        self.assertEqual(live_status['daily_run']['daily_audio']['audio_status'], 'ready')

        lines = build_report(state_dir=state,
                             ledger_file=Path(__file__).parent / 'fixtures/morning-report/ledger.json',
                             prices_file=Path(__file__).parent / 'fixtures/morning-report/prices.json',
                             now=NOW)
        self.assertEqual(len(lines), 5)
        self.assertRegex(lines[0], r'^Edition: ready at 07:00 ET; [3-9]\d* stories$')
        spoken = re.search(r'^Narration: (?:off \(flag_disabled\)|written); ([\d.]+) min / (\d+) words; audio ready$', lines[1])
        self.assertIsNotNone(spoken, lines[1])
        self.assertGreaterEqual(float(spoken.group(1)), 3.0)
        self.assertLessEqual(float(spoken.group(1)), 6.0)
        self.assertRegex(lines[2], r'^Feeds: \d+ failing$')
        self.assertEqual(lines[3], 'Spend (estimate): today $0.12/$0.50 ($0.38 left); month $1.20/$5.00 ($3.80 left)')
        self.assertEqual(lines[4], 'Real builds run: daily 1; episodes 0')

    def test_six_daily_supply_scenarios_through_script_check_json_and_audio(self):
        for name in ('rich', 'one-item', 'zero-item', 'repeat-heavy', 'search-failure', 'cap-reserve-short'):
            with self.subTest(day=name):
                edition = self._run_case(name)
                if name == 'one-item':
                    self.assertTrue(any(row.get('kicker') == 'Update:' for row in edition['sections']))
                if name == 'zero-item':
                    self.assertFalse(any(row.get('supply_origin') == 'feed' for row in edition['sections']))

    def test_five_realistic_fictional_days_use_the_full_ladder_without_repeats(self):
        for name in ('rich', 'thin', 'repeat-heavy', 'one-topic', 'many-topics'):
            with self.subTest(day=name):
                self._run_case(name)

    def test_one_three_and_six_topic_scripts_report_actual_duration_from_cited_fixtures(self):
        background_fixture = json.loads((Path(__file__).parent / 'fixtures' /
                                         'daily-script-background.json').read_text(encoding='utf-8'))

        def fixture_background(topic_id, *, angle, remaining_words, exclude_ids):
            segment_id = f"stored-reference-{topic_id}-{angle}"
            if segment_id in exclude_ids or topic_id not in background_fixture:
                return None
            return {'segment_id': segment_id, 'source_kind': 'reference',
                    'scorecard_passed': True, 'reviewed_at': '2026-11-01',
                    'sentences': [{'text': text,
                                   'source_sentence_ids': [f'{segment_id}-sentence-{index}'],
                                   'citation_numbers': [1]}
                                  for index, text in enumerate(background_fixture[topic_id], 1)]}

        for name, topic_count in (('one-topic', 1), ('thin', 3), ('many-topics', 6)):
            with self.subTest(day=name, topics=topic_count):
                edition = self._run_case(name, background_provider=fixture_background)
                daily = edition['daily_briefing']
                self.assertEqual(len(daily['topic_allocation']['topics']), topic_count)
                self.assertGreater(daily['duration_minutes'], 0)
                self.assertEqual(daily['duration_seconds'],
                                 round(daily['spoken_words'] * 60 / daily['narrator_words_per_minute']))

                script_facts = [row for row in daily['briefing_script']
                                if row.get('kind') in {'claim', 'background'}]
                self.assertTrue(script_facts)
                evidence_by_id = {}
                for section in edition['sections']:
                    for link in section.get('claim_links', []):
                        for sentence_id in link.get('source_sentence_ids', []):
                            evidence_by_id[sentence_id] = (link['text'], link['citation_numbers'])
                for segment in daily['background_segments']:
                    for sentence in segment.get('sentences', []):
                        for sentence_id in sentence.get('source_sentence_ids', []):
                            evidence_by_id[sentence_id] = (sentence['text'], sentence['citation_numbers'])
                for line in script_facts:
                    self.assertTrue(line['source_sentence_ids'], line)
                    self.assertTrue(line['citation_numbers'], line)
                    self.assertTrue(set(line['source_sentence_ids']) <= set(evidence_by_id), line)
                    self.assertTrue(any(
                        line['cited_text'] == text + ''.join(f' [{number}]' for number in citations)
                        for sentence_id in line['source_sentence_ids']
                        for text, citations in [evidence_by_id[sentence_id]]), line)
                normalized = [' '.join(row['text'].casefold().split()) for row in script_facts]
                self.assertEqual(len(normalized), len(set(normalized)),
                                 'factual material is never repeated to meet duration')
                if topic_count < 6:
                    if name in {'one-topic', 'thin'}:
                        self.assertTrue(daily['background_segments'],
                                        'multi-sentence cited fixture background remains eligible')
                        self.assertTrue(any(row['kind'] == 'background_intro' and
                                            row['text'].startswith('Worth knowing.')
                                            for row in daily['briefing_script']))
                    else:
                        self.assertFalse(daily['background_segments'],
                                         'one-sentence cited background is dropped by the final content gate')
                        self.assertFalse(any(row['kind'] == 'background_intro'
                                             for row in daily['briefing_script']))

    def test_daily_audio_flows_through_http_and_morning_report(self):
        self._run_case('rich', verify_delivery=True)

    def test_tts_failure_keeps_approved_daily_text_ready_without_stale_player_metadata(self):
        self._run_case('rich', tts_failure=True)


def _fixture_episode_renderer(episode, calls, *args, **kwargs):
    calls.append(episode)
    return daily_audio.render_episode_audio(episode, *args, **kwargs)


if __name__ == '__main__':
    unittest.main()
