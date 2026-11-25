import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
import io
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, wait as wait_futures
from contextvars import copy_context
from dataclasses import FrozenInstanceError
from threading import Thread
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from contextlib import contextmanager, redirect_stderr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingest import generation_api
from ingest import usage


PROFILE = {'version': 1, 'words': 'Fictional payments and banking interests.', 'minutes': 12,
           'background': None, 'topics': [{'id': 'payments', 'label': 'Payments', 'scope': 'Payment systems',
                                           'include': ['settlement'], 'exclude': [], 'depth': 'standard',
                                           'cadence': 'daily', 'mustFollow': False, 'sourcePhrase': 'payments'}],
           'muted': []}
REAL_SHAPED_PROFILE = {'version': 1, 'words': 'payments and Pinevale health insurance', 'minutes': 12,
                      'background': None, 'topics': [{'id': 'payments', 'label': 'Payments',
                      'scope': 'Payment rails and bank services', 'include': ['payments'], 'exclude': [],
                      'depth': 'standard', 'cadence': 'daily', 'mustFollow': False,
                      'sourcePhrase': 'payments'}], 'muted': []}


class GenerationApiTests(unittest.TestCase):
    def test_shared_topic_pool_is_filtered_to_each_reader(self):
        chips = {'source_id': 'source-1', 'url': 'https://fictional.test/chips', 'topics': ['chips']}
        climate = {'source_id': 'source-2', 'url': 'https://fictional.test/climate', 'topics': ['climate']}
        shared = {'topic_items': {'chips': [chips], 'climate': [climate]}}
        self.assertEqual(generation_api._shared_items_for_profile(
            shared, {'topics': [{'id': 'chips'}]}), [chips])
        self.assertEqual(generation_api._shared_items_for_profile(
            shared, {'topics': [{'id': 'climate'}]}), [climate])
        self.assertEqual(generation_api._shared_items_for_profile(
            {}, {'topics': [{'id': 'chips'}]}), None)
        self.assertNotIn('multi_reader', generation_api._public_daily_run({'status': 'complete'}))
        public = generation_api._public_daily_run({'status': 'complete', 'multi_reader': {
            'local_date': '2026-11-25', 'readers': [
                {'reader_id': 'a1b2c3d4', 'status': 'ready', 'cost_usd': 0.1,
                 'email': 'private@example.test'}]}})
        self.assertEqual(public['multi_reader']['readers'], [
            {'reader_id': 'a1b2c3d4', 'status': 'ready', 'cost_usd': 0.1, 'reason': None}])
        self.assertEqual(public['profiles_completed'], 1)


    def test_profile_accepts_bounded_reader_selected_local_source_ids(self):
        selected = generation_api.validate_profile({**PROFILE, 'local_sources': [
            'fictional-larkspur-feed', 'fictional-mesa-feed']})
        self.assertEqual(selected['local_sources'], ['fictional-larkspur-feed', 'fictional-mesa-feed'])
        with self.assertRaises(ValueError):
            generation_api.validate_profile({**PROFILE, 'local_sources': ['not/a/valid-id']})




    def test_article_fetch_host_summary_keeps_only_bounded_success_counts(self):
        summary = generation_api._article_fetch_by_host([
            {'host': 'www.kffhealthnews.org', 'reason': 'ok', 'method': 'publisher_selector',
             'evidence_chars': 850},
            {'host': 'www.kffhealthnews.org', 'reason': 'no_readable_text', 'method': None,
             'url': 'https://secret.example/path', 'text': 'must not be copied'},
            {'host': 'https://invalid.example/path', 'reason': 'ok', 'method': 'readability'},
        ])
        self.assertEqual(summary, {'www.kffhealthnews.org': {
            'attempted': 2, 'succeeded': 1, 'methods': {'publisher_selector': 1}}})

    def test_cached_article_plan_requires_recent_feed_fresh_publication_and_allowed_access(self):
        from ingest import core
        now = core.utc_now()
        source = {'id': 'fictional', 'enabled': True, 'robots_ok': True,
                  'article_access': 'transient-read', 'topics': ['payments']}
        def item(key, **changes):
            return {'source_id': source['id'], 'url': f'https://news.example.test/{key}',
                    'title': 'Payment settlement systems update', 'snippet': 'Payments settlement systems.',
                    'topics': ['payments'], 'fetched_at': core.iso_date(now - timedelta(minutes=30)),
                    'published': core.iso_date(now - timedelta(hours=12)), **changes}
        rows = [item('stale-feed', fetched_at=core.iso_date(now - timedelta(hours=3))),
                item('stale-story', published=core.iso_date(now - timedelta(hours=73))),
                item('weaker', title='Payments update', snippet='Payments.'), item('best')]
        plan = generation_api._cached_article_plan(PROFILE, rows, [source], now)
        self.assertEqual([row['url'] for row, _ in plan], ['https://news.example.test/best'])
        for change in ({'paywalled': True}, {'enabled': False}, {'ai_bots_disallowed': True},
                       {'article_access': 'headline'}, {'robots_ok': False}):
            self.assertEqual(generation_api._cached_article_plan(PROFILE, rows, [{**source, **change}], now), [])

    def _run_prefetch_feed_case(self, label, *, timed_out=False):
        from ingest import core, score_edition, write_edition

        now = core.utc_now()
        source = {'id': 'fictional-feed-a', 'name': 'Fictional payments outlet',
                  'url': 'https://payments.example.test/feed.xml', 'format': 'rss',
                  'topics': ['payments'], 'enabled': True, 'robots_ok': True,
                  'article_access': 'full-text', 'reuse_terms': 'fictional fixture only',
                  'allowed_domains': ['payments.example.test', 'articles.example.test',
                                      'other-articles.example.test']}
        same_host_peer = {'id': 'fictional-feed-peer', 'name': 'Fictional payments peer',
                          'url': 'https://payments.example.test/peer-feed.xml', 'format': 'rss',
                          'topics': ['payments'], 'enabled': True, 'robots_ok': True,
                          'article_access': 'full-text', 'reuse_terms': 'fictional fixture only',
                          'allowed_domains': list(source['allowed_domains'])}
        other_feed_source = {'id': 'fictional-feed-c', 'name': 'Fictional clearing outlet',
                             'url': 'https://clearing.example.test/feed.xml', 'format': 'rss',
                             'topics': ['payments'], 'enabled': True, 'robots_ok': True,
                             'article_access': 'full-text', 'reuse_terms': 'fictional fixture only',
                             'allowed_domains': ['clearing.example.test', 'PAYMENTS.EXAMPLE.TEST.',
                                                 'other-articles.example.test']}
        cached_event_a = {'source_id': source['id'], 'url': 'https://payments.example.test/event-a',
                          'title': 'Fictional payment settlement update',
                          'snippet': 'A fictional payment settlement schedule changed.',
                          'topics': ['payments'],
                          'published': core.iso_date(now - timedelta(hours=12)),
                          'fetched_at': core.iso_date(now - timedelta(minutes=30))}
        feed_event_b = {'source_id': source['id'], 'url': 'https://payments.example.test/event-b',
                        'title': 'Fictional payment clearing update',
                        'snippet': 'A fictional payment clearing schedule changed.',
                        'topics': ['payments'], 'published': core.iso_date(now - timedelta(hours=12)),
                        'fetched_at': core.iso_date(now - timedelta(minutes=1))}
        feed_body_b = ('A fictional clearing network changed its settlement schedule for a limited group of payment '
                       'participants. The update applies to institutions that use the network for routine transfers '
                       'and settlement processing. Officials described the operational steps in a published notice '
                       'and said implementation will take place in stages. No real organization or event is '
                       'represented by this fixture, and the scenario is provided only to exercise transient body '
                       'handling during a fictional feed refresh.')
        if timed_out:
            source['url'] = 'https://payments.example.test./feed.xml'
            cached_event_a['url'] = 'https://articles.example.test/event-a'
        ready_sources = [source, same_host_peer, other_feed_source] if timed_out else [source]
        stored_items = [dict(cached_event_a)]
        events = []
        ingest_calls = []
        writer_items = []
        writer_fetch_results = []
        plan_observations = []
        shared_context = {}
        workers = []
        prefetch_instances = []
        prefetch_futures = []
        original_plan = generation_api._cached_article_plan
        original_prefetch = core.TransientArticlePrefetch

        def ingest(sources, *_args, **kwargs):
            ingest_calls.append((list(sources), kwargs.get('max_workers')))
            events.append('feed_start')
            if source not in sources:
                events.append('feed_skipped')
                return {'parsed_items': 0, 'fetched_sources': 0, 'errors': 0,
                        'source_results': {source['id']: 'recent_feed_article_first'}}
            if timed_out:
                events.append('feed_timeout_result')
                return {'parsed_items': 0, 'fetched_sources': 0, 'errors': 1,
                        'source_results': {source['id']: 'timeout', same_host_peer['id']: 'ok',
                                           other_feed_source['id']: 'ok'}}
            stored_items[:] = [dict(cached_event_a), dict(feed_event_b)]
            events.append('feed_complete')
            return {'parsed_items': 1, 'fetched_sources': 1, 'errors': 0,
                    'source_results': {source['id']: 'ok'},
                    'transient_feed_bodies': {(source['id'], feed_event_b['url']): feed_body_b},
                    'transient_feed_methods': {(source['id'], feed_event_b['url']): 'feed_full'}}

        def observe_plan(profile, items, sources, now=None):
            events.append('prefetch_plan')
            plan_observations.append(([dict(item) for item in items], list(sources)))
            return original_plan(profile, items, sources, now)

        def prefetch_factory(plan, fetcher):
            prefetch = original_prefetch(plan, fetcher)
            prefetch_instances.append(prefetch)
            prefetch_futures.extend(list(prefetch._futures.values()))
            return prefetch

        def article_transport(url, _source, **_kwargs):
            events.append('article_transport')
            return 'Fictional cached article body.'

        def writer(_profile, **kwargs):
            writer_items.extend(kwargs['items'])
            fetcher = kwargs['article_fetcher']
            events.append('writer_fetch')
            writer_fetch_results.append(fetcher(cached_event_a['url'], source))
            if timed_out:
                writer_fetch_results.append(
                    fetcher('https://other-articles.example.test/uncached-event', source))
                writer_fetch_results.append(
                    fetcher('https://other-articles.example.test/peer-event', same_host_peer))
                writer_fetch_results.append(
                    fetcher('https://other-articles.example.test/third-event', other_feed_source))
                self.assertEqual(fetcher.diagnostics[-3:], [
                    {'source_id': source['id'], 'reason': 'feed_host_timeout'},
                    {'source_id': same_host_peer['id'], 'reason': 'feed_host_timeout'},
                    {'source_id': other_feed_source['id'], 'reason': 'feed_host_timeout'}])
            return ({'editions': [{'id': f'fictional-prefetch-feed-{label}',
                                   'revision_id': f'fictional-prefetch-feed-{label}-r1',
                                   'kind': 'daily', 'generated': True,
                                   'sections': [{'id': 'fictional-story-a',
                                                 'cluster_id': 'fictional-cluster-a',
                                                 'heading': 'Fictional payment settlement update',
                                                 'body': 'A fictional payment schedule changed.',
                                                 'topic_ids': ['payments'],
                                                 'source_ids': [source['id']]}],
                                   'coverage': []}]},
                    {'sources': [{'id': source['id'], 'registry_id': source['id']}]},
                    {'sentence_evidence': {}, 'candidate_attempts': 0,
                                      'candidate_attempt_limit': 0})

        flags = self.root / f'flags-{label}.json'
        flags.write_text('{}')
        if timed_out:
            shared_context.update(
                article_cache={cached_event_a['url']: 'Fictional in-memory cached article.'},
                article_fetch_budget={'calls': 24, 'limit': 24})
        def thread_factory(*args, **kwargs):
            worker = Thread(*args, **kwargs)
            workers.append(worker)
            return worker

        with patch.dict('os.environ', {'BRIEFING_FLAGS_FILE': str(flags), 'BRIEFING_TTS': '0'}), \
                 patch.object(generation_api.core, 'KILL_SWITCH_PATH', self.root / 'kill-switch'), \
                 patch.object(generation_api.core, 'load_store', side_effect=lambda: list(stored_items)), \
                 patch.object(generation_api.core, 'ingest_enabled', side_effect=ingest), \
                 patch.object(generation_api.core, 'fetch_article_text', side_effect=article_transport) as fetch, \
                 patch.object(generation_api.core, 'TransientArticlePrefetch', side_effect=prefetch_factory), \
                 patch.object(generation_api, '_cached_article_plan', side_effect=observe_plan), \
                 patch.object(write_edition, 'generate_edition', side_effect=writer), \
                 patch.object(score_edition, 'score_edition', return_value={
                     'critical_failed': False, 'failed_critical': [], 'checks': []}), \
                 patch.object(generation_api, 'Thread', side_effect=thread_factory):
                try:
                    code, started = generation_api.start_generation(
                        PROFILE, self.root / f'prefetch-feed-output-{label}', registry=ready_sources,
                        shared_context=shared_context)
                    self.assertEqual(code, 202)
                    job = None
                    for _ in range(500):
                        _, job = generation_api.job_response(started['job_id'])
                        if job['status'] in {'complete', 'failed'}:
                            break
                        time.sleep(0.01)
                    self.assertIsNotNone(job, 'generation job did not produce a terminal response')
                    self.assertIn(job['status'], {'complete', 'failed'},
                                  f'generation job did not reach terminal status: {job!r}')
                finally:
                    for worker in workers:
                        worker.join(timeout=10)
                    alive_workers = [worker for worker in workers if worker.is_alive()]
                    if alive_workers:
                        self.fail('fixture generation worker remained alive after bounded join')
                    done_futures, pending_futures = wait_futures(prefetch_futures, timeout=10)
                    if pending_futures:
                        self.fail('fixture prefetch future remained unresolved after bounded wait')
                    future_errors = []
                    for future in done_futures:
                        try:
                            future.result()
                        except BaseException as error:
                            future_errors.append(error)
                    if future_errors:
                        self.fail('fixture prefetch future raised before bounded cleanup')

        self.assertEqual(job['status'], 'complete', job)
        self.assertEqual(ingest_calls, [(ready_sources, 4)])
        self.assertEqual(len(prefetch_instances), 1)
        self.assertEqual(len(prefetch_futures), 0 if timed_out else 1)
        self.assertEqual(len(plan_observations), 1)
        plan_items, plan_sources = plan_observations[0]
        self.assertIn(cached_event_a['url'], [item['url'] for item in plan_items])
        if timed_out:
            self.assertEqual(events, ['feed_start', 'feed_timeout_result', 'prefetch_plan', 'writer_fetch'])
            self.assertEqual(plan_sources, [])
            self.assertEqual(writer_fetch_results,
                             ['Fictional in-memory cached article.', None, None, None])
            self.assertEqual(shared_context['article_fetch_budget'], {'calls': 24, 'limit': 24})
            self.assertEqual(fetch.call_count, 0)
            self.assertEqual([item['url'] for item in writer_items], [cached_event_a['url']])
            return
        self.assertIn(feed_event_b['url'], [item['url'] for item in plan_items])
        self.assertEqual(plan_sources, [source])
        self.assertLess(events.index('feed_complete'), events.index('prefetch_plan'))
        self.assertLess(events.index('prefetch_plan'), events.index('article_transport'))
        self.assertEqual(writer_fetch_results, ['Fictional cached article body.'])
        self.assertEqual(shared_context['article_fetch_budget'], {'calls': 1, 'limit': 24})
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual([item['url'] for item in writer_items],
                         [cached_event_a['url'], feed_event_b['url']])
        self.assertTrue(writer_items[0]['prefetched_article'])
        self.assertEqual(writer_items[1]['feed_body'], feed_body_b)
        self.assertEqual(writer_items[1]['feed_body_method'], 'feed_full')
        self.assertGreaterEqual(len(writer_items[1]['feed_body']), 400)
        self.assertGreaterEqual(len(writer_items[1]['feed_body'].split('.')) - 1, 3)



    def test_article_fetcher_reuses_cached_evidence_and_shares_one_run_cap(self):
        context = {}
        source = {'id': 'fictional-source'}
        with patch.object(generation_api.core, 'fetch_article_text', return_value='fictional article body') as fetch:
            first = generation_api._budgeted_article_fetcher(time.monotonic() + 5, max_fetches=1,
                                                             shared_context=context)
            second = generation_api._budgeted_article_fetcher(time.monotonic() + 5, max_fetches=1,
                                                              shared_context=context)
            self.assertEqual(first('https://news.example.test/story?utm_source=one', source),
                             'fictional article body')
            self.assertEqual(second('https://news.example.test/story?utm_source=two', source),
                             'fictional article body')
            self.assertIsNone(second('https://news.example.test/other', source))
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(context['article_fetch_budget']['calls'], 1)

    def setUp(self):
        generation_api._jobs.clear()
        generation_api._active_job = None
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state_patches = [
            patch.object(generation_api, 'STATUS_PATH', self.root / 'status.json'),
            patch.object(generation_api, 'FEED_HEALTH_PATH', self.root / 'feed-health.json'),
            patch.object(generation_api, 'GENERATION_LOCK_PATH', self.root / 'generation.lock'),
        ]
        for state_patch in self.state_patches:
            state_patch.start()

    def tearDown(self):
        for state_patch in self.state_patches:
            state_patch.stop()
        self.temp.cleanup()

    def test_profile_save_is_atomic_private_and_readable(self):
        path = self.root / 'profile.json'
        status, body = generation_api.save_profile(PROFILE, path)
        self.assertEqual((status, body), (200, {'saved': True}))
        self.assertEqual(generation_api.load_profile(path), PROFILE)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(generation_api.save_profile({'topics': 'bad'}, path)[0], 400)

    def test_today_seen_marker_activates_repeat_suppression_for_that_readers_history(self):
        from ingest.paths import profile_identifier
        from ingest.repeats import filter_candidates
        repeat_fixture = json.loads((Path(__file__).parent / 'fixtures' / 'repeat-suppression.json').read_text())
        profile_path = self.root / 'profile.json'
        profiles_dir = self.root / 'client-profiles'
        generated_root = self.root / 'generated'
        with patch.object(generation_api, 'PROFILE_PATH', profile_path), \
                patch.object(generation_api, 'CLIENT_PROFILES_DIR', profiles_dir), \
                patch.object(generation_api, 'GENERATED_DIR', generated_root):
            self.assertEqual(generation_api.save_profile(PROFILE, profile_path)[0], 200)
            profile_id = profile_identifier(PROFILE)
            bundle = json.loads(json.dumps(repeat_fixture['history'][0]))
            edition = bundle['editions']['editions'][0]
            edition.update({'id': 'daily-seen-fixture', 'revision_id': 'daily-seen-fixture-r1',
                            'generated': True, 'profile_id': profile_id})
            edition.pop('seen_by_reader', None)
            edition.pop('reader_seen_at', None)
            profile_dir = generation_api.profile_generated_dir(PROFILE, generated_root)
            profile_dir.mkdir(parents=True, exist_ok=True)
            (profile_dir / 'current.json').write_text(json.dumps(bundle))
            (profile_dir / 'editions.json').write_text(json.dumps(bundle['editions']))
            (profile_dir / 'sources.json').write_text(json.dumps(bundle['sources']))
            (profile_dir / 'scorecard.json').write_text(json.dumps({}))
            (profile_dir / 'editions-history.json').write_text(json.dumps([bundle]))

            # Seen state is stored in each profile's private edition history.
            # Identical edition identifiers in another reader's history stay unseen.
            second_profile = {**PROFILE, 'words': 'Fictional secondary compliance interests.'}
            self.assertEqual(generation_api.save_profile(second_profile)[0], 200)
            second_profile_dir = generation_api.profile_generated_dir(second_profile, generated_root)
            second_profile_dir.mkdir(parents=True, exist_ok=True)
            second_bundle = json.loads(json.dumps(bundle))
            (second_profile_dir / 'current.json').write_text(json.dumps(second_bundle))
            (second_profile_dir / 'editions-history.json').write_text(json.dumps([second_bundle]))

            status, body = generation_api.mark_edition_seen(
                profile_id, 'daily-seen-fixture', 'daily-seen-fixture-r1',
                now=datetime(2026, 11, 19, 11, 0, tzinfo=timezone.utc))
            self.assertEqual(status, 200)
            self.assertEqual(body, {'seen': True, 'changed': True})
            saved_history = json.loads((profile_dir / 'editions-history.json').read_text())
            saved_edition = saved_history[0]['editions']['editions'][0]
            self.assertTrue(saved_edition['seen_by_reader'])
            self.assertEqual(saved_edition['reader_seen_at'], '2026-11-19T11:00:00Z')
            saved_export = json.loads((profile_dir / 'editions.json').read_text())['editions'][0]
            self.assertTrue(saved_export['seen_by_reader'])
            self.assertEqual(saved_export['reader_seen_at'], '2026-11-19T11:00:00Z')
            replacement = json.loads(json.dumps(bundle))
            replacement_edition = replacement['editions']['editions'][0]
            replacement_edition.update(id='daily-replacement', revision_id='daily-replacement-r1')
            replacement_edition.pop('seen_by_reader', None)
            replacement_edition.pop('reader_seen_at', None)
            generation_api._store_edition(replacement['editions'], replacement['sources'], {}, profile_dir)
            round_trip_history = json.loads((profile_dir / 'editions-history.json').read_text())
            preserved_seen = round_trip_history[1]['editions']['editions'][0]
            self.assertTrue(preserved_seen['seen_by_reader'])
            self.assertEqual(preserved_seen['reader_seen_at'], '2026-11-19T11:00:00Z')
            second_history = json.loads((second_profile_dir / 'editions-history.json').read_text())
            second_edition = second_history[0]['editions']['editions'][0]
            self.assertNotIn('seen_by_reader', second_edition)
            self.assertNotIn('reader_seen_at', second_edition)
            self.assertNotEqual(profile_identifier(second_profile), profile_id)
            still_new, other_counts, _ = filter_candidates(
                {'payments': [repeat_fixture['candidates']['zelle_sol']]}, second_history,
                datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc))
            self.assertEqual(len(still_new['payments']), 1)
            self.assertEqual(other_counts['repeat_suppressed'], 0)
            filtered, counts, _ = filter_candidates(
                {'payments': [repeat_fixture['candidates']['zelle_sol']]}, saved_history,
                datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc))
            self.assertEqual(filtered['payments'], [])
            self.assertEqual(counts['repeat_suppressed'], 1)

    def test_reader_triggered_daily_generation_uses_daily_cost_caller(self):
        from ingest import usage
        observed = []
        def generate(_profile):
            observed.append(usage._CALLER.get())
            return ({'editions': [{'id': 'fictional-daily', 'kind': 'daily', 'sections': []}]},
                    {'sources': []}, {'checks': []})
        code, started = generation_api.start_generation(
            PROFILE, self.root / 'daily-caller', generate=generate, registry=[])
        self.assertEqual(code, 202)
        for _ in range(100):
            _, job = generation_api.job_response(started['job_id'])
            if job['status'] in {'complete', 'failed'}:
                break
            time.sleep(0.01)
        self.assertEqual(job['status'], 'complete', job)
        self.assertEqual(observed, ['daily'])

    def test_history_merge_keeps_seen_duplicate_and_more_than_fourteen_revisions(self):
        def bundle(index, *, seen=False):
            edition = {
                'id': 'fictional-daily', 'revision_id': f'fictional-revision-{index}',
                'kind': 'daily', 'generated': True,
                'generated_at': '2026-11-23T12:00:00Z', 'sections': [],
            }
            if seen:
                edition.update(seen_by_reader=True, reader_seen_at='2026-11-23T12:05:00Z')
            return {'editions': {'editions': [edition]}, 'sources': {'sources': []}}

        recent = [bundle(0), *[bundle(index) for index in range(1, 20)]]
        existing = [bundle(0, seen=True)]
        merged = generation_api._merge_edition_history(recent, existing)
        self.assertEqual(len(merged), 20)
        first = merged[0]['editions']['editions'][0]
        self.assertTrue(first['seen_by_reader'])
        self.assertEqual(first['reader_seen_at'], '2026-11-23T12:05:00Z')

    def test_profile_accepts_only_bounded_weights_for_followed_topics(self):
        path = self.root / 'profile.json'
        weighted = {**PROFILE, 'behavior_weights': {'payments': 1.4}}
        self.assertEqual(generation_api.save_profile(weighted, path)[0], 200)
        self.assertEqual(generation_api.load_profile(path)['behavior_weights'], {'payments': 1.4})
        self.assertEqual(generation_api.save_profile({**PROFILE, 'behavior_weights': {'payments': 1.8}}, path)[0], 400)
        self.assertEqual(generation_api.save_profile({**PROFILE, 'behavior_weights': {'other-topic': 1.2}}, path)[0], 400)

    def test_behavior_weight_changes_do_not_create_a_new_profile_identity(self):
        from ingest.paths import profile_identifier
        self.assertEqual(profile_identifier(PROFILE),
                         profile_identifier({**PROFILE, 'behavior_weights': {'payments': 1.4}}))

    def test_profile_response_reads_saved_profile_and_reports_missing_profile(self):
        path = self.root / 'profile.json'
        self.assertEqual(generation_api.profile_response(path), (404, {'error': 'profile_not_found'}))
        generation_api.save_profile(PROFILE, path)
        self.assertEqual(generation_api.profile_response(path), (200, {'profile': PROFILE}))

    def test_known_profiles_are_limited_to_three_seen_within_thirty_days(self):
        path = self.root / 'profile.json'
        now = datetime.now(timezone.utc)
        profiles = [{**PROFILE, 'words': f'Fictional client profile {index}'} for index in range(5)]
        for profile in profiles:
            generation_api.save_profile(profile, path)
        for profile, days_ago in zip(profiles[:-1], (40, 4, 3, 2)):
            record_path = generation_api.client_profile_path(profile, path)
            record = json.loads(record_path.read_text())
            record['last_seen_at'] = (now - timedelta(days=days_ago)).isoformat().replace('+00:00', 'Z')
            generation_api._atomic_json(record_path, record)
        self.assertEqual(generation_api.load_known_profiles(path, now=now),
                         [profiles[-1], profiles[-2], profiles[-3]])

    def test_mirror_scheduled_edition_rewrites_profile_identity_in_bundle_and_history(self):
        source = {**PROFILE, 'words': 'Fictional source profile'}
        target = {**PROFILE, 'words': 'Fictional target profile'}
        generated = self.root / 'generated'
        source_dir = generated / 'profiles' / generation_api.profile_identifier(source)
        target_dir = generated / 'profiles' / generation_api.profile_identifier(target)
        edition = {'id': 'shared-edition', 'revision_id': 'source-rev', 'generated': True,
                   'profile_id': generation_api.profile_identifier(source)}
        bundle = {'editions': {'editions': [edition]}, 'sources': {'sources': []}, 'scorecard': {}}
        for name in ('current.json', 'editions.json', 'sources.json', 'scorecard.json', 'editions-history.json'):
            generation_api._atomic_json(source_dir / name, [bundle] if name == 'editions-history.json' else bundle)
        prior = {'editions': {'editions': [{'id': 'target-old', 'revision_id': 'target-rev',
                                             'generated': True, 'profile_id': generation_api.profile_identifier(target)}]},
                 'sources': {'sources': []}, 'scorecard': {}}
        generation_api._atomic_json(target_dir / 'editions-history.json', [prior])
        with patch.object(generation_api, 'GENERATED_DIR', generated):
            self.assertTrue(generation_api.mirror_scheduled_edition(source, target))
        current = json.loads((target_dir / 'current.json').read_text())
        self.assertEqual(current['editions']['editions'][0]['profile_id'],
                         generation_api.profile_identifier(target))
        history = json.loads((target_dir / 'editions-history.json').read_text())
        self.assertEqual(history[0]['editions']['editions'][0]['profile_id'],
                         generation_api.profile_identifier(target))
        self.assertEqual([row['editions']['editions'][0]['revision_id'] for row in history],
                         ['source-rev', 'target-rev'])

    def test_job_returns_progress_then_stores_current_and_previous(self):
        directory = self.root / 'generated'
        (directory / 'editions.json').parent.mkdir(parents=True)
        (directory / 'editions.json').write_text(json.dumps({'editions': [{'id': 'old'}]}))
        (directory / 'sources.json').write_text(json.dumps({'sources': []}))
        def generate(profile):
            time.sleep(0.03)
            return ({'editions': [{'id': 'new', 'kind': 'daily', 'generated': True}]},
                    {'sources': []}, {'checks': [], 'article_fetch_outcomes': [
                        {'host': 'paymentsdive.com', 'reason': 'ok', 'method': 'jsonld_article_body',
                         'evidence_chars': 1200}]})
        status, body = generation_api.start_generation(PROFILE, directory, generate=generate, registry=[])
        self.assertEqual(status, 202)
        self.assertTrue(body['job_id'])
        second_status, second = generation_api.start_generation(PROFILE, directory, generate=generate, registry=[])
        self.assertEqual(second_status, 409)
        self.assertEqual(second['job_id'], body['job_id'])
        for _ in range(100):
            status, job = generation_api.job_response(body['job_id'])
            if job['status'] in {'complete', 'failed'}:
                break
            time.sleep(0.01)
        self.assertEqual((status, job['status']), (200, 'complete'))
        self.assertEqual(job['progress']['stage'], 'complete')
        self.assertEqual(json.loads((directory / 'editions.json').read_text())['editions'][0]['id'], 'new')
        self.assertEqual(json.loads((directory / 'previous.json').read_text())['editions'][0]['id'], 'old')
        self.assertEqual(job['run_status']['article_fetch_by_host'], {
            'paymentsdive.com': {'attempted': 1, 'succeeded': 1,
                                 'methods': {'jsonld_article_body': 1}}})

    def test_scheduled_generation_reuses_one_feed_fetch_snapshot_across_profiles(self):
        def generate(profile):
            return ({'editions': [{'id': f"edition-{profile['words']}", 'kind': 'daily',
                                   'generated': True, 'sections': []}]},
                    {'sources': []}, {'checks': []})

        def wait(job_id):
            for _ in range(100):
                code, job = generation_api.job_response(job_id)
                if code != 200 or job['status'] in {'complete', 'failed'}:
                    return code, job
                time.sleep(0.01)
            self.fail('fixture generation did not complete')

        context = {}
        counts = {'parsed_items': 1, 'fetched_sources': 1, 'errors': 0, 'source_results': {}}
        with patch.object(generation_api.core, 'ingest_enabled', return_value=counts) as ingest:
            for index, words in enumerate(('Fictional first client', 'Fictional second client')):
                profile = {**PROFILE, 'words': words}
                code, started = generation_api.start_generation(
                    profile, self.root / f'profile-{index}', generate=generate, registry=[],
                    is_default=True, publish_default=False, shared_context=context)
                self.assertEqual(code, 202)
                result_code, job = wait(started['job_id'])
                self.assertEqual((result_code, job['status']), (200, 'complete'))
        self.assertEqual(ingest.call_count, 1)
        self.assertIn('feed_snapshot', context)

    def test_scheduled_generation_migrates_legacy_default_and_mirrors_the_profile(self):
        generated = self.root / 'generated'
        legacy_bundle = {
            'editions': {'editions': [{'id': 'legacy-default', 'revision_id': 'legacy-r1',
                                       'kind': 'daily', 'generated': True}]},
            'sources': {'sources': []}, 'scorecard': {'passed': True},
        }
        legacy = generated / 'current.json'
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps(legacy_bundle))
        (generated / 'editions.json').write_text(json.dumps(legacy_bundle['editions']))
        (generated / 'sources.json').write_text(json.dumps(legacy_bundle['sources']))
        (generated / 'editions-history.json').write_text(json.dumps([legacy_bundle]))

        def generate(_profile):
            return ({'editions': [{'id': 'scheduled-default', 'revision_id': 'scheduled-r2',
                                   'kind': 'daily', 'generated': True}]},
                    {'sources': []}, {'checks': []})

        default_dir = generated / 'default'
        with patch.object(generation_api, 'GENERATED_DIR', generated), \
             patch.object(generation_api, 'LEGACY_GENERATED_DIR', generated), \
             patch.object(generation_api, 'DEFAULT_EDITION_DIR', default_dir), \
             patch.object(generation_api.core, 'ingest_enabled', return_value={
                 'parsed_items': 0, 'fetched_sources': 0, 'errors': 0, 'source_results': {}}):
            code, started = generation_api.start_generation(
                PROFILE, generate=generate, registry=[], is_default=True)
            self.assertEqual(code, 202)
            for _ in range(100):
                _, job = generation_api.job_response(started['job_id'])
                if job['status'] in {'complete', 'failed'}:
                    break
                time.sleep(0.01)
        self.assertEqual(job['status'], 'complete', job)
        self.assertEqual(json.loads((default_dir / 'previous.json').read_text())['editions'][0]['id'],
                         'legacy-default')
        self.assertEqual(json.loads((default_dir / 'current.json').read_text())['editions']['editions'][0]['id'],
                         'scheduled-default')
        profile_dir = generation_api.profile_generated_dir(PROFILE, generated)
        self.assertEqual(json.loads((profile_dir / 'current.json').read_text())['editions']['editions'][0]['id'],
                         'scheduled-default')
        self.assertEqual(json.loads((default_dir / 'editions-history.json').read_text())[0]['editions']['editions'][0]['id'],
                         'scheduled-default')

    def test_real_shaped_payments_profile_fetches_writes_scores_and_publishes(self):
        from ingest import score_edition, write_edition
        directory = self.root / 'generated'
        history_fixture = Path(__file__).parent / 'fixtures' / 'repeat-suppression.json'
        (directory / 'editions-history.json').parent.mkdir(parents=True, exist_ok=True)
        (directory / 'editions-history.json').write_text(json.dumps(
            json.loads(history_fixture.read_text())['history']))
        edition = {'id': 'payments-live-shaped', 'revision_id': 'payments-live-shaped-r1',
                   'kind': 'daily', 'generated': True, 'generated_at': '2026-11-17T10:00:00Z',
                   'sections': [{'id': f'story-{n}', 'cluster_id': f'cluster-{n}', 'heading': f'Story {n}',
                                 'body': f'Body {n}', 'source_ids': [], 'topic_ids': ['payments']}
                                for n in range(1, 4)], 'coverage': [], 'summary': 'Three stories'}
        source = {'id': 'payments-test', 'name': 'Fictional payments source',
                  'url': 'https://example.org/feed.xml', 'format': 'rss', 'topics': ['payments'],
                  'enabled': True, 'robots_ok': True}
        ingested = {'parsed_items': 1, 'fetched_sources': 1, 'errors': 0,
                    'source_results': {'payments-test': 'ok'}}
        with patch.object(generation_api.core, 'ingest_enabled', return_value=ingested) as fetch, \
             patch.object(write_edition, 'generate_edition', return_value=(
                 {'editions': [edition]}, {'sources': []},
                 {'sentence_evidence': {}, 'repeat_suppressed': 2, 'stale_suppressed': 1,
                  'source_funnels': {'payments': {'sample-source': {'fetched': 4, 'matched': 3,
                                                                      'accepted': 2, 'rejected': 2}}}})) as writer, \
             patch.object(score_edition, 'score_edition', return_value={
                 'critical_failed': False, 'failed_critical': [], 'results': []}) as score:
            code, body = generation_api.start_generation(REAL_SHAPED_PROFILE, directory, registry=[source])
            self.assertEqual(code, 202)
            for _ in range(100):
                _, job = generation_api.job_response(body['job_id'])
                if job['status'] in {'complete', 'failed'}:
                    break
                time.sleep(0.01)
        self.assertEqual(job['status'], 'complete', job)
        self.assertEqual(job['stories_written'], 3)
        self.assertTrue(job['scorecard_path'])
        saved_report = json.loads((directory / job['scorecard_path']).read_text())
        self.assertEqual([row['status'] for row in saved_report['story_results']], ['passed'] * 3)
        self.assertEqual(saved_report['source_funnels']['payments']['sample-source'],
                         {'fetched': 4, 'matched': 3, 'accepted': 2, 'rejected': 2})
        self.assertTrue(json.loads((directory / 'current.json').read_text())['editions']['editions'][0]['generated'])
        self.assertEqual(fetch.call_args.args[0], [source])
        self.assertEqual(writer.call_args.args[0], REAL_SHAPED_PROFILE)
        self.assertEqual(len(writer.call_args.kwargs['recent_history']), 1)
        self.assertTrue(callable(writer.call_args.kwargs['article_fetcher']))
        self.assertEqual(writer.call_args.kwargs['daily_word_target'], 750)
        status = json.loads(generation_api.STATUS_PATH.read_text())
        self.assertEqual((status['repeat_suppressed'], status['stale_suppressed']), (2, 1))
        self.assertEqual(score.call_count, 4)

    def test_default_generation_asks_writer_for_daily_context_depth(self):
        from ingest import score_edition, write_edition
        directory = self.root / 'scheduled-default'
        edition = {'id': 'daily-depth-fixture', 'revision_id': 'daily-depth-fixture-r1',
                   'kind': 'daily', 'generated': True, 'sections': [{
                       'id': 'story-depth', 'cluster_id': 'cluster-depth',
                       'heading': 'Fictional payment schedule changes',
                       'topic_ids': ['payments'], 'source_ids': [],
                       'claim_links': [{'section': 'what_happened',
                           'text': 'A fictional payment network changed its settlement schedule.',
                           'verified': True, 'source_sentence_ids': ['fixture-sentence'],
                           'citation_numbers': [1]}],
                   }], 'coverage': []}
        score = {'critical_failed': False, 'failed_critical': [], 'checks': []}
        flags = self.root / 'flags.json'
        flags.write_text('{}')
        with patch.dict('os.environ', {'BRIEFING_FLAGS_FILE': str(flags), 'BRIEFING_TTS': '0'}), \
             patch.object(generation_api, 'GENERATED_DIR', directory), \
             patch.object(generation_api, 'LEGACY_GENERATED_DIR', directory), \
             patch.object(generation_api, 'DEFAULT_EDITION_DIR', directory), \
             patch.object(generation_api.core, 'ingest_enabled', return_value={
                 'parsed_items': 1, 'fetched_sources': 1, 'errors': 0, 'source_results': {}}), \
             patch.object(generation_api.core, 'load_store', return_value=[]) as load_store, \
             patch.object(write_edition, 'generate_edition', return_value=(
                 {'editions': [edition]}, {'sources': []}, {'sentence_evidence': {}})) as writer, \
             patch.object(score_edition, 'score_edition', return_value=score), \
             patch('dev.content_check.check_content', return_value=[
                 {'name': 'fixture', 'passed': True, 'reasons': ['no issue']} ]):
            code, started = generation_api.start_generation(
                REAL_SHAPED_PROFILE, directory, registry=[], is_default=True)
            self.assertEqual(code, 202)
            for _ in range(100):
                _, job = generation_api.job_response(started['job_id'])
                if job['status'] in {'complete', 'failed'}:
                    break
                time.sleep(0.01)
        self.assertEqual(job['status'], 'complete', job)
        self.assertEqual(writer.call_args.kwargs['daily_word_target'], 750)
        self.assertGreaterEqual(load_store.call_count, 1)
        self.assertEqual(writer.call_args.kwargs['items'], [])

    def test_default_generation_builds_from_cached_unseen_window_after_total_feed_outage(self):
        from ingest import core, score_edition, write_edition
        now = core.utc_now()
        source = {'id': 'payments-cache-fixture', 'name': 'Fictional payments source',
                  'url': 'https://payments.example.test/feed.xml', 'format': 'rss',
                  'topics': ['payments'], 'enabled': True, 'robots_ok': True,
                  'article_access': 'full-text', 'reuse_terms': 'attribution'}
        second_source = {'id': 'payments-cache-fixture-two', 'name': 'Another fictional payments source',
                         'url': 'https://another-payments.example.test/feed.xml', 'format': 'rss',
                         'topics': ['payments'], 'enabled': True, 'robots_ok': True}
        cached = {'source_id': source['id'], 'title': 'Fictional payment settlement change',
                  'snippet': ('A fictional payment network changed its payment settlement schedule. [1] '
                              'The network added a banking review before transfers settle. [1]'),
                  'url': 'https://payments.example.test/settlement-change',
                  'published': core.iso_date(now - timedelta(days=45)),
                  'fetched_at': core.iso_date(now - timedelta(days=45)), 'topics': ['payments']}
        directory = self.root / 'outage-daily'
        edition = {'id': 'cached-outage-fixture', 'revision_id': 'cached-outage-fixture-r1',
                   'kind': 'daily', 'generated': True, 'sections': [{
                       'id': 'cached-story', 'cluster_id': 'cached-cluster',
                       'heading': cached['title'], 'topic_ids': ['payments'],
                       'source_ids': [source['id']], 'body': cached['snippet'],
                       'claim_links': [{'section': 'what_happened', 'text': cached['snippet'],
                           'source_sentence_ids': ['cached-sentence'], 'citation_numbers': [1],
                           'verified': True}]}], 'coverage': []}
        generated_sources = {'sources': [{'id': source['id'], 'publisher': source['name'],
            'url': cached['url'], 'published_at': cached['published'], 'reuse_terms': 'attribution'}]}

        def writer(_profile, **kwargs):
            self.assertEqual(kwargs['items'], [cached], 'the 45-day cached item reaches the daily writer')
            self.assertEqual(kwargs['recent_history'], [], 'this fixture story has not been seen')
            return {'editions': [edition]}, generated_sources, {'sentence_evidence': {}}

        with patch.dict('os.environ', {'BRIEFING_TTS': '0'}), \
             patch.object(generation_api.core, 'load_store', return_value=[cached]), \
             patch.object(generation_api.core, 'ingest_enabled', return_value={
                 'parsed_items': 0, 'fetched_sources': 0, 'errors': 2,
                 'source_results': {source['id']: 'network_error',
                                    second_source['id']: 'network_error'}}), \
             patch.object(write_edition, 'generate_edition', side_effect=writer), \
             patch.object(score_edition, 'score_edition', return_value={
                 'critical_failed': False, 'failed_critical': [], 'checks': []}):
            code, started = generation_api.start_generation(
                REAL_SHAPED_PROFILE, directory, registry=[source, second_source], is_default=True)
            self.assertEqual(code, 202)
            for _ in range(100):
                _, job = generation_api.job_response(started['job_id'])
                if job['status'] in {'complete', 'failed'}:
                    break
                time.sleep(0.01)

        self.assertEqual(job['status'], 'complete', job)
        self.assertEqual(job['stories_written'], 1)
        self.assertEqual(job['run_status']['feed_health_event'], 'outage')
        self.assertEqual(job['run_status']['feeds_disabled'], [])
        self.assertEqual(job['run_status']['feed_health_counts'],
                         {'enabled': 2, 'backing_off': 0, 'disabled_permanent': 0})
        self.assertFalse(generation_api.FEED_HEALTH_PATH.exists())

    def test_zero_candidate_failure_does_not_disable_sources_or_replace_last_good_edition(self):
        from ingest import score_edition, write_edition
        directory = self.root / 'zero-candidates'
        directory.mkdir()
        old_edition = {'editions': [{'id': 'last-good-fictional', 'revision_id': 'r1'}]}
        old_sources = {'sources': []}
        (directory / 'editions.json').write_text(json.dumps(old_edition))
        (directory / 'sources.json').write_text(json.dumps(old_sources))
        (directory / 'current.json').write_text(json.dumps({'editions': old_edition, 'sources': old_sources}))
        source = {'id': 'payments-failing-test', 'name': 'Fictional payments source',
                  'url': 'https://example.org/feed.xml', 'format': 'rss', 'topics': ['payments'],
                  'enabled': True, 'robots_ok': True}
        with patch.object(generation_api.core, 'ingest_enabled', return_value={
                 'parsed_items': 952, 'fetched_sources': 1, 'errors': 1,
                 'source_results': {'payments-failing-test': 'error'}, 'source_stats': {}}), \
             patch.object(write_edition, 'generate_edition', return_value=(
                 {'editions': [{'id': 'empty-fictional', 'kind': 'daily', 'sections': [], 'coverage': []}]},
                 {'sources': []}, {'candidate_attempts': 0, 'candidate_attempt_limit': 0,
                                  'candidate_pool_diagnostics': {'payments': {'candidate_clusters': 0}},
                                  'candidate_results': [], 'sentence_evidence': {}})), \
             patch.object(score_edition, 'score_edition', return_value={
                 'critical_failed': False, 'failed_critical': [], 'checks': []}):
            code, started = generation_api.start_generation(REAL_SHAPED_PROFILE, directory, registry=[source])
            self.assertEqual(code, 202)
            for _ in range(100):
                _, job = generation_api.job_response(started['job_id'])
                if job['status'] in {'complete', 'failed'}:
                    break
                time.sleep(0.01)
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['failure_reason'], 'scorecard_critical_failed:no_passing_stories')
        self.assertEqual(job['run_status']['feeds_disabled'], [])
        self.assertFalse(generation_api.FEED_HEALTH_PATH.exists())
        self.assertEqual(job['run_status']['feed_health_event'], 'outage')
        self.assertEqual(job['run_status']['feed_health_counts'],
                         {'enabled': 1, 'backing_off': 0, 'disabled_permanent': 0})
        self.assertEqual(json.loads((directory / 'editions.json').read_text()), old_edition)
        self.assertEqual(json.loads((directory / 'sources.json').read_text()), old_sources)
        self.assertEqual(json.loads((directory / 'current.json').read_text()),
                         {'editions': old_edition, 'sources': old_sources})

    def test_cost_capped_two_story_edition_is_explicitly_incomplete_and_not_published(self):
        directory = self.root / 'cost-capped'
        directory.mkdir()
        last_good = {'editions': [{'id': 'fictional-last-good', 'kind': 'daily'}]}
        last_sources = {'sources': []}
        last_bundle = {'editions': last_good, 'sources': last_sources}
        (directory / 'editions.json').write_text(json.dumps(last_good))
        (directory / 'sources.json').write_text(json.dumps(last_sources))
        (directory / 'current.json').write_text(json.dumps(last_bundle))
        partial = {'editions': [{'id': 'fictional-budget-limited', 'kind': 'daily',
                                 'sections': [{'cluster_id': 'fictional-one'},
                                              {'cluster_id': 'fictional-two'}]}]}
        sources = {'sources': []}
        report = {'critical_failed': True, 'failed_critical': ['substantive_story_count'],
                  'candidate_results': [{'status': 'budget_exhausted', 'reason': 'cost_cap_reached'}]}

        with patch.object(generation_api.core, 'ingest_enabled', return_value={
                'parsed_items': 0, 'fetched_sources': 0, 'errors': 0, 'source_results': {}}):
            code, started = generation_api.start_generation(
                PROFILE, directory, generate=lambda _profile: (partial, sources, report), registry=[])
        self.assertEqual(code, 202)
        for _ in range(500):
            _, job = generation_api.job_response(started['job_id'])
            if job['status'] in {'complete', 'failed'}:
                break
            time.sleep(0.01)

        self.assertEqual(job['status'], 'failed', job)
        self.assertEqual(job['failure_reason'], 'scorecard_critical_failed:substantive_story_count')
        self.assertEqual(json.loads((directory / 'current.json').read_text()), last_bundle)
        self.assertEqual(json.loads((directory / 'editions.json').read_text()), last_good)

    def test_article_fetcher_skips_requests_that_cannot_fit_the_writer_budget(self):
        deadline = time.monotonic() - 1
        fetcher = generation_api._budgeted_article_fetcher(deadline)
        with patch.object(generation_api.core, 'fetch_article_text') as fetch:
            self.assertIsNone(fetcher('https://example.test/story', {'allow_article_fetch': True}))
        fetch.assert_not_called()

    def test_article_fetcher_caps_transient_page_reads_per_run(self):
        fetcher = generation_api._budgeted_article_fetcher(time.monotonic() + 60, max_fetches=1)
        with patch.object(generation_api.core, 'fetch_article_text', return_value='ephemeral evidence') as fetch:
            self.assertEqual(fetcher('https://example.test/1', {'allow_article_fetch': True}), 'ephemeral evidence')
            self.assertIsNone(fetcher('https://example.test/2', {'allow_article_fetch': True}))
        self.assertEqual(fetch.call_count, 1)

    def test_one_passing_story_does_not_claim_thin_without_exhausting_supply(self):
        from ingest import score_edition, write_edition
        directory = self.root / 'short-generated'
        edition = {'id': 'payments-short', 'revision_id': 'payments-short-r1',
                   'kind': 'daily', 'generated': True, 'generated_at': '2026-11-17T10:00:00Z',
                   'summary': 'A payment service update.',
                   'sections': [{'id': 'story-1', 'cluster_id': 'cluster-1', 'heading': 'A payment service update',
                                 'body': 'The update changes payment processing.', 'source_ids': ['src-1'],
                                 'topic_ids': ['payments']}],
                   'coverage': [{'topic_id': 'payments', 'state': 'covered'}]}
        source = {'id': 'payments-test', 'registry_id': 'payments-test', 'publisher': 'Fictional payments publisher'}
        registry = [{'id': 'payments-test', 'enabled': True, 'reuse_terms': 'attribution'}]
        fake_score = {'critical_failed': False, 'failed_critical': [], 'checks': []}
        with patch.object(generation_api.core, 'ingest_enabled', return_value={
                 'parsed_items': 8, 'source_results': {}, 'errors': 0}), \
             patch.object(write_edition, 'generate_edition', return_value=(
                 {'editions': [edition]}, {'sources': [source]}, {'sentence_evidence': {}})), \
             patch.object(score_edition, 'score_edition', return_value=fake_score):
            code, body = generation_api.start_generation(REAL_SHAPED_PROFILE, directory, registry=registry)
            self.assertEqual(code, 202)
            for _ in range(100):
                _, job = generation_api.job_response(body['job_id'])
                if job['status'] in {'complete', 'failed'}:
                    break
                time.sleep(0.01)
        self.assertEqual(job['status'], 'complete', job)
        self.assertEqual(job['stories_written'], 1)
        published = json.loads((directory / 'current.json').read_text())['editions']['editions'][0]
        self.assertFalse(published['thin_day'])
        self.assertNotIn('availability_note', published)
        self.assertTrue(any(row['status'] == 'not_configured'
                            for row in published['daily_briefing']['supply_ladder']))
        self.assertEqual(json.loads(generation_api.STATUS_PATH.read_text())['stories_written'], 1)

    def test_kill_switch_failure_names_the_reason_without_a_path(self):
        from ingest import write_edition
        with patch.object(generation_api.core, 'ingest_enabled', return_value={
                 'parsed_items': 0, 'source_results': {}, 'errors': 0,
                 'disabled_by_kill_switch': True}), \
             patch.object(write_edition, 'generate_edition', side_effect=AssertionError('writer must not run')):
            code, body = generation_api.start_generation(REAL_SHAPED_PROFILE, self.root / 'generated', registry=[])
            self.assertEqual(code, 202)
            for _ in range(100):
                _, job = generation_api.job_response(body['job_id'])
                if job['status'] in {'complete', 'failed'}:
                    break
                time.sleep(0.01)
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['failure_reason'], 'ingestion_disabled_by_kill_switch')
        self.assertEqual(job['progress']['message'],
                         'The daily briefing is temporarily paused. Please try again later.')
        status = json.loads((self.root / 'status.json').read_text())
        self.assertEqual(status['reason'], 'ingestion_disabled_by_kill_switch')

    def test_failure_reason_names_safe_generation_stage_and_exception_type(self):
        from ingest import write_edition
        with patch.object(generation_api.core, 'ingest_enabled', return_value={
                 'parsed_items': 0, 'source_results': {}, 'errors': 0}), \
             patch.object(write_edition, 'generate_edition', side_effect=RuntimeError('private words and token')):
            _, body = generation_api.start_generation(REAL_SHAPED_PROFILE, self.root / 'failed', registry=[])
            for _ in range(100):
                _, job = generation_api.job_response(body['job_id'])
                if job['status'] in {'complete', 'failed'}:
                    break
                time.sleep(0.01)
        self.assertEqual(job['failure_reason'], 'generation_failed:edition_writer:RuntimeError')
        self.assertEqual(job['progress']['message'],
                         'The daily briefing could not be prepared just now. Please try again later.')
        self.assertNotIn('private words', json.dumps(job))

    def test_runtime_failure_logging_includes_traceback_without_exception_text(self):
        output = io.StringIO()
        def raise_with_private_detail():
            private_detail = 'private profile words and secret marker'
            raise RuntimeError(private_detail)
        with redirect_stderr(output):
            try:
                raise_with_private_detail()
            except RuntimeError as error:
                generation_api._log_generation_traceback(error, 'feed_fetch', 'fictional-job-id')
        logged = output.getvalue()
        self.assertIn('Traceback (most recent call last):', logged)
        self.assertIn('error_type=RuntimeError', logged)
        self.assertIn('No safe diagnostic message available.', logged)
        self.assertNotIn('private profile words', logged)
        self.assertNotIn('secret marker', logged)
        self.assertEqual(generation_api._failure_reason(RuntimeError("can't start new thread"), 'feed_fetch'),
                         'generation_failed:thread_capacity:RuntimeError')
        self.assertEqual(generation_api._failure_message('generation_failed:thread_capacity:RuntimeError'),
                         'The daily briefing could not start just now. Please try again later.')
        for reason in ('ingestion_disabled_by_kill_switch', 'gemini_call_limit',
                       'generation_failed:feed_fetch:ValueError', 'scorecard_critical_failed:too_few_stories'):
            message = generation_api._failure_message(reason)
            self.assertNotRegex(message.lower(), r'kill switch|gemini|runtimeerror|service thread|scorecard|critical quality')
            self.assertTrue(message.endswith('.') or message.endswith('later.'))

    def test_unknown_job_is_not_found(self):
        self.assertEqual(generation_api.job_response('x'), (404, {'error': 'job_not_found'}))

    def test_failed_generation_keeps_last_good_bundle_and_records_safe_reason(self):
        directory = self.root / 'generated'
        directory.mkdir()
        good = {'editions': [{'id': 'good', 'revision_id': 'good-r1'}]}
        sources = {'sources': []}
        (directory / 'current.json').write_text(json.dumps({'editions': good, 'sources': sources}))
        (directory / 'editions.json').write_text(json.dumps(good))
        status_path = self.root / 'status.json'
        health_path = self.root / 'feed-health.json'
        with patch.object(generation_api, 'STATUS_PATH', status_path), \
             patch.object(generation_api, 'FEED_HEALTH_PATH', health_path), \
             patch.object(generation_api.core, 'ingest_enabled', return_value={
                 'parsed_items': 0, 'source_results': {}, 'errors': 0
             }):
            code, body = generation_api.start_generation(PROFILE, directory,
                generate=lambda _profile: (_ for _ in ()).throw(RuntimeError('private upstream details')),
                registry=[])
            self.assertEqual(code, 202)
            for _ in range(100):
                _, job = generation_api.job_response(body['job_id'])
                if job['status'] in {'complete', 'failed'}:
                    break
                time.sleep(0.01)
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(json.loads((directory / 'current.json').read_text())['editions'], good)
        status = json.loads(status_path.read_text())
        self.assertFalse(status['ok'])
        self.assertEqual(status['reason'], 'generation_failed:edition_writer:RuntimeError')
        self.assertNotIn('private upstream details', status['reason'])

    def test_store_keeps_enough_distinct_editions_for_fourteen_day_repeat_window(self):
        directory = self.root / 'generated'
        for index in range(17):
            editions = {'editions': [{'id': 'daily', 'revision_id': f'r{index}'}]}
            generation_api._store_edition(editions, {'sources': []}, {'ok': True}, directory)
        history = json.loads((directory / 'editions-history.json').read_text())
        self.assertEqual(len(history), 17)
        self.assertEqual(history[0]['editions']['editions'][0]['revision_id'], 'r16')
        self.assertEqual(history[-1]['editions']['editions'][0]['revision_id'], 'r0')

    def test_single_failed_feed_is_an_outage_and_keeps_health_unchanged(self):
        source = {'id': 'test-feed', 'enabled': True, 'robots_ok': True}
        health_path = self.root / 'feed-health.json'
        health = {}
        diagnostics = {}
        for _ in range(3):
            disabled = generation_api._update_feed_health([source], health,
                {'test-feed': 'timeout'}, health_path=health_path, diagnostics=diagnostics)
        self.assertEqual(disabled, [])
        self.assertEqual(health, {})
        self.assertEqual(diagnostics['feed_health_event'], 'outage')
        self.assertFalse(health_path.exists())

    def test_job_persists_health_counts_and_skips_backing_off_sources_on_next_run(self):
        registry = [{'id': name, 'enabled': True, 'robots_ok': True} for name in ('a', 'b', 'c', 'd')]
        first = {'parsed_items': 0, 'source_results': {'a': 'http_429', 'b': 'http_410', 'c': 'ok', 'd': 'ok'}}
        second = {'parsed_items': 0, 'source_results': {'c': 'not_modified', 'd': 'ok'}}
        def generate(_profile):
            return {'editions': [{'id': 'fixture', 'kind': 'daily'}]}, {'sources': []}, {'checks': []}
        with patch.object(generation_api.core, 'ingest_enabled', side_effect=[first, second]) as ingest:
            for index in range(2):
                code, started = generation_api.start_generation(PROFILE, self.root / 'counts',
                                                                generate=generate, registry=registry)
                self.assertEqual(code, 202)
                for _ in range(100):
                    _, job = generation_api.job_response(started['job_id'])
                    if job['status'] in {'complete', 'failed'}:
                        break
                    time.sleep(.01)
                self.assertEqual(job['status'], 'complete')
                status = json.loads(generation_api.STATUS_PATH.read_text())
                self.assertEqual(status['feed_health_counts'],
                                 {'enabled': 2, 'backing_off': 1, 'disabled_permanent': 1})
                self.assertIsNone(status['feed_health_event'])
            self.assertEqual(ingest.call_args_list[0].args[0], registry)
            self.assertEqual(ingest.call_args_list[1].args[0], registry[2:])
        health = json.loads(generation_api.FEED_HEALTH_PATH.read_text())
        self.assertEqual(health['a']['consecutive_failures'], 1)
        self.assertEqual(health['a']['last_error'], 'transient')
        self.assertEqual(health['b']['last_error'], 'permanent')
        self.assertEqual(health['c']['consecutive_failures'], 0)

    def test_status_defaults_and_failure_reason_classification(self):
        status, value = generation_api.status_response(self.root / 'missing-status.json')
        self.assertEqual(status, 200)
        self.assertIsNone(value['last_run_at'])
        path = self.root / 'status.json'
        path.write_text(json.dumps({'last_run_at': '2026-11-17T11:02:00Z', 'ok': True,
                                    'items_fetched': 4, 'stories_written': 2,
                                    'feeds_disabled': [{'source_id': 'feed-1', 'reason': 'offline',
                                                        'retry_at': 'later', 'reader_text': 'must not leak'}],
                                    'reason': None, 'extra': 'must not leak'}))
        _, safe = generation_api.status_response(path)
        self.assertNotIn('extra', safe)
        self.assertNotIn('reader_text', safe['feeds_disabled'][0])
        self.assertEqual(generation_api._failure_reason(TimeoutError()), 'generation_timeout')
        self.assertEqual(generation_api._failure_reason(RuntimeError('gemini_call_limit')), 'gemini_call_limit')
        self.assertEqual(generation_api._failure_reason(RuntimeError('Edition generation disabled by kill switch: /private/path')),
                         'ingestion_disabled_by_kill_switch')

    def test_status_exposes_only_safe_daily_run_summary_fields(self):
        fixture = Path(__file__).parent / 'fixtures' / 'daily-run-status.json'
        payload = json.loads(fixture.read_text(encoding='utf-8'))
        payload['daily_run']['daily_briefing']['search_summary'] = 'private query must not leak'
        payload['daily_run']['daily_briefing']['supply_ladder'][0]['topics'] = ['private topic phrase']
        payload['daily_run']['daily_audio']['audio_url'] = '/private/audio.wav'
        payload['daily_run']['failure_reason'] = 'private provider failure'
        payload['daily_run']['profiles_attempted'] = 2
        payload['daily_run']['profiles_completed'] = 1
        payload['daily_run']['topic_supply'] = [
            {'topic_id': 'payments', 'stories': 1, 'candidates': 4, 'state': 'limited',
             'gap': 'Payments: one story after open web search.'},
            {'topic_id': 'bad topic id', 'gap': 'must not leak'}]
        payload['daily_run']['profile_runs'] = [
            {'profile_id': 'a' * 24, 'status': 'complete', 'edition_id': 'fictional-edition',
             'stories': 3, 'topic_count': 2, 'reused_from_profile_id': None,
             'profile_text': 'must not leak'},
            {'profile_id': 'not-an-id', 'status': 'complete', 'edition_id': 'private', 'stories': 99},
        ]
        path = self.root / 'status.json'
        path.write_text(json.dumps(payload), encoding='utf-8')

        _, public = generation_api.status_response(path)
        report = public['daily_run']
        self.assertEqual(report['local_date'], '2026-11-15')
        self.assertEqual(report['topic_supply'], [
            {'topic_id': 'payments', 'stories': 1, 'candidates': 4, 'state': 'limited',
             'gap': 'Payments: one story after open web search.'}])
        self.assertEqual(report['daily_briefing'], {
            'target_minutes': 5, 'spoken_words': 720, 'duration_seconds': 288,
            'supply_ladder': [
                {'rung': 'feed', 'status': 'available'},
                {'rung': 'open_web', 'status': 'available'},
                {'rung': 'followups', 'status': 'not_needed'},
                {'rung': 'background', 'status': 'available'},
            ],
        })
        self.assertEqual(report['daily_narration'], {'status': 'written', 'reason': None})
        self.assertEqual(report['daily_audio'], {'audio_status': 'ready', 'ready_at': '2026-11-15T10:00:00Z'})
        self.assertEqual((report['profiles_attempted'], report['profiles_completed']), (2, 1))
        self.assertEqual(report['profile_runs'], [{'profile_id': 'a' * 24, 'status': 'complete',
                                                   'edition_id': 'fictional-edition', 'stories': 3,
                                                   'gemini_calls': None, 'topic_count': 2,
                                                   'reused_from_profile_id': None}])
        self.assertNotIn('search_summary', report['daily_briefing'])
        self.assertNotIn('failure_reason', report)
        self.assertNotIn('must not leak', json.dumps(report))

        payload['daily_run']['status'] = []
        payload['daily_run']['daily_briefing']['supply_ladder'][0]['status'] = {'bad': 'shape'}
        payload['daily_run']['daily_audio']['audio_status'] = []
        payload['daily_run']['daily_narration'] = {
            'status': 'fallback', 'reason': 'private provider error',
            'verifier_diagnostics': ['PRIVATE_VERIFIER_DETAIL_DO_NOT_EXPOSE'],
            'debug_file': '/private/fictional/verifier-debug.json'}
        path.write_text(json.dumps(payload), encoding='utf-8')
        status_code, malformed = generation_api.status_response(
            path, now=datetime(2026, 11, 16, 12, tzinfo=timezone.utc))
        self.assertEqual(status_code, 200)
        self.assertIsNone(malformed['daily_run']['status'])
        self.assertEqual(malformed['daily_monitor']['status'], 'stale')
        self.assertEqual(malformed['daily_run']['daily_briefing']['supply_ladder'][0]['status'], 'unknown')
        self.assertEqual(malformed['daily_run']['daily_audio']['audio_status'], 'unavailable')
        self.assertEqual(malformed['daily_run']['daily_narration'], {
            'status': 'fallback', 'reason': 'narration_failed',
            'message': 'The narration could not be completed.'})
        self.assertNotIn('PRIVATE_VERIFIER_DETAIL_DO_NOT_EXPOSE', json.dumps(malformed))
        self.assertNotIn('/private/fictional/verifier-debug.json', json.dumps(malformed))

    def test_daily_narration_job_status_keeps_only_safe_status_and_reason(self):
        self.assertEqual(generation_api._daily_narration_job_status({
            'daily_narration': {'status': 'fallback', 'reason': 'narration_writer_shape',
                                'debug_file': '/private/path'}}),
            {'status': 'fallback', 'reason': 'narration_writer_shape',
             'message': 'The narration could not be written in the required format.'})
        self.assertEqual(generation_api._daily_narration_job_status({
            'daily_narration': {'status': 'fallback', 'reason': 'secret token'}}),
            {'status': 'fallback', 'reason': 'narration_failed',
             'message': 'The narration could not be completed.'})
        self.assertEqual(generation_api._daily_narration_job_status({
            'daily_narration': {'status': 'off', 'reason': 'flag_disabled'}}),
            {'status': 'off', 'reason': 'flag_disabled'})
        self.assertEqual(generation_api._safe_narration_exception_reason(
            ValueError('narration_key_permissions')), 'narration_key_permissions')
        self.assertEqual(generation_api._safe_narration_exception_reason(
            ValueError('private path token')), 'ValueError')
        from dev.interpreter_proxy import ProxyError
        proxy_error = ProxyError(502, 'invalid_model_response', 'invalid_json',
                                 {'request': 'must not leak'})
        safe_reason = 'ProxyError:502:invalid_model_response:invalid_json'
        self.assertEqual(generation_api._safe_narration_exception_reason(proxy_error), safe_reason)
        proxy_status = generation_api._daily_narration_job_status(
            {'daily_narration': {'status': 'fallback', 'reason': safe_reason}})
        self.assertEqual(proxy_status['reason'], safe_reason)
        self.assertNotIn('must not leak', json.dumps(proxy_status))

    def test_daily_narration_status_exposes_bounded_attempt_diagnostics_only(self):
        status = generation_api._daily_narration_job_status({'daily_narration': {
            'status': 'fallback', 'reason': 'narration_underdeveloped_with_unused_evidence',
            'lost_at_stage': 'length_gate', 'expansion_used': True,
            'attempts': [{'attempt': 1, 'stories_in': 3, 'words_out': 240,
                          'accepted_words': 220, 'target_words': 700,
                          'target_words_per_story': 223, 'gate': 'soft',
                          'finding_codes': ['narration_underdeveloped_with_unused_evidence'],
                          'repair_pass': False, 'expansion_requested': True, 'expansion_pass': False,
                          'finding_details': [{'section_id': 'fictional-story-1',
                              'story_heading': 'Fictional public notice',
                              'sentence_index': 2, 'code': 'unsupported_sentence',
                              'general_context': False, 'verifier_supported': True,
                              'deterministic_supported': False,
                              'text': 'private rejected draft sentence'},
                              {'section_id': '../private', 'code': 'unsupported_sentence'}],
                          'draft': 'must not leak'},
                         {'attempt': 2, 'stories_in': 3, 'words_out': 680,
                          'accepted_words': 660, 'target_words': 700,
                          'gate': 'pass', 'finding_codes': [],
                          'repair_pass': True, 'expansion_pass': True}],
        }})
        self.assertEqual(status['lost_at_stage'], 'length_gate')
        self.assertTrue(status['expansion_used'])
        self.assertEqual(len(status['attempts']), 2)
        self.assertEqual(status['attempts'][0]['words_out'], 240)
        self.assertTrue(status['attempts'][0]['expansion_requested'])
        self.assertFalse(status['attempts'][0]['expansion_pass'])
        self.assertEqual(status['attempts'][0]['finding_details'], [{
            'section_id': 'fictional-story-1', 'code': 'unsupported_sentence',
            'sentence_index': 2, 'story_heading': 'Fictional public notice',
            'general_context': False, 'verifier_supported': True,
            'deterministic_supported': False}])
        self.assertNotIn('must not leak', json.dumps(status))
        self.assertNotIn('private rejected draft sentence', json.dumps(status))
        self.assertIn('message', status)

    def test_narration_failure_preserves_critical_content_gate_and_clears_script(self):
        edition = {'audio': {'state': 'pending'},
                   'daily_briefing': {'briefing_script': [{'text': 'Old narration'}],
                                      'background_segments': [{'segment_id': 'bg'}],
                                      'spoken_words': 12, 'duration_seconds': 8}}
        report = {'critical_failed': True, 'failed_critical': ['content_quality']}
        self.assertTrue(generation_api._publish_text_only_after_narration_failure(
            edition, report, {'status': 'fallback', 'reason': 'narration_underdeveloped_with_unused_evidence'}))
        self.assertEqual(report['failed_critical'], ['content_quality'])
        self.assertTrue(report['critical_failed'], 'narration fallback must not clear a hard content failure')
        self.assertEqual(report['daily_narration'], {
            'status': 'fallback', 'reason': 'narration_underdeveloped_with_unused_evidence',
            'message': 'The draft stayed short after one evidence based expansion pass.'})
        self.assertEqual(edition['daily_briefing']['briefing_script'], [])
        self.assertEqual(edition['daily_briefing']['background_segments'], [])
        self.assertEqual(edition['daily_briefing']['spoken_words'], 0)
        self.assertTrue(edition['daily_briefing']['text_only'])
        self.assertEqual(edition['audio']['state'], 'pending')

    def test_daily_narration_exceptions_short_scripts_and_empty_results_publish_text_only(self):
        from dev import content_check
        from dev import interpreter_proxy
        from ingest import daily_briefing, daily_narration, score_edition, write_edition
        from ingest.daily_audio import daily_episode_from_edition, render_daily_audio

        stories = [{'id': 'story-fixture', 'cluster_id': 'cluster-fixture',
                    'heading': 'Fictional payment schedule update',
                    'body': 'A fictional payment network changes its settlement process.',
                    'source_ids': ['source-fixture'], 'topic_ids': ['payments']}]
        edition = {'id': 'fictional-narration-fallback', 'revision_id': 'fictional-r1',
                   'kind': 'daily', 'generated': True, 'generated_at': '2026-11-24T06:00:00Z',
                   'local_date': '2026-11-24', 'sections': stories,
                   'coverage': [{'topic_id': 'payments', 'state': 'covered'}]}
        sources = [{'id': 'source-fixture', 'registry_id': 'source-fixture',
                    'publisher': 'Fictional Bulletin', 'url': 'https://example.test/story',
                    'published_at': '2026-11-24', 'reuse_terms': 'attribution'}]
        candidate = {'editions': [edition]}
        detail = {'sentence_evidence': {}, 'candidate_results': [], 'candidate_attempts': 1,
                  'candidate_attempt_limit': 1,
                  'article_read_details': [{'topic_id': 'payments', 'candidate': 1,
                      'source_reads': [{'source_id': 'fixture-source', 'chars': 321, 'words': 49,
                                        'sentences': 5, 'body_sentences': 4,
                                        'feed_body_counted': False}]}],
                  'article_evidence_thresholds': {'single_source_chars': 300,
                      'single_source_sentences': 3, 'corroborating_source_chars': 200,
                      'corroborating_source_sentences': 2, 'required_corroborating_sources': 2}}
        clean_score = {'critical_failed': False, 'failed_critical': [], 'checks': []}
        fixture_key = self.root / 'fixture-narration-key'
        fixture_key.write_text('fixture-only', encoding='utf-8')
        fixture_key.chmod(0o600)
        outcomes = (
            ('type-error', TypeError('fixture-only broken call'), 'TypeError'),
            ('too-short', {'status': 'fallback',
                           'reason': 'narration_underdeveloped_with_unused_evidence'},
             'narration_underdeveloped_with_unused_evidence'),
            ('empty', None, 'narration_unavailable'),
        )

        for case, narration_result, expected_reason in outcomes:
            with self.subTest(case=case):
                directory = self.root / f'narration-{case}'
                candidate_copy = json.loads(json.dumps(candidate))
                narration_patch = (
                    patch.object(daily_narration, 'write_spoken_script', side_effect=narration_result)
                    if isinstance(narration_result, Exception) else
                    patch.object(daily_narration, 'write_spoken_script', return_value=narration_result))

                def assemble(target, *_args, **_kwargs):
                    target['daily_briefing'] = {
                        'briefing_script': [{'kind': 'greeting', 'text': 'Old fixture script.'}],
                        'background_segments': [], 'spoken_words': 9, 'duration_seconds': 4,
                    }

                with patch.dict('os.environ', {'BRIEFING_DAILY_NARRATION': 'on'}), \
                     patch.object(interpreter_proxy, 'KEY_FILE', fixture_key), \
                     patch.object(generation_api.core, 'ingest_enabled', return_value={
                         'parsed_items': 1, 'fetched_sources': 1, 'errors': 0,
                         'source_results': {'source-fixture': 'ok'}}), \
                     patch.object(write_edition, 'generate_edition', return_value=(
                         candidate_copy, {'sources': sources}, detail)), \
                     patch.object(score_edition, 'score_edition', return_value=clean_score), \
                     patch.object(daily_briefing, 'assemble', side_effect=assemble), \
                     patch.object(content_check, 'check_content', return_value=[
                         {'name': 'fixture content checks', 'passed': True, 'reasons': []}]), \
                     narration_patch:
                    code, started = generation_api.start_generation(
                        PROFILE, directory, registry=[], is_default=True, publish_default=False)
                    self.assertEqual(code, 202)
                    for _ in range(200):
                        _, job = generation_api.job_response(started['job_id'])
                        if job['status'] in {'complete', 'failed'}:
                            break
                        time.sleep(0.01)

                self.assertEqual(job['status'], 'complete', job)
                published = job['edition_ready']['edition']
                saved = json.loads((directory / 'current.json').read_text())['editions']['editions'][0]
                self.assertEqual(published['daily_briefing']['briefing_script'], [])
                self.assertTrue(published['daily_briefing']['text_only'])
                self.assertEqual(published['audio']['state'], 'pending')
                self.assertEqual(saved['daily_briefing']['briefing_script'], [])
                self.assertEqual(daily_episode_from_edition(published)['chapters'], [])
                self.assertEqual(render_daily_audio(published)['audio_status'], 'pending')
                self.assertEqual(job['daily_narration']['status'], 'fallback')
                self.assertEqual(job['daily_narration']['reason'], expected_reason)
                saved_report = json.loads((directory / job['scorecard_path']).read_text())
                self.assertEqual(saved_report['article_read_details'], detail['article_read_details'])
                self.assertEqual(saved_report['article_evidence_thresholds'],
                                 detail['article_evidence_thresholds'])

    def test_final_content_gate_replaces_pre_narration_review_and_merges_failures(self):
        report = {'critical_failed': False, 'failed_critical': ['evidence_links'],
                  'content_checks': [{'name': 'quality', 'passed': True}]}
        final_checks = [
            {'name': 'repeats', 'passed': False, 'reasons': ['fictional repeat']},
            {'name': 'quality', 'passed': False, 'reasons': ['fictional spoken defect']},
        ]
        failures = generation_api._record_content_gate(report, final_checks)
        self.assertEqual(failures, ['content_repeats', 'content_quality'])
        self.assertIs(report['content_checks'], final_checks)
        self.assertTrue(report['critical_failed'])
        self.assertEqual(report['failed_critical'],
                         ['evidence_links', 'content_repeats', 'content_quality'])

    def test_final_daily_content_repair_drops_one_named_sponsored_story(self):
        from dev.content_check import check_content
        from ingest import daily_briefing

        stories = []
        sources = []
        for number in range(1, 6):
            source_id = f'fixture-source-{number}'
            stories.append({
                'id': f'fixture-story-{number}', 'cluster_id': f'fixture-cluster-{number}',
                'heading': f'Payment settlement update {number}',
                'body': f'Payment settlement item {number} reports a bank transfer delay. [1] '
                        f'A second payment sentence describes the settlement process. [1]',
                'source_ids': [source_id], 'topic_ids': ['payments'],
                'takeaway': f'Unique payment takeaway number {number}.'})
            sources.append({'id': source_id, 'url':
                            ('https://fictional.test/sponsored/payment' if number == 3 else
                             f'https://fictional.test/payment-{number}'),
                            'published_at': '2026-11-24T08:00:00Z'})
        edition = {'kind': 'daily', 'generated_at': '2026-11-24T12:00:00Z',
                   'sections': stories, 'coverage': [],
                   'daily_briefing': {'background_segments': [], 'briefing_script': []}}
        checks = check_content({'edition': edition, 'sources': sources}, [], PROFILE,
                               now=datetime(2026, 11, 25, tzinfo=timezone.utc))
        self.assertFalse(next(row for row in checks if row['name'] == 'quality')['passed'])

        with patch.object(daily_briefing, 'assemble', side_effect=lambda *_args, **_kwargs: None):
            dropped = generation_api._repair_daily_content_items(
                edition, checks, PROFILE, source_dates={}, source_tiers={},
                recent_history=[], detail={})

        self.assertEqual(dropped, [{'type': 'story', 'item_id': 'fixture-cluster-3',
                                    'label': 'Payment settlement update 3',
                                    'reason': 'sponsored_source_path'}])
        self.assertEqual(len([row for row in edition['sections'] if row.get('cluster_id')]), 4)
        remaining = check_content({'edition': edition, 'sources': sources}, [], PROFILE,
                                  now=datetime(2026, 11, 25, tzinfo=timezone.utc))
        self.assertTrue(next(row for row in remaining if row['name'] == 'quality')['passed'])

    def test_final_daily_content_repair_drops_thin_cited_background_segment(self):
        from dev.content_check import check_content
        from ingest import daily_briefing

        story = {'id': 'fixture-story', 'cluster_id': 'fixture-cluster',
                 'heading': 'Payment settlement update',
                 'body': 'Payment settlement reports a bank transfer delay. [1] '
                         'A second payment sentence describes processing. [1]',
                 'source_ids': ['fixture-source'], 'topic_ids': ['payments'],
                 'takeaway': 'Unique payment takeaway.'}
        segment = {'segment_id': 'fixture-background', 'topic_id': 'payments',
                   'label': 'Payment background',
                   'sentences': [{'text': 'Payments use a settlement process.',
                                  'citation_numbers': [1]}]}
        edition = {'kind': 'daily', 'generated_at': '2026-11-24T12:00:00Z',
                   'sections': [story], 'coverage': [],
                   'daily_briefing': {'background_segments': [segment], 'briefing_script': []}}
        sources = [{'id': 'fixture-source', 'url': 'https://fictional.test/payment',
                    'published_at': '2026-11-24T08:00:00Z'}]
        checks = check_content({'edition': edition, 'sources': sources}, [], PROFILE,
                               now=datetime(2026, 11, 25, tzinfo=timezone.utc))
        self.assertTrue(any('segment 1 (Payment background)' in reason
                            for row in checks if row['name'] == 'quality'
                            for reason in row['reasons']))

        def reassemble(candidate, _profile, *, background_fill, **_kwargs):
            self.assertIsNone(background_fill('payments'))
            candidate['daily_briefing']['background_segments'] = []

        with patch.object(daily_briefing, 'assemble', side_effect=reassemble):
            dropped = generation_api._repair_daily_content_items(
                edition, checks, PROFILE, source_dates={}, source_tiers={},
                recent_history=[], detail={})

        self.assertEqual(dropped, [{'type': 'segment', 'item_id': 'fixture-background',
                                    'label': 'Payment background',
                                    'reason': 'insufficient_cited_substance'}])
        self.assertEqual(edition['daily_briefing']['background_segments'], [])
        remaining = check_content({'edition': edition, 'sources': sources}, [], PROFILE,
                                  now=datetime(2026, 11, 25, tzinfo=timezone.utc))
        self.assertTrue(next(row for row in remaining if row['name'] == 'quality')['passed'])

    def test_generation_publishes_repaired_edition_and_audits_dropped_item(self):
        from dev import content_check
        from ingest import daily_briefing, score_edition, write_edition

        stories = []
        sources = []
        for number in range(1, 6):
            source_id = f'fixture-source-{number}'
            stories.append({'id': f'fixture-story-{number}',
                            'cluster_id': f'fixture-cluster-{number}',
                            'heading': f'Payment settlement update {number}',
                            'body': f'Payment settlement item {number} reports a bank transfer delay. [1] '
                                    'A second payment sentence describes processing. [1]',
                            'source_ids': [source_id], 'topic_ids': ['payments']})
            sources.append({'id': source_id, 'registry_id': source_id})
        candidate = {'editions': [{'id': 'fixture-daily', 'kind': 'daily',
                                   'generated_at': '2026-11-24T12:00:00Z',
                                   'local_date': '2026-11-24', 'sections': stories,
                                   'coverage': [{'topic_id': 'payments', 'state': 'covered'}]}]}
        detail = {'sentence_evidence': {}, 'candidate_results': [],
                  'candidate_attempts': 5, 'candidate_attempt_limit': 5}
        failure = [{'name': 'quality', 'passed': False,
                    'reasons': ['story 3 (Payment settlement update 3): cited source uses a sponsored content path']}]
        passing = [{'name': 'quality', 'passed': True, 'reasons': ['no issue found']}]

        def assemble(edition, *_args, **_kwargs):
            edition['daily_briefing'] = {'spoken_words': 0, 'target_spoken_words': 600,
                                         'background_segments': [], 'briefing_script': []}

        with patch.dict('os.environ', {'BRIEFING_DAILY_NARRATION': 'off'}), \
             patch.object(generation_api.core, 'ingest_enabled', return_value={
                 'parsed_items': 5, 'fetched_sources': 5, 'errors': 0, 'source_results': {}}), \
             patch.object(write_edition, 'generate_edition', return_value=(
                 candidate, {'sources': sources}, detail)), \
             patch.object(score_edition, 'score_edition', return_value={
                 'critical_failed': False, 'failed_critical': [], 'checks': []}), \
             patch.object(daily_briefing, 'assemble', side_effect=assemble), \
             patch.object(content_check, 'check_content', side_effect=[failure, passing]) as check:
            code, started = generation_api.start_generation(
                PROFILE, self.root / 'repaired-generation', registry=[],
                is_default=True, publish_default=False)
            self.assertEqual(code, 202)
            for _ in range(200):
                _, job = generation_api.job_response(started['job_id'])
                if job['status'] in {'complete', 'failed'}:
                    break
                time.sleep(0.01)

        self.assertEqual(job['status'], 'complete', job)
        self.assertEqual(check.call_count, 2)
        edition = job['edition_ready']['edition']
        self.assertEqual(len([row for row in edition['sections'] if row.get('cluster_id')]), 4)
        scorecard = json.loads((self.root / 'repaired-generation' / job['scorecard_path']).read_text())
        self.assertEqual(scorecard['content_gate_dropped_items'], [{
            'type': 'story', 'item_id': 'fixture-cluster-3',
            'label': 'Payment settlement update 3', 'reason': 'sponsored_source_path'}])
        self.assertEqual(scorecard['stories_passed_checks'], 4)

    def test_final_daily_content_repair_does_not_mutate_when_all_stories_fail(self):
        from ingest import daily_briefing
        edition = {'sections': [{'id': 'fixture-story', 'cluster_id': 'fixture-cluster',
                                 'heading': 'Payment update', 'source_ids': ['fixture-source'],
                                 'topic_ids': ['payments']}],
                   'daily_briefing': {'background_segments': []}}
        original = json.loads(json.dumps(edition))
        failed = [{'name': 'quality', 'passed': False,
                   'reasons': ['story 1 (Payment update): sponsored source path']}]
        with patch.object(daily_briefing, 'assemble') as assemble:
            dropped = generation_api._repair_daily_content_items(
                edition, failed, PROFILE, source_dates={}, source_tiers={},
                recent_history=[], detail={})
        self.assertIsNone(dropped)
        self.assertEqual(edition, original)
        assemble.assert_not_called()

    def test_clean_accounts_off_generation_keeps_golden_edition_shape(self):
        from dev import content_check
        from ingest import daily_briefing, score_edition, write_edition

        story = {'kind': 'story', 'id': 'fixture-story', 'cluster_id': 'fixture-cluster',
                 'heading': 'Payment settlement update',
                 'body': 'Payment settlement reports a bank transfer delay. [1] '
                         'A second payment sentence describes processing. [1]',
                 'source_ids': ['fixture-source'], 'topic_ids': ['payments'],
                 'takeaway': 'Unique payment takeaway.'}
        candidate = {'editions': [{'kind': 'daily', 'id': 'fixture-edition',
                                   'generated_at': '2026-11-24T12:00:00Z',
                                   'sections': [story], 'coverage': []}]}
        sources = [{'id': 'fixture-source', 'url': 'https://fictional.test/payment',
                    'published_at': '2026-11-24T08:00:00Z'}]
        golden = json.loads(json.dumps(candidate['editions'][0]))
        golden['daily_briefing'] = {'spoken_words': 300, 'background_segments': [],
                                    'briefing_script': []}

        def assemble(edition, *_args, **_kwargs):
            edition['daily_briefing'] = {'spoken_words': 300, 'background_segments': [],
                                         'briefing_script': []}

        with patch.dict('os.environ', {'BRIEFING_ACCOUNTS': 'off',
                                       'BRIEFING_DAILY_NARRATION': 'off'}), \
             patch.object(generation_api.core, 'ingest_enabled', return_value={
                 'parsed_items': 1, 'fetched_sources': 1, 'errors': 0, 'source_results': {}}), \
             patch.object(write_edition, 'generate_edition', return_value=(
                 candidate, {'sources': sources}, {'sentence_evidence': {}})), \
             patch.object(score_edition, 'score_edition', return_value={
                 'critical_failed': False, 'failed_critical': [], 'checks': []}), \
             patch.object(daily_briefing, 'assemble', side_effect=assemble), \
             patch.object(content_check, 'check_content', return_value=[
                 {'name': name, 'passed': True, 'reasons': ['no issue found']}
                 for name in ('quality', 'citations', 'relevance', 'story count')]) as check:
            code, started = generation_api.start_generation(
                PROFILE, self.root / 'golden-clean', registry=[],
                is_default=True, publish_default=False)
            self.assertEqual(code, 202)
            for _ in range(200):
                _, job = generation_api.job_response(started['job_id'])
                if job['status'] in {'complete', 'failed'}:
                    break
                time.sleep(0.01)

        self.assertEqual(job['status'], 'complete', job)
        actual = job['edition_ready']['edition']
        self.assertEqual(set(actual), {'audio', 'coverage', 'daily_briefing', 'generated_at',
                                       'id', 'kind', 'profile_id', 'revision_id', 'script_hash',
                                       'sections', 'summary', 'thin_day'})
        self.assertEqual(actual['sections'], golden['sections'])
        self.assertEqual(actual['daily_briefing'], golden['daily_briefing'])
        self.assertEqual(actual['audio']['state'], 'pending')
        self.assertEqual(set(actual['audio']), {'revision_id', 'script_hash', 'state'})
        self.assertEqual(check.call_count, 1)
        scorecard = json.loads((self.root / 'golden-clean' / job['scorecard_path']).read_text())
        self.assertNotIn('content_gate_dropped_items', scorecard)

    def test_failed_stories_are_dropped_and_remaining_sources_pruned(self):
        from ingest.score_edition import _check
        stories = [{'id': f's{n}', 'cluster_id': f'c{n}', 'heading': f'H{n}', 'body': f'B{n}',
                    'source_ids': [f'src{n}'], 'topic_ids': ['payments']} for n in range(1, 5)]
        edition = {'sections': stories, 'coverage': [{'topic_id': 'payments', 'state': 'covered'}],
                   'summary': 'old', 'generated_at': '2026-11-17T12:00:00Z', 'audio': {'state': 'recorded'}}
        edition['coverage'][0].update(candidate_count=2, few_results=True)
        sources = [{'id': f'src{n}'} for n in range(1, 5)]
        def scorer(candidate, *_args):
            row = candidate['sections'][0]
            failed = [4] if row['id'] == 's2' else []
            return {'critical_failed': bool(failed), 'failed_critical': failed,
                    'checks': [_check(4, 'Numbers, dates, names', not failed, 'test')]}
        kept_sources, outcomes = generation_api._score_and_filter_stories(edition, sources, [], {}, scorer)
        self.assertEqual([row['id'] for row in edition['sections']], ['s1', 's3', 's4'])
        self.assertEqual([row['id'] for row in kept_sources], ['src1', 'src3', 'src4'])
        self.assertEqual(outcomes[1]['status'], 'dropped')
        self.assertEqual(outcomes[1]['topic_ids'], ['payments'],
                         'story rejection stays attributable to the topic supply funnel')
        self.assertEqual(edition['coverage'][0]['candidate_count'], 2)
        self.assertTrue(edition['coverage'][0]['few_results'])
        self.assertIn('2 fresh candidate(s)', edition['coverage'][0]['reason'])
        self.assertEqual(edition['coverage'][0]['state'], 'covered')
        self.assertEqual(edition['audio']['state'], 'pending')
        self.assertNotEqual(edition['revision_id'], None)

    def test_all_scorecard_dropped_candidates_are_limited_not_quiet(self):
        edition = {'sections': [{'id': 's1', 'cluster_id': 'c1', 'topic_ids': ['health'], 'source_ids': []}],
                   'coverage': [{'topic_id': 'health', 'state': 'covered', 'candidate_count': 3}],
                   'generated_at': '2026-11-17T12:00:00Z'}
        def fail(*_args):
            return {'critical_failed': True, 'failed_critical': [4], 'checks': []}
        generation_api._score_and_filter_stories(edition, [], [], {}, fail)
        self.assertEqual(edition['coverage'][0]['state'], 'limited')
        self.assertIn('Fresh items were found', edition['coverage'][0]['reason'])

    def test_content_failure_codes_are_bounded_deduplicated_and_fail_closed(self):
        self.assertEqual(generation_api._content_failure_codes(None), ['content_unavailable'])
        self.assertEqual(generation_api._content_failure_codes([None]), ['content_unavailable'])
        self.assertEqual(generation_api._content_failure_codes([
            {'name': 'Daily length', 'passed': False},
            {'name': 'Daily length', 'passed': False},
            {'name': 'citations', 'passed': True},
        ]), ['content_daily_length'])
        self.assertEqual(generation_api._content_failure_codes([
            {'name': 'private article text must never become a status code', 'passed': False},
        ]), ['content_unknown'])

    def test_default_daily_content_failure_keeps_last_good_and_skips_narration(self):
        from dev import content_check
        from ingest import daily_briefing, score_edition, write_edition

        directory = self.root / 'content-gated'
        directory.mkdir()
        last_good = {'editions': [{'id': 'fictional-last-good', 'kind': 'daily'}]}
        last_sources = {'sources': []}
        last_bundle = {'editions': last_good, 'sources': last_sources}
        (directory / 'current.json').write_text(json.dumps(last_bundle))
        (directory / 'editions.json').write_text(json.dumps(last_good))
        (directory / 'sources.json').write_text(json.dumps(last_sources))
        stories = [{
            'id': f'fictional-story-{index}', 'cluster_id': f'fictional-cluster-{index}',
            'heading': f'Fictional payment update {index}',
            'body': f'Fictional payment evidence {index}. [1]',
            'source_ids': [f'fictional-source-{index}'], 'topic_ids': ['payments'],
        } for index in range(1, 2)]
        sources = [{'id': f'fictional-source-{index}', 'registry_id': f'fictional-registry-{index}'}
                   for index in range(1, 2)]
        candidate = {'editions': [{'id': 'fictional-rejected-daily', 'kind': 'daily',
                                   'generated_at': '2026-11-22T12:00:00Z',
                                   'local_date': '2026-11-22', 'sections': stories,
                                   'coverage': [{'topic_id': 'payments', 'state': 'covered'}]}]}
        detail = {'sentence_evidence': {}, 'candidate_results': [], 'candidate_attempts': 3,
                  'candidate_attempt_limit': 3}
        clean_score = {'critical_failed': False, 'failed_critical': [], 'checks': []}
        failed_checks = [{'name': 'quality', 'passed': False,
                          'reasons': ['story 1 (Fictional payment update 1): sponsored source path']}]

        def assemble(edition, *_args, **_kwargs):
            edition['daily_briefing'] = {
                'briefing_script': [{'kind': 'greeting', 'text': 'Three fictional updates.'}],
                'spoken_words': 300, 'duration_seconds': 120,
            }

        with patch.object(generation_api.core, 'ingest_enabled', return_value={
                'parsed_items': 3, 'fetched_sources': 3, 'errors': 0, 'source_results': {}}), \
             patch.object(write_edition, 'generate_edition', return_value=(candidate, {'sources': sources}, detail)), \
             patch.object(score_edition, 'score_edition', return_value=clean_score), \
             patch.object(daily_briefing, 'assemble', side_effect=assemble), \
             patch.object(content_check, 'check_content', return_value=failed_checks) as check, \
             patch('dev.interpreter_proxy.call_gemini_json') as narration_transport:
            code, started = generation_api.start_generation(
                PROFILE, directory, registry=[], is_default=True, publish_default=False)
            self.assertEqual(code, 202)
            for _ in range(200):
                _, job = generation_api.job_response(started['job_id'])
                if job['status'] in {'complete', 'failed'}:
                    break
                time.sleep(0.01)

        self.assertEqual(job['status'], 'failed', job)
        self.assertEqual(job['failure_reason'], 'scorecard_critical_failed:content_quality')
        self.assertEqual(json.loads((directory / 'current.json').read_text()), last_bundle)
        check.assert_called_once()
        narration_transport.assert_not_called()
        saved = json.loads((directory / job['scorecard_path']).read_text())
        self.assertEqual(saved['failed_critical'], ['content_quality'])
        self.assertEqual(saved['content_checks'], failed_checks)

    def test_critical_scorecard_failure_does_not_publish(self):
        from ingest import score_edition, write_edition
        with patch.object(generation_api.core, 'ingest_enabled', return_value={
                 'parsed_items': 0, 'source_results': {}, 'errors': 0}), \
             patch.object(write_edition, 'generate_edition', return_value=(
                 {'editions': [{'id': 'candidate', 'revision_id': 'candidate-r1', 'sections': []}]},
                 {'sources': []}, {'sentence_evidence': {}})), \
             patch.object(score_edition, 'score_edition', return_value={
                 'critical_failed': True, 'failed_critical': ['evidence_links']}):
            code, body = generation_api.start_generation(PROFILE, self.root / 'generated', registry=[])
            for _ in range(100):
                _, job = generation_api.job_response(body['job_id'])
                if job['status'] in {'complete', 'failed'}:
                    break
                time.sleep(0.01)
        self.assertEqual(code, 202)
        self.assertEqual(job['error'], 'scorecard_critical_failed')
        self.assertFalse((self.root / 'generated' / 'current.json').exists())
        self.assertIn('evidence_links', json.loads((self.root / 'status.json').read_text())['reason'])
        self.assertTrue(job['scorecard_path'])
        persisted = json.loads((self.root / 'generated' / job['scorecard_path']).read_text())
        self.assertTrue(persisted['critical_failed'])

    def test_generation_enforces_gemini_call_and_wall_clock_caps(self):
        self.assertEqual(generation_api.MAX_GEMINI_CALLS_PER_RUN, 40)
        self.assertEqual(generation_api.MAX_GENERATION_SECONDS, 180)
        self.assertEqual(generation_api.GENERATION_FINALIZE_RESERVE_SECONDS, 10)
        self.assertEqual(generation_api.FEED_FETCH_BUDGET_SECONDS, 60)
        self.assertEqual(generation_api.ARTICLE_EXTRACTION_BUDGET_SECONDS, 80)
        self.assertEqual(generation_api.MAX_ARTICLE_FETCHES_PER_RUN, 24)
        from ingest import write_edition
        # The callback is checked before every outbound call; the 41st is rejected.
        with patch.object(generation_api.core, 'ingest_enabled', return_value={
                 'parsed_items': 0, 'source_results': {}, 'errors': 0}), \
             patch.object(write_edition, 'generate_edition', side_effect=lambda *a, **kw: [kw['before_model_call']() for _ in range(41)]):
            _, body = generation_api.start_generation(PROFILE, self.root / 'generated', registry=[])
            for _ in range(100):
                _, job = generation_api.job_response(body['job_id'])
                if job['status'] in {'complete', 'failed'}:
                    break
                time.sleep(0.01)
        self.assertEqual(job['error'], 'gemini_call_limit')
        self.assertEqual(job['gemini_calls'], 40)

    def test_daily_article_deadline_leaves_thirty_seconds_for_the_writer(self):
        writer_deadline, article_deadline = generation_api._daily_generation_deadlines(1000.0)
        self.assertEqual(writer_deadline, 1170.0)
        self.assertEqual(article_deadline, 1140.0)
        self.assertEqual(writer_deadline - article_deadline, 30.0)

    def test_fill_callback_keeps_the_configured_forty_call_job_cap(self):
        from ingest import write_edition
        def writer(*args, **kwargs):
            for _ in range(40): kwargs['before_model_call']()
            with self.assertRaisesRegex(RuntimeError, 'gemini_call_limit'):
                kwargs['before_model_call']()
            kwargs['on_fill_start']()
            kwargs['before_model_call']()  # 41st refused before provider transport
        with patch.object(generation_api.core, 'ingest_enabled', return_value={
                 'parsed_items': 0, 'source_results': {}, 'errors': 0}), \
             patch.object(write_edition, 'generate_edition', side_effect=writer):
            _, body = generation_api.start_generation(PROFILE, self.root / 'generated', registry=[])
            for _ in range(100):
                _, job = generation_api.job_response(body['job_id'])
                if job['status'] in {'complete', 'failed'}: break
                time.sleep(0.01)
        self.assertEqual(job['error'], 'gemini_call_limit')
        self.assertEqual(job['gemini_calls'], 40)
        self.assertEqual(generation_api.MAX_GEMINI_CALLS_PER_RUN, 40)

    def test_generation_stops_after_wall_clock_budget(self):
        with patch.object(generation_api, 'MAX_GENERATION_SECONDS', 0.01), \
             patch.object(generation_api.core, 'ingest_enabled', side_effect=lambda *a, **kw: (time.sleep(0.02) or {
                 'parsed_items': 0, 'source_results': {}, 'errors': 0})):
            _, body = generation_api.start_generation(PROFILE, self.root / 'generated', registry=[])
            for _ in range(100):
                _, job = generation_api.job_response(body['job_id'])
                if job['status'] in {'complete', 'failed'}:
                    break
                time.sleep(0.01)
        self.assertEqual(job['error'], 'generation_timeout')
        self.assertFalse((self.root / 'generated' / 'current.json').exists())


if __name__ == '__main__':
    unittest.main()
