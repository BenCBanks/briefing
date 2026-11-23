"""Fictional correction publication, history and concurrency fixtures; no providers."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dev.ingest.corrections import CorrectionError, CorrectionService
from dev.tests.corrections_test import PROFILE, EPISODE_ID, FixedQueue, edition_payload, episode_job


class CorrectionEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.edition = edition_payload()
        self.edition['sources'] = [{'id': 'fictional-source', 'title': 'Fictional minutes'}]
        story = self.edition['edition']['sections'][0]
        story['body'] = 'The fictional council met Monday. [1] The vote was unanimous. [1]\n\nA second paragraph remains unchanged.'
        story['audio_script']['paragraphs'][0]['text'] = story['body'].split('\n\n')[0]
        story['section_blocks'] = {'what_happened': [story['body'].split('\n\n')[0]]}
        other = deepcopy(story)
        other['id'] = 'story-two'
        self.edition['edition']['sections'].append(other)
        self.edition['edition']['chapters'] = [
            {'id': 'story-one', 'audio': {'file': 'old.wav'}, 'audio_start_seconds': 42},
            {'id': 'story-two', 'audio': {'file': 'unchanged.wav'}}]
        self.job = episode_job()
        self.job['episode']['chapters'].append({'id': 'chapter-two', 'sentences': [
            {'id': 's3', 'text': 'An unrelated fictional update.', 'source_numbers': [1]}],
            'audio': {'state': 'recorded'}})
        self.service = self.new_service()

    def new_service(self):
        return CorrectionService(self.temp.name, edition_reader=lambda _: deepcopy(self.edition),
                                 episode_reader=lambda _p, _i: deepcopy(self.job), audio_queue=FixedQueue())

    def story_patch(self, **changes):
        return dict({'target_type': 'story', 'target_id': 'story-one',
                     'edition_id': 'fictional-edition', 'paragraph_index': 0, 'sentence_index': 1,
                     'old_text': 'The vote was unanimous.', 'new_text': 'The vote was split.',
                     'what_changed': 'Corrected the fictional vote count.',
                     'evidence': [{'source_id': 'fictional-source', 'quote': 'The fictional vote was split.'}]},
                    **changes)

    def episode_patch(self, **changes):
        return dict({'target_type': 'episode', 'target_id': EPISODE_ID,
                     'chapter_id': 'chapter-one', 'sentence_index': 0,
                     'old_text': 'The fictional town changed its schedule.',
                     'new_text': 'The fictional town retained its schedule.',
                     'what_changed': 'Corrected the fictional schedule.',
                     'evidence': [{'source_id': 'fictional-source', 'quote': 'The fictional schedule is unchanged.'}]},
                    **changes)

    def test_daily_sentence_parity_and_only_affected_chapter_stale(self):
        before = deepcopy(self.edition)
        edition = self.service.create(PROFILE, self.story_patch())['edition']
        expected = 'The fictional council met Monday. [1] The vote was split. [1]'
        story = edition['sections'][0]
        self.assertEqual(story['body'].split('\n\n')[0], expected)
        self.assertEqual(story['audio_script']['paragraphs'][0]['text'], expected)
        self.assertEqual(story['section_blocks']['what_happened'], [expected])
        self.assertEqual(edition['sections'][1], before['edition']['sections'][1])
        self.assertTrue(edition['corrected'])
        self.assertEqual(story['correction_note'], self.story_patch()['what_changed'])
        self.assertNotEqual(edition['revision_id'], 'edition-r1')
        self.assertEqual(edition['chapters'][0]['audio']['state'], 'stale')
        self.assertNotIn('audio_start_seconds', edition['chapters'][0])
        self.assertEqual(edition['chapters'][1], before['edition']['chapters'][1])
        self.assertEqual(self.edition, before)

    def test_order_across_stories_and_same_second_is_publication_order(self):
        with patch('dev.ingest.corrections._timestamp', return_value='2026-11-18T00:00:00Z'):
            first = self.service.create(PROFILE, self.story_patch(target_id='story-two'))['edition']
            second = self.service.create(PROFILE, self.story_patch())['edition']
            third = self.service.create(PROFILE, self.story_patch(target_id='story-two',
                old_text='The vote was split.', new_text='The vote was postponed.'))['edition']
        replayed = self.new_service().apply_edition(self.edition, PROFILE)['edition']
        self.assertEqual(replayed, third)
        self.assertEqual(self.service.apply_edition({'edition': third}, PROFILE)['edition'], third)
        self.assertEqual(second['corrections'][-1]['previous_revision_id'], first['revision_id'])
        self.assertEqual(third['corrections'][-1]['previous_revision_id'], second['revision_id'])
        self.edition = {'edition': {'id': 'tomorrow', 'sections': []}, 'sources': []}
        retained = self.new_service().revision(PROFILE, 'edition', 'fictional-edition',
                                              first['revision_id'])
        self.assertEqual(retained['revision_id'], first['revision_id'])
        self.assertEqual([row['body'] for row in retained['sections']],
                         [row['body'] for row in first['sections']])
        self.assertEqual(retained['audio_status'], 'superseded',
                         'the snapshot keeps its publish-time invalidation state')
        self.assertEqual(first['audio_status'], 'regenerating',
                         'queue state is surfaced dynamically without rewriting the snapshot')
        original = self.service.revision(PROFILE, 'edition', 'fictional-edition', 'edition-r1')
        self.assertIn('unanimous', original['sections'][0]['body'])

    def test_episode_revision_retention_evidence_and_stale_audio(self):
        created = self.service.create(PROFILE, self.episode_patch())
        episode = created['episode']
        self.assertTrue(episode['corrected'])
        self.assertEqual(episode['correction_note'], self.episode_patch()['what_changed'])
        self.assertEqual(episode['chapters'][0]['audio']['state'], 'stale')
        self.assertEqual(episode['chapters'][1], self.job['episode']['chapters'][1])
        self.assertEqual(episode['chapters'][0]['sentences'][0]['source_ids'], ['fictional-source'])
        self.assertEqual(episode['chapters'][0]['sentences'][0]['source_numbers'], [1])
        original = self.service.revision(PROFILE, 'episode', EPISODE_ID, 'episode-r1')
        self.assertEqual(original['chapters'], self.job['episode']['chapters'])
        self.assertEqual(self.service.revision(PROFILE, 'episode', EPISODE_ID, episode['revision_id']), episode)
        replayed = self.service.apply_job({'status': 'done', 'episode': episode}, PROFILE)
        self.assertEqual(replayed['episode'], episode)
        self.assertTrue(replayed['corrected'])
        self.assertEqual(len(episode['corrections']), 1)

    def test_invalid_evidence_and_stale_revision_leave_no_event(self):
        for changes in ({'evidence': []}, {'evidence': [{'source_id': 'missing', 'quote': 'Fictional quote'}]},
                        {'evidence': [{'source_id': 'fictional-source', 'quote': '', 'private': 'x'}]},
                        {'previous_revision_id': 'old-revision'}, {'sentence_index': 99}):
            with self.subTest(changes=changes), self.assertRaises(CorrectionError):
                self.service.create(PROFILE, self.story_patch(**changes))
        self.assertEqual(self.service.list(PROFILE), [])

    def test_public_log_allowlist_and_profile_isolation(self):
        self.service.create(PROFILE, self.story_patch())
        public = self.service.list(PROFILE)[0]
        for forbidden in ('content', 'previous_content', 'evidence', 'new_text', 'profile_id'):
            self.assertNotIn(forbidden, public)
        self.assertNotIn('The fictional vote was split.', json.dumps(public))
        self.assertEqual(public['source_ids'], ['fictional-source'])
        other = {'minutes': 8, 'topics': [{'id': 'other-fictional-topic', 'label': 'Other'}]}
        self.assertEqual(self.service.list(other), [])
        with self.assertRaises(CorrectionError) as error:
            self.service.revision(other, 'edition', 'fictional-edition', 'edition-r1')
        self.assertEqual(error.exception.status, 404)
        record = {'profile_id': 'a' * 24, 'private_report': 'private', 'evidence': [], 'id': 'safe'}
        self.assertEqual(self.service.public_record(record), {'id': 'safe'})

    def test_concurrent_writers_publish_once_and_readers_see_complete_revisions(self):
        def publish(_):
            try:
                return self.new_service().create(PROFILE, self.story_patch())['edition']['revision_id']
            except CorrectionError as error:
                return error.code
        def read(_):
            return self.new_service().apply_edition(self.edition, PROFILE)['edition']
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(publish if i % 2 else read, i) for i in range(32)]
            results = [future.result() for future in futures]
        writes = results[1::2]
        self.assertEqual(writes.count('correction_revision_conflict'), 15)
        self.assertEqual(len(self.service.list(PROFILE)), 1)
        for edition in results[::2]:
            expected = 'split' if edition['corrected'] else 'unanimous'
            self.assertIn(expected, edition['sections'][0]['body'])

    def test_history_overlay_and_uncorrected_defaults(self):
        self.assertFalse(self.service.apply_job(self.job, PROFILE)['episode']['corrected'])
        self.assertFalse(self.service.apply_edition(self.edition, PROFILE)['edition']['corrected'])
        self.service.create(PROFILE, self.story_patch())
        payload = {'edition': {'id': 'tomorrow', 'sections': []}, 'history': [self.edition]}
        history = self.service.apply_edition(payload, PROFILE)['history'][0]['edition']
        self.assertTrue(history['corrected'])
        self.assertIn('split', history['sections'][0]['body'])

    def test_failed_publication_leaves_old_log_and_revision_intact(self):
        first = self.service.create(PROFILE, self.story_patch())['edition']
        path = next(Path(self.temp.name).rglob('story-*.jsonl'))
        before = path.read_bytes()
        with patch('dev.ingest.corrections.os.replace', side_effect=OSError('fixture failure')):
            with self.assertRaises(CorrectionError):
                self.service.create(PROFILE, self.story_patch(
                    old_text='The vote was split.', new_text='The vote was postponed.'))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.service.apply_edition(self.edition, PROFILE)['edition'], first)
        self.assertEqual(list(path.parent.glob('*.tmp')), [])

    def test_cross_story_reads_are_consistent_while_publishing(self):
        def publish():
            for i in range(12):
                self.new_service().create(PROFILE, self.story_patch(
                    target_id='story-one' if i % 2 == 0 else 'story-two',
                    old_text='The vote was unanimous.' if i < 2 else f'The vote had {i - 2} supporters.',
                    new_text=f'The vote had {i} supporters.'))
        def read():
            for _ in range(30):
                self.new_service().apply_edition(self.edition, PROFILE)
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(publish)] + [pool.submit(read) for _ in range(4)]
            for future in futures:
                future.result()
        final = self.service.apply_edition(self.edition, PROFILE)['edition']
        self.assertEqual(len(final['corrections']), 12)


if __name__ == '__main__':
    unittest.main()
