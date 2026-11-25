import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dev.morning_report import summarize
from dev.tools.morning_report import _narration_line


TODAY = '2026-11-18'


def write_fixture(state, *, stories=3, thin=False, edition_date=TODAY, status_ok=True):
    root = Path(state)
    current = root / 'generated' / 'default' / 'current.json'
    current.parent.mkdir(parents=True)
    sections = [
        {'id': f'story-{index}', 'cluster_id': f'fictional-{index}',
         'source_ids': ['source-full', 'source-feed']}
        for index in range(stories)
    ]
    bundle = {'editions': {'editions': [{'id': 'fictional-daily', 'kind': 'daily',
                'local_date': edition_date, 'thin_day': thin, 'sections': sections}]},
              'sources': {'sources': [
                  {'id': 'source-full', 'evidence_depth': 'full-text'},
                  {'id': 'source-feed', 'evidence_depth': 'feed-full'}]}}
    current.write_text(json.dumps(bundle), encoding='utf-8')
    (root / 'status.json').write_text(json.dumps({
        'last_run_at': '2026-11-18T10:00:00Z', 'ok': status_ok,
        'daily_run': {'status': 'complete' if status_ok else 'failed',
                      'usage': {'gemini_calls': 5, 'elapsed_seconds': 42.5},
                      'feeds': {'attempted': 8, 'failed': 1, 'disabled': 0},
                      'failure_reason': None if status_ok else 'generation_failed:edition_writer:RuntimeError'},
    }), encoding='utf-8')


class MorningReportTests(unittest.TestCase):
    def test_narration_proxy_reason_is_shown_without_private_details(self):
        reason = 'ProxyError:502:invalid_model_response:invalid_json'
        self.assertEqual(_narration_line({'daily_narration': {'status': 'fallback', 'reason': reason}}),
                         f'fallback ({reason})')
        self.assertEqual(_narration_line({'daily_narration': {
            'status': 'fallback', 'reason': 'ProxyError:502:secret value'}}),
            'fallback (narration_failed)')

    def test_full_today_report_is_six_lines_and_successful(self):
        with tempfile.TemporaryDirectory() as temp:
            write_fixture(temp)
            lines, code = summarize(temp, TODAY)
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 6)
        self.assertEqual(lines[1], f'Edition: {TODAY} current')
        self.assertEqual(lines[2], 'Stories: 3 / 3 (ready)')
        self.assertIn('Evidence depth: feed-full 1, full-text 1', lines[3])
        self.assertEqual(lines[4], 'Feeds: 8 attempted, 1 failed, 0 disabled')
        self.assertIn('5 Gemini calls, 42.5s', lines[5])

    def test_thin_edition_is_reported_and_nonzero_without_deleting_it(self):
        with tempfile.TemporaryDirectory() as temp:
            write_fixture(temp, stories=1, thin=True)
            lines, code = summarize(temp, TODAY)
            saved = json.loads((Path(temp) / 'generated/default/current.json').read_text())
        self.assertEqual(code, 1)
        self.assertEqual(lines[2], 'Stories: 1 / 3 (thin day)')
        self.assertTrue(saved['editions']['editions'][0]['thin_day'])

    def test_stale_or_missing_edition_fails_even_if_last_run_status_says_ok(self):
        with tempfile.TemporaryDirectory() as temp:
            write_fixture(temp, edition_date='2026-11-17', status_ok=True)
            lines, code = summarize(temp, TODAY)
        self.assertEqual(code, 1)
        self.assertEqual(lines[1], 'Edition: 2026-11-17 (expected 2026-11-18)')

        with tempfile.TemporaryDirectory() as temp:
            Path(temp, 'status.json').write_text(json.dumps({'ok': True}), encoding='utf-8')
            lines, code = summarize(temp, TODAY)
        self.assertEqual(code, 1)
        self.assertEqual(lines[1], 'Edition: missing (expected 2026-11-18)')

    def test_failure_text_is_sanitized(self):
        with tempfile.TemporaryDirectory() as temp:
            write_fixture(temp, status_ok=False)
            payload = json.loads((Path(temp) / 'status.json').read_text())
            payload['daily_run']['failure_reason'] = 'private words and a secret key'
            (Path(temp) / 'status.json').write_text(json.dumps(payload))
            lines, code = summarize(temp, TODAY)
        self.assertEqual(code, 1)
        self.assertIn('failures: generation_failed', lines[-1])
        self.assertNotIn('private words', '\n'.join(lines))

    def test_failed_latest_run_does_not_pass_on_stale_ok_flag(self):
        with tempfile.TemporaryDirectory() as temp:
            write_fixture(temp)
            path = Path(temp) / 'status.json'
            payload = json.loads(path.read_text())
            payload['daily_run']['status'] = 'failed'
            path.write_text(json.dumps(payload))
            _lines, code = summarize(temp, TODAY)
        self.assertEqual(code, 1)


if __name__ == '__main__':
    unittest.main()
