"""Topic starter profiles use only topic-scoped fixture retrieval and temp storage."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
import os
from pathlib import Path
import tempfile
import unittest

from dev.ingest.topic_profiles import (DEFAULT_EXCLUSIONS, TopicProfileStore,
                                      build_topic_profile, follow_topic)


FIXTURE = Path(__file__).with_name('fixtures') / 'topic-profile-topics.json'


class TopicProfileTests(unittest.TestCase):
    def setUp(self):
        self.topics = json.loads(FIXTURE.read_text())['topics']
        self.tiers = {}
        for topic in self.topics:
            for index in range(5):
                domain = f'{topic["id"]}-source-{index + 1}.example.test'
                self.tiers[domain] = {'tier': 'primary' if index < 3 else 'major',
                                      'reason': 'Fictional reviewed authority for this fixture.'}
        self.temp = tempfile.TemporaryDirectory()
        self.store = TopicProfileStore(Path(self.temp.name) / 'topic-profiles.json')
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def retriever(self, topic):
        def retrieve(query, topic_id):
            self.calls.append((topic_id, query))
            rows, subareas = [], []
            for index in range(5):
                host = f'{topic_id}-source-{index + 1}.example.test'
                evidence = f'{topic["label"]}: fictional authority record {index + 1}.'
                rows.append({'name': f'Fictional Authority {index + 1}',
                             'url': f'https://{host}/research/topic-{topic_id}',
                             'topic_ids': [topic_id], 'relevance_evidence': evidence,
                             'rank': 10 - index, 'body': 'must remain transient'})
                if index == 0:
                    subareas.extend([
                        {'label': 'Official reports', 'source_url': f'https://{host}/research/topic-{topic_id}',
                         'evidence': evidence},
                        {'label': 'Unsupported invention', 'source_url': f'https://{host}/research/topic-{topic_id}',
                         'evidence': 'This phrase is absent from retrieval evidence.'},
                    ])
            # A high-ranked result from another topic must never leak across.
            wrong = self.topics[(self.topics.index(topic) + 1) % len(self.topics)]
            wrong_host = f'{wrong["id"]}-source-1.example.test'
            rows.append({'name': 'Wrong topic authority',
                         'url': f'https://{wrong_host}/research/other-topic',
                         'topic_ids': [wrong['id']], 'relevance_evidence': 'Different topic.', 'rank': 1000})
            return {'sources': rows, 'sub_areas': subareas}
        return retrieve

    def test_ten_topic_profiles_use_only_own_scope_and_five_retrieved_authorities(self):
        profiles = []
        for topic in self.topics:
            profile = follow_topic(topic, self.retriever(topic), store=self.store,
                                   tiers=self.tiers, today='2026-11-18T12:00:00Z')
            profiles.append(profile)
            if topic['scope'].startswith('A topic you named'):
                self.assertEqual(profile['source_status'], 'needs_clarification')
                self.assertEqual(profile['sources'], [])
                self.assertEqual(profile['sub_areas'], [])
                continue
            self.assertEqual(profile['source_status'], 'ready')
            self.assertEqual(len(profile['sources']), 5)
            self.assertEqual(len({row['domain'] for row in profile['sources']}), 5)
            self.assertTrue(all(row['domain'].startswith(f'{topic["id"]}-source-')
                                for row in profile['sources']))
            self.assertTrue(all(row['authority_tier'] in {'primary', 'major'} for row in profile['sources']))
            self.assertEqual(profile['sub_areas'], [{'label': 'Official reports',
                'source_url': profile['sources'][0]['url'],
                'evidence': profile['sources'][0]['relevance_evidence']}])
            self.assertNotIn('body', json.dumps(profile))
            self.assertTrue(all(value in profile['aliases'] for value in [topic['label'], *topic['include']]))
            self.assertEqual(profile['exclusions'][:len(DEFAULT_EXCLUSIONS)], list(DEFAULT_EXCLUSIONS))
            self.assertEqual(profile['scope'], '' if topic['scope'].startswith('A topic you named') else topic['scope'])

        self.assertEqual(len(self.store.list()), 10)
        self.assertEqual(len(self.calls), 8, 'ambiguous terms wait for clarification before retrieval')
        for topic_id, query in self.calls:
            selected = next(item for item in self.topics if item['id'] == topic_id)
            self.assertIn(selected['label'], query)
            self.assertNotIn('A topic you named', query)
        mercury = self.store.get('mercury')
        jaguar = self.store.get('jaguar')
        self.assertTrue(mercury['needs_clarification'])
        self.assertTrue(jaguar['needs_clarification'])
        self.assertEqual(mercury['source_status'], 'needs_clarification')
        self.assertEqual(mercury['clarifying_question'], 'Which part of Mercury should the briefing focus on?')
        self.assertFalse(any(topic_id in {'mercury', 'jaguar'} for topic_id, _query in self.calls))
        self.assertEqual(profiles[4]['suggested_daily_weight'], 3)
        self.assertEqual(profiles[6]['suggested_daily_weight'], 1)
        self.assertEqual(profiles[0]['suggested_daily_weight'], 2)

    def test_topic_profile_is_locally_editable_and_private(self):
        topic = self.topics[0]
        profile = follow_topic(topic, self.retriever(topic), store=self.store, tiers=self.tiers)
        edited = self.store.edit(profile['topic_id'], {
            'scope': 'Reader-edited payment settlement scope',
            'aliases': ['Payment settlement', 'Instant settlement'],
            'sub_areas': [{'label': 'Reader-selected area', 'source_url': profile['sources'][0]['url'],
                           'evidence': profile['sources'][0]['relevance_evidence']}],
            'exclusions': ['Token promotions'], 'daily_weight': 3,
        }, now='2026-11-18T13:00:00Z')
        self.assertEqual(edited['scope'], 'Reader-edited payment settlement scope')
        self.assertEqual(edited['daily_weight'], 3)
        self.assertEqual(edited['suggested_daily_weight'], 2)
        self.assertEqual(self.store.get(profile['topic_id']), edited)
        self.assertEqual(os.stat(self.store.path).st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            self.store.edit(profile['topic_id'], {'sources': []})
        with self.assertRaises(ValueError):
            self.store.edit(profile['topic_id'], {'daily_weight': 4})

    def test_edit_is_idempotent_across_reload_and_keeps_topic_scope_local(self):
        rural = next(topic for topic in self.topics if topic['id'] == 'rural-broadband')
        local_topic = next(topic for topic in self.topics if topic['id'] == 'fernvale-housing')
        rural_profile = follow_topic(rural, self.retriever(rural), store=self.store,
                                     tiers=self.tiers, today='2026-11-18T12:00:00Z')
        local_profile = follow_topic(local_topic, self.retriever(local_topic), store=self.store,
                                         tiers=self.tiers, today='2026-11-18T12:00:00Z')

        changes = {
            'scope': 'Rural fiber coverage and funding in Pinevale',
        }
        edited = self.store.edit(rural_profile['topic_id'], changes, now='2026-11-18T13:00:00Z')
        reloaded_store = TopicProfileStore(self.store.path)
        repeated = reloaded_store.edit(rural_profile['topic_id'], changes, now='2026-11-18T13:00:00Z')
        next_edition_snapshot = {profile['topic_id']: profile for profile in reloaded_store.list()}

        self.assertEqual(repeated, edited, 'reapplying the same edit after reload is idempotent')
        self.assertEqual(edited['topic_id'], rural_profile['topic_id'])
        self.assertEqual(edited['scope'], 'Rural fiber coverage and funding in Pinevale')
        self.assertEqual(edited['exclusions'], rural_profile['exclusions'])
        self.assertEqual(len(next_edition_snapshot), 2)
        self.assertEqual(next_edition_snapshot[rural['id']], edited)
        self.assertEqual(next_edition_snapshot[local_topic['id']], local_profile)
        self.assertEqual(next_edition_snapshot[local_topic['id']]['scope'],
                         'Home building and affordability in Fernvale')
        self.assertNotIn('Pinevale', next_edition_snapshot[local_topic['id']]['scope'])

        local_query = next(query for topic_id, query in self.calls if topic_id == local_topic['id'])
        self.assertIn('Fernvale', local_query)
        self.assertNotIn('Pinevale', local_query)

    def test_following_existing_topic_reuses_its_saved_starter_without_retrieval(self):
        topic = self.topics[0]
        original = follow_topic(topic, self.retriever(topic), store=self.store, tiers=self.tiers)
        calls_before = len(self.calls)
        reused = follow_topic(topic, lambda *_args: self.fail('existing topic must not retrieve again'),
                              store=self.store, tiers=self.tiers)
        self.assertEqual(reused, original)
        self.assertEqual(len(self.calls), calls_before)

    def test_short_or_low_authority_retrieval_stays_incomplete_without_padding(self):
        topic = self.topics[0]
        def sparse(query, topic_id):
            return {'sources': [
                {'name': 'Fictional official', 'url': f'https://{topic_id}-source-1.example.test/research',
                 'topic_ids': [topic_id], 'relevance_evidence': 'Topic evidence.'},
                {'name': 'Fictional wire', 'url': 'https://wire.unknown.example.test/story',
                 'topic_ids': [topic_id], 'relevance_evidence': 'Topic evidence.'},
                {'name': 'Unsafe URL', 'url': 'http://bad.example.test/',
                 'topic_ids': [topic_id], 'relevance_evidence': 'Topic evidence.'},
            ]}
        profile = build_topic_profile(topic, sparse, tiers=self.tiers, today='2026-11-18T12:00:00Z')
        self.assertEqual(len(profile['sources']), 1)
        self.assertEqual(profile['source_status'], 'needs_more_sources')

    def test_profile_rejects_bad_topic_and_requires_retriever(self):
        with self.assertRaises(ValueError):
            build_topic_profile({'id': 'not a slug', 'label': 'X', 'scope': 'X'}, lambda *_: {})
        with self.assertRaises(ValueError):
            build_topic_profile(self.topics[0], None, tiers=self.tiers)


if __name__ == '__main__':
    unittest.main()
