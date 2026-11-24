import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingest import edition_api, generation_api
from ingest.paths import profile_identifier
from ingest.edition_api import edition_response


class EditionApiTests(unittest.TestCase):
    def test_no_generated_output_returns_sample_fallback_signal(self):
        with tempfile.TemporaryDirectory() as temp:
            status, body = edition_response(Path(temp))
        self.assertEqual(status, 200)
        self.assertEqual(body, {'generated': False, 'edition': None, 'sources': [], 'history': []})

    def test_latest_generated_daily_and_sources_are_returned(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            edition = {'id': 'daily-test', 'kind': 'daily', 'generated': True,
                       'sections': [{'id': 'story', 'source_ids': ['source-1']} ]}
            sources = [{'id': 'source-1', 'url': 'https://news.example.test/story'}]
            (root / 'editions.json').write_text(json.dumps({'editions': [edition]}))
            (root / 'sources.json').write_text(json.dumps({'sources': sources}))
            status, body = edition_response(root)
        self.assertEqual(status, 200)
        self.assertTrue(body['generated'])
        self.assertEqual(body['edition']['id'], 'daily-test')
        self.assertEqual(body['sources'][0]['url'], sources[0]['url'])

    def test_prior_generated_editions_are_exposed_with_their_own_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = {'id': 'daily-new', 'revision_id': 'new-r1', 'kind': 'daily', 'generated': True,
                       'sections': [{'id': 'new-story', 'source_ids': ['new-source']} ]}
            previous = {'id': 'daily-old', 'revision_id': 'old-r1', 'kind': 'daily', 'generated': True,
                        'sections': [{'id': 'old-story', 'source_ids': ['old-source']} ]}
            current_sources = [{'id': 'new-source', 'url': 'https://news.example.test/new'}]
            previous_sources = [{'id': 'old-source', 'url': 'https://news.example.test/old'}]
            current_bundle = {'editions': {'editions': [current]}, 'sources': {'sources': current_sources}}
            previous_bundle = {'editions': {'editions': [previous]}, 'sources': {'sources': previous_sources}}
            (root / 'current.json').write_text(json.dumps(current_bundle))
            (root / 'editions-history.json').write_text(json.dumps([current_bundle, previous_bundle]))
            status, body = edition_response(root)
        self.assertEqual(status, 200)
        self.assertEqual([(row['edition']['id'], row['sources'][0]['id']) for row in body['history']],
                         [('daily-old', 'old-source')])

    def test_history_can_be_skipped_for_the_default_today_api_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = {'id': 'daily-new', 'revision_id': 'new-r1', 'kind': 'daily', 'generated': True,
                       'sections': []}
            prior = {'id': 'daily-old', 'revision_id': 'old-r1', 'kind': 'daily', 'generated': True,
                     'sections': []}
            bundle = {'editions': {'editions': [current]}, 'sources': {'sources': []}}
            historical = {'editions': {'editions': [prior]}, 'sources': {'sources': []}}
            (root / 'current.json').write_text(json.dumps(bundle))
            (root / 'editions-history.json').write_text(json.dumps([historical] * 90))
            with patch.object(edition_api, '_history_rows',
                              side_effect=AssertionError('Today must not load or scan history')):
                status, body = edition_response(root, include_history=False)
        self.assertEqual(status, 200)
        self.assertEqual(body['edition']['id'], 'daily-new')
        self.assertEqual(body['history'], [])

    def test_history_remains_available_while_today_uses_the_sample_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sample = {'id': 'daily-sample', 'revision_id': 'sample-r1', 'kind': 'daily',
                      'generated': False, 'sections': []}
            previous = {'id': 'daily-old', 'revision_id': 'old-r1', 'kind': 'daily', 'generated': True,
                        'sections': [{'id': 'old-story', 'source_ids': ['old-source']} ]}
            bundle = {'editions': {'editions': [sample]}, 'sources': {'sources': []}}
            history = {'editions': {'editions': [previous]},
                       'sources': {'sources': [{'id': 'old-source'}]}}
            (root / 'editions.json').write_text(json.dumps(bundle))
            (root / 'sources.json').write_text(json.dumps({'sources': []}))
            (root / 'editions-history.json').write_text(json.dumps([history]))
            status, body = edition_response(root)
        self.assertEqual(status, 200)
        self.assertFalse(body['generated'])
        self.assertEqual(body['history'][0]['edition']['id'], 'daily-old')

    def test_legacy_generated_layout_migrates_to_default_without_changing_originals(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = root / 'generated'
            default = legacy / 'default'
            legacy.mkdir()
            edition = {'id': 'legacy-daily', 'kind': 'daily', 'generated': True,
                       'sections': [{'id': 'story', 'source_ids': ['source-1']} ]}
            editions = {'editions': [edition]}
            sources = {'sources': [{'id': 'source-1', 'url': 'https://news.example.test/story'}]}
            files = {
                'current.json': {'editions': editions, 'sources': sources, 'scorecard': {'ok': True}},
                'editions.json': editions,
                'editions-history.json': [{'editions': editions, 'sources': sources}],
                'sources.json': sources,
            }
            originals = {}
            for name, value in files.items():
                original = json.dumps(value).encode()
                (legacy / name).write_bytes(original)
                originals[name] = original
            with patch.object(edition_api, 'GENERATED_DIR', default), \
                 patch.object(edition_api, 'LEGACY_GENERATED_DIR', legacy):
                status, body = edition_api.edition_response()
            self.assertEqual(status, 200)
            self.assertTrue(body['generated'])
            self.assertEqual(body['edition']['id'], 'legacy-daily')
            for name, original in originals.items():
                self.assertEqual((default / name).read_bytes(), original)
                self.assertEqual((legacy / name).read_bytes(), original)

    def test_active_lookup_returns_exact_words_only_legacy_edition_and_history(self):
        fixture = Path(__file__).resolve().parent / 'fixtures/legacy-state'
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            shutil.copytree(fixture / 'generated', state / 'generated')
            shutil.copy2(fixture / 'profile.json', state / 'profile.json')
            default = state / 'generated/default'
            with patch.object(generation_api, 'PROFILE_PATH', state / 'profile.json'), \
                 patch.object(generation_api, 'GENERATED_DIR', state / 'generated'), \
                 patch.object(edition_api, 'GENERATED_DIR', default), \
                 patch.object(edition_api, 'LEGACY_GENERATED_DIR', state / 'generated'):
                status, body = edition_api.edition_response(profile='active')
            self.assertEqual(status, 200)
            self.assertEqual(body['edition']['id'], 'legacy-live-daily-2026-11-17')
            self.assertEqual(len(body['edition']['sections']), 2)
            self.assertEqual([row['edition']['id'] for row in body['history']],
                             ['legacy-live-daily-2026-11-16'])

    def test_default_and_active_profile_lookups_stay_isolated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            generated = root / 'generated'
            default = generated / 'default'
            legacy = generated
            profile = {'version': 1, 'words': 'fictional profile', 'minutes': 12,
                       'topics': [{'id': 'payments', 'label': 'Payments', 'scope': '',
                                   'include': ['payments'], 'exclude': []}]}
            profile_dir = generation_api.profile_generated_dir(profile, generated)
            default_edition = {'id': 'scheduled-default', 'kind': 'daily', 'generated': True,
                               'sections': [{'id': 'default-story', 'source_ids': []}]}
            profile_edition = {'id': 'private-profile', 'kind': 'daily', 'generated': True,
                               'profile_id': profile_identifier(profile),
                               'sections': [{'id': 'profile-story', 'source_ids': []}]}
            empty_sources = {'sources': []}
            default.mkdir(parents=True)
            profile_dir.mkdir(parents=True)
            (default / 'current.json').write_text(json.dumps({'editions': {'editions': [default_edition]},
                                                               'sources': empty_sources}))
            (profile_dir / 'current.json').write_text(json.dumps({'editions': {'editions': [profile_edition]},
                                                                  'sources': empty_sources}))
            with patch.object(generation_api, 'PROFILE_PATH', root / 'profile.json'), \
                 patch.object(generation_api, 'GENERATED_DIR', generated), \
                 patch.object(edition_api, 'GENERATED_DIR', default), \
                 patch.object(edition_api, 'LEGACY_GENERATED_DIR', legacy):
                generation_api.save_profile(profile, root / 'profile.json')
                status, default_body = edition_api.edition_response()
                active_status, active_body = edition_api.edition_response(profile='active')
            self.assertEqual((status, active_status), (200, 200))
            self.assertEqual(default_body['edition']['id'], 'scheduled-default')
            self.assertEqual(active_body['edition']['id'], 'private-profile')

    def test_changed_active_profile_never_inherits_legacy_edition(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            generated = root / 'generated'
            generated.mkdir()
            legacy = {'id': 'legacy-headline-only', 'revision_id': 'legacy-r1', 'kind': 'daily',
                      'generated': True, 'sections': [{'id': 'legacy-story', 'source_ids': ['old-source']} ]}
            old_profile = {'version': 1, 'words': 'Payments headlines', 'minutes': 12,
                           'topics': [{'id': 'payments', 'label': 'Payments', 'scope': '',
                                       'include': ['payments'], 'exclude': []}]}
            changed_profile = {**old_profile, 'words': 'Payments and insurance policy',
                               'topics': [{**old_profile['topics'][0], 'scope': 'Pinevale insurance policy'}]}
            bundle = {'editions': {'editions': [legacy]}, 'sources': {'sources': [{'id': 'old-source'}]}}
            (generated / 'current.json').write_text(json.dumps(bundle))
            (generated / 'editions.json').write_text(json.dumps(bundle['editions']))
            (generated / 'sources.json').write_text(json.dumps(bundle['sources']))
            profile_file = root / 'profile.json'
            profile_file.write_text(json.dumps(old_profile))
            with patch.object(generation_api, 'PROFILE_PATH', profile_file), \
                 patch.object(generation_api, 'GENERATED_DIR', generated), \
                 patch.object(edition_api, 'GENERATED_DIR', generated / 'default'), \
                 patch.object(edition_api, 'LEGACY_GENERATED_DIR', generated):
                self.assertTrue(edition_api.edition_response(profile='active')[1]['generated'])
                generation_api.save_profile(changed_profile, profile_file)
                status, body = edition_api.edition_response(profile='active')
                default_body = edition_api.edition_response(profile='default')[1]
            self.assertEqual(status, 200)
            self.assertFalse(body['generated'])
            self.assertIsNone(body['edition'])
            self.assertFalse((generation_api.profile_generated_dir(changed_profile, generated) / 'current.json').exists())
            self.assertEqual(default_body['edition']['id'], 'legacy-headline-only')

    def test_invalid_source_reference_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'editions.json').write_text(json.dumps({'editions': [{'id': 'd', 'kind': 'daily', 'generated': True,
                'sections': [{'source_ids': ['missing']}]}]}))
            (root / 'sources.json').write_text(json.dumps({'sources': []}))
            status, body = edition_response(root)
        self.assertEqual(status, 500)
        self.assertEqual(body['error'], 'generated_source_reference_invalid')


if __name__ == '__main__':
    unittest.main()
