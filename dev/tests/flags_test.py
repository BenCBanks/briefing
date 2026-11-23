"""Read-only runtime flags with isolated fixture files, never live settings."""
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
from unittest.mock import patch

from dev.ingest.flags import (effective_flags, flag_enabled, freshness_days,
                              non_default_flags, setting, setting_details)


class FlagsTests(unittest.TestCase):
    def test_default_env_override_and_reload_without_process_mutation(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {}, clear=True):
            os.environ['BRIEFING_STATE_DIR'] = root
            path = Path(root) / 'flags.json'
            self.assertFalse(flag_enabled('retrieval_fill'))
            self.assertFalse(flag_enabled('search_first'))
            self.assertFalse(flag_enabled('daily_narration'))
            self.assertTrue(flag_enabled('writer'))
            self.assertFalse(flag_enabled('relevance_tune'))
            self.assertEqual(setting('episodes'), 'real')
            path.write_text(json.dumps({'retrieval_fill': True, 'search_first': True,
                                        'writer': 'on', 'relevance': 'on', 'daily_narration': True,
                                        'relevance_tune': True}))
            self.assertTrue(flag_enabled('retrieval_fill'))
            self.assertTrue(flag_enabled('search_first'))
            self.assertTrue(flag_enabled('writer'))
            self.assertTrue(flag_enabled('relevance'))
            self.assertTrue(flag_enabled('relevance_tune'))
            self.assertTrue(flag_enabled('daily_narration'))
            self.assertNotIn('BRIEFING_RETRIEVAL_FILL', os.environ)
            with patch.dict(os.environ, {'BRIEFING_RETRIEVAL_FILL': 'off'}):
                self.assertFalse(flag_enabled('retrieval_fill'))
            path.write_text('{"retrieval_fill": false, "writer": false}')
            self.assertFalse(flag_enabled('retrieval_fill'))
            self.assertFalse(flag_enabled('writer'))

    def test_bad_config_never_enables_fill_or_arbitrary_environment(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {}, clear=True):
            path = Path(root) / 'config.json'
            os.environ['BRIEFING_FLAGS_FILE'] = str(path)
            for content in ('{', '[]', '{"retrieval_fill": 1}', '{"retrieval_fill": "yes"}',
                            '{"GEMINI_API_KEY": "fictional"}', ' ' * 16385):
                path.write_text(content)
                self.assertFalse(flag_enabled('retrieval_fill'))
                self.assertNotIn('GEMINI_API_KEY', os.environ)
            path.write_text('{"retrieval_fill": "on"}')
            self.assertTrue(flag_enabled('retrieval_fill'))

    def test_numeric_settings_are_validated_and_read_from_fixture_flags(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {}, clear=True):
            path = Path(root) / 'flags.json'
            os.environ['BRIEFING_FLAGS_FILE'] = str(path)
            self.assertEqual(freshness_days(), 90)
            path.write_text(json.dumps({'freshness_days': 90, 'candidate_attempt_limit': 30,
                                        'story_writer_workers': 2, 'gemini_calls_per_run': 40}))
            self.assertEqual(freshness_days(), 90)
            self.assertEqual(setting('candidate_attempt_limit'), 30)
            self.assertEqual(setting('story_writer_workers'), 2)
            self.assertEqual(setting('gemini_calls_per_run'), 40)
            path.write_text('{"freshness_days": 0, "candidate_attempt_limit": 100}')
            self.assertEqual(freshness_days(), 90)
            self.assertEqual(setting('candidate_attempt_limit'), 30)
            with patch.dict(os.environ, {'BRIEFING_FRESHNESS_DAYS': '45'}):
                self.assertEqual(freshness_days(), 45)
            with patch.dict(os.environ, {'BRIEFING_FRESHNESS_DAYS': '900'}):
                self.assertEqual(freshness_days(), 90)

    def test_each_known_flag_reports_env_file_or_default_source(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {}, clear=True):
            os.environ['BRIEFING_STATE_DIR'] = root
            path = Path(root) / 'flags.json'
            self.assertEqual(setting_details('episodes'),
                             {'value': 'real', 'default': 'real', 'source': 'default'})
            path.write_text(json.dumps({'episodes': 'fake', 'retrieval_fill': True,
                                        'unrecognized': 'private'}))
            self.assertEqual(setting_details('episodes')['source'], 'file')
            self.assertEqual(setting('episodes'), 'fake')
            self.assertEqual(setting_details('retrieval_fill')['source'], 'file')
            with patch.dict(os.environ, {'BRIEFING_EPISODES': 'real'}):
                self.assertEqual(setting_details('episodes'),
                                 {'value': 'real', 'default': 'real', 'source': 'env'})
            with patch.dict(os.environ, {'BRIEFING_EPISODES': 'pretend'}):
                self.assertEqual(setting_details('episodes')['source'], 'file')
                self.assertEqual(setting('episodes'), 'fake')
            effective = effective_flags()
            self.assertNotIn('unrecognized', effective)
            self.assertNotIn('unrecognized', non_default_flags())
