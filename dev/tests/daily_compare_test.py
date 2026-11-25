import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'dev/tools'))

from dev.test_guard import enforce_test_environment
enforce_test_environment()

import json
import os
import tempfile
import unittest
from argparse import Namespace
from unittest.mock import patch

import compare_daily


def fake_snapshot(*, added_key=False, warnings=()):
    status = {'daily_run': {'status': 'complete', 'stories': 1,
                            'profiles_attempted': 1, 'profiles_completed': 1,
                            'daily_audio': {'audio_status': 'ready'}}}
    edition = {'id': 'fixture-edition', 'sections': [
        {'cluster_id': 'fixture-story', 'topic_ids': ['digital']}],
        'audio': {'audio_status': 'ready'}}
    if added_key:
        edition['new_structure'] = {'shape_only': True}
    return compare_daily.snapshot_result(
        status, edition,
        {'text_call': {'count': 1, 'units': 1, 'cost_usd': '0.01000000'},
         'total_cost_usd': '0.01000000'}, list(warnings))


class DailyCompareTests(unittest.TestCase):
    def test_topic_counts_include_topics_with_no_story(self):
        status = {'daily_run': {'topic_supply': [
            {'topic_id': 'digital', 'stories': 2}]}}
        result = compare_daily.snapshot_result(status, {'sections': []}, {}, [],
                                               topic_ids=['digital', 'finance'])
        self.assertEqual(result['stories_per_topic'], {'digital': 2, 'finance': 0})

    def test_comparison_daily_calls_use_non_reserved_budget(self):
        calls = []

        class Usage:
            @staticmethod
            def usage_caller(name):
                calls.append(name)
                return name

        self.assertEqual(compare_daily._comparison_usage_caller(Usage, 'daily'), 'other')
        self.assertEqual(compare_daily._comparison_usage_caller(Usage, 'episode'), 'episode')
        self.assertEqual(calls, ['other', 'episode'])

    def test_frozen_snapshot_sanitizes_tuple_diagnostic_keys(self):
        safe = compare_daily._json_compatible({'source_results': {('host', 1): 'timeout'}})
        self.assertEqual(safe, {'source_results': {"('host', 1)": 'timeout'}})
        self.assertEqual(json.loads(json.dumps(safe)), safe)

    def test_shape_diff_ignores_content_and_reports_schema_change(self):
        baseline = fake_snapshot()
        same_structure = fake_snapshot(warnings=['old warning'])
        same_structure['stories_per_topic'] = {'digital': 3}
        diff = compare_daily.compare_results(baseline, same_structure)
        self.assertTrue(diff['same_shape'])
        self.assertEqual(diff['stories_per_topic']['b'], {'digital': 3})

        changed = fake_snapshot(added_key=True, warnings=['new warning'])
        diff = compare_daily.compare_results(baseline, changed)
        self.assertFalse(diff['same_shape'])
        self.assertIn('$.new_structure', diff['shape_diffs']['edition_shape']['added'])
        self.assertEqual(diff['new_warnings'], ['new warning'])
        from contextlib import redirect_stdout
        from io import StringIO
        with redirect_stdout(StringIO()):
            self.assertEqual(compare_daily._print_diff('base', 'candidate', diff), 1)

    def test_harness_compares_fake_runs_without_provider_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            ref_a = Path(temp) / 'main'
            ref_b = Path(temp) / 'candidate'
            ref_a.mkdir()
            ref_b.mkdir()
            calls = []

            def fake_child(_tool, checkout, _state, _profile, result_file,
                           *, frozen_input=None, export_input=None, **_kwargs):
                calls.append((checkout.name, frozen_input, export_input))
                result_file = Path(result_file)
                if export_input:
                    Path(export_input).write_text(json.dumps({'items': [], 'counts': {}}))
                    result = fake_snapshot()
                    result['ledger_cost_by_call_type'] = {'total_cost_usd': '0.00200000'}
                else:
                    result = fake_snapshot(warnings=['fixture warning'] if checkout.name == 'candidate' else [])
                result_file.write_text(json.dumps(result))
                return 0, ''

            args = Namespace(repo=Path(temp), ref_a=str(ref_a), ref_b=str(ref_b), max_cost='1.00')
            with patch.dict(os.environ, {'BRIEFING_EPISODES': 'fake'}), \
                 patch.object(compare_daily, '_run_child', side_effect=fake_child):
                from contextlib import redirect_stdout
                from io import StringIO
                output = StringIO()
                with redirect_stdout(output):
                    code = compare_daily._run_comparison(args)

        self.assertEqual(code, 0)
        self.assertIn('status.json shape: same', output.getvalue())
        self.assertIn('new in B: fixture warning', output.getvalue())
        self.assertEqual(len(calls), 3)  # one frozen-input pass, then A and B
        self.assertEqual(calls[1][1], calls[2][1])


if __name__ == '__main__':
    unittest.main()
